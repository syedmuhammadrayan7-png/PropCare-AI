import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app
from backend.services import repository
from backend.stage3 import propcare_deep_agent as stage3


def _use_test_data(tmp_path, monkeypatch):
    for name in ("tenants.json", "units.json", "maintenance.json", "payments.json"):
        source = Path(repository.DATA_DIR) / name
        (tmp_path / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(repository, "DATA_DIR", tmp_path)


class _DeepAgentResult:
    def __init__(self, resolution):
        self.resolution = resolution

    def invoke(self, *_args, **_kwargs):
        return {"structured_response": self.resolution}


def _resolution(**overrides):
    baseline = {
        "tenant_id": "T-1001", "issue_category": "billing", "priority": "low", "specialists_used": ["billing"],
        "assigned_team": "Billing", "maintenance_status": None, "payment_status": "paid", "proposed_credit": None,
        "approved_credit": None, "approval_required": False, "approval_status": None,
        "action_taken": "Reviewed payment status.", "summary": "Your payment status has been reviewed.", "status": "completed",
    }
    baseline.update(overrides)
    return stage3.Stage3Resolution(**baseline)


def test_stage3_builds_real_deep_agent_and_loads_house_rules():
    agent = stage3.build_stage3_agent()
    assert type(agent).__name__ == "CompiledStateGraph"
    assert "Never approve financial credits automatically" in stage3.HOUSE_RULES
    assert stage3.AGENTS_PATH.exists()


def test_stage3_billing_has_no_maintenance_fields(tmp_path, monkeypatch):
    _use_test_data(tmp_path, monkeypatch)
    monkeypatch.setattr(stage3, "build_stage3_agent", lambda: _DeepAgentResult(_resolution()))
    thread_id, result = stage3.start_stage3("T-1001", "Can you check whether my rent for this month has been paid?")
    assert result["status"] == "completed"
    assert result["resolution"]["specialists_used"] == ["billing"]
    assert result["resolution"]["request_id"] is None
    assert result["resolution"]["maintenance_status"] is None
    assert result["resolution"]["assigned_team"] == "Billing"
    assert result["resolution"]["payment_status"] == "paid"
    assert stage3.STAGE3_THREADS[thread_id]["tenant_id"] == "T-1001"


def test_stage3_maintenance_reuse_and_admin_approval(tmp_path, monkeypatch):
    _use_test_data(tmp_path, monkeypatch)
    created = repository.add_maintenance_request("T-1001", "U-MH-B-804", "HVAC", "Heater is not working.", "medium", "Awaiting Assignment")
    open_request = repository.update_maintenance_request(created.request_id, "Awaiting Assignment", "awaiting_assignment")
    repeated = next(item for item in repository.get_requests("T-1001",) if item.request_id == created.request_id)
    assert repeated.request_id == open_request.request_id
    compensation = _resolution(issue_category="HVAC", priority="high", specialists_used=["maintenance", "billing"], request_id=created.request_id, assigned_team="Awaiting Assignment", maintenance_status="awaiting_assignment", proposed_credit=5000, approval_required=True, action_taken="Reused an open HVAC work order and recommended a credit.")
    monkeypatch.setattr(stage3, "build_stage3_agent", lambda: _DeepAgentResult(compensation))
    thread_id, pending = stage3.start_stage3("T-1001", "My heater has failed again and I want compensation.")
    assert pending["status"] == "waiting_for_approval"
    rejected = stage3.resume_stage3(thread_id, "reject")
    assert rejected["resolution"]["approval_status"] == "rejected"
    assert rejected["resolution"]["approval_required"] is False
    assert rejected["resolution"]["maintenance_status"] == "awaiting_assignment"
    assert rejected["resolution"]["assigned_team"] == "Unassigned"
    assert repository.get_requests("T-1001")[-1].request_id == created.request_id


def test_stage3_general_request_includes_real_open_work_and_billing_context(tmp_path, monkeypatch):
    _use_test_data(tmp_path, monkeypatch)
    existing = repository.get_requests("T-1001")[0]
    repository.update_maintenance_request(existing.request_id, "Climate Systems", "in_progress")
    general = _resolution(
        issue_category="general",
        specialists_used=["resident_services"],
        assigned_team=None,
        maintenance_status=None,
        payment_status=None,
        action_taken="Reviewed resident records.",
        summary="No current maintenance issues were found.",
    )
    monkeypatch.setattr(stage3, "build_stage3_agent", lambda: _DeepAgentResult(general))
    _, result = stage3.start_stage3(
        "T-1001",
        "I've had several problems recently and I'm not sure whether this should be handled by maintenance or billing. Please investigate my account and property history.",
    )
    resolution = result["resolution"]
    assert resolution["payment_status"] == "paid"
    assert any(item["request_id"] == existing.request_id and item["status"] == "in_progress" for item in resolution["related_open_requests"])
    assert existing.request_id in resolution["summary"]


def test_stage3_approval_edit_and_approve_keep_maintenance_separate(tmp_path, monkeypatch):
    _use_test_data(tmp_path, monkeypatch)
    existing = repository.get_requests("T-1001")[0]
    compensation = _resolution(
        issue_category="HVAC", priority="high", specialists_used=["maintenance", "billing"],
        request_id=existing.request_id, proposed_credit=5000, approval_required=True,
        action_taken="Recommended a service credit after reviewing recurring HVAC issues.",
    )
    monkeypatch.setattr(stage3, "build_stage3_agent", lambda: _DeepAgentResult(compensation))
    edited_id, pending = stage3.start_stage3("T-1001", "My heater has failed again and I want compensation.")
    assert pending["resolution"]["approval_status"] == "pending"
    edited = stage3.resume_stage3(edited_id, "edit", 3500)["resolution"]
    assert edited["approval_status"] == "edited_approved"
    assert edited["approved_credit"] == 3500
    assert edited["maintenance_status"] == existing.status.value

    approved_id, _ = stage3.start_stage3("T-1001", "My heater has failed again and I want compensation.")
    approved = stage3.resume_stage3(approved_id, "approve")["resolution"]
    assert approved["approval_status"] == "approved"
    assert approved["approved_credit"] == 5000


def _token(role: str) -> str:
    credentials = {
        "tenant": {"email": "ayesha.khan@demo.propcare.pk", "password": "TenantDemo123!"},
        "admin": {"email": "manager@demo.propcare.pk", "password": "AdminDemo123!"},
    }[role]
    response = TestClient(app).post("/api/auth/login", json={**credentials, "role": role})
    assert response.status_code == 200
    return response.json()["token"]


def test_stage3_compensation_api_creates_queue_item_and_resumes_all_decisions(tmp_path, monkeypatch):
    _use_test_data(tmp_path, monkeypatch)
    stage3.STAGE3_PENDING.clear()
    stage3.STAGE3_THREADS.clear()
    existing = repository.get_requests("T-1001")[0]
    compensation = _resolution(
        issue_category="HVAC", priority="high", specialists_used=["maintenance", "billing"],
        request_id=existing.request_id, proposed_credit=5000, approval_required=True,
        action_taken="Maintenance and billing recommend a manager-reviewed service credit.",
        summary="Recurring heater failures support a PKR 5,000 service-credit recommendation.",
    )
    monkeypatch.setattr(stage3, "build_stage3_agent", lambda: _DeepAgentResult(compensation))
    client = TestClient(app)
    tenant_headers = {"Authorization": f"Bearer {_token('tenant')}"}
    admin_headers = {"Authorization": f"Bearer {_token('admin')}"}

    pending = client.post("/api/stage3/support", headers=tenant_headers, json={"message": "My heater has failed again and I want compensation."})
    assert pending.status_code == 200
    body = pending.json()
    assert body["status"] == "waiting_for_approval"
    assert body["resolution"]["tenant_id"] == "T-1001"
    assert body["resolution"]["approval_required"] is True
    assert body["resolution"]["approval_status"] == "pending"
    assert body["approval"].items() >= {
        "thread_id": body["thread_id"], "tenant_id": "T-1001", "maintenance_request_id": existing.request_id,
        "proposed_credit": 5000, "approved_credit": None, "decision": "pending", "status": "pending",
        "reason": compensation.summary, "resolved_at": None,
    }.items()
    assert body["approval"]["created_at"]
    queue = client.get("/api/admin/stage2/approvals", headers=admin_headers)
    assert queue.status_code == 200
    assert any(item["thread_id"] == body["thread_id"] and item["stage"] == "stage3" for item in queue.json())

    approved = client.post(f"/api/admin/stage2/approvals/{body['thread_id']}/resume", headers=admin_headers, json={"decision": "approve"})
    assert approved.status_code == 200
    assert approved.json()["resolution"]["approval_status"] == "approved"
    assert approved.json()["approval"]["approved_credit"] == 5000
    assert approved.json()["approval"]["decision"] == "approved"
    tenant_result = client.get(f"/api/stage3/threads/{body['thread_id']}", headers=tenant_headers)
    assert tenant_result.json()["resolution"]["approved_credit"] == 5000
    assert tenant_result.json()["resolution"]["maintenance_status"] == existing.status.value
    financial_activity = client.get("/api/tenants/T-1001/financial-activity", headers=tenant_headers)
    assert financial_activity.status_code == 200
    assert financial_activity.json()[0]["status"] == "approved"
    assert financial_activity.json()[0]["resolved_at"]
    assert client.get("/api/admin/approvals/history", headers=admin_headers).json()[0]["thread_id"] == body["thread_id"]

    for decision, amount, expected_status in (("edit", 2000, "edited_approved"), ("reject", None, "rejected")):
        thread_id = "edit-2000-regression" if decision == "edit" else "reject-regression"
        created = client.post("/api/stage3/support", headers=tenant_headers, json={"message": "My heater has failed again and I want compensation.", "thread_id": thread_id}).json()
        result = client.post(
            f"/api/admin/stage2/approvals/{created['thread_id']}/resume",
            headers=admin_headers,
            json={"decision": decision, **({"approved_credit": amount} if amount else {})},
        )
        assert result.status_code == 200
        assert result.json()["resolution"]["approval_status"] == expected_status
        assert result.json()["resolution"]["approved_credit"] == amount
        assert result.json()["approval"]["decision"] == expected_status
        if decision == "edit":
            ledger = json.loads((tmp_path / "approval_history.json").read_text(encoding="utf-8"))
            records_for_workflow = [item for item in ledger if item["thread_id"] == created["thread_id"]]
            assert len(records_for_workflow) == 1
            assert records_for_workflow[0]["approval_id"] == f"approval-{created['thread_id']}"
            assert records_for_workflow[0]["proposed_credit"] == 5000
            assert records_for_workflow[0]["approved_credit"] == 2000
            assert records_for_workflow[0]["status"] == "edited_approved"
            activity = client.get("/api/tenants/T-1001/financial-activity", headers=tenant_headers).json()
            edited = next(item for item in activity if item["thread_id"] == created["thread_id"])
            assert edited["proposed_credit"] == 5000
            assert edited["approved_credit"] == 2000
            assert len([item for item in activity if item["approval_id"] == edited["approval_id"]]) == 1

    invalid = client.post("/api/stage3/support", headers=tenant_headers, json={"message": "My heater has failed again and I want compensation."}).json()
    invalid_edit = client.post(f"/api/admin/stage2/approvals/{invalid['thread_id']}/resume", headers=admin_headers, json={"decision": "edit", "approved_credit": 0})
    assert invalid_edit.status_code == 422

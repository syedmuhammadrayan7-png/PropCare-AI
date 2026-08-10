import logging
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app
from backend.services import repository
from backend.stage2.propcare_graph import PENDING_APPROVALS, get_stage2_state, resume_stage2, start_stage2

client = TestClient(app)


def _headers(role: str) -> dict[str, str]:
    credentials = {
        "tenant": {"email": "ayesha.khan@demo.propcare.pk", "password": "TenantDemo123!"},
        "admin": {"email": "manager@demo.propcare.pk", "password": "AdminDemo123!"},
    }[role]
    response = client.post("/api/auth/login", json={**credentials, "role": role})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _use_test_data(tmp_path, monkeypatch):
    for name in ("tenants.json", "units.json", "maintenance.json", "payments.json"):
        source = Path(repository.DATA_DIR) / name
        (tmp_path / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(repository, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "")


def test_stage2_normal_ac_creates_assignable_request(tmp_path, monkeypatch):
    _use_test_data(tmp_path, monkeypatch)
    _, result = start_stage2("T-1002", "The bedroom AC is broken and needs repair.")
    request = result["maintenance_request"]
    assert request["status"] == "awaiting_assignment"
    assert result["resolution"]["request_id"] == request["request_id"]
    assigned = repository.update_maintenance_request(request["request_id"], "Islamabad HVAC Response", "scheduled")
    assert assigned and assigned.assigned_team == "Islamabad HVAC Response" and assigned.status.value == "scheduled"


def test_stage2_rent_question_uses_billing_only(tmp_path, monkeypatch, caplog):
    _use_test_data(tmp_path, monkeypatch)
    caplog.set_level(logging.INFO, logger="backend.stage2.propcare_graph")
    before = repository.get_requests("T-1001")
    _, result = start_stage2("T-1001", "Can you check whether my rent for this month has been paid?")
    assert result["completed_specialists"] == ["billing"]
    assert result["maintenance_request"] is None if "maintenance_request" in result else True
    assert repository.get_requests("T-1001") == before
    messages = "\n".join(record.message for record in caplog.records)
    assert "node=supervisor selected=billing route_source=fast_path" in messages
    assert "node=supervisor selected=finish route_source=fast_path" in messages
    assert "name=check_rent_status" in messages


def test_stage2_ambiguous_request_starts_and_resolves_without_work_order(tmp_path, monkeypatch):
    _use_test_data(tmp_path, monkeypatch)
    message = "I've had several problems recently and I'm not sure whether this should be handled by maintenance or billing. Please investigate my account and property history."
    before = repository.get_requests("T-1001")
    _, result = start_stage2("T-1001", message)
    assert result["status"] == "completed"
    assert result["resolution"]["summary"]
    assert "maintenance_request" not in result
    assert result["completed_specialists"] == ["resident_services", "billing"]
    assert repository.get_requests("T-1001") == before


def test_stage2_heater_request_persists_for_tenant_and_admin(tmp_path, monkeypatch):
    _use_test_data(tmp_path, monkeypatch)
    # Complete the seeded heater ticket in this isolated dataset so this is a genuinely fresh request.
    repository.update_maintenance_request("MR-24026", "Islamabad HVAC Response", "completed")
    _, result = start_stage2("T-1001", "The bedroom heater is broken and the heating is not working.")
    request = result["maintenance_request"]
    tenant = repository.get_tenant("T-1001")
    assert request["category"] == "HVAC"
    assert request["tenant_id"] == "T-1001"
    assert request["unit_id"] == tenant.unit_id
    assert request["status"] == "awaiting_assignment"
    _, retried = start_stage2("T-1001", "The bedroom heater is broken and the heating is not working.")
    assert retried["maintenance_request"]["request_id"] == request["request_id"]
    assert len([item for item in repository.get_requests("T-1001") if item.request_id == request["request_id"]]) == 1
    tenant_requests = client.get("/api/tenants/T-1001/requests", headers=_headers("tenant"))
    admin_requests = client.get("/api/admin/requests", headers=_headers("admin"))
    assert tenant_requests.status_code == admin_requests.status_code == 200
    assert any(item["request_id"] == request["request_id"] for item in tenant_requests.json())
    assert any(item["request_id"] == request["request_id"] for item in admin_requests.json())
    updated = repository.update_maintenance_request(request["request_id"], "Islamabad HVAC Response", "scheduled")
    assert updated and updated.status.value == "scheduled"
    assert next(item for item in client.get("/api/tenants/T-1001/requests", headers=_headers("tenant")).json() if item["request_id"] == request["request_id"])["status"] == "scheduled"


def test_stage2_credit_approval_edit_and_rejection_are_persisted(tmp_path, monkeypatch):
    _use_test_data(tmp_path, monkeypatch)
    message = "My AC has broken for the third time this month and I want compensation."
    approved_thread, interrupted = start_stage2("T-1001", message)
    assert interrupted.get("__interrupt__") and approved_thread in PENDING_APPROVALS
    assert PENDING_APPROVALS[approved_thread]["approval"]["proposed_credit"] == 5000
    approved = resume_stage2(approved_thread, {"decision": "approve", "approved_credit": 5000})
    assert approved["approval_decision"]["decision"] == "approve"
    assert approved["resolution"]["approval_decision"] == "approved"
    assert approved["resolution"]["proposed_credit"] == 5000
    assert approved["resolution"]["approved_credit"] == 5000
    assert get_stage2_state(approved_thread)["resolution"]["approved_credit"] == 5000
    edited_thread, interrupted = start_stage2("T-1001", message)
    assert interrupted.get("__interrupt__")
    edited = resume_stage2(edited_thread, {"decision": "edit", "approved_credit": 3200})
    assert edited["approval_decision"]["decision"] == "edit"
    assert edited["resolution"]["approval_decision"] == "approved"
    assert edited["resolution"]["proposed_credit"] == 5000
    assert edited["resolution"]["approved_credit"] == 3200
    rejected_thread, interrupted = start_stage2("T-1001", message)
    maintenance_status = interrupted["maintenance_request"]["status"]
    assert interrupted.get("__interrupt__")
    rejected = resume_stage2(rejected_thread, {"decision": "reject"})
    assert rejected["approval_decision"]["decision"] == "reject"
    assert rejected["resolution"]["approval_decision"] == "rejected"
    assert rejected["resolution"]["proposed_credit"] == 5000
    assert rejected["resolution"]["approved_credit"] is None
    assert rejected["resolution"]["status"] == maintenance_status
    assert "continues unchanged" in rejected["resolution"]["compensation_message"]

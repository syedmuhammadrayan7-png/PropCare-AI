import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.auth import DEMO_ACCOUNTS, create_token
from backend.main import app
from backend.schemas.models import TenantResolution
from backend.services import property_service
from backend.services import repository


def test_lookup_tenant():
    tenant = property_service.lookup_tenant("T-1001")
    assert tenant and tenant.name == "Ayesha Khan" and tenant.unit_id == "U-MH-B-804"


def test_maintenance_history_filters_category():
    history = property_service.check_maintenance_history("T-1001", "HVAC")
    assert len(history) >= 2
    assert all(record.category == "HVAC" for record in history)


def test_rent_status():
    payment = property_service.check_rent_status("T-1002")
    assert payment and payment.payment_status == "overdue"


def test_create_maintenance_request(tmp_path, monkeypatch):
    for name in ("tenants.json", "maintenance.json"):
        source = Path(repository.DATA_DIR) / name
        (tmp_path / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(repository, "DATA_DIR", tmp_path)
    created = property_service.create_maintenance_request("T-1001", "Access", "Entry fob stopped working", "medium", "Resident Services")
    saved = json.loads((tmp_path / "maintenance.json").read_text(encoding="utf-8"))
    assert created.request_id.startswith("MR-") and saved[-1]["request_id"] == created.request_id


def test_tenant_active_requests_endpoint_filters_sorts_and_refreshes(tmp_path, monkeypatch):
    for name in ("tenants.json", "maintenance.json"):
        source = Path(repository.DATA_DIR) / name
        (tmp_path / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(repository, "DATA_DIR", tmp_path)
    records = json.loads((tmp_path / "maintenance.json").read_text(encoding="utf-8"))
    next(record for record in records if record["request_id"] == "MR-24026")["status"] = "in_progress"
    (tmp_path / "maintenance.json").write_text(json.dumps(records), encoding="utf-8")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_token(DEMO_ACCOUNTS[0])}"}

    active = client.get("/api/tenants/T-1001/requests", headers=headers)
    assert active.status_code == 200
    assert active.headers["cache-control"] == "no-store, max-age=0"
    active_ids = [item["request_id"] for item in active.json()]
    assert "MR-24026" in active_ids
    assert "MR-24018" not in active_ids and "MR-24012" not in active_ids
    assert len(active_ids) == len(set(active_ids))
    assert [item["request_id"] for item in client.get("/api/tenants/T-1001/requests/history", headers=headers).json()] != active_ids

    completed = client.post(
        "/api/admin/requests/MR-24026/assignment",
        headers={"Authorization": f"Bearer {create_token(DEMO_ACCOUNTS[1])}"},
        json={"assigned_team": "Electrical Services", "status": "completed"},
    )
    assert completed.status_code == 200 and completed.json()["status"] == "completed"
    records = json.loads((tmp_path / "maintenance.json").read_text(encoding="utf-8"))
    assert next(record for record in records if record["request_id"] == "MR-24026")["status"] == "completed"
    assert "MR-24026" not in [item["request_id"] for item in client.get("/api/tenants/T-1001/requests", headers=headers).json()]

    created = property_service.create_maintenance_request("T-1001", "HVAC", "Fresh heater issue.", "high", "Awaiting Assignment")
    refreshed = client.get("/api/tenants/T-1001/requests", headers=headers).json()
    assert refreshed[0]["request_id"] == created.request_id
    assert sum(item["request_id"] == created.request_id for item in refreshed) == 1

    assigned = client.post(
        f"/api/admin/requests/{created.request_id}/assignment",
        headers={"Authorization": f"Bearer {create_token(DEMO_ACCOUNTS[1])}"},
        json={"assigned_team": "HVAC Maintenance", "status": "assigned"},
    )
    assert assigned.status_code == 200
    tenant_after_assignment = client.get("/api/tenants/T-1001/requests", headers=headers).json()
    assert next(item for item in tenant_after_assignment if item["request_id"] == created.request_id)["assigned_team"] == "HVAC Maintenance"


def test_resolution_schema_validation():
    resolution = TenantResolution(
        tenant_id="T-1001", request_id="MR-24018", issue_category="HVAC", priority="high",
        assigned_team="Climate Systems", action_taken="Checked prior request", approval_required=False,
        status="in_progress", summary="Climate Systems is reviewing the recurring cooling issue.",
    )
    assert resolution.priority.value == "high"

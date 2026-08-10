"""Small JSON repository layer that can later be replaced by a database adapter."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.schemas.models import MaintenanceRequest, Payment, Tenant, Unit

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ACTIVE_REQUEST_STATUSES = {
    "awaiting_assignment", "assigned", "scheduled", "in_progress", "pending", "waiting_for_approval", "open",
}
INACTIVE_REQUEST_STATUSES = {"completed", "resolved", "closed", "cancelled"}


def normalize_request_status(value: str) -> str:
    """Make legacy JSON status spellings safe for the canonical API contract."""
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"inprogress": "in_progress", "awaitingassignment": "awaiting_assignment", "waitingforapproval": "waiting_for_approval"}
    return aliases.get(normalized, normalized)


def _read(name: str) -> list[dict[str, Any]]:
    path = DATA_DIR / name
    if name == "approval_history.json" and not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write(name: str, records: list[dict[str, Any]]) -> None:
    # Atomic replacement keeps the demo data intact if an interrupted write occurs.
    path = DATA_DIR / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def get_tenant(tenant_id: str) -> Tenant | None:
    record = next((item for item in _read("tenants.json") if item["tenant_id"] == tenant_id), None)
    return Tenant.model_validate(record) if record else None


def get_unit(unit_id: str) -> Unit | None:
    record = next((item for item in _read("units.json") if item["unit_id"] == unit_id), None)
    return Unit.model_validate(record) if record else None


def get_requests(tenant_id: str | None = None) -> list[MaintenanceRequest]:
    records = _read("maintenance.json")
    if tenant_id:
        records = [item for item in records if item["tenant_id"] == tenant_id]
    return [MaintenanceRequest.model_validate({**item, "status": normalize_request_status(item["status"])}) for item in records]


def get_active_requests(tenant_id: str) -> list[MaintenanceRequest]:
    """Return tenant work orders that still require action, newest update first."""
    requests = get_requests(tenant_id)
    active = [request for request in requests if request.status.value in ACTIVE_REQUEST_STATUSES]
    return sorted(active, key=lambda request: request.updated_at or request.created_at, reverse=True)


def get_payments(tenant_id: str) -> list[Payment]:
    return [Payment.model_validate(item) for item in _read("payments.json") if item["tenant_id"] == tenant_id]


def get_approval_history(tenant_id: str | None = None) -> list[dict[str, Any]]:
    records = [{**record, "approval_id": record.get("approval_id") or f"approval-{record['thread_id']}"} for record in _read("approval_history.json")]
    if tenant_id:
        records = [item for item in records if item["tenant_id"] == tenant_id]
    # A lifecycle is represented by exactly one stable approval ID. If a legacy
    # file contains duplicate snapshots, return only the newest persisted one.
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record["approval_id"]
        previous = unique.get(key)
        if previous is None or (record.get("resolved_at") or record["created_at"]) > (previous.get("resolved_at") or previous["created_at"]):
            unique[key] = record
    return sorted(unique.values(), key=lambda item: item.get("resolved_at") or item["created_at"], reverse=True)


def get_approval_record(thread_id: str) -> dict[str, Any] | None:
    return next((record for record in get_approval_history() if record["thread_id"] == thread_id), None)


def save_approval_record(record: dict[str, Any]) -> dict[str, Any]:
    records = _read("approval_history.json")
    record = {**record, "approval_id": record.get("approval_id") or f"approval-{record['thread_id']}"}
    existing = next((index for index, item in enumerate(records) if (item.get("approval_id") or f"approval-{item['thread_id']}") == record["approval_id"]), None)
    if existing is None:
        records.append(record)
    else:
        records[existing] = record
    _write("approval_history.json", records)
    return record


def add_maintenance_request(
    tenant_id: str, unit_id: str, category: str, description: str, priority: str, assigned_team: str
) -> MaintenanceRequest:
    records = _read("maintenance.json")
    next_number = max((int(item["request_id"].split("-")[1]) for item in records), default=24000) + 1
    record = {
        "request_id": f"MR-{next_number}", "tenant_id": tenant_id, "unit_id": unit_id,
        "category": category, "description": description, "priority": priority,
        "status": "open", "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "assigned_team": assigned_team,
    }
    records.append(record)
    _write("maintenance.json", records)
    return MaintenanceRequest.model_validate(record)


def update_maintenance_request(request_id: str, assigned_team: str | None = None, status: str | None = None) -> MaintenanceRequest | None:
    records = _read("maintenance.json")
    record = next((item for item in records if item["request_id"] == request_id), None)
    if not record:
        return None
    if assigned_team:
        record["assigned_team"] = assigned_team
    if status:
        record["status"] = normalize_request_status(status)
    record["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write("maintenance.json", records)
    persisted = next(item for item in _read("maintenance.json") if item["request_id"] == request_id)
    return MaintenanceRequest.model_validate({**persisted, "status": normalize_request_status(persisted["status"])})

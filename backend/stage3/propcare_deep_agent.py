"""Stage 3: a Deep Agents coordinator with specialist subagents."""

import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from deepagents import create_deep_agent
from langchain.tools import ToolRuntime, tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from backend.config import get_openai_settings
from backend.services import property_service, repository

logger = logging.getLogger(__name__)
AGENTS_PATH = Path(__file__).with_name("AGENTS.md")
HOUSE_RULES = AGENTS_PATH.read_text(encoding="utf-8")
CHECKPOINTER = InMemorySaver()
STAGE3_THREADS: dict[str, dict] = {}
STAGE3_PENDING: dict[str, dict] = {}


class Stage3Context(BaseModel):
    tenant_id: str


class SpecialistFinding(BaseModel):
    specialist: Literal["maintenance", "billing", "resident_services"]
    request_id: str | None = None
    maintenance_status: str | None = None
    payment_status: str | None = None
    proposed_credit: float | None = None
    summary: str


class Stage3Resolution(BaseModel):
    # Identity is injected by the authenticated API boundary, never supplied by the model.
    tenant_id: str = ""
    request_id: str | None = None
    issue_category: str
    priority: Literal["low", "medium", "high", "urgent"] = "low"
    specialists_used: list[Literal["maintenance", "billing", "resident_services"]]
    assigned_team: str | None = None
    maintenance_status: str | None = None
    payment_status: str | None = None
    proposed_credit: float | None = None
    approved_credit: float | None = None
    approval_required: bool = False
    approval_status: Literal["pending", "approved", "edited_approved", "rejected"] | None = None
    related_open_requests: list[dict[str, str]] = Field(default_factory=list)
    action_taken: str
    summary: str
    status: str


def _log(event: str, **fields: object) -> None:
    logger.info("stage3 event=%s %s", event, " ".join(f"{key}={value}" for key, value in fields.items()))


def _json(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, default=str)


@tool
def maintenance_history(runtime: ToolRuntime[Stage3Context]) -> str:
    """Return the authenticated tenant's maintenance history only."""
    tenant_id = runtime.context.tenant_id
    started = time.perf_counter()
    result = property_service.check_maintenance_history(tenant_id)
    _log("tool", name="maintenance_history", elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
    return _json([item.model_dump(mode="json") for item in result])


@tool
def create_or_reuse_maintenance_request(category: str, description: str, priority: Literal["low", "medium", "high", "urgent"], runtime: ToolRuntime[Stage3Context]) -> str:
    """Create a work order for the authenticated tenant, or reuse a matching open work order."""
    tenant_id = runtime.context.tenant_id
    started = time.perf_counter()
    history = property_service.check_maintenance_history(tenant_id, category)
    open_request = next((item for item in history if item.status.value in {"open", "awaiting_assignment", "assigned", "scheduled", "in_progress"}), None)
    if open_request:
        request = open_request
        reused = True
    else:
        request = property_service.create_maintenance_request(tenant_id, category, description, priority, "Awaiting Assignment")
        request = property_service.assign_maintenance_request(request.request_id, "Awaiting Assignment", "awaiting_assignment") or request
        reused = False
    _log("tool", name="create_or_reuse_maintenance_request", request_id=request.request_id, reused=reused, elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
    return _json(request.model_dump(mode="json"))


@tool
def billing_context(runtime: ToolRuntime[Stage3Context]) -> str:
    """Return the authenticated tenant's rent/payment status only."""
    started = time.perf_counter()
    payment = property_service.check_rent_status(runtime.context.tenant_id)
    _log("tool", name="billing_context", elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
    return _json(payment.model_dump(mode="json") if payment else None)


@tool
def resident_context(runtime: ToolRuntime[Stage3Context]) -> str:
    """Return resident, open work-order, and rent context for a broad resident request."""
    started = time.perf_counter()
    tenant = property_service.lookup_tenant(runtime.context.tenant_id)
    unit = property_service.lookup_unit(tenant.unit_id) if tenant else None
    open_requests = [
        request.model_dump(mode="json")
        for request in property_service.check_maintenance_history(runtime.context.tenant_id)
        if request.status.value in {"open", "awaiting_assignment", "assigned", "scheduled", "in_progress"}
    ]
    payment = property_service.check_rent_status(runtime.context.tenant_id)
    _log("tool", name="resident_context", elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
    return _json({
        "tenant": tenant.model_dump(mode="json") if tenant else None,
        "unit": unit.model_dump(mode="json") if unit else None,
        "open_maintenance_requests": open_requests,
        "current_payment": payment.model_dump(mode="json") if payment else None,
    })


def _subagents() -> list[dict]:
    return [
        {"name": "maintenance-agent", "description": "Use for physical maintenance, HVAC, heater, plumbing, electrical, repair, or recurring service issues. Creates or reuses work orders.", "system_prompt": f"{HOUSE_RULES}\nYou are the Maintenance Agent. Use only maintenance tools. Return concise structured findings. Never handle billing-only questions.", "tools": [maintenance_history, create_or_reuse_maintenance_request], "response_format": SpecialistFinding},
        {"name": "billing-agent", "description": "Use for rent, payment, billing, compensation, service-credit, or refund context. Never approve money.", "system_prompt": f"{HOUSE_RULES}\nYou are the Billing Agent. Use billing_context. You may recommend PKR 5,000 only when recurring maintenance evidence is supplied by the coordinator; never approve it. Return concise structured findings.", "tools": [billing_context], "response_format": SpecialistFinding},
        {"name": "resident-services-agent", "description": "Use for tenant/unit/lease questions and ambiguous requests requiring resident and property context.", "system_prompt": f"{HOUSE_RULES}\nYou are the Resident Services Agent. Use resident_context and return concise structured findings. Do not create maintenance work orders unless the coordinator has a concrete physical issue.", "tools": [resident_context], "response_format": SpecialistFinding},
    ]


def build_stage3_agent():
    api_key, model_name, timeout = get_openai_settings()
    model = ChatOpenAI(model=model_name, api_key=api_key, temperature=0, timeout=timeout, max_retries=0)
    return create_deep_agent(
        model=model,
        subagents=_subagents(),
        context_schema=Stage3Context,
        response_format=Stage3Resolution,
        checkpointer=CHECKPOINTER,
        memory=[str(AGENTS_PATH)],
        name="propcare-operations-deep-agent",
        system_prompt=(f"You are the PropCare Operations Deep Agent.\n{HOUSE_RULES}\n"
                       "The authenticated tenant identity is already present in runtime context. Never ask a tenant for an ID and never rely on a model-generated tenant_id. Plan and delegate using the task tool. Delegate every domain decision to one or more named specialists; use maintenance-agent for physical issues, billing-agent for billing, and resident-services-agent for ambiguity. Combine their structured findings into one Stage3Resolution. Set approval_required=true for a proposed financial credit and never approve it."),
    )


_OPEN_REQUEST_STATUSES = {"open", "awaiting_assignment", "assigned", "scheduled", "in_progress"}
_PLACEHOLDER_TEAMS = {"", "awaiting assignment", "unassigned", "none", "n/a"}
_COMPENSATION_TERMS = {"compensation", "service credit", "credit", "reimbursement", "refund"}


def _request_summary(request) -> dict[str, str]:
    """Expose only the tenant-safe work-order fields the result card needs."""
    team = request.assigned_team.strip() if request.assigned_team else ""
    return {
        "request_id": request.request_id,
        "category": request.category,
        "status": request.status.value,
        "priority": request.priority.value,
        "assigned_team": team if team.lower() not in _PLACEHOLDER_TEAMS else "Unassigned",
    }


def _normalise_resolution(tenant_id: str, message: str, payload: dict) -> dict:
    """Make result cards reflect persisted records rather than model-supplied labels."""
    payload["tenant_id"] = tenant_id
    history = property_service.check_maintenance_history(tenant_id)
    requests_by_id = {request.request_id: request for request in history}
    open_requests = [request for request in history if request.status.value in _OPEN_REQUEST_STATUSES]
    payload["related_open_requests"] = [_request_summary(request) for request in open_requests]

    selected = requests_by_id.get(payload.get("request_id"))
    specialists = set(payload.get("specialists_used") or [])
    is_billing_only = specialists == {"billing"} and not payload.get("request_id")

    if is_billing_only:
        payment = property_service.check_rent_status(tenant_id)
        payload.update({
            "assigned_team": "Billing",
            "maintenance_status": None,
            "request_id": None,
            "priority": "low",
            "payment_status": payment.payment_status if payment else payload.get("payment_status"),
        })
    elif selected:
        payload.update({
            "issue_category": selected.category,
            "priority": selected.priority.value,
            "maintenance_status": selected.status.value,
            "assigned_team": _request_summary(selected)["assigned_team"],
        })

    # Broad resident investigations receive the facts resident_context retrieves,
    # even if the coordinator's prose omitted an open work order.
    if "resident_services" in specialists:
        payment = property_service.check_rent_status(tenant_id)
        if payment:
            payload["payment_status"] = payment.payment_status
        if open_requests:
            open_fact = "; ".join(
                f"{item.request_id} is {item.status.value.replace('_', ' ')}"
                for item in open_requests
            )
            summary = payload.get("summary", "")
            if "no current maintenance" in summary.lower():
                payload["summary"] = f"Resident records were reviewed. Current open maintenance: {open_fact}."
            elif all(item.request_id not in summary for item in open_requests):
                payload["summary"] = f"{summary.rstrip('.')} Current open maintenance: {open_fact}."

    response_text = " ".join(str(payload.get(field) or "") for field in ("summary", "action_taken")).lower()
    compensation_requested = any(term in message.lower() for term in _COMPENSATION_TERMS)
    approval_signalled = payload.get("approval_required") or payload.get("proposed_credit") or (
        compensation_requested and any(term in response_text for term in ("approval", "manager", "property manager"))
    )

    # A model may state that manager approval is required without filling every
    # structured field. Convert that explicit recommendation into a safe,
    # server-owned pending approval; never infer an approval or payment.
    if approval_signalled:
        payload["approval_required"] = True
        payload["proposed_credit"] = payload.get("proposed_credit") or 5000
        payload["approval_status"] = "pending"
        payload["status"] = "waiting_for_approval"
    return payload


def start_stage3(tenant_id: str, message: str, thread_id: str | None = None) -> tuple[str, dict]:
    thread_id = thread_id or str(uuid.uuid4())
    _log("workflow_start", thread_id=thread_id, tenant_id=tenant_id)
    try:
        result = build_stage3_agent().invoke(
            {"messages": [{"role": "user", "content": message}]},
            context=Stage3Context(tenant_id=tenant_id),
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": 30},
        )
        resolution = result["structured_response"]
    except Exception:
        logger.exception("Stage 3 provider/coordinator failure tenant_id=%s", tenant_id)
        raise
    payload = _normalise_resolution(tenant_id, message, resolution.model_dump(mode="json"))
    STAGE3_THREADS[thread_id] = {"tenant_id": tenant_id, "status": "completed", "resolution": payload}
    if payload["approval_required"] and payload.get("proposed_credit"):
        selected = next((request for request in property_service.check_maintenance_history(tenant_id) if request.request_id == payload.get("request_id")), None)
        approval = {
            "approval_id": f"approval-{thread_id}",
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "maintenance_request_id": payload.get("request_id"),
            "proposed_credit": payload["proposed_credit"],
            "approved_credit": None,
            "decision": "pending",
            "status": "pending",
            "reason": payload.get("summary") or "Stage 3 specialist recommendation",
            "maintenance_context": _request_summary(selected) if selected else None,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "resolved_at": None,
        }
        repository.save_approval_record(approval)
        STAGE3_PENDING[thread_id] = {
            "stage": "stage3", "thread_id": thread_id, "tenant_id": tenant_id,
            "message": message, "approval": approval, "resolution": payload,
        }
        STAGE3_THREADS[thread_id] = {"tenant_id": tenant_id, "status": "waiting_for_approval", "resolution": payload}
        _log("approval_required", thread_id=thread_id, proposed_credit=payload["proposed_credit"])
        return thread_id, STAGE3_THREADS[thread_id]
    _log("final_resolution", thread_id=thread_id, specialists=payload["specialists_used"])
    return thread_id, STAGE3_THREADS[thread_id]


def resume_stage3(thread_id: str, decision: str, approved_credit: float | None = None) -> dict:
    pending = STAGE3_PENDING.pop(thread_id)
    resolution = pending["resolution"].copy()
    # The ledger, not the graph's in-memory snapshot, owns the final financial state.
    approval = (repository.get_approval_record(thread_id) or pending["approval"]).copy()
    selected = next((request for request in property_service.check_maintenance_history(pending["tenant_id"]) if request.request_id == approval.get("maintenance_request_id")), None)
    if selected:
        request_context = _request_summary(selected)
        approval["maintenance_context"] = request_context
        resolution.update({
            "assigned_team": request_context["assigned_team"],
            "maintenance_status": request_context["status"],
            "priority": request_context["priority"],
        })
    proposed = resolution.get("proposed_credit")
    if decision == "reject":
        resolution.update({"approval_required": False, "approval_status": "rejected", "approved_credit": None, "status": "completed", "summary": f"{resolution['summary']} The service-credit request was not approved; maintenance continues unchanged."})
        approval.update({"decision": "rejected", "status": "rejected", "approved_credit": None})
    else:
        amount = approved_credit if decision == "edit" and approved_credit is not None else proposed
        lifecycle_status = "edited_approved" if decision == "edit" else "approved"
        resolution.update({"approval_required": False, "approval_status": lifecycle_status, "approved_credit": amount, "status": "completed", "summary": f"{resolution['summary']} A PKR {amount:,.0f} service credit was approved."})
        approval.update({"decision": lifecycle_status, "status": lifecycle_status, "approved_credit": amount})
    approval["resolved_at"] = datetime.now().isoformat(timespec="seconds")
    repository.save_approval_record(approval)
    STAGE3_THREADS[thread_id] = {"tenant_id": pending["tenant_id"], "status": "completed", "resolution": resolution, "approval": approval}
    _log("approval_resumed", thread_id=thread_id, decision=decision)
    return STAGE3_THREADS[thread_id]

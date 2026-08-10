"""Supervised Stage 2 LangGraph workflow with raw approval interrupts."""

import logging
import re
import time
import uuid
from typing import Literal, TypedDict

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from backend.config import get_openai_settings
from backend.services import property_service

logger = logging.getLogger(__name__)


class PropCareState(TypedDict, total=False):
    messages: list[dict]
    tenant_id: str
    issue_category: str
    priority: str
    selected_specialist: Literal["maintenance", "billing", "resident_services", "finish"]
    completed_specialists: list[str]
    maintenance_request: dict | None
    proposed_credit: float | None
    approval_decision: dict | None
    resolution: dict | None
    status: str
    approval_reason: str | None


class SupervisorDecision(BaseModel):
    next_specialist: Literal["maintenance", "billing", "resident_services", "finish"] = Field(description="Next specialist or finish")
    issue_category: str
    priority: Literal["low", "medium", "high", "urgent"]
    reason: str


CHECKPOINTER = InMemorySaver()
PENDING_APPROVALS: dict[str, dict] = {}

MAINTENANCE_TERMS = {"ac", "heater", "heating", "hvac", "furnace", "boiler", "radiator", "leak", "plumbing", "plumber", "electrical", "electric", "repair", "broken", "drain", "pipe"}
BILLING_TERMS = {"rent", "paid", "payment", "bill", "billing", "compensation", "credit", "refund"}
HVAC_TERMS = {"ac", "heater", "heating", "hvac", "furnace", "boiler", "radiator"}


def _tokens(message: str) -> set[str]:
    return set(re.findall(r"[a-z]+", message.lower()))


def _is_maintenance_issue(message: str) -> bool:
    return bool(_tokens(message) & MAINTENANCE_TERMS)


def _is_billing_issue(message: str) -> bool:
    return bool(_tokens(message) & BILLING_TERMS)


def _is_ambiguous_request(message: str) -> bool:
    terms = _tokens(message)
    return ({"maintenance", "billing"} <= terms) or ("not sure" in message.lower() and bool(terms & {"property", "account"}))


def _maintenance_category(message: str) -> str:
    terms = _tokens(message)
    if terms & HVAC_TERMS:
        return "HVAC"
    if terms & {"leak", "plumbing", "plumber", "drain", "pipe"}:
        return "Plumbing"
    if terms & {"electrical", "electric"}:
        return "Electrical"
    return "Maintenance"


def _log_timing(event: str, started_at: float, **context: object) -> None:
    fields = " ".join(f"{key}={value}" for key, value in context.items())
    logger.info("stage2_timing event=%s elapsed_ms=%.1f %s", event, (time.perf_counter() - started_at) * 1000, fields)


def _timed_service_call(name: str, callback, *args):
    started_at = time.perf_counter()
    try:
        return callback(*args)
    finally:
        _log_timing("service_call", started_at, name=name)


def _message(state: PropCareState) -> str:
    return state["messages"][-1]["content"]


def _fallback_supervisor(state: PropCareState) -> SupervisorDecision:
    text = _message(state).lower()
    done = set(state.get("completed_specialists", []))
    if _is_ambiguous_request(text) and not done:
        return SupervisorDecision(next_specialist="resident_services", issue_category="resident_services", priority="low", reason="The request needs tenant and property context before specialist routing.")
    if "maintenance" not in done and _is_maintenance_issue(text):
        return SupervisorDecision(next_specialist="maintenance", issue_category=_maintenance_category(text), priority="high" if "broken" in text or "leak" in text else "medium", reason="The request needs property maintenance context.")
    if "billing" not in done and (_is_billing_issue(text) or state.get("proposed_credit")):
        return SupervisorDecision(next_specialist="billing", issue_category="billing", priority="low", reason="The request needs payment or service-credit review.")
    if not done:
        return SupervisorDecision(next_specialist="resident_services", issue_category="resident_services", priority="low", reason="The request needs resident or lease context.")
    return SupervisorDecision(next_specialist="finish", issue_category=state.get("issue_category", "general"), priority=state.get("priority", "low"), reason="All required specialists have returned their findings.")


def _fast_supervisor_decision(state: PropCareState) -> SupervisorDecision | None:
    """Avoid model latency for obvious flows while retaining the graph supervisor node."""
    message = _message(state)
    completed = set(state.get("completed_specialists", []))
    if _is_ambiguous_request(message):
        return None
    maintenance = _is_maintenance_issue(message)
    billing = _is_billing_issue(message)
    if maintenance and "maintenance" not in completed:
        return SupervisorDecision(next_specialist="maintenance", issue_category=_maintenance_category(message), priority="high" if "broken" in message.lower() else "medium", reason="Obvious physical maintenance request.")
    if billing and not maintenance and "billing" not in completed:
        return SupervisorDecision(next_specialist="billing", issue_category="billing", priority="low", reason="Obvious billing-only request.")
    if maintenance and billing and "maintenance" in completed and "billing" not in completed:
        return SupervisorDecision(next_specialist="billing", issue_category="billing", priority="low", reason="Maintenance review completed; compensation needs billing review.")
    if completed and (maintenance or billing):
        return SupervisorDecision(next_specialist="finish", issue_category=state.get("issue_category", "general"), priority=state.get("priority", "low"), reason="Obvious request has completed its required specialists.")
    return None


def _supervisor_agent() -> object:
    api_key, model_name, timeout = get_openai_settings()
    return create_agent(
        model=ChatOpenAI(model=model_name, api_key=api_key, temperature=0, timeout=timeout, max_retries=0),
        tools=[],
        response_format=SupervisorDecision,
        system_prompt=("You are PropCare's Stage 2 supervisor. Select only one next specialist: maintenance, billing, resident_services, or finish. "
                       "You must send a physical property issue (including HVAC, heater, heating, plumbing, or electrical) to maintenance first, then return to decide if billing is required. "
                       "A rent/payment/billing-only question must go only to billing and must never create a maintenance request. "
                       "Never approve money; identify it for the approval workflow."),
    )


def supervisor(state: PropCareState) -> dict:
    # The LLM supervisor remains available for ambiguous requests; common single-domain paths avoid provider round trips.
    started_at = time.perf_counter()
    decision = _fast_supervisor_decision(state)
    route_source = "fast_path"
    if decision is None:
        route_source = "llm"
        llm_started_at = time.perf_counter()
        try:
            agent = _supervisor_agent()
            result = agent.invoke({"messages": [{"role": "user", "content": f"Tenant: {state['tenant_id']}\nCompleted specialists: {state.get('completed_specialists', [])}\nRequest: {_message(state)}\nCurrent credit: {state.get('proposed_credit')}"}]}, config={"recursion_limit": 8})
            decision = result["structured_response"]
        except Exception as error:
            logger.warning("Stage 2 supervisor provider call failed; using deterministic fallback: %s", error, exc_info=True)
            decision = _fallback_supervisor(state)
            route_source = "fallback"
        finally:
            _log_timing("supervisor_llm", llm_started_at, route_source=route_source)
    # Keep the LLM supervisor dynamic, but enforce the product boundaries around real work orders.
    if _is_maintenance_issue(_message(state)) and "maintenance" not in state.get("completed_specialists", []) and decision.next_specialist != "maintenance":
        decision = SupervisorDecision(next_specialist="maintenance", issue_category=_maintenance_category(_message(state)), priority="high" if "broken" in _message(state).lower() else "medium", reason="Physical property issues require maintenance review before any other specialist.")
    if decision.next_specialist == "maintenance" and not _is_maintenance_issue(_message(state)):
        if _is_ambiguous_request(_message(state)):
            completed = set(state.get("completed_specialists", []))
            next_specialist = "resident_services" if "resident_services" not in completed else "billing" if "billing" not in completed else "finish"
            decision = SupervisorDecision(next_specialist=next_specialist, issue_category="resident_services" if next_specialist == "resident_services" else "billing" if next_specialist == "billing" else "general", priority="low", reason="Ambiguous requests need tenant and property context before a work order can be created.")
        elif _is_billing_issue(_message(state)):
            decision = SupervisorDecision(next_specialist="billing", issue_category="billing", priority="low", reason="Billing-only requests do not create maintenance work orders.")
    _log_timing("node", started_at, node="supervisor", selected=decision.next_specialist, route_source=route_source)
    return {"selected_specialist": decision.next_specialist, "issue_category": decision.issue_category, "priority": decision.priority, "status": "routing"}


def maintenance_agent(state: PropCareState) -> dict:
    started_at = time.perf_counter()
    category = _maintenance_category(_message(state))
    history = _timed_service_call("check_maintenance_history", property_service.check_maintenance_history, state["tenant_id"], category)
    open_match = next((item for item in history if item.status.value in {"open", "awaiting_assignment", "assigned", "scheduled", "in_progress"}), None)
    if open_match:
        request = open_match
    else:
        priority = "high" if any(word in _message(state).lower() for word in ("broken", "leak", "urgent")) else "medium"
        request = _timed_service_call("create_maintenance_request", property_service.create_maintenance_request, state["tenant_id"], category, _message(state), priority, "Awaiting Assignment")
        request = _timed_service_call("assign_maintenance_request", property_service.assign_maintenance_request, request.request_id, "Awaiting Assignment", "awaiting_assignment") or request
    recurring = len(history) >= 2
    _log_timing("node", started_at, node="maintenance", request_id=request.request_id, reused=bool(open_match))
    return {"maintenance_request": request.model_dump(mode="json"), "issue_category": request.category, "priority": request.priority.value, "completed_specialists": [*state.get("completed_specialists", []), "maintenance"], "approval_reason": "Recurring maintenance evidence found." if recurring else None, "status": "maintenance_reviewed"}


def billing_agent(state: PropCareState) -> dict:
    started_at = time.perf_counter()
    payment = _timed_service_call("check_rent_status", property_service.check_rent_status, state["tenant_id"])
    text = _message(state).lower()
    recurring = bool(state.get("approval_reason"))
    credit = 5000.0 if recurring and any(word in text for word in ("compensation", "credit", "refund")) else None
    _log_timing("node", started_at, node="billing", proposed_credit=credit)
    return {"proposed_credit": credit, "completed_specialists": [*state.get("completed_specialists", []), "billing"], "status": "billing_reviewed", "approval_reason": "Recurring HVAC issue with compensation request." if credit else state.get("approval_reason"), "resolution": {"payment_status": payment.payment_status if payment else None, "amount": payment.amount if payment else None}}


def resident_services_agent(state: PropCareState) -> dict:
    started_at = time.perf_counter()
    tenant = _timed_service_call("lookup_tenant", property_service.lookup_tenant, state["tenant_id"])
    unit = _timed_service_call("lookup_unit", property_service.lookup_unit, tenant.unit_id) if tenant else None
    _log_timing("node", started_at, node="resident_services")
    return {"completed_specialists": [*state.get("completed_specialists", []), "resident_services"], "status": "resident_reviewed", "resolution": {"tenant": tenant.name if tenant else None, "unit": unit.unit_number if unit else None}}


def approval(state: PropCareState) -> dict:
    logger.info("stage2_timing event=node node=approval status=interrupted")
    decision = interrupt({"type": "service_credit", "tenant_id": state["tenant_id"], "maintenance_request": state.get("maintenance_request"), "proposed_credit": state.get("proposed_credit"), "reason": state.get("approval_reason"), "recommended_action": "Approve, edit, or reject the proposed PKR credit."})
    # The resumed Command payload becomes durable graph state before final resolution.
    logger.info("stage2_timing event=node node=approval status=resumed")
    return {"approval_decision": {"decision": decision.get("decision"), "approved_credit": decision.get("approved_credit"), "note": decision.get("note")}, "status": "approval_resumed"}


def final_resolution(state: PropCareState) -> dict:
    started_at = time.perf_counter()
    decision = state.get("approval_decision") or {}
    decision_name = decision.get("decision")
    proposed_credit = state.get("proposed_credit")
    credit = proposed_credit if decision_name in {"approve", "edit"} else None
    if decision_name == "edit" and decision.get("approved_credit") is not None:
        credit = decision["approved_credit"]
    request = state.get("maintenance_request") or {}
    if decision_name == "edit":
        compensation_status = "approved"
        compensation_message = f"A revised PKR {credit:,.0f} service credit was approved."
    elif decision_name == "approve":
        compensation_status = "approved"
        compensation_message = f"A PKR {credit:,.0f} service credit was approved."
    elif decision_name == "reject":
        compensation_status = "rejected"
        compensation_message = "Your service-credit request was not approved. Your maintenance request continues unchanged."
    elif proposed_credit:
        compensation_status = "pending"
        compensation_message = "Your service-credit request is awaiting admin review."
    else:
        compensation_status = "not_requested"
        compensation_message = "No service credit was requested."
    if decision_name in {"approve", "edit", "reject"}:
        summary = f"Your maintenance request is being coordinated. {compensation_message}"
    elif request:
        summary = f"Your {request.get('category', 'maintenance')} request is recorded and awaiting assignment."
    else:
        payment = (state.get("resolution") or {}).get("payment_status")
        summary = "Your payment status has been reviewed." if payment else "Your resident request has been reviewed."
    _log_timing("node", started_at, node="final")
    return {"resolution": {"tenant_id": state["tenant_id"], "request_id": request.get("request_id"), "issue_category": state.get("issue_category", "general"), "priority": state.get("priority", "low"), "assigned_team": request.get("assigned_team", "Resident Services"), "action_taken": "Stage 2 specialist review completed.", "approval_required": False, "status": request.get("status", "resolved"), "summary": summary, "approval_decision": compensation_status, "proposed_credit": proposed_credit, "approved_credit": credit, "compensation_message": compensation_message}, "status": "completed"}


def route_after_supervisor(state: PropCareState) -> str:
    if state["selected_specialist"] == "finish" and state.get("proposed_credit") and not state.get("approval_decision"):
        return "approval"
    return state["selected_specialist"]


def build_stage2_graph():
    graph = StateGraph(PropCareState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("maintenance", maintenance_agent)
    graph.add_node("billing", billing_agent)
    graph.add_node("resident_services", resident_services_agent)
    graph.add_node("approval", approval)
    graph.add_node("final", final_resolution)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", route_after_supervisor, {"maintenance": "maintenance", "billing": "billing", "resident_services": "resident_services", "finish": "final", "approval": "approval"})
    graph.add_edge("maintenance", "supervisor")
    graph.add_edge("billing", "supervisor")
    graph.add_edge("resident_services", "supervisor")
    graph.add_edge("approval", "final")
    graph.add_edge("final", END)
    return graph.compile(checkpointer=CHECKPOINTER)


STAGE2_GRAPH = build_stage2_graph()


def start_stage2(tenant_id: str, message: str, thread_id: str | None = None) -> tuple[str, dict]:
    thread_id = thread_id or str(uuid.uuid4())
    started_at = time.perf_counter()
    result = STAGE2_GRAPH.invoke({"messages": [{"role": "user", "content": message}], "tenant_id": tenant_id, "completed_specialists": [], "status": "started"}, config={"configurable": {"thread_id": thread_id}, "recursion_limit": 20})
    _log_timing("workflow", started_at, operation="start", interrupted=bool(result.get("__interrupt__")))
    if result.get("__interrupt__"):
        pending = result["__interrupt__"][0]
        approval = getattr(pending, "value", pending)
        PENDING_APPROVALS[thread_id] = {"tenant_id": tenant_id, "message": message, "approval": approval}
    return thread_id, result


def resume_stage2(thread_id: str, decision: dict) -> dict:
    started_at = time.perf_counter()
    result = STAGE2_GRAPH.invoke(Command(resume=decision), config={"configurable": {"thread_id": thread_id}, "recursion_limit": 20})
    _log_timing("workflow", started_at, operation="resume", decision=decision.get("decision"))
    PENDING_APPROVALS.pop(thread_id, None)
    return result


def get_stage2_state(thread_id: str) -> dict:
    """Return the persisted state for a tenant-owned Stage 2 workflow thread."""
    snapshot = STAGE2_GRAPH.get_state({"configurable": {"thread_id": thread_id}})
    return dict(snapshot.values)

import logging

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.agent.propcare_agent import (
    OpenAIConnectionUnavailable,
    OpenAIRequestFailed,
    OpenAIRequestTimeout,
    resolve_tenant_message,
)
from backend.auth import DEMO_ACCOUNTS, authenticate, create_token, current_session, require_role
from backend.schemas.models import AdminOverview, ApprovalDecision, AssignmentUpdate, DemoCredentials, DemoSession, LoginRequest, Stage2Message, SupportMessage, TenantResolution
from backend.services import property_service
from backend.services.repository import get_approval_history, get_requests
from backend.stage2.propcare_graph import PENDING_APPROVALS, get_stage2_state, resume_stage2, start_stage2
from backend.stage3.propcare_deep_agent import STAGE3_PENDING, STAGE3_THREADS, resume_stage3, start_stage3

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "stage": "1-langchain"}


@router.post("/auth/login", response_model=DemoSession)
def login(payload: LoginRequest):
    account = authenticate(payload.email, payload.password, payload.role)
    if not account:
        raise HTTPException(status_code=401, detail="Invalid demo credentials for this portal.")
    return DemoSession(token=create_token(account), role=account.role, name=account.name, tenant_id=account.tenant_id)


@router.get("/auth/demo-credentials", response_model=DemoCredentials)
def demo_credentials(role: str):
    account = next((item for item in DEMO_ACCOUNTS if item.role == role), None)
    if not account:
        raise HTTPException(status_code=404, detail="Demo role not found.")
    return DemoCredentials(email=account.email, password=account.password)


@router.get("/auth/session", response_model=DemoSession)
def session(current: dict = Depends(current_session)):
    return DemoSession(token="", role=current["role"], name=current["name"], tenant_id=current.get("tenant_id"))


@router.get("/tenants/{tenant_id}")
def tenant(tenant_id: str, session: dict = Depends(require_role("tenant"))):
    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="You can only access your own resident data.")
    result = property_service.lookup_tenant(tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return result


@router.get("/tenants/{tenant_id}/requests")
def tenant_requests(tenant_id: str, response: Response, session: dict = Depends(require_role("tenant"))):
    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="You can only access your own resident data.")
    if not property_service.lookup_tenant(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return property_service.check_active_maintenance_requests(tenant_id)


@router.get("/tenants/{tenant_id}/requests/history")
def tenant_request_history(tenant_id: str, session: dict = Depends(require_role("tenant"))):
    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="You can only access your own resident data.")
    if not property_service.lookup_tenant(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    return property_service.check_maintenance_history(tenant_id)


@router.get("/tenants/{tenant_id}/unit")
def tenant_unit(tenant_id: str, session: dict = Depends(require_role("tenant"))):
    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="You can only access your own resident data.")
    tenant_record = property_service.lookup_tenant(tenant_id)
    unit = property_service.lookup_unit(tenant_record.unit_id) if tenant_record else None
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    return unit


@router.get("/tenants/{tenant_id}/payments")
def tenant_payments(tenant_id: str, session: dict = Depends(require_role("tenant"))):
    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="You can only access your own resident data.")
    if not property_service.lookup_tenant(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    return [property_service.check_rent_status(tenant_id)]


@router.get("/tenants/{tenant_id}/financial-activity")
def tenant_financial_activity(tenant_id: str, response: Response, session: dict = Depends(require_role("tenant"))):
    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="You can only access your own financial activity.")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return get_approval_history(tenant_id)


@router.get("/admin/overview", response_model=AdminOverview)
def admin_overview(_: dict = Depends(require_role("admin"))):
    requests = get_requests()
    return AdminOverview(
        open_requests=sum(item.status.value in {"open", "in_progress", "scheduled"} for item in requests),
        urgent_issues=sum(item.priority.value == "urgent" and item.status.value != "resolved" for item in requests),
        active_units=4,
        pending_approvals=2,
        recent_activity=[
            "Emergency Response dispatched to Juniper Residences, B-302",
            "Climate Systems reviewing a recurring HVAC report at Harbor House",
            "August rent reconciliation flagged one overdue account",
        ],
    )


@router.get("/admin/requests")
def admin_requests(_: dict = Depends(require_role("admin"))):
    requests = []
    for request in get_requests():
        tenant = property_service.lookup_tenant(request.tenant_id)
        unit = property_service.lookup_unit(request.unit_id)
        requests.append({**request.model_dump(mode="json"), "tenant": tenant.name if tenant else request.tenant_id, "property": unit.property_name if unit else None})
    return requests


@router.post("/support/message", response_model=TenantResolution)
def support_message(payload: SupportMessage, session: dict = Depends(require_role("tenant"))):
    tenant_id = session["tenant_id"]
    if payload.tenant_id and payload.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="You can only create requests for your own resident account.")
    if not property_service.lookup_tenant(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        return resolve_tenant_message(tenant_id, payload.message)
    except OpenAIRequestTimeout as error:
        raise HTTPException(status_code=504, detail=str(error)) from error
    except OpenAIConnectionUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except OpenAIRequestFailed as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        logger.exception("Unexpected support coordinator failure for tenant %s", tenant_id)
        raise HTTPException(status_code=502, detail="The support coordinator could not complete this request.") from error


@router.post("/stage2/support")
def stage2_support(payload: Stage2Message, session: dict = Depends(require_role("tenant"))):
    try:
        thread_id, result = start_stage2(session["tenant_id"], payload.message, payload.thread_id)
    except Exception as error:
        logger.exception("Stage 2 workflow start failed for tenant %s", session["tenant_id"])
        raise HTTPException(status_code=502, detail="The Stage 2 workflow could not start. Please retry.") from error
    if result.get("__interrupt__"):
        approval = PENDING_APPROVALS[thread_id]
        return {"thread_id": thread_id, "status": "awaiting_approval", "approval": {"proposed_credit": approval["approval"].get("proposed_credit")}}
    return {"thread_id": thread_id, "status": result.get("status"), "resolution": result.get("resolution")}


@router.post("/stage3/support")
def stage3_support(payload: Stage2Message, session: dict = Depends(require_role("tenant"))):
    try:
        thread_id, result = start_stage3(session["tenant_id"], payload.message, payload.thread_id)
    except Exception as error:
        logger.exception("Stage 3 workflow start failed for tenant %s", session["tenant_id"])
        raise HTTPException(status_code=502, detail="The Stage 3 operations agent could not start. Please retry.") from error
    pending = result["status"] == "waiting_for_approval"
    return {
        "thread_id": thread_id,
        "status": result["status"],
        "resolution": result.get("resolution"),
        "approval": STAGE3_PENDING[thread_id]["approval"] if pending else None,
    }


@router.get("/stage3/threads/{thread_id}")
def stage3_thread(thread_id: str, session: dict = Depends(require_role("tenant"))):
    result = STAGE3_THREADS.get(thread_id)
    if not result:
        raise HTTPException(status_code=404, detail="Stage 3 workflow thread not found.")
    if result["tenant_id"] != session["tenant_id"]:
        raise HTTPException(status_code=403, detail="You can only access your own workflow.")
    return {"thread_id": thread_id, "status": result["status"], "resolution": result["resolution"]}


@router.get("/stage2/threads/{thread_id}")
def stage2_thread(thread_id: str, session: dict = Depends(require_role("tenant"))):
    state = get_stage2_state(thread_id)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow thread not found.")
    if state.get("tenant_id") != session["tenant_id"]:
        raise HTTPException(status_code=403, detail="You can only access your own workflow.")
    return {"thread_id": thread_id, "status": state.get("status"), "resolution": state.get("resolution")}


@router.get("/admin/stage2/approvals")
def stage2_approvals(_: dict = Depends(require_role("admin"))):
    approvals = []
    for thread_id, item in PENDING_APPROVALS.items():
        approval = item["approval"]
        request = approval.get("maintenance_request") or {}
        tenant = property_service.lookup_tenant(item["tenant_id"])
        unit = property_service.lookup_unit(tenant.unit_id) if tenant else None
        approvals.append({
            "thread_id": thread_id,
            "tenant_id": item["tenant_id"],
            "tenant": tenant.name if tenant else item["tenant_id"],
            "unit": unit.unit_number if unit else None,
            "property": unit.property_name if unit else None,
            "issue": item["message"],
            "evidence": approval.get("reason"),
            "recommended_action": approval.get("recommended_action"),
            "proposed_credit": approval.get("proposed_credit"),
            "maintenance_request_id": request.get("request_id"),
        })
    for thread_id, item in STAGE3_PENDING.items():
        resolution = item["resolution"]
        approval = item["approval"]
        tenant = property_service.lookup_tenant(item["tenant_id"])
        unit = property_service.lookup_unit(tenant.unit_id) if tenant else None
        approvals.append({"thread_id": thread_id, "approval_id": approval["approval_id"], "tenant_id": item["tenant_id"], "tenant": tenant.name if tenant else item["tenant_id"], "unit": unit.unit_number if unit else None, "property": unit.property_name if unit else None, "issue": item["message"], "evidence": approval["reason"], "recommended_action": resolution["action_taken"], "proposed_credit": approval["proposed_credit"], "maintenance_request_id": approval["maintenance_request_id"], "maintenance_context": approval["maintenance_context"], "stage": "stage3"})
    return approvals


@router.get("/admin/approvals/history")
def approval_history(_: dict = Depends(require_role("admin"))):
    approvals = []
    for record in get_approval_history():
        tenant = property_service.lookup_tenant(record["tenant_id"])
        approvals.append({**record, "tenant": tenant.name if tenant else record["tenant_id"]})
    return approvals


@router.post("/admin/stage2/approvals/{thread_id}/resume")
def stage2_resume(thread_id: str, payload: ApprovalDecision, _: dict = Depends(require_role("admin"))):
    if thread_id in STAGE3_PENDING:
        if payload.decision == "edit" and payload.approved_credit is None:
            raise HTTPException(status_code=422, detail="Edit & approve requires a positive credit amount.")
        result = resume_stage3(thread_id, payload.decision, payload.approved_credit)
        return {"thread_id": thread_id, "status": result["status"], "resolution": result["resolution"], "approval": result.get("approval")}
    if thread_id not in PENDING_APPROVALS:
        raise HTTPException(status_code=404, detail="Approval workflow not found.")
    result = resume_stage2(thread_id, payload.model_dump(exclude_none=True))
    return {"thread_id": thread_id, "status": result.get("status"), "resolution": result.get("resolution")}


@router.post("/admin/requests/{request_id}/assignment")
def assign_request(request_id: str, payload: AssignmentUpdate, _: dict = Depends(require_role("admin"))):
    updated = property_service.assign_maintenance_request(request_id, payload.assigned_team, payload.status.value)
    if not updated:
        raise HTTPException(status_code=404, detail="Maintenance request not found.")
    return updated

"""Stable service interfaces used by API routes and LangChain tools."""

from backend.schemas.models import MaintenanceRequest, Payment, Tenant, Unit
from backend.services import repository


def lookup_tenant(tenant_id: str) -> Tenant | None:
    return repository.get_tenant(tenant_id)


def lookup_unit(unit_id: str) -> Unit | None:
    return repository.get_unit(unit_id)


def check_maintenance_history(tenant_id: str, category: str | None = None) -> list[MaintenanceRequest]:
    history = repository.get_requests(tenant_id)
    return [request for request in history if not category or request.category.lower() == category.lower()]


def check_active_maintenance_requests(tenant_id: str) -> list[MaintenanceRequest]:
    return repository.get_active_requests(tenant_id)


def check_rent_status(tenant_id: str) -> Payment | None:
    payments = repository.get_payments(tenant_id)
    return max(payments, key=lambda payment: payment.billing_month, default=None)


def create_maintenance_request(
    tenant_id: str, category: str, description: str, priority: str, assigned_team: str
) -> MaintenanceRequest:
    tenant = lookup_tenant(tenant_id)
    if tenant is None:
        raise ValueError(f"Tenant {tenant_id} was not found")
    return repository.add_maintenance_request(
        tenant_id, tenant.unit_id, category, description, priority, assigned_team
    )


def assign_maintenance_request(request_id: str, assigned_team: str, status: str) -> MaintenanceRequest | None:
    return repository.update_maintenance_request(request_id, assigned_team, status)

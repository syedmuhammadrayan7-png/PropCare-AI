"""LangChain tools are deliberately thin wrappers around the service layer."""

from langchain.tools import tool

from backend.services import property_service


@tool
def lookup_tenant(tenant_id: str) -> dict:
    """Look up a PropCare tenant by their tenant ID. Use this first for a support request."""
    tenant = property_service.lookup_tenant(tenant_id)
    return tenant.model_dump(mode="json") if tenant else {"error": "Tenant not found"}


@tool
def lookup_unit(unit_id: str) -> dict:
    """Look up property and unit details by unit ID."""
    unit = property_service.lookup_unit(unit_id)
    return unit.model_dump(mode="json") if unit else {"error": "Unit not found"}


@tool
def check_maintenance_history(tenant_id: str, category: str | None = None) -> list[dict]:
    """Check a tenant's actual maintenance history; optionally filter by category such as HVAC or Plumbing."""
    return [item.model_dump(mode="json") for item in property_service.check_maintenance_history(tenant_id, category)]


@tool
def check_rent_status(tenant_id: str) -> dict:
    """Check the latest rent/payment status for a tenant."""
    payment = property_service.check_rent_status(tenant_id)
    return payment.model_dump(mode="json") if payment else {"error": "No payment record found"}


@tool
def create_maintenance_request(
    tenant_id: str, category: str, description: str, priority: str, assigned_team: str
) -> dict:
    """Create a real local mock maintenance ticket after verifying the tenant. Categories include HVAC, Plumbing, Electrical, Access, and General."""
    created = property_service.create_maintenance_request(tenant_id, category, description, priority, assigned_team)
    return created.model_dump(mode="json")


PROPCare_TOOLS = [lookup_tenant, lookup_unit, check_maintenance_history, check_rent_status, create_maintenance_request]

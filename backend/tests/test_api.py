from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def tenant_token() -> str:
    response = client.post("/api/auth/login", json={"email": "ayesha.khan@demo.propcare.pk", "password": "TenantDemo123!", "role": "tenant"})
    assert response.status_code == 200
    return response.json()["token"]


def admin_token() -> str:
    response = client.post("/api/auth/login", json={"email": "manager@demo.propcare.pk", "password": "AdminDemo123!", "role": "admin"})
    assert response.status_code == 200
    return response.json()["token"]


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["stage"] == "1-langchain"


def test_tenant_endpoint():
    response = client.get("/api/tenants/T-1001", headers={"Authorization": f"Bearer {tenant_token()}"})
    assert response.status_code == 200
    assert response.json()["unit_id"] == "U-MH-B-804"


def test_demo_auth_and_role_protection():
    assert client.post("/api/auth/login", json={"email": "ayesha.khan@demo.propcare.pk", "password": "wrong", "role": "tenant"}).status_code == 401
    assert client.get("/api/admin/requests", headers={"Authorization": f"Bearer {tenant_token()}"}).status_code == 403
    assert client.get("/api/admin/requests", headers={"Authorization": f"Bearer {admin_token()}"}).status_code == 200


def test_agent_module_imports_without_api_key():
    from backend.agent.propcare_agent import build_agent
    assert callable(build_agent)

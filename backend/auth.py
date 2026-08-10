"""Minimal signed-token demo authentication. This is intentionally not production auth."""

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from backend.config import load_environment


@dataclass(frozen=True)
class DemoAccount:
    email: str
    password: str
    role: str
    tenant_id: str | None = None
    name: str = ""


DEMO_ACCOUNTS = (
    DemoAccount("ayesha.khan@demo.propcare.pk", "TenantDemo123!", "tenant", "T-1001", "Ayesha Khan"),
    DemoAccount("manager@demo.propcare.pk", "AdminDemo123!", "admin", None, "Sana Malik"),
)


def _secret() -> bytes:
    load_environment()
    return os.getenv("DEMO_AUTH_SECRET", "propcare-stage-1-demo-secret-change-before-production").encode()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def authenticate(email: str, password: str, role: str) -> DemoAccount | None:
    return next((account for account in DEMO_ACCOUNTS if account.email == email and account.password == password and account.role == role), None)


def create_token(account: DemoAccount) -> str:
    payload = {"role": account.role, "tenant_id": account.tenant_id, "name": account.name, "exp": int(time.time()) + 60 * 60 * 8}
    encoded = _encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = _encode(hmac.new(_secret(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def parse_token(token: str) -> dict:
    try:
        encoded, signature = token.split(".", 1)
        expected = _encode(hmac.new(_secret(), encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        payload = json.loads(_decode(encoded))
        if payload["exp"] < time.time():
            raise ValueError("expired")
        return payload
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=401, detail="Your demo session is invalid or expired. Please sign in again.") from error


def current_session(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in is required.")
    return parse_token(authorization.removeprefix("Bearer "))


def require_role(role: str):
    def dependency(session: dict = Depends(current_session)) -> dict:
        if session.get("role") != role:
            raise HTTPException(status_code=403, detail="This demo account does not have access to this area.")
        return session
    return dependency

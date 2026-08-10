"""Small, explicit runtime configuration for the Stage 1 backend."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent


def load_environment() -> None:
    """Load root settings first, then allow an optional backend-only override."""
    load_dotenv(ROOT_DIR / ".env")
    load_dotenv(BACKEND_DIR / ".env", override=True)


def get_openai_settings() -> tuple[str, str, float]:
    load_environment()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured. Add it to .env or backend/.env.")

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    try:
        timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45"))
    except ValueError as error:
        raise RuntimeError("OPENAI_TIMEOUT_SECONDS must be a number.") from error
    if timeout <= 0:
        raise RuntimeError("OPENAI_TIMEOUT_SECONDS must be greater than zero.")
    return api_key, model, timeout

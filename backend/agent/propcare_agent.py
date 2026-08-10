import logging

from langchain.agents import create_agent
from langchain.agents.middleware import PIIMiddleware
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError

from backend.agent.prompts import SYSTEM_PROMPT
from backend.config import get_openai_settings
from backend.schemas.models import TenantResolution
from backend.tools.propcare_tools import PROPCare_TOOLS

logger = logging.getLogger(__name__)


class OpenAIRequestTimeout(RuntimeError):
    """The provider did not answer within the configured request timeout."""


class OpenAIConnectionUnavailable(RuntimeError):
    """The backend could not establish a connection to OpenAI."""


class OpenAIRequestFailed(RuntimeError):
    """OpenAI returned a non-timeout provider error."""


def build_agent():
    """Create the Stage 1 LangChain agent on demand, after environment validation."""
    api_key, model_name, timeout = get_openai_settings()
    logger.info("Initializing PropCare Stage 1 agent | model=%s | timeout=%ss | api_key_loaded=yes", model_name, timeout)
    model = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        temperature=0,
        timeout=timeout,
        # Do not retry provider calls automatically: a retry after a tool call could duplicate a ticket.
        max_retries=0,
    )
    return create_agent(
        model=model,
        tools=PROPCare_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        response_format=TenantResolution,
        middleware=[
            PIIMiddleware("email", strategy="redact", apply_to_input=True, apply_to_output=True),
            PIIMiddleware(
                "phone_number",
                detector=r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}",
                strategy="redact",
                apply_to_input=True,
                apply_to_output=True,
            ),
        ],
    )


def resolve_tenant_message(tenant_id: str, message: str) -> TenantResolution:
    agent = build_agent()
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": f"Tenant ID: {tenant_id}\nResident message: {message}"}]},
            # Prevent an unexpected model/tool loop from running indefinitely.
            config={"recursion_limit": 12},
        )
        return result["structured_response"]
    except APITimeoutError as error:
        logger.warning("OpenAI timed out while resolving tenant %s", tenant_id)
        raise OpenAIRequestTimeout("OpenAI request timed out. Please retry.") from error
    except APIConnectionError as error:
        logger.warning("OpenAI connection failed while resolving tenant %s: %s", tenant_id, error)
        raise OpenAIConnectionUnavailable("OpenAI connection is unavailable. Please retry.") from error
    except APIStatusError as error:
        logger.warning("OpenAI returned status %s while resolving tenant %s", error.status_code, tenant_id)
        raise OpenAIRequestFailed("OpenAI request failed. Please retry.") from error

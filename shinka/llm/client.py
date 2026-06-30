"""LLM client factory — Azure/OpenAI only (this fork runs Azure exclusively).

Azure uses ``base_url=<resource>/openai/v1`` so the SDK hits the Responses API
(`/openai/v1/responses`) rather than the deployment-based path.
"""

from typing import Any, Tuple
import os
import openai
import instructor
from shinka.env import load_shinka_dotenv
from .constants import TIMEOUT
from .providers.model_resolver import resolve_model_backend

load_shinka_dotenv()

_SUPPORTED = ("openai", "azure_openai")


# Shinka model ids deployed ONLY on the East US 2 resource. They route to the
# AZURE_EASTUS2_* creds when those env vars are present; otherwise they fall back
# to the default AZURE_API_* resource (backward-compatible).
_EASTUS2_ONLY_MODELS = {"azure-gpt-5.4-pro"}


def _build_azure_base_url() -> str:
    endpoint = os.getenv("AZURE_API_ENDPOINT")
    if not endpoint:
        raise ValueError("AZURE_API_ENDPOINT is required for Azure OpenAI models.")
    return endpoint.rstrip("/") + "/openai/v1"


def _azure_creds_for(model_name: str) -> Tuple[str, str, str]:
    """Return ``(api_key, api_version, base_url)`` for an Azure model. East-US-2-only
    models (e.g. ``gpt-5.4-pro``) route to the ``AZURE_EASTUS2_*`` resource when those
    env vars are set; every other model uses the default ``AZURE_API_*`` resource."""
    base_id = model_name.split("@", 1)[0]
    if base_id in _EASTUS2_ONLY_MODELS and os.getenv("AZURE_EASTUS2_ENDPOINT"):
        return (
            os.getenv("AZURE_EASTUS2_API_KEY"),
            os.getenv("AZURE_EASTUS2_API_VERSION", "preview"),
            os.getenv("AZURE_EASTUS2_ENDPOINT").rstrip("/") + "/openai/v1",
        )
    return (
        os.getenv("AZURE_OPENAI_API_KEY"),
        os.getenv("AZURE_API_VERSION", "preview"),
        _build_azure_base_url(),
    )


def _unsupported(model_name: str, provider: str):
    raise ValueError(
        f"Only Azure/OpenAI providers are supported in this fork "
        f"(got provider={provider!r} for model {model_name!r}). Use an "
        f"`azure-*` model id."
    )


def get_client_llm(
    model_name: str, structured_output: bool = False
) -> Tuple[Any, str, str]:
    """Sync client for ``model_name``. Returns (client, api_model_name, provider)."""
    resolved = resolve_model_backend(model_name)
    provider = resolved.provider
    api_model_name = resolved.api_model_name

    if provider == "openai":
        client = openai.OpenAI(timeout=TIMEOUT)
    elif provider == "azure_openai":
        api_key, api_version, base_url = _azure_creds_for(model_name)
        client = openai.AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            base_url=base_url,
            timeout=TIMEOUT,
        )
    else:
        _unsupported(model_name, provider)

    if structured_output:
        client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    return client, api_model_name, provider


def get_async_client_llm(
    model_name: str, structured_output: bool = False
) -> Tuple[Any, str, str]:
    """Async client for ``model_name``. Returns (client, api_model_name, provider)."""
    resolved = resolve_model_backend(model_name)
    provider = resolved.provider
    api_model_name = resolved.api_model_name

    if provider == "openai":
        client = openai.AsyncOpenAI(timeout=TIMEOUT)
    elif provider == "azure_openai":
        api_key, api_version, base_url = _azure_creds_for(model_name)
        client = openai.AsyncAzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            base_url=base_url,
            timeout=TIMEOUT,
        )
    else:
        _unsupported(model_name, provider)

    if structured_output:
        client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    return client, api_model_name, provider

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


def _build_azure_base_url() -> str:
    endpoint = os.getenv("AZURE_API_ENDPOINT")
    if not endpoint:
        raise ValueError("AZURE_API_ENDPOINT is required for Azure OpenAI models.")
    return endpoint.rstrip("/") + "/openai/v1"


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
        client = openai.AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION", "preview"),
            base_url=_build_azure_base_url(),
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
        client = openai.AsyncAzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION", "preview"),
            base_url=_build_azure_base_url(),
            timeout=TIMEOUT,
        )
    else:
        _unsupported(model_name, provider)

    if structured_output:
        client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    return client, api_model_name, provider

from typing import Any, Tuple
import os
import openai
import instructor
from shinka.env import load_shinka_dotenv
from .constants import TIMEOUT
from .providers.model_resolver import resolve_model_backend

load_shinka_dotenv()


def _build_azure_base_url() -> str:
    endpoint = os.getenv("AZURE_API_ENDPOINT")
    if not endpoint:
        raise ValueError("AZURE_API_ENDPOINT is required for Azure OpenAI models.")
    return endpoint.rstrip("/") + "/openai/v1"


def get_client_llm(
    model_name: str, structured_output: bool = False
) -> Tuple[Any, str, str]:
    """Get the client and model for the given model name.

    Args:
        model_name (str): The name of the model to get the client.

    Raises:
        ValueError: If the model is not supported.

    Returns:
        Tuple[Any, str, str]: (client, API model name, resolved provider).
    """
    resolved = resolve_model_backend(model_name)
    provider = resolved.provider
    api_model_name = resolved.api_model_name

    if provider == "openai":
        client = openai.OpenAI(timeout=TIMEOUT)  # 20 minutes
        if structured_output:
            client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    elif provider == "azure_openai":
        # Use base_url with the v1 OpenAI-compatible Azure endpoint to bypass
        # AzureOpenAI's deployment-based URL injection — required for the
        # responses API (which only exists at /openai/v1/responses, not at the
        # classic /openai/deployments/{model}/responses path).
        client = openai.AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION", "preview"),
            base_url=_build_azure_base_url(),
            timeout=TIMEOUT,  # 20 minutes
        )
        if structured_output:
            client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    else:
        raise ValueError(f"Model {model_name} not supported.")

    return client, api_model_name, provider


def get_async_client_llm(
    model_name: str, structured_output: bool = False
) -> Tuple[Any, str, str]:
    """Get the async client and model for the given model name.

    Args:
        model_name (str): The name of the model to get the client.

    Raises:
        ValueError: If the model is not supported.

    Returns:
        Tuple[Any, str, str]: (async client, API model name, resolved provider).
    """
    resolved = resolve_model_backend(model_name)
    provider = resolved.provider
    api_model_name = resolved.api_model_name

    if provider == "openai":
        client = openai.AsyncOpenAI(timeout=TIMEOUT)
        if structured_output:
            client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    elif provider == "azure_openai":
        client = openai.AsyncAzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION", "preview"),
            base_url=_build_azure_base_url(),
            timeout=TIMEOUT,
        )
        if structured_output:
            client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    else:
        raise ValueError(f"Model {model_name} not supported.")

    return client, api_model_name, provider

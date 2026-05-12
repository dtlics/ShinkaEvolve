from dataclasses import dataclass
import os
from typing import Any, Optional, Tuple

import openai

from shinka.env import load_shinka_dotenv

from .providers.pricing import get_provider

load_shinka_dotenv()

TIMEOUT = 600


@dataclass(frozen=True)
class ResolvedEmbeddingModel:
    original_model_name: str
    api_model_name: str
    provider: str
    base_url: Optional[str] = None
    api_key_env_name: Optional[str] = None


def resolve_embedding_backend(model_name: str) -> ResolvedEmbeddingModel:
    """Resolve runtime backend info for OpenAI/Azure embedding models."""
    provider = get_provider(model_name)
    if provider == "azure":
        api_model_name = model_name.split("azure-", 1)[-1]
        return ResolvedEmbeddingModel(
            original_model_name=model_name,
            api_model_name=api_model_name,
            provider=provider,
            base_url=None,
        )
    if provider == "openai":
        return ResolvedEmbeddingModel(
            original_model_name=model_name,
            api_model_name=model_name,
            provider=provider,
            base_url=None,
        )

    raise ValueError(
        f"Embedding model {model_name} not supported. "
        "Use a known pricing.csv model (openai or azure-...)."
    )


def get_client_embed(model_name: str) -> Tuple[Any, str]:
    """Get the client and model for the given embedding model name."""
    resolved = resolve_embedding_backend(model_name)
    provider = resolved.provider

    if provider == "openai":
        client = openai.OpenAI(timeout=TIMEOUT)
    elif provider == "azure":
        client = openai.AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION"),
            azure_endpoint=os.getenv("AZURE_API_ENDPOINT"),
            timeout=TIMEOUT,
        )
    else:
        raise ValueError(f"Embedding model {model_name} not supported.")

    return client, resolved.api_model_name


def get_async_client_embed(model_name: str) -> Tuple[Any, str]:
    """Get the async client and model for the given embedding model name."""
    resolved = resolve_embedding_backend(model_name)
    provider = resolved.provider

    if provider == "openai":
        client = openai.AsyncOpenAI()
    elif provider == "azure":
        client = openai.AsyncAzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION"),
            azure_endpoint=os.getenv("AZURE_API_ENDPOINT"),
        )
    else:
        raise ValueError(f"Embedding model {model_name} not supported.")

    return client, resolved.api_model_name

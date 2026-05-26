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
    """Resolve runtime backend info for embedding model ids. Azure/OpenAI only."""
    provider = get_provider(model_name)
    if provider == "azure":
        api_model_name = model_name.split("azure-", 1)[-1]
        return ResolvedEmbeddingModel(
            original_model_name=model_name,
            api_model_name=api_model_name,
            provider=provider,
            base_url=None,
        )
    if provider in ("openai",):
        return ResolvedEmbeddingModel(
            original_model_name=model_name,
            api_model_name=model_name,
            provider=provider,
            base_url=None,
        )
    raise ValueError(
        f"Embedding model {model_name} not supported (Azure-only fork). "
        "Use 'azure-text-embedding-3-small' or a known OpenAI embedding model."
    )


def _azure_embed_kwargs() -> dict:
    # Embeddings need a stable api-version (distinct from AZURE_API_VERSION,
    # which serves the chat /responses path on 'preview').
    return {
        "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
        "api_version": os.getenv("AZURE_EMBEDDING_API_VERSION", "2024-10-21"),
        "azure_endpoint": os.getenv("AZURE_API_ENDPOINT"),
        "timeout": TIMEOUT,
    }


def get_client_embed(model_name: str) -> Tuple[Any, str]:
    """Sync embedding client. Azure/OpenAI only."""
    resolved = resolve_embedding_backend(model_name)
    if resolved.provider == "openai":
        client = openai.OpenAI(timeout=TIMEOUT)
    elif resolved.provider == "azure":
        client = openai.AzureOpenAI(**_azure_embed_kwargs())
    else:
        raise ValueError(f"Embedding model {model_name} not supported.")
    return client, resolved.api_model_name


def get_async_client_embed(model_name: str) -> Tuple[Any, str]:
    """Async embedding client. Azure/OpenAI only."""
    resolved = resolve_embedding_backend(model_name)
    if resolved.provider == "openai":
        client = openai.AsyncOpenAI()
    elif resolved.provider == "azure":
        kw = _azure_embed_kwargs()
        kw.pop("timeout", None)
        client = openai.AsyncAzureOpenAI(**kw)
    else:
        raise ValueError(f"Embedding model {model_name} not supported.")
    return client, resolved.api_model_name

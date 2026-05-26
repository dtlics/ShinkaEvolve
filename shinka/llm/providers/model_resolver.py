"""Resolve a model id to its backend. Azure/OpenAI only.

`azure-<deployment>` → Azure (the fork's normal case). Known pricing.csv models
resolve via `get_provider` (typically `openai`). Everything else is rejected.
"""

from dataclasses import dataclass
from typing import Optional

from .pricing import get_provider


@dataclass(frozen=True)
class ResolvedModel:
    original_model_name: str
    api_model_name: str
    provider: str
    base_url: Optional[str] = None
    api_key_env_name: Optional[str] = None


def resolve_model_backend(model_name: str) -> ResolvedModel:
    """Resolve runtime backend info. Azure/OpenAI only."""
    provider = get_provider(model_name)
    if provider is not None:
        return ResolvedModel(
            original_model_name=model_name,
            api_model_name=model_name,
            provider=provider,
            base_url=None,
        )

    if model_name.startswith("azure-"):
        api_model_name = model_name.split("azure-", 1)[-1]
        if not api_model_name:
            raise ValueError("Azure model name is missing after 'azure-' prefix.")
        return ResolvedModel(
            original_model_name=model_name,
            api_model_name=api_model_name,
            provider="azure_openai",
            base_url=None,
        )

    raise ValueError(
        f"Model '{model_name}' is not supported (Azure-only fork). "
        "Use an 'azure-<deployment>' id or a known pricing.csv model."
    )

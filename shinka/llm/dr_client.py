"""Azure DR client factory (Phase 5 of research-grounding).

``o3-deep-research`` lives on a separate Azure AI Foundry project from the
regular ``gpt-*`` deployments, so it needs its own client construction:
the endpoint URL is different, the API version may differ, and the
per-call timeout has to be longer (we cap polling at 30 min).

Secrets stay in env vars -- this module never accepts or stores raw keys.
The ``EvolutionConfig.dr_endpoint_env`` and ``dr_api_key_env`` fields
name the variables to read, defaulting to ``AZURE_DR_ENDPOINT`` and
``AZURE_DR_API_KEY``.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import openai

from .constants import POLL_TIMEOUT_DR

logger = logging.getLogger(__name__)


class DRConfigurationError(RuntimeError):
    """Raised when the DR endpoint or key env vars are missing."""


def _normalize_endpoint(endpoint: str) -> str:
    """Strip ``/openai/v1/responses`` (or any trailing path) so the OpenAI
    SDK appends its own path correctly.

    The Azure AI Foundry "Responses API" URL the user provisions looks like::

        https://<resource>.services.ai.azure.com/api/projects/<proj>/openai/v1/responses

    The OpenAI SDK expects ``azure_endpoint`` to be the base origin (no
    ``openai/v1/...`` suffix); it appends ``openai/v1`` itself. Pasting the
    full URL silently produces 404s on retrieve. We strip back to the
    project root: ``...services.ai.azure.com/api/projects/<proj>``.
    """
    cleaned = endpoint.strip()
    if not cleaned:
        return cleaned
    # Drop trailing slashes.
    while cleaned.endswith("/"):
        cleaned = cleaned[:-1]
    # Strip the API-version suffix Azure Foundry hands out (
    # ``/openai/v1/responses`` and similar).
    for suffix in ("/openai/v1/responses", "/openai/v1"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned


def _read_env_credentials(
    endpoint_env: str, api_key_env: str
) -> tuple[str, str]:
    endpoint = os.getenv(endpoint_env, "").strip()
    api_key = os.getenv(api_key_env, "").strip()
    missing = []
    if not endpoint:
        missing.append(endpoint_env)
    if not api_key:
        missing.append(api_key_env)
    if missing:
        raise DRConfigurationError(
            "Deep-research credentials missing env var(s): "
            + ", ".join(missing)
            + ". Set them in your shell, ~/.zshrc, or a project-local .env "
            "file before running with enable_deep_research=True."
        )
    return _normalize_endpoint(endpoint), api_key


def get_async_dr_client(
    *,
    endpoint_env: str = "AZURE_DR_ENDPOINT",
    api_key_env: str = "AZURE_DR_API_KEY",
    api_version: Optional[str] = None,
    timeout: float = POLL_TIMEOUT_DR,
) -> openai.AsyncAzureOpenAI:
    """Construct the async Azure client for ``o3-deep-research``.

    The caller is responsible for invoking ``client.responses.create`` via
    the Phase 2a ``create_and_poll_async`` helper -- DR runs are long
    enough that the bg+poll machinery is mandatory, not optional.
    """
    endpoint, api_key = _read_env_credentials(endpoint_env, api_key_env)
    # Azure DR's responses API is on a preview track; fall back to the
    # general Foundry default when AZURE_API_VERSION is unset.
    effective_api_version = api_version or os.getenv(
        "AZURE_API_VERSION", "preview"
    )
    logger.debug(
        "Constructing DR client endpoint=%s api_version=%s timeout=%s",
        endpoint,
        effective_api_version,
        timeout,
    )
    return openai.AsyncAzureOpenAI(
        api_key=api_key,
        api_version=effective_api_version,
        azure_endpoint=endpoint,
        timeout=timeout,
    )


def get_dr_client(
    *,
    endpoint_env: str = "AZURE_DR_ENDPOINT",
    api_key_env: str = "AZURE_DR_API_KEY",
    api_version: Optional[str] = None,
    timeout: float = POLL_TIMEOUT_DR,
) -> openai.AzureOpenAI:
    """Sync variant of :func:`get_async_dr_client`.

    Only useful for offline tooling; the runner uses the async client.
    """
    endpoint, api_key = _read_env_credentials(endpoint_env, api_key_env)
    effective_api_version = api_version or os.getenv(
        "AZURE_API_VERSION", "preview"
    )
    return openai.AzureOpenAI(
        api_key=api_key,
        api_version=effective_api_version,
        azure_endpoint=endpoint,
        timeout=timeout,
    )


__all__ = [
    "DRConfigurationError",
    "get_async_dr_client",
    "get_dr_client",
]

"""Azure DR client factory for ``o3-deep-research`` (phase 2 of research-grounding).

Deep research runs through a **separate Azure resource** from the
general ``gpt-*`` chat/reasoning endpoint configured in
``shinka.llm.client``. The user provisions a dedicated Foundry project
for the DR deployment and exposes it via two new env vars:

* ``AZURE_DR_ENDPOINT`` — the Azure AI services base URL (e.g.
  ``https://<resource>.services.ai.azure.com/api/projects/<project>``).
  We append ``/openai/v1`` so the responses API path resolves the same
  way ``shinka.llm.client._build_azure_base_url`` does for the main
  endpoint.
* ``AZURE_DR_API_KEY`` — the key for that resource. Distinct from
  ``AZURE_OPENAI_API_KEY`` even when the two resources live in the
  same tenant.
* ``AZURE_DR_API_VERSION`` (optional) — defaults to ``"preview"``,
  matching the main endpoint's default. ``o3-deep-research`` is in
  preview, so the preview API surface is what we want.

The DR endpoint is **not** routed through the bandit-driven
``llm_models`` pool. The DR summarizer constructs a client via
``get_dr_async_client`` directly, which keeps DR cost separable from
proposer/meta cost in the Azure dashboard (the ``purpose=dr_stage_*``
tag on each call also feeds this distinction).

Why a separate file
-------------------
Reusing ``shinka.llm.client.get_async_client_llm`` would force the DR
endpoint into the same env-var pair as the main endpoint, conflicting
with the user's "separate Azure resource" decision. Reusing the model
resolver is unnecessary too: DR has exactly one deployment per run, so
we accept the deployment name as a constructor arg rather than
plumbing it through ``resolve_model_backend``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Tuple

import openai

from shinka.env import load_shinka_dotenv

from .background_model import (
    BackgroundOpenAIResponsesModel,
    DEFAULT_POLL_INTERVAL_SEC,
)

load_shinka_dotenv()

logger = logging.getLogger(__name__)


# DR runs can take ~10-30 minutes per call. The default client timeout
# from the main path (3600s) is plenty; we surface ``DR_TIMEOUT`` here
# for the BackgroundOpenAIResponsesModel's poll-wall cap so the user
# can tighten it if they want shorter individual stage timeouts.
DR_TIMEOUT: float = 1800.0

# Initial backoff cadence. Polls start at 5s (DR jobs always need
# more than that) and back off geometrically to 60s. The
# BackgroundOpenAIResponsesModel uses a fixed interval; for DR we
# accept that simpler model rather than implementing a separate
# poll-with-backoff path here. 5s gives a tight handle on
# completion latency without burning Azure rate limits.
DR_POLL_INTERVAL_SEC: float = 5.0

# Queue-stuck cap. If DR sits in ``queued`` (never moves to
# ``in_progress``) past this, abort. DR jobs we've seen in practice
# move into in_progress within a minute or two; 10 min gives plenty
# of slack while still catching the "stuck forever" failure mode.
DR_MAX_QUEUED_WAIT_SEC: float = 600.0


# Env-var names we read. Constants here so the summarizer can
# reference them in error messages without re-deriving the spelling.
DR_ENDPOINT_ENV: str = "AZURE_DR_ENDPOINT"
DR_API_KEY_ENV: str = "AZURE_DR_API_KEY"
DR_API_VERSION_ENV: str = "AZURE_DR_API_VERSION"


def _build_dr_base_url(endpoint: str) -> str:
    """Canonicalize the DR endpoint to the responses-API base URL.

    Mirrors ``shinka.llm.client._build_azure_base_url``: the public
    endpoint is something like
    ``https://<resource>.services.ai.azure.com/api/projects/<project>``
    and the responses API lives at ``/openai/v1/responses``. We strip
    a trailing slash and append ``/openai/v1`` so the AsyncAzureOpenAI
    client's ``base_url`` resolves to the right path.

    The user may supply the URL already with ``/openai/v1`` appended
    (per their internal docs) — in that case we leave it alone so
    we don't double-append.
    """
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/openai/v1"):
        return endpoint
    if endpoint.endswith("/openai/v1/responses"):
        # The user pasted the full responses URL; drop the /responses
        # suffix because AsyncAzureOpenAI appends the path itself.
        return endpoint[: -len("/responses")]
    return endpoint + "/openai/v1"


def get_dr_async_client(
    *,
    endpoint_env: str = DR_ENDPOINT_ENV,
    api_key_env: str = DR_API_KEY_ENV,
    api_version_env: str = DR_API_VERSION_ENV,
    timeout: float = DR_TIMEOUT,
) -> Tuple[Any, str]:
    """Construct the ``AsyncAzureOpenAI`` client for the DR endpoint.

    Returns ``(client, base_url)`` so callers can log the URL they're
    talking to (handy when debugging which Azure resource served a
    given request).

    Raises ``RuntimeError`` if the endpoint/key env vars aren't set —
    this is a programmer error, not a transient one (DR runs require
    the user to have provisioned the resource), so we fail loud.
    """
    endpoint = os.getenv(endpoint_env)
    if not endpoint:
        raise RuntimeError(
            f"{endpoint_env} is required for deep-research calls. "
            "Set it in .env or export it in your shell — the deep-research "
            "endpoint is a separate Azure resource from the main gpt-* "
            "endpoint."
        )
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"{api_key_env} is required for deep-research calls."
        )
    api_version = os.getenv(api_version_env, "preview")
    base_url = _build_dr_base_url(endpoint)

    client = openai.AsyncAzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        base_url=base_url,
        timeout=timeout,
    )
    return client, base_url


class DeepResearchModel(BackgroundOpenAIResponsesModel):
    """``BackgroundOpenAIResponsesModel`` configured for DR cadence.

    Same submit-and-poll mechanics as the main proposer model. The
    only difference is the poll cadence and timeout: DR jobs can take
    20-30 minutes; we poll at 5s rather than 2s to keep Azure poll
    pressure down, and we cap the wall at 30 minutes. If the job
    overruns, the caller (``DeepResearchSummarizer``) catches
    ``BackgroundPollTimeout`` and falls back to the cached/placeholder
    brief — DR cost is sunk at that point and crashing the meta cycle
    would lose the rest of the run's progress.
    """

    def __init__(
        self,
        model: Any,
        openai_client: Any,
        *,
        poll_interval_sec: float = DR_POLL_INTERVAL_SEC,
        poll_timeout_sec: float = DR_TIMEOUT,
        max_queued_wait_sec: float = DR_MAX_QUEUED_WAIT_SEC,
    ) -> None:
        super().__init__(
            model=model,
            openai_client=openai_client,
            poll_interval_sec=poll_interval_sec,
            poll_timeout_sec=poll_timeout_sec,
            max_queued_wait_sec=max_queued_wait_sec,
        )

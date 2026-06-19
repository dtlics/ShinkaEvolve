"""Azure DR client factory for ``o3-deep-research`` (the R1 deep-research discovery route).

Deep research runs through a **separate Azure resource** from the
general ``gpt-*`` chat/reasoning endpoint configured in
``shinka.llm.client``. Both resources use the same umbrella-URL form
(``https://<resource>.openai.azure.com``); the DR client is a parallel
factory keyed on its own env vars so credentials, cost, and routing
stay separable.

Env vars
--------
* ``AZURE_DR_ENDPOINT`` — bare Azure OpenAI resource URL, e.g.
  ``https://<resource>.openai.azure.com``. We append ``/openai/v1`` so
  the responses API path resolves the same way
  ``shinka.llm.client._build_azure_base_url`` does for the main
  endpoint. The Azure AI Services project URL
  (``https://<resource>.services.ai.azure.com/api/projects/<project>``)
  also works — both surfaces serve the same deployments, and
  ``_build_dr_base_url`` normalizes either form to the responses-API
  base.
* ``AZURE_DR_API_KEY`` — key for that resource. Distinct from
  ``AZURE_OPENAI_API_KEY`` even when both resources share a tenant.
* ``AZURE_DR_API_VERSION`` (optional) — defaults to ``"preview"``,
  matching the main endpoint's default. ``o3-deep-research`` is in
  preview, so the preview API surface is what we want.

The DR endpoint is **not** routed through the bandit-driven
``llm_models`` pool. The DR summarizer constructs a client via
``get_dr_async_client`` directly, which keeps DR cost separable from
proposer/meta cost in the Azure dashboard (the ``purpose=dr_stage_*``
tag on each call also feeds this distinction).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Tuple

import openai

from shinka.env import load_shinka_dotenv

from ..constants import PER_REQUEST_TIMEOUT

load_shinka_dotenv()

logger = logging.getLogger(__name__)


# DR runs can take ~10-30 minutes per call. The default client timeout
# from the main path (3600s) is plenty; we surface ``DR_TIMEOUT`` here
# for the BackgroundOpenAIResponsesModel's poll-wall cap so the user
# can tighten it if they want shorter individual stage timeouts.
DR_TIMEOUT: float = float(os.environ.get("AZURE_DR_TIMEOUT_SEC", "3600.0"))  # 60 min:
# with the web_search tool actually enabled, o3-deep-research routinely runs >30 min
# (the old 1800s cap timed out mid-research with status still 'in_progress').

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

    Mirrors ``shinka.llm.client._build_azure_base_url``: bare resource
    URL → appends ``/openai/v1`` so AsyncAzureOpenAI's ``base_url``
    resolves to the responses API. Both URL surfaces work — the
    Azure-OpenAI umbrella form (``https://<resource>.openai.azure.com``)
    and the AI Services project form
    (``https://<resource>.services.ai.azure.com/api/projects/<proj>``).
    We accept either and tolerate a user pasting the full
    ``/openai/v1`` or ``/openai/v1/responses`` URL so the env-var
    contract is forgiving.
    """
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/openai/v1"):
        return endpoint
    if endpoint.endswith("/openai/v1/responses"):
        # User pasted the full responses URL; drop ``/responses``
        # since AsyncAzureOpenAI appends the path itself.
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


def _usage_cost(response: Any, model: str) -> float:
    """Best-effort token cost from response.usage × pricing.csv (0.0 if unavailable).
    Shared by the success path AND the failure raises (P7-T6) so a DR call that burned
    tokens but then failed terminally still reports its billed cost to the ledger."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0.0
    in_tok = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", 0) or 0
    try:
        from shinka.llm.providers.pricing import calculate_cost

        ic, oc = calculate_cost(model, int(in_tok), int(out_tok))
        return float(ic) + float(oc)
    except Exception as exc:
        logger.warning("DR cost computation failed for %s: %s", model, exc)
        return 0.0


def _err_detail(response: Any) -> Tuple[Any, Any, Any]:
    """Pull the structured failure reason off a Responses object: (error.code,
    error.message, incomplete_details.reason). All optional. A failed DR job's REAL cause
    (e.g. web_search_preview not provisioned on the resource, quota/rate, model-version)
    lives here, not in the bare status — surfaced so the orchestrator/journal can see it."""
    err = getattr(response, "error", None)
    code = getattr(err, "code", None)
    msg = getattr(err, "message", None)
    inc = getattr(getattr(response, "incomplete_details", None), "reason", None)
    return code, msg, inc


async def run_dr_call(
    client: Any,
    *,
    model: str,
    system_msg: str,
    user_msg: str,
    reasoning_effort: str = "medium",
    max_tool_calls: int = 20,
    background: bool = True,
    tools: list | None = None,
    poll_interval_sec: float = DR_POLL_INTERVAL_SEC,
    poll_timeout_sec: float = DR_TIMEOUT,
    per_request_timeout_sec: float = PER_REQUEST_TIMEOUT,
    call_metadata: dict | None = None,
    max_output_tokens: int | None = 200_000,
) -> tuple[str, float]:
    """Submit a single ``o3-deep-research`` call and return its text output.

    Bypasses the agents SDK — DR is one-shot, no tool loop on our side
    (the model uses internal web tools). Background mode + polling
    keeps the connection lifetime short, matching the long-running
    inference characteristic of DR.

    Returns ``(text, cost_estimate)``. L41: DR IS priced (pricing.csv prices o3-deep-research
    at 10/40 per 1M; _usage_cost computes it) — ``cost`` is the usage-derived token cost (0.0
    only when usage is absent, e.g. a failed job). The caller (deep_research.py) logs it to
    journal/calls via log_external_call (NOT a meta_briefs.cost column — that is the per-island
    META round's cost, a different concern).

    On poll timeout (now best-effort CANCELLED, L46) or terminal-but-failed status, raises so the
    caller can surface a placeholder brief instead of crashing.
    """
    import asyncio

    create_kwargs: dict = {
        "model": model,
        "instructions": system_msg,
        "input": user_msg,
        "background": background,
        "reasoning": {"effort": reasoning_effort},
        "max_tool_calls": max_tool_calls,
        # o3-deep-research REQUIRES at least one of web_search_preview / mcp /
        # file_search tools (else HTTP 400). Default to the built-in web search so
        # the model grounds itself; callers may override via the `tools` arg.
        "tools": tools if tools is not None else [{"type": "web_search_preview"}],
    }
    if max_output_tokens is not None:
        # Cost guardrail. o3-deep-research at $40/1M output -> 200K caps a single
        # call at ~$8 (worst case with full output); typical DR calls cost $1-2.
        # The model returns status='incomplete' with reason='max_output_tokens' if
        # exceeded, and the partial brief is still extractable.
        create_kwargs["max_output_tokens"] = int(max_output_tokens)
    if call_metadata:
        create_kwargs["metadata"] = {
            str(k): str(v) for k, v in call_metadata.items()
        }

    submitted = await asyncio.wait_for(
        client.responses.create(**create_kwargs), timeout=per_request_timeout_sec
    )
    response_id = getattr(submitted, "id", None)
    if response_id is None:
        raise RuntimeError("DR submission did not return a response id")

    last_status: str = getattr(submitted, "status", "unknown") or "unknown"
    response: Any = submitted
    terminal = {"completed", "failed", "incomplete", "cancelled", "expired"}
    # TWO-LEVEL timeout (matches the main bg transport). poll_timeout_sec is the TOTAL job
    # wall, enforced as a true monotonic DEADLINE; each status GET is capped at the SHORT
    # per_request_timeout_sec and a hung GET is RETRIED, not abandoned. (The old loop used a
    # summed-interval `elapsed += poll_interval_sec` clock, so a slow/hung retrieve() drifted
    # the wall arbitrarily late.)
    _deadline = time.monotonic() + poll_timeout_sec
    while last_status not in terminal:
        remaining = _deadline - time.monotonic()
        if remaining <= 0:
            _code, _msg, _inc = _err_detail(response)
            _err = TimeoutError(
                f"DR response {response_id} did not finish: last status={last_status!r} "
                f"after {poll_timeout_sec:.1f}s (wall)"
                + (f" error.code={_code!r}" if _code else "")
            )
            _err.cost = _usage_cost(response, model)  # P7-T6: bill what was spent
            _err.submitted = True
            _err.error_code = _code
            _err.error_message = _msg
            # L46: best-effort CANCEL the abandoned background job before raising. Otherwise it
            # keeps running server-side (a single DR job fires many large reasoning+search calls
            # for 30-60 min) and keeps BILLING + consuming the quota-constrained o3-deep-research
            # deployment (this repo's CONFIRMED failure mode). Never let a cancel failure mask
            # the TimeoutError.
            try:
                await asyncio.wait_for(
                    client.responses.cancel(response_id), timeout=per_request_timeout_sec
                )
            except Exception:
                logger.warning("DR cancel failed for %s (job may keep billing)", response_id)
            raise _err
        await asyncio.sleep(min(poll_interval_sec, remaining))
        _req_to = min(per_request_timeout_sec, max(0.001, _deadline - time.monotonic()))
        try:
            response = await asyncio.wait_for(
                client.responses.retrieve(response_id), timeout=_req_to
            )
        except asyncio.TimeoutError:
            logger.warning(
                "DR retrieve(%s) exceeded the %.0fs per-request cap — retrying (job still polling)",
                response_id, per_request_timeout_sec,
            )
            continue
        last_status = getattr(response, "status", "unknown") or "unknown"

    if last_status not in ("completed", "incomplete"):
        # failed / cancelled / expired → genuinely no usable output. Surface the REAL reason
        # (error.code/message, incomplete_details.reason) and flag it as submitted so the
        # caller floors the spend — the bare status alone hides WHY the DR job failed.
        _code, _msg, _inc = _err_detail(response)
        _err = RuntimeError(
            f"DR response {response_id} terminal status={last_status!r}"
            + (f" error.code={_code!r}" if _code else "")
            + (f" error.message={_msg!r}" if _msg else "")
            + (f" incomplete_details.reason={_inc!r}" if _inc else "")
        )
        _err.cost = _usage_cost(response, model)  # P7-T6: bill what was spent before failing
        _err.submitted = True
        _err.error_code = _code
        _err.error_message = _msg
        raise _err
    if last_status == "incomplete":
        # The model hit a cap (max_output_tokens / max_tool_calls / reasoning
        # budget) but the partial output is normally still a usable brief — so we
        # extract it instead of crashing the whole meta cycle. Log WHY so the cap
        # can be raised if briefs come back truncated.
        logger.warning(
            "DR response %s ended 'incomplete' (%r) — returning partial output",
            response_id,
            getattr(response, "incomplete_details", None),
        )

    # Extract the model's final text output. Different SDK versions
    # expose this on different attributes; try the canonical ones.
    text = getattr(response, "output_text", None)
    if not text:
        output_items = getattr(response, "output", None) or []
        for item in output_items:
            content = getattr(item, "content", None) or []
            for c in content:
                t = getattr(c, "text", None)
                if t:
                    text = t
                    break
            if text:
                break
    if not text:
        text = ""

    # Token cost from usage × pricing.csv (o3-deep-research is priced there).
    # output_tokens includes the model's reasoning/thinking tokens (Responses
    # API), so this captures the full billable token cost. The web_search tool
    # cost is NOT in usage; the caller (deep_research.py) adds a surcharge.
    cost = _usage_cost(response, model)
    return text, cost


# L41: the dead DeepResearchModel class was removed — it had ZERO importers, and the active DR
# path is run_dr_call()/get_dr_async_client() above. Its docstring referenced a nonexistent
# DeepResearchSummarizer and a wrong 30-min wall (the real DR_TIMEOUT is 60 min).

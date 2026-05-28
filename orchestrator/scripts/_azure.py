"""Shared Azure background-mode LLM call (submit + poll), with cost.

Used by the inner-loop LLM subroutines (`mutate.py`, `meta_summarize.py`). This
is the resilient transport for long Azure reasoning calls: `responses.create(
background=True)` + poll, so a long-idle TCP connection can't be silently killed.
Cost is computed from `usage` via shinka's pricing.

IMMUTABLE plumbing — do not rewrite as part of a strategy rewrite. (The *prompts*
sent through it are mutable; this transport is not.)
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from typing import Any, Dict, Optional, Tuple

_POLL_INTERVAL_SEC = 3.0
# Per-call poll-wall cap. 1 hour by design: bg+poll exists precisely to allow long
# thinking time without TCP idle-kill, so the cap should only catch genuinely-stuck
# requests (server-side hangs), not slow-but-progressing reasoning. Cost is bounded
# by max_output_tokens (see `_MAX_OUTPUT_TOKENS_BY_MODEL` below), not by wall-clock.
# Override via SHINKA_BG_POLL_TIMEOUT_SEC.
_POLL_TIMEOUT_SEC = float(os.environ.get("SHINKA_BG_POLL_TIMEOUT_SEC", "3600"))
_TERMINAL = {"completed", "failed", "incomplete", "cancelled", "expired"}

# Per-model max output token caps. Sized so a single max-output call costs < $10
# (output is the dominant cost; input is bounded by our prompt size). Pricing per
# CLAUDE.md (Main resource deployments):
#   azure-gpt-5.4-pro  : $180/1M out -> 50_000 tok  ~= $9.00 max
#   azure-gpt-5.5      : $30 /1M out -> 200_000 tok ~= $6.00 max
#   azure-gpt-5.3-codex: $14 /1M out -> 200_000 tok ~= $2.80 max
#   azure-gpt-5.4-mini : $4.5/1M out -> 200_000 tok ~= $0.90 max
# This is a guardrail for runaway malfunctions, not a throttle on normal use;
# typical calls finish well under these caps. Callers may override via
# `bg_query(..., max_output_tokens=...)`.
_MAX_OUTPUT_TOKENS_BY_MODEL: Dict[str, int] = {
    "azure-gpt-5.4-pro": 50_000,
}
_DEFAULT_MAX_OUTPUT_TOKENS = 200_000


def _resolve_max_output_tokens(model_name: str) -> int:
    return _MAX_OUTPUT_TOKENS_BY_MODEL.get(model_name, _DEFAULT_MAX_OUTPUT_TOKENS)


def _extract_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text
    for item in getattr(response, "output", None) or []:
        for c in getattr(item, "content", None) or []:
            t = getattr(c, "text", None)
            if t:
                return t
    return ""


def _usage_cost(response: Any, api_model_name: str) -> float:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0.0
    in_tok = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", 0) or 0
    try:
        from shinka.llm.providers.pricing import calculate_cost

        ic, oc = calculate_cost(api_model_name, int(in_tok), int(out_tok))
        return float(ic) + float(oc)
    except Exception:
        return 0.0


async def _bg_call(
    client, api_model_name, system_msg, user_msg, reasoning_effort, call_metadata,
    poll_interval, poll_timeout, max_output_tokens,
) -> Tuple[str, float]:
    create_kwargs: Dict[str, Any] = {
        "model": api_model_name,
        "instructions": system_msg,
        "input": user_msg,
        "background": True,
    }
    if reasoning_effort and reasoning_effort != "disabled":
        create_kwargs["reasoning"] = {"effort": reasoning_effort}
    if call_metadata:
        create_kwargs["metadata"] = {str(k): str(v) for k, v in call_metadata.items()}
    if max_output_tokens is not None:
        # OpenAI Responses API: bounds the output token count. The model returns
        # status='incomplete' with reason='max_output_tokens' if it would exceed
        # this; the partial output is still extractable. Our cost guardrail.
        create_kwargs["max_output_tokens"] = int(max_output_tokens)

    try:
        submitted = await client.responses.create(**create_kwargs)
        rid = getattr(submitted, "id", None)
        if rid is None:
            raise RuntimeError("Azure submission returned no response id")
        status = getattr(submitted, "status", "unknown") or "unknown"
        response = submitted
        # WALL-CLOCK timeout (not summed poll_intervals): a slow/hanging retrieve()
        # makes elapsed-by-interval badly undercount real time, so a "720s" cap could
        # run 20+ min wall (observed). time.monotonic() bounds the true wall duration.
        _t0 = time.monotonic()
        while status not in _TERMINAL:
            elapsed = time.monotonic() - _t0
            if elapsed > poll_timeout:
                raise TimeoutError(f"Azure response {rid} stuck at {status!r} after {elapsed:.0f}s (wall)")
            await asyncio.sleep(poll_interval)
            response = await client.responses.retrieve(rid)
            status = getattr(response, "status", "unknown") or "unknown"
        if status != "completed":
            raise RuntimeError(f"Azure response {rid} terminal status={status!r}")
        return _extract_text(response), _usage_cost(response, api_model_name)
    finally:
        # Close the async client WITHIN this event loop. Otherwise the underlying
        # httpx AsyncClient is closed by its finalizer after asyncio.run() has torn
        # the loop down, which raises a noisy "Event loop is closed" traceback to
        # stderr on every call (harmless, but it buries real errors).
        closer = getattr(client, "close", None) or getattr(client, "aclose", None)
        if closer is not None:
            try:
                maybe = closer()
                if inspect.isawaitable(maybe):
                    await maybe
            except Exception:
                pass


def bg_query(
    model_name: str,
    system_msg: str,
    user_msg: str,
    reasoning_effort: Optional[str] = None,
    call_metadata: Optional[Dict[str, Any]] = None,
    poll_interval: float = _POLL_INTERVAL_SEC,
    poll_timeout: float = _POLL_TIMEOUT_SEC,
    max_output_tokens: Optional[int] = None,
) -> Tuple[str, float]:
    """One Azure background-mode call. Returns (text, cost). Azure/OpenAI only.

    `max_output_tokens` defaults to a per-model cap (see _MAX_OUTPUT_TOKENS_BY_MODEL)
    sized so a single max-output call costs < $10. Pass an explicit value to override.
    """
    from shinka.llm.client import get_async_client_llm
    from shinka.llm.providers.model_resolver import resolve_model_backend

    provider = resolve_model_backend(model_name).provider
    if provider not in ("azure_openai", "openai"):
        raise ValueError(f"bg_query is Azure/OpenAI-only (got provider={provider!r}).")
    if max_output_tokens is None:
        max_output_tokens = _resolve_max_output_tokens(model_name)
    client, api_model_name, _ = get_async_client_llm(model_name)
    return asyncio.run(
        _bg_call(
            client, api_model_name, system_msg, user_msg,
            reasoning_effort, call_metadata, poll_interval, poll_timeout,
            max_output_tokens,
        )
    )

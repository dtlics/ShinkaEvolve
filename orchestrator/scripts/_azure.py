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
# Per-call poll-wall cap. Lowered from 1800s after the 2026-05-27 run: codex/mini finish
# a medium-reasoning mutation in ~3-4 min, so 12 min leaves ample margin while cutting off
# genuinely-slow/stuck picks (gpt-5.5/pro at medium run 25-40 min) ~2.5x faster. Override
# via SHINKA_BG_POLL_TIMEOUT_SEC. The latency-aware selection prior (select_llm.py) AVOIDS
# slow picks; this just bounds the cost when one slips through (e.g. via the floor).
_POLL_TIMEOUT_SEC = float(os.environ.get("SHINKA_BG_POLL_TIMEOUT_SEC", "720"))
_TERMINAL = {"completed", "failed", "incomplete", "cancelled", "expired"}


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
    poll_interval, poll_timeout,
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
) -> Tuple[str, float]:
    """One Azure background-mode call. Returns (text, cost). Azure/OpenAI only."""
    from shinka.llm.client import get_async_client_llm
    from shinka.llm.providers.model_resolver import resolve_model_backend

    provider = resolve_model_backend(model_name).provider
    if provider not in ("azure_openai", "openai"):
        raise ValueError(f"bg_query is Azure/OpenAI-only (got provider={provider!r}).")
    client, api_model_name, _ = get_async_client_llm(model_name)
    return asyncio.run(
        _bg_call(
            client, api_model_name, system_msg, user_msg,
            reasoning_effort, call_metadata, poll_interval, poll_timeout,
        )
    )

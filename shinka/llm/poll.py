"""Background + poll helpers for the OpenAI / Azure OpenAI Responses API.

Phase 2 of the research-grounding plan moves every LLM call through
``responses.create(background=True, store=True)`` followed by polling on
``responses.retrieve(id)`` until a terminal status. The benefit is
resumability: an upstream task that takes 20 min won't drop mid-response
when the HTTP socket closes, and transient connection errors during
polling don't restart the model -- we just retry the retrieve.

What this module owns:

- ``create_and_poll`` / ``create_and_poll_async`` for plain text outputs.
- ``create_and_poll_parse`` / ``create_and_poll_parse_async`` for
  structured-output calls (``responses.parse``).
- Bookkeeping around ``responses.delete(id)`` so the caller can opt out
  of Azure's 31-day retention via ``delete_after=True`` (default).

What this module does NOT own (yet):

- Tool dispatch on the ``requires_action`` status. Phase 3 introduces a
  tool loop that pauses polling, submits ``submit_tool_outputs``, and
  resumes. For Phase 2 we keep polling through ``requires_action`` --
  callers without tools will never see it, and callers with tools will
  override this helper.

Failure modes
~~~~~~~~~~~~~

- Terminal ``failed`` / ``cancelled`` / ``expired`` raise ``RuntimeError``.
- ``incomplete`` is returned as-is so callers (e.g. the DR summarizer)
  can salvage partial output instead of crashing the whole cycle.
- ``APIConnectionError`` / ``RateLimitError`` during ``retrieve`` are
  retried up to ``POLL_RETRIEVE_RETRIES`` times with the same backoff
  cadence as the poll itself; on exhaustion we raise.
- Hitting ``poll_timeout`` raises ``TimeoutError`` *without* deleting the
  server-side response (so a follow-up can re-poll if desired).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Type

import openai
from pydantic import BaseModel

from .constants import (
    POLL_INTERVAL_GROWTH,
    POLL_INTERVAL_INITIAL,
    POLL_INTERVAL_MAX,
    POLL_RETRIEVE_RETRIES,
    POLL_TIMEOUT_DEFAULT,
)

logger = logging.getLogger(__name__)

# Terminal statuses returned by the Responses API.
_TERMINAL_OK = ("completed",)
_TERMINAL_PARTIAL = ("incomplete",)
_TERMINAL_ERROR = ("failed", "cancelled", "expired")
# Status that signals "client must invoke a custom function tool and submit
# the result back via responses.submit_tool_outputs(...)".
_REQUIRES_ACTION = "requires_action"

ToolDispatcher = Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]
AsyncToolDispatcher = Callable[
    [List[Dict[str, Any]]], Awaitable[List[Dict[str, Any]]]
]

# Transient errors that should bounce off the retrieve loop instead of
# crashing the whole call. APIConnectionError covers DNS / TCP / TLS;
# RateLimitError is 429; APITimeoutError is the underlying httpx timeout.
_TRANSIENT_RETRIEVE_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
)


class PollTimeoutError(TimeoutError):
    """Raised when ``poll_timeout`` elapses before a terminal status."""

    def __init__(self, response_id: str, last_status: Optional[str]) -> None:
        super().__init__(
            f"Polling exceeded budget for response_id={response_id!r} "
            f"(last status={last_status!r})"
        )
        self.response_id = response_id
        self.last_status = last_status


class PollFailedError(RuntimeError):
    """Raised when the response reaches ``failed`` / ``cancelled`` / ``expired``."""

    def __init__(self, response: Any) -> None:
        message = (
            f"Response {getattr(response, 'id', '?')!r} ended with status "
            f"{getattr(response, 'status', '?')!r}"
        )
        error = getattr(response, "error", None)
        if error is not None:
            message = f"{message}: {error}"
        super().__init__(message)
        self.response = response


def _next_interval(current: float) -> float:
    return min(current * POLL_INTERVAL_GROWTH, POLL_INTERVAL_MAX)


def _extract_pending_tool_calls(response: Any) -> List[Dict[str, Any]]:
    """Pull ``requires_action`` function calls out of a Response.

    The Responses API surfaces pending function calls under
    ``response.required_action.submit_tool_outputs.tool_calls``. We return
    a list of ``{"call_id", "name", "arguments"}`` dicts for downstream
    dispatch. Returns ``[]`` if no action is required (so callers can keep
    polling without raising).
    """
    required = getattr(response, "required_action", None)
    if not required:
        return []
    submit = getattr(required, "submit_tool_outputs", None)
    if not submit:
        return []
    raw_calls = getattr(submit, "tool_calls", None) or []
    out: List[Dict[str, Any]] = []
    for raw in raw_calls:
        call_id = getattr(raw, "id", None) or getattr(raw, "call_id", None)
        function = getattr(raw, "function", None)
        name = getattr(function, "name", None) if function else getattr(raw, "name", None)
        arguments_raw = (
            getattr(function, "arguments", None) if function else getattr(raw, "arguments", None)
        )
        # Arguments arrive as a JSON-encoded string; decode for the dispatcher.
        try:
            arguments = json.loads(arguments_raw) if arguments_raw else {}
        except json.JSONDecodeError:
            arguments = {"_raw": arguments_raw}
        out.append(
            {"call_id": call_id, "name": name, "arguments": arguments}
        )
    return out


def _best_effort_delete_sync(client: Any, response_id: str) -> None:
    try:
        client.responses.delete(response_id)
    except Exception as exc:  # noqa: BLE001 -- cleanup must never raise
        logger.warning(
            "Best-effort delete failed for response_id=%s: %s", response_id, exc
        )


async def _best_effort_delete_async(client: Any, response_id: str) -> None:
    try:
        await client.responses.delete(response_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Best-effort delete failed for response_id=%s: %s", response_id, exc
        )


def _create_kwargs(create_kwargs: dict) -> dict:
    """Force ``background=True`` and ``store=True``; both are required for
    retrieval to work. Callers should pass ``delete_after=True`` if they
    want the response purged after retrieval (the default for privacy)."""
    forced = dict(create_kwargs)
    forced["background"] = True
    forced["store"] = True
    return forced


def _submit_with_transient_retry_sync(
    create_callable, kwargs: dict
) -> Any:
    """Retry transient errors on the initial responses.create()/parse()."""
    attempts_left = POLL_RETRIEVE_RETRIES
    interval = POLL_INTERVAL_INITIAL
    last_exc: Optional[BaseException] = None
    while attempts_left > 0:
        try:
            return create_callable(**kwargs)
        except _TRANSIENT_RETRIEVE_ERRORS as exc:
            last_exc = exc
            attempts_left -= 1
            if attempts_left <= 0:
                break
            logger.info(
                "Transient create error (%d retries left): %s",
                attempts_left,
                exc,
            )
            time.sleep(interval)
            interval = _next_interval(interval)
    assert last_exc is not None
    raise last_exc


async def _submit_with_transient_retry_async(
    create_callable, kwargs: dict
) -> Any:
    attempts_left = POLL_RETRIEVE_RETRIES
    interval = POLL_INTERVAL_INITIAL
    last_exc: Optional[BaseException] = None
    while attempts_left > 0:
        try:
            return await create_callable(**kwargs)
        except _TRANSIENT_RETRIEVE_ERRORS as exc:
            last_exc = exc
            attempts_left -= 1
            if attempts_left <= 0:
                break
            logger.info(
                "Transient create error (%d retries left): %s",
                attempts_left,
                exc,
            )
            await asyncio.sleep(interval)
            interval = _next_interval(interval)
    assert last_exc is not None
    raise last_exc


def create_and_poll(
    client: Any,
    *,
    poll_timeout: float = POLL_TIMEOUT_DEFAULT,
    delete_after: bool = True,
    tool_dispatcher: Optional[ToolDispatcher] = None,
    **create_kwargs: Any,
) -> Any:
    """Submit a ``responses.create(background=True)`` call and poll to terminal.

    On ``completed`` / ``incomplete`` returns the final Response object.
    On ``failed`` / ``cancelled`` / ``expired`` raises ``PollFailedError``.
    On polling-budget exhaustion raises ``PollTimeoutError`` and leaves
    the server-side response in place (no delete).

    When the API returns ``status="requires_action"`` (custom function tool
    pending), ``tool_dispatcher`` is invoked with the list of pending
    function-call dicts and is expected to return a matching list of
    ``{"tool_call_id", "output"}`` results which we submit via
    ``client.responses.submit_tool_outputs``. Without a dispatcher we keep
    polling -- safe for callers that pass only server-side tools.
    """
    initial = _submit_with_transient_retry_sync(
        client.responses.create, _create_kwargs(create_kwargs)
    )
    response_id = initial.id
    if initial.status in _TERMINAL_OK + _TERMINAL_PARTIAL:
        # Some short calls complete inline; skip polling.
        if delete_after:
            _best_effort_delete_sync(client, response_id)
        return initial
    if initial.status in _TERMINAL_ERROR:
        if delete_after:
            _best_effort_delete_sync(client, response_id)
        raise PollFailedError(initial)

    deadline = time.monotonic() + poll_timeout
    interval = POLL_INTERVAL_INITIAL
    last_response: Any = initial
    transient_retries_left = POLL_RETRIEVE_RETRIES

    while True:
        if time.monotonic() >= deadline:
            raise PollTimeoutError(
                response_id, getattr(last_response, "status", None)
            )
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        try:
            last_response = client.responses.retrieve(response_id)
            transient_retries_left = POLL_RETRIEVE_RETRIES  # reset on success
        except _TRANSIENT_RETRIEVE_ERRORS as exc:
            transient_retries_left -= 1
            if transient_retries_left <= 0:
                logger.error(
                    "Exhausted retrieve retries for response_id=%s: %s",
                    response_id,
                    exc,
                )
                raise
            logger.info(
                "Transient retrieve error for response_id=%s (%d retries left): %s",
                response_id,
                transient_retries_left,
                exc,
            )
            interval = _next_interval(interval)
            continue

        status = getattr(last_response, "status", None)
        if status in _TERMINAL_OK + _TERMINAL_PARTIAL:
            if delete_after:
                _best_effort_delete_sync(client, response_id)
            return last_response
        if status in _TERMINAL_ERROR:
            if delete_after:
                _best_effort_delete_sync(client, response_id)
            raise PollFailedError(last_response)
        if status == _REQUIRES_ACTION and tool_dispatcher is not None:
            pending = _extract_pending_tool_calls(last_response)
            outputs = tool_dispatcher(pending) if pending else []
            if outputs:
                client.responses.submit_tool_outputs(
                    response_id, tool_outputs=outputs
                )
            interval = POLL_INTERVAL_INITIAL  # reset after action
            continue

        interval = _next_interval(interval)


async def create_and_poll_async(
    client: Any,
    *,
    poll_timeout: float = POLL_TIMEOUT_DEFAULT,
    delete_after: bool = True,
    tool_dispatcher: Optional[AsyncToolDispatcher] = None,
    **create_kwargs: Any,
) -> Any:
    """Async mirror of :func:`create_and_poll`."""
    initial = await _submit_with_transient_retry_async(
        client.responses.create, _create_kwargs(create_kwargs)
    )
    response_id = initial.id
    if initial.status in _TERMINAL_OK + _TERMINAL_PARTIAL:
        if delete_after:
            await _best_effort_delete_async(client, response_id)
        return initial
    if initial.status in _TERMINAL_ERROR:
        if delete_after:
            await _best_effort_delete_async(client, response_id)
        raise PollFailedError(initial)

    deadline = time.monotonic() + poll_timeout
    interval = POLL_INTERVAL_INITIAL
    last_response: Any = initial
    transient_retries_left = POLL_RETRIEVE_RETRIES

    while True:
        if time.monotonic() >= deadline:
            raise PollTimeoutError(
                response_id, getattr(last_response, "status", None)
            )
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        try:
            last_response = await client.responses.retrieve(response_id)
            transient_retries_left = POLL_RETRIEVE_RETRIES
        except _TRANSIENT_RETRIEVE_ERRORS as exc:
            transient_retries_left -= 1
            if transient_retries_left <= 0:
                logger.error(
                    "Exhausted retrieve retries for response_id=%s: %s",
                    response_id,
                    exc,
                )
                raise
            logger.info(
                "Transient retrieve error for response_id=%s (%d retries left): %s",
                response_id,
                transient_retries_left,
                exc,
            )
            interval = _next_interval(interval)
            continue

        status = getattr(last_response, "status", None)
        if status in _TERMINAL_OK + _TERMINAL_PARTIAL:
            if delete_after:
                await _best_effort_delete_async(client, response_id)
            return last_response
        if status in _TERMINAL_ERROR:
            if delete_after:
                await _best_effort_delete_async(client, response_id)
            raise PollFailedError(last_response)
        if status == _REQUIRES_ACTION and tool_dispatcher is not None:
            pending = _extract_pending_tool_calls(last_response)
            outputs = await tool_dispatcher(pending) if pending else []
            if outputs:
                await client.responses.submit_tool_outputs(
                    response_id, tool_outputs=outputs
                )
            interval = POLL_INTERVAL_INITIAL
            continue

        interval = _next_interval(interval)


def create_and_poll_parse(
    client: Any,
    *,
    text_format: Type[BaseModel],
    poll_timeout: float = POLL_TIMEOUT_DEFAULT,
    delete_after: bool = True,
    **create_kwargs: Any,
) -> Any:
    """``responses.parse`` variant of :func:`create_and_poll`.

    Uses ``responses.parse(background=True, ...)`` for the initial submit
    so structured outputs go through the same bg+poll machinery.
    """
    initial = _submit_with_transient_retry_sync(
        client.responses.parse,
        {"text_format": text_format, **_create_kwargs(create_kwargs)},
    )
    response_id = initial.id
    if initial.status in _TERMINAL_OK + _TERMINAL_PARTIAL:
        if delete_after:
            _best_effort_delete_sync(client, response_id)
        return initial
    if initial.status in _TERMINAL_ERROR:
        if delete_after:
            _best_effort_delete_sync(client, response_id)
        raise PollFailedError(initial)

    deadline = time.monotonic() + poll_timeout
    interval = POLL_INTERVAL_INITIAL
    last_response: Any = initial
    transient_retries_left = POLL_RETRIEVE_RETRIES

    while True:
        if time.monotonic() >= deadline:
            raise PollTimeoutError(
                response_id, getattr(last_response, "status", None)
            )
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        try:
            last_response = client.responses.retrieve(response_id)
            transient_retries_left = POLL_RETRIEVE_RETRIES
        except _TRANSIENT_RETRIEVE_ERRORS as exc:
            transient_retries_left -= 1
            if transient_retries_left <= 0:
                raise
            logger.info(
                "Transient retrieve error for response_id=%s: %s",
                response_id,
                exc,
            )
            interval = _next_interval(interval)
            continue

        status = getattr(last_response, "status", None)
        if status in _TERMINAL_OK + _TERMINAL_PARTIAL:
            if delete_after:
                _best_effort_delete_sync(client, response_id)
            return last_response
        if status in _TERMINAL_ERROR:
            if delete_after:
                _best_effort_delete_sync(client, response_id)
            raise PollFailedError(last_response)

        interval = _next_interval(interval)


async def create_and_poll_parse_async(
    client: Any,
    *,
    text_format: Type[BaseModel],
    poll_timeout: float = POLL_TIMEOUT_DEFAULT,
    delete_after: bool = True,
    **create_kwargs: Any,
) -> Any:
    """Async mirror of :func:`create_and_poll_parse`."""
    initial = await _submit_with_transient_retry_async(
        client.responses.parse,
        {"text_format": text_format, **_create_kwargs(create_kwargs)},
    )
    response_id = initial.id
    if initial.status in _TERMINAL_OK + _TERMINAL_PARTIAL:
        if delete_after:
            await _best_effort_delete_async(client, response_id)
        return initial
    if initial.status in _TERMINAL_ERROR:
        if delete_after:
            await _best_effort_delete_async(client, response_id)
        raise PollFailedError(initial)

    deadline = time.monotonic() + poll_timeout
    interval = POLL_INTERVAL_INITIAL
    last_response: Any = initial
    transient_retries_left = POLL_RETRIEVE_RETRIES

    while True:
        if time.monotonic() >= deadline:
            raise PollTimeoutError(
                response_id, getattr(last_response, "status", None)
            )
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        try:
            last_response = await client.responses.retrieve(response_id)
            transient_retries_left = POLL_RETRIEVE_RETRIES
        except _TRANSIENT_RETRIEVE_ERRORS as exc:
            transient_retries_left -= 1
            if transient_retries_left <= 0:
                raise
            logger.info(
                "Transient retrieve error for response_id=%s: %s",
                response_id,
                exc,
            )
            interval = _next_interval(interval)
            continue

        status = getattr(last_response, "status", None)
        if status in _TERMINAL_OK + _TERMINAL_PARTIAL:
            if delete_after:
                await _best_effort_delete_async(client, response_id)
            return last_response
        if status in _TERMINAL_ERROR:
            if delete_after:
                await _best_effort_delete_async(client, response_id)
            raise PollFailedError(last_response)

        interval = _next_interval(interval)


__all__ = [
    "PollFailedError",
    "PollTimeoutError",
    "create_and_poll",
    "create_and_poll_async",
    "create_and_poll_parse",
    "create_and_poll_parse_async",
]

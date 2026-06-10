"""Background-mode subclass of ``OpenAIResponsesModel``.

The upstream ``agents.models.openai_responses.OpenAIResponsesModel`` calls
``client.responses.create(**create_kwargs)`` and returns whatever comes
back. When ``background=True`` is passed through, the API returns a
``Response`` object with ``status="queued"`` rather than a completed
response — the SDK has no native polling, so it would treat the queued
object as a final answer and the agent Runner would exit with empty
output.

This subclass overrides ``_fetch_response`` for the non-streaming path:

1. Inject ``background=True`` into the create kwargs.
2. Submit; receive a queued/in-progress ``Response``.
3. Poll ``client.responses.retrieve(id)`` until the response reaches a
   terminal state (``completed`` / ``failed`` / ``incomplete`` /
   ``cancelled``).
4. Return the terminal ``Response``.

Streaming requests fall back to the parent implementation (which does
SSE-based delivery and is liveness-observable on its own). The two
modes can in principle compose (``background=True`` + ``stream=True``)
but we defer that combination until we have evidence we need it.

Why background mode at all: long reasoning calls on Azure can sit idle
on a TCP connection for tens of minutes. Azure LB / NAT can silently
kill the idle socket; the client's httpx pool hands out the dead
socket on the next request, producing the multi-hour silent hang
documented in commit ``fd018d8``. Background mode decouples client
connection lifetime from inference duration: each poll is a sub-second
status check, and connection death cannot strand work in progress on
the server side.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Literal, cast

from agents.models.openai_responses import OpenAIResponsesModel

from ..constants import PER_REQUEST_TIMEOUT

logger = logging.getLogger(__name__)


# Terminal statuses for a background ``Response``. Anything outside this
# set means we should keep polling.
_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "incomplete",
        "cancelled",
        "expired",
    }
)

# Statuses we expect to see while a background job is still working.
_PENDING_STATUSES = frozenset({"queued", "in_progress"})

DEFAULT_POLL_INTERVAL_SEC = 2.0
DEFAULT_POLL_TIMEOUT_SEC = 3600.0  # 1 hour; matches our outer LLM timeout.
DEFAULT_MAX_QUEUED_WAIT_SEC = 300.0  # If "queued" for > 5 min, abort.


class BackgroundPollTimeout(TimeoutError):
    """Raised when a background response did not reach a terminal status.

    The caller is expected to escalate to its outer retry layer; do not
    swallow this exception, since it signals that the work was almost
    certainly wasted and a fresh attempt is preferable to waiting longer.
    """

    def __init__(self, response_id: str, last_status: str, elapsed_sec: float):
        super().__init__(
            f"background response {response_id} did not finish: "
            f"last status={last_status!r} after {elapsed_sec:.1f}s"
        )
        self.response_id = response_id
        self.last_status = last_status
        self.elapsed_sec = elapsed_sec


class BackgroundOpenAIResponsesModel(OpenAIResponsesModel):
    """Submit Responses-API calls with ``background=True`` and poll.

    Polling parameters
    ------------------
    poll_interval_sec
        Seconds to sleep between successive ``retrieve`` calls. Default
        ``2.0`` — keeps polling overhead negligible while staying within
        an order of magnitude of model output cadence.
    poll_timeout_sec
        Hard wall-clock cap for a single ``_fetch_response`` call.
        Defaults to ``3600`` (1 hour) to match our existing LLM
        timeout. On timeout we attempt a best-effort ``cancel`` and
        raise ``BackgroundPollTimeout``.
    max_queued_wait_sec
        If a response stays in ``queued`` (never moves to
        ``in_progress``) longer than this, abort. Defaults to ``300``
        (5 minutes). Mitigates the community-reported "stuck in queued
        forever" failure mode.
    """

    def __init__(
        self,
        model: Any,
        openai_client: Any,
        *,
        model_is_explicit: bool = True,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        poll_timeout_sec: float = DEFAULT_POLL_TIMEOUT_SEC,
        max_queued_wait_sec: float = DEFAULT_MAX_QUEUED_WAIT_SEC,
        per_request_timeout_sec: float = PER_REQUEST_TIMEOUT,
    ) -> None:
        super().__init__(
            model=model,
            openai_client=openai_client,
            model_is_explicit=model_is_explicit,
        )
        self.poll_interval_sec = poll_interval_sec
        self.poll_timeout_sec = poll_timeout_sec
        self.max_queued_wait_sec = max_queued_wait_sec
        # SHORT per-HTTP-request cap: a hung status GET is abandoned + retried, so it can't
        # ride the whole poll_timeout_sec wall (see shinka.llm.constants.PER_REQUEST_TIMEOUT).
        self.per_request_timeout_sec = per_request_timeout_sec

    async def _fetch_response(  # type: ignore[override]
        self,
        system_instructions: str | None,
        input: Any,
        model_settings: Any,
        tools: list[Any],
        output_schema: Any,
        handoffs: list[Any],
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        stream: Literal[True] | Literal[False] = False,
        prompt: Any = None,
    ) -> Any:
        if stream:
            # Streaming path is liveness-observable via SSE event cadence
            # and the parent already handles it correctly. We don't add
            # background-mode polling on top — composing them is feasible
            # but not yet justified by need.
            return await super()._fetch_response(
                system_instructions=system_instructions,
                input=input,
                model_settings=model_settings,
                tools=tools,
                output_schema=output_schema,
                handoffs=handoffs,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                stream=stream,
                prompt=prompt,
            )

        create_kwargs = self._build_response_create_kwargs(
            system_instructions=system_instructions,
            input=input,
            model_settings=model_settings,
            tools=tools,
            output_schema=output_schema,
            handoffs=handoffs,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            stream=False,
            prompt=prompt,
        )
        create_kwargs["background"] = True

        client = self._get_client()
        response = await asyncio.wait_for(
            client.responses.create(**create_kwargs), timeout=self.per_request_timeout_sec
        )
        response_id = getattr(response, "id", None)
        status = getattr(response, "status", None)

        if status in _TERMINAL_STATUSES:
            return cast(Any, response)

        if response_id is None:
            # If we got something un-pollable back, surface it rather
            # than spinning. The parent's existing error pathways will
            # handle ``status`` values they don't recognize.
            return cast(Any, response)

        start_time = time.monotonic()
        queued_since = start_time if status == "queued" else None

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > self.poll_timeout_sec:
                await self._best_effort_cancel(client, response_id)
                raise BackgroundPollTimeout(
                    response_id=response_id,
                    last_status=status or "unknown",
                    elapsed_sec=elapsed,
                )
            if (
                queued_since is not None
                and (time.monotonic() - queued_since) > self.max_queued_wait_sec
            ):
                await self._best_effort_cancel(client, response_id)
                raise BackgroundPollTimeout(
                    response_id=response_id,
                    last_status="queued",
                    elapsed_sec=time.monotonic() - queued_since,
                )

            await asyncio.sleep(self.poll_interval_sec)
            try:
                response = await asyncio.wait_for(
                    client.responses.retrieve(response_id),
                    timeout=self.per_request_timeout_sec,
                )
            except asyncio.TimeoutError:
                # A hung status GET is abandoned and RETRIED — never let one wedged request
                # ride the whole poll_timeout_sec wall (the top-of-loop deadline check still
                # bounds the TOTAL job time and triggers best-effort cancel when it expires).
                logger.warning(
                    "background retrieve(%s) exceeded the %.0fs per-request cap — retrying",
                    response_id, self.per_request_timeout_sec,
                )
                continue
            status = getattr(response, "status", None)

            if status in _TERMINAL_STATUSES:
                return cast(Any, response)
            if status == "in_progress":
                # Reset the queued-wait clock once we leave the queue.
                queued_since = None
            elif status not in _PENDING_STATUSES:
                # Unknown status; let the parent error handling deal
                # with it rather than poll forever.
                logger.warning(
                    "background response %s returned unexpected status %r",
                    response_id,
                    status,
                )
                return cast(Any, response)

    async def _best_effort_cancel(self, client: Any, response_id: str) -> None:
        """Cancel a background response on timeout, swallowing errors.

        Cancellation is courtesy — even if it fails (e.g., the response
        has already terminated, the API is unreachable, the SDK version
        doesn't expose ``cancel``), we still raise the timeout to the
        outer retry layer. Any work the server might still do after a
        failed cancel is wasted but bounded by the server's own caps.
        """
        cancel = getattr(getattr(client, "responses", None), "cancel", None)
        if not callable(cancel):
            return
        try:
            await cancel(response_id)
        except Exception as exc:
            logger.info(
                "best-effort cancel of background response %s failed: %s",
                response_id,
                exc,
            )

"""``RobustRunner`` — outer retry loop around ``agents.Runner.run``.

The agents SDK's ``OpenAIResponsesModel._get_client`` reuses one
``AsyncOpenAI`` instance for the lifetime of the model object. This is
the same anti-pattern we patched out of upstream shinka in commit
``72f8dc2``: when a connection in the underlying httpx pool gets killed
by an Azure load balancer / NAT timeout, retries against the same
client hit the same poisoned pool, producing the multi-hour silent
hang.

``RobustRunner`` solves this by accepting an *agent factory* and
constructing a fresh ``Agent`` (which builds a fresh
``BackgroundOpenAIResponsesModel``, which builds a fresh
``AsyncOpenAI``) per retry attempt. The old agent's client is
best-effort closed via ``aclose()`` between attempts so the dead pool
is discarded promptly.

Only a curated set of transport-shaped exceptions trigger a retry —
non-transport errors (bad input, auth failures, validation errors)
re-raise immediately, since retry would just repeat the failure.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from .background_model import BackgroundPollTimeout

logger = logging.getLogger(__name__)


# Default exception types that justify a fresh-client retry. We do
# NOT include things like ``ValueError`` or ``TypeError`` since those
# indicate bad input that won't be fixed by a new client.
def _default_retry_exceptions() -> tuple[type[BaseException], ...]:
    """Compute retry-exception tuple at call time so optional imports
    don't make module import fail."""
    excs: list[type[BaseException]] = [BackgroundPollTimeout]
    try:
        import openai

        excs.extend(
            [
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.RateLimitError,
                openai.APIStatusError,
            ]
        )
    except ImportError:
        pass
    try:
        import httpx

        excs.extend(
            [
                httpx.ConnectError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.PoolTimeout,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
            ]
        )
    except ImportError:
        pass
    return tuple(excs)


DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_INITIAL_SEC = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0


# Type alias for the agent factory: a zero-arg callable that returns
# either an ``Agent`` synchronously or an ``Awaitable[Agent]``.
AgentFactory = Callable[[], "Any | Awaitable[Any]"]


class RobustRunner:
    """Run an agent with fresh-client retry semantics.

    Parameters
    ----------
    agent_factory
        Zero-arg callable that returns a fresh ``Agent``. Called once
        per attempt; the previous agent's client is closed before a
        new one is constructed. May be sync or async.
    max_attempts
        Total attempts including the first try. Default ``3``.
    backoff_initial_sec
        Initial sleep before the second attempt. Doubles with each
        further failure (``backoff_initial_sec * backoff_factor ** attempt``).
    backoff_factor
        Multiplier between attempts. Default ``2.0``.
    retry_on
        Exception classes that trigger a retry. Default covers
        OpenAI / httpx transport errors and ``BackgroundPollTimeout``.

    Usage
    -----
    >>> def make_agent():
    ...     client = AsyncAzureOpenAI(...)
    ...     model = BackgroundOpenAIResponsesModel(
    ...         model="gpt-5.4-pro", openai_client=client
    ...     )
    ...     return Agent(name="shinka", instructions="...", model=model)
    >>> runner = RobustRunner(make_agent)
    >>> result = await runner.run("propose a patch", max_turns=15)
    """

    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_initial_sec: float = DEFAULT_BACKOFF_INITIAL_SEC,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        retry_on: tuple[type[BaseException], ...] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._agent_factory = agent_factory
        self._max_attempts = max_attempts
        self._backoff_initial_sec = backoff_initial_sec
        self._backoff_factor = backoff_factor
        self._retry_on = retry_on or _default_retry_exceptions()

    async def run(self, input: Any, **runner_kwargs: Any) -> Any:
        """Invoke ``agents.Runner.run`` with retry-on-transport-error.

        ``input`` and ``**runner_kwargs`` pass through to
        ``Runner.run``: ``context``, ``max_turns``, ``hooks``,
        ``run_config``, ``error_handlers``, ``previous_response_id``,
        ``auto_previous_response_id``, ``conversation_id``, ``session``.
        """
        # Local import keeps the agents SDK out of import-time for
        # callers that only need the type definitions.
        from agents import Runner

        last_exc: BaseException | None = None
        for attempt in range(self._max_attempts):
            agent = await self._make_agent()
            try:
                return await Runner.run(agent, input, **runner_kwargs)
            except self._retry_on as exc:
                last_exc = exc
                logger.info(
                    "RobustRunner attempt %d/%d failed with %s: %s",
                    attempt + 1,
                    self._max_attempts,
                    type(exc).__name__,
                    exc,
                )
                await self._dispose_agent(agent)
                if attempt < self._max_attempts - 1:
                    sleep_sec = self._backoff_initial_sec * (
                        self._backoff_factor**attempt
                    )
                    await asyncio.sleep(sleep_sec)
            except BaseException:
                # Non-retryable. Still clean up the client, then bubble.
                await self._dispose_agent(agent)
                raise

        # Exhausted attempts.
        assert last_exc is not None
        raise last_exc

    async def _make_agent(self) -> Any:
        """Resolve the factory result, accepting sync or async callables."""
        result = self._agent_factory()
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _dispose_agent(self, agent: Any) -> None:
        """Best-effort close the AsyncOpenAI client attached to the agent's model.

        We dig through ``agent.model._client`` because the agents SDK
        stores the client there (see ``OpenAIResponsesModel.__init__``).
        Any failure is logged and swallowed; the agent will be
        garbage-collected regardless, and the next attempt builds a
        fresh one.
        """
        model = getattr(agent, "model", None)
        client = getattr(model, "_client", None) if model is not None else None
        if client is None:
            return
        aclose = getattr(client, "aclose", None) or getattr(client, "close", None)
        if not callable(aclose):
            return
        try:
            result = aclose()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.info(
                "RobustRunner failed to dispose AsyncOpenAI client (ignored): %s",
                exc,
            )

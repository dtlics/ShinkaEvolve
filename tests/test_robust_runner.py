"""Unit tests for ``RobustRunner``.

Focus areas:
- Fresh agent created per attempt (verify factory called multiple times
  on retry).
- Retryable exceptions trigger fresh-client retry; non-retryable
  re-raise immediately.
- Old agent's client gets disposed (``aclose`` awaited) between
  attempts.
- Final result returned cleanly when an attempt succeeds.
- Last attempt's exception re-raised after exhausting ``max_attempts``.

We monkeypatch ``agents.Runner.run`` so the tests don't exercise the
real loop.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shinka.llm.agent.background_model import BackgroundPollTimeout
from shinka.llm.agent.robust_runner import RobustRunner


def _make_fake_agent(name: str = "test-agent") -> MagicMock:
    """Build a fake Agent with a model.client.aclose AsyncMock so we
    can assert disposal."""
    agent = MagicMock(name=name)
    agent.model = MagicMock()
    agent.model._client = MagicMock()
    agent.model._client.aclose = AsyncMock(return_value=None)
    return agent


def _patch_runner(monkeypatch: pytest.MonkeyPatch, run_mock: AsyncMock) -> None:
    """Replace ``agents.Runner.run`` with a tracking AsyncMock."""
    import agents

    monkeypatch.setattr(agents.Runner, "run", run_mock)


def test_success_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: factory called once, Runner.run succeeds, result returned."""
    agent = _make_fake_agent()
    factory = MagicMock(return_value=agent)
    sentinel_result = MagicMock(name="RunResult")
    run_mock = AsyncMock(return_value=sentinel_result)
    _patch_runner(monkeypatch, run_mock)

    runner = RobustRunner(factory, max_attempts=3, backoff_initial_sec=0.001)
    result = asyncio.run(runner.run("hello", max_turns=5))

    assert result is sentinel_result
    assert factory.call_count == 1
    run_mock.assert_awaited_once_with(agent, "hello", max_turns=5)
    # Successful path doesn't dispose the agent — the caller owns the
    # result, which holds references to agent state.
    agent.model._client.aclose.assert_not_awaited()


def test_retries_on_retryable_exception_with_fresh_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First attempt raises a retryable exception; second attempt
    succeeds. Factory must be called twice; first agent's client
    aclose must have been awaited."""
    agent1 = _make_fake_agent("agent-1")
    agent2 = _make_fake_agent("agent-2")
    factory = MagicMock(side_effect=[agent1, agent2])
    sentinel_result = MagicMock(name="RunResult")

    exc = BackgroundPollTimeout(
        response_id="resp_1", last_status="queued", elapsed_sec=5.0
    )
    run_mock = AsyncMock(side_effect=[exc, sentinel_result])
    _patch_runner(monkeypatch, run_mock)

    runner = RobustRunner(factory, max_attempts=3, backoff_initial_sec=0.001)
    result = asyncio.run(runner.run("hello"))

    assert result is sentinel_result
    assert factory.call_count == 2
    assert run_mock.await_count == 2
    # First agent's client should have been disposed.
    agent1.model._client.aclose.assert_awaited_once()
    # Second agent's client must not be disposed (it produced the result).
    agent2.model._client.aclose.assert_not_awaited()


def test_non_retryable_exception_propagates_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-retryable exception (e.g., ValueError) must NOT trigger
    retry; it should propagate after disposing the client."""
    agent = _make_fake_agent()
    factory = MagicMock(return_value=agent)

    run_mock = AsyncMock(side_effect=ValueError("bad input"))
    _patch_runner(monkeypatch, run_mock)

    runner = RobustRunner(factory, max_attempts=5, backoff_initial_sec=0.001)
    with pytest.raises(ValueError, match="bad input"):
        asyncio.run(runner.run("hello"))

    # Only one attempt.
    assert factory.call_count == 1
    # Client should still have been disposed before bubbling.
    agent.model._client.aclose.assert_awaited_once()


def test_exhausts_attempts_then_raises_last_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every attempt raises a retryable, raise the *last* exception
    after exhausting max_attempts."""
    agents_list = [_make_fake_agent(f"agent-{i}") for i in range(3)]
    factory = MagicMock(side_effect=agents_list)

    excs = [
        BackgroundPollTimeout(response_id="r1", last_status="queued", elapsed_sec=1.0),
        BackgroundPollTimeout(response_id="r2", last_status="queued", elapsed_sec=2.0),
        BackgroundPollTimeout(response_id="r3", last_status="queued", elapsed_sec=3.0),
    ]
    run_mock = AsyncMock(side_effect=excs)
    _patch_runner(monkeypatch, run_mock)

    runner = RobustRunner(factory, max_attempts=3, backoff_initial_sec=0.001)
    with pytest.raises(BackgroundPollTimeout) as exc_info:
        asyncio.run(runner.run("hello"))

    # Last exception is the one that surfaces.
    assert exc_info.value.response_id == "r3"
    assert factory.call_count == 3
    assert run_mock.await_count == 3
    # All three agents' clients should have been disposed.
    for agent in agents_list:
        agent.model._client.aclose.assert_awaited_once()


def test_backoff_grows_exponentially(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff sleep should grow: initial, initial*factor, initial*factor**2..."""
    agents_list = [_make_fake_agent(f"agent-{i}") for i in range(3)]
    factory = MagicMock(side_effect=agents_list)

    excs = [
        BackgroundPollTimeout(response_id="r1", last_status="queued", elapsed_sec=1.0),
        BackgroundPollTimeout(response_id="r2", last_status="queued", elapsed_sec=2.0),
    ]
    sentinel = MagicMock()
    run_mock = AsyncMock(side_effect=[*excs, sentinel])
    _patch_runner(monkeypatch, run_mock)

    sleep_calls: list[float] = []

    async def _capture_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _capture_sleep)

    runner = RobustRunner(
        factory,
        max_attempts=3,
        backoff_initial_sec=0.5,
        backoff_factor=3.0,
    )
    asyncio.run(runner.run("hello"))

    # Expect sleeps after attempts 0 and 1 (no sleep after attempt 2
    # because it succeeded). Values: 0.5 * 3^0 = 0.5, 0.5 * 3^1 = 1.5.
    assert sleep_calls == pytest.approx([0.5, 1.5])


def test_async_agent_factory_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """An async factory should also work — the runner awaits the coro."""
    agent = _make_fake_agent()

    async def factory() -> Any:
        return agent

    sentinel = MagicMock()
    run_mock = AsyncMock(return_value=sentinel)
    _patch_runner(monkeypatch, run_mock)

    runner = RobustRunner(factory, max_attempts=2, backoff_initial_sec=0.001)
    result = asyncio.run(runner.run("hello"))

    assert result is sentinel
    run_mock.assert_awaited_once_with(agent, "hello")


def test_dispose_swallows_aclose_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If aclose raises, we don't let it mask the original exception."""
    agent1 = _make_fake_agent("agent-1")
    agent1.model._client.aclose = AsyncMock(side_effect=RuntimeError("aclose busted"))
    agent2 = _make_fake_agent("agent-2")
    factory = MagicMock(side_effect=[agent1, agent2])

    excs = [
        BackgroundPollTimeout(response_id="r1", last_status="queued", elapsed_sec=1.0),
    ]
    sentinel = MagicMock()
    run_mock = AsyncMock(side_effect=[*excs, sentinel])
    _patch_runner(monkeypatch, run_mock)

    runner = RobustRunner(factory, max_attempts=2, backoff_initial_sec=0.001)
    # Should still succeed on retry despite the broken aclose.
    result = asyncio.run(runner.run("hello"))
    assert result is sentinel


def test_missing_client_does_not_crash_dispose(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the agent's model has no _client (mocked-out provider),
    disposal should be a no-op rather than crash."""
    agent = MagicMock()
    agent.model = None
    factory = MagicMock(return_value=agent)

    excs = [
        BackgroundPollTimeout(response_id="r1", last_status="queued", elapsed_sec=1.0),
    ]
    sentinel = MagicMock()
    run_mock = AsyncMock(side_effect=[*excs, sentinel])
    _patch_runner(monkeypatch, run_mock)

    runner = RobustRunner(factory, max_attempts=2, backoff_initial_sec=0.001)
    result = asyncio.run(runner.run("hello"))
    assert result is sentinel


def test_max_attempts_validation() -> None:
    """max_attempts must be >= 1."""
    with pytest.raises(ValueError, match="max_attempts"):
        RobustRunner(lambda: None, max_attempts=0)

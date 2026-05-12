"""Tests for the bg+poll helpers introduced in Phase 2 of research-grounding.

The Responses API is stubbed with a tiny fake client that records the
sequence of ``create`` / ``retrieve`` / ``delete`` calls. We verify:

- ``background=True`` and ``store=True`` are forced on the initial ``create``.
- The retrieve loop runs until a terminal status is observed.
- ``delete_after`` controls whether the response is purged.
- Transient errors during retrieve are retried up to ``POLL_RETRIEVE_RETRIES``.
- ``failed`` raises ``PollFailedError``; ``incomplete`` returns the response.
- Wall-clock budgets raise ``PollTimeoutError``.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Callable, List

import openai
import pytest

from shinka.llm.poll import (
    PollFailedError,
    PollTimeoutError,
    create_and_poll,
    create_and_poll_async,
)


def _fake_response(
    response_id: str = "resp_test",
    status: str = "queued",
    output: Any = None,
    error: Any = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id, status=status, output=output, error=error
    )


class FakeResponses:
    """Records create/retrieve/delete calls for assertions."""

    def __init__(self, statuses: List[str], *, fail_retrieve_times: int = 0):
        # ``statuses`` is consumed left-to-right: the first entry is the
        # response status from ``create``; subsequent entries are statuses
        # from ``retrieve`` calls.
        self._statuses = list(statuses)
        self._fail_retrieve_times = fail_retrieve_times
        self.create_calls: List[dict] = []
        self.retrieve_calls: List[str] = []
        self.delete_calls: List[str] = []

    def _next_status(self) -> str:
        if not self._statuses:
            raise AssertionError("FakeResponses ran out of canned statuses")
        return self._statuses.pop(0)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return _fake_response(status=self._next_status())

    def retrieve(self, response_id: str):
        self.retrieve_calls.append(response_id)
        if self._fail_retrieve_times > 0:
            self._fail_retrieve_times -= 1
            raise openai.APIConnectionError(request=None)
        return _fake_response(response_id=response_id, status=self._next_status())

    def delete(self, response_id: str):
        self.delete_calls.append(response_id)


class FakeClient:
    def __init__(self, responses: FakeResponses):
        self.responses = responses


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    """Replace the global ``time.sleep`` and async sleep with no-ops; record
    the requested durations so timing assertions are possible."""
    durations: List[float] = []

    def _record(seconds: float) -> None:
        durations.append(seconds)

    monkeypatch.setattr(time, "sleep", _record)

    async def _async_record(seconds: float) -> None:
        durations.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _async_record)
    return durations


def test_create_and_poll_forces_background_and_store(monkeypatch):
    _patch_sleep(monkeypatch)
    fake = FakeResponses(statuses=["queued", "completed"])
    client = FakeClient(fake)

    result = create_and_poll(client, model="gpt-5", input=[{"role": "user"}])

    assert result.status == "completed"
    create_kwargs = fake.create_calls[0]
    assert create_kwargs["background"] is True
    assert create_kwargs["store"] is True
    assert create_kwargs["model"] == "gpt-5"
    assert fake.delete_calls == ["resp_test"]


def test_create_and_poll_returns_immediately_on_inline_completion(monkeypatch):
    durations = _patch_sleep(monkeypatch)
    fake = FakeResponses(statuses=["completed"])
    client = FakeClient(fake)

    result = create_and_poll(client, model="gpt-5", input=[])

    assert result.status == "completed"
    assert fake.retrieve_calls == []  # no polling needed
    assert durations == []
    assert fake.delete_calls == ["resp_test"]


def test_create_and_poll_polls_through_in_progress(monkeypatch):
    durations = _patch_sleep(monkeypatch)
    fake = FakeResponses(
        statuses=["queued", "in_progress", "in_progress", "completed"]
    )
    client = FakeClient(fake)

    result = create_and_poll(client, model="gpt-5", input=[])

    assert result.status == "completed"
    assert len(fake.retrieve_calls) == 3
    # Three poll waits, each non-negative; second/third should be >= first
    # (exponential growth).
    assert len(durations) == 3
    assert durations[1] >= durations[0]
    assert durations[2] >= durations[1]


def test_create_and_poll_skips_delete_when_disabled(monkeypatch):
    _patch_sleep(monkeypatch)
    fake = FakeResponses(statuses=["queued", "completed"])
    client = FakeClient(fake)

    create_and_poll(client, delete_after=False, model="gpt-5", input=[])

    assert fake.delete_calls == []


def test_create_and_poll_raises_on_failed_status(monkeypatch):
    _patch_sleep(monkeypatch)
    fake = FakeResponses(statuses=["queued", "failed"])
    client = FakeClient(fake)

    with pytest.raises(PollFailedError):
        create_and_poll(client, model="gpt-5", input=[])
    # Cleanup still attempted
    assert fake.delete_calls == ["resp_test"]


def test_create_and_poll_returns_incomplete_response(monkeypatch):
    _patch_sleep(monkeypatch)
    fake = FakeResponses(statuses=["queued", "incomplete"])
    client = FakeClient(fake)

    result = create_and_poll(client, model="gpt-5", input=[])

    assert result.status == "incomplete"


def test_create_and_poll_retries_transient_retrieve_errors(monkeypatch):
    _patch_sleep(monkeypatch)
    # 2 statuses get consumed: 1 from create (queued), 1 from successful
    # retrieve (completed). The 2 simulated APIConnectionErrors don't consume
    # any statuses.
    fake = FakeResponses(
        statuses=["queued", "completed"],
        fail_retrieve_times=2,
    )
    client = FakeClient(fake)

    result = create_and_poll(client, model="gpt-5", input=[])

    assert result.status == "completed"
    # 2 failed + 1 successful retrieve
    assert len(fake.retrieve_calls) == 3


def test_create_and_poll_raises_when_retrieve_retries_exhausted(monkeypatch):
    _patch_sleep(monkeypatch)
    fake = FakeResponses(
        statuses=["queued"],
        fail_retrieve_times=99,  # never recovers
    )
    client = FakeClient(fake)

    with pytest.raises(openai.APIConnectionError):
        create_and_poll(client, model="gpt-5", input=[])


def test_create_and_poll_raises_on_timeout(monkeypatch):
    # Use a real-clock-ish timeout by mocking monotonic to advance past the
    # deadline on the second check.
    clock = [0.0]
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        "shinka.llm.poll.time.monotonic", lambda: clock[0]
    )

    def advance_sleep(seconds: float) -> None:
        clock[0] += 100.0  # blow past any deadline

    monkeypatch.setattr(time, "sleep", advance_sleep)

    fake = FakeResponses(statuses=["queued", "in_progress", "in_progress"])
    client = FakeClient(fake)

    with pytest.raises(PollTimeoutError):
        create_and_poll(client, poll_timeout=1.0, model="gpt-5", input=[])

    # Server-side response should NOT be deleted on timeout so a re-poll
    # is possible later.
    assert fake.delete_calls == []
    # restore for cleanliness
    monkeypatch.setattr("shinka.llm.poll.time.monotonic", real_monotonic)


# --- async mirror ---


class FakeAsyncResponses:
    def __init__(self, statuses: List[str], *, fail_retrieve_times: int = 0):
        self._statuses = list(statuses)
        self._fail_retrieve_times = fail_retrieve_times
        self.create_calls: List[dict] = []
        self.retrieve_calls: List[str] = []
        self.delete_calls: List[str] = []

    def _next_status(self) -> str:
        if not self._statuses:
            raise AssertionError("FakeAsyncResponses ran out of canned statuses")
        return self._statuses.pop(0)

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return _fake_response(status=self._next_status())

    async def retrieve(self, response_id: str):
        self.retrieve_calls.append(response_id)
        if self._fail_retrieve_times > 0:
            self._fail_retrieve_times -= 1
            raise openai.APIConnectionError(request=None)
        return _fake_response(response_id=response_id, status=self._next_status())

    async def delete(self, response_id: str):
        self.delete_calls.append(response_id)


class FakeAsyncClient:
    def __init__(self, responses: FakeAsyncResponses):
        self.responses = responses


def test_create_and_poll_async_completes(monkeypatch):
    _patch_sleep(monkeypatch)
    fake = FakeAsyncResponses(statuses=["queued", "in_progress", "completed"])
    client = FakeAsyncClient(fake)

    result = asyncio.run(create_and_poll_async(client, model="gpt-5", input=[]))

    assert result.status == "completed"
    assert len(fake.retrieve_calls) == 2
    create_kwargs = fake.create_calls[0]
    assert create_kwargs["background"] is True
    assert create_kwargs["store"] is True


def test_create_and_poll_async_retries_transient(monkeypatch):
    _patch_sleep(monkeypatch)
    fake = FakeAsyncResponses(
        statuses=["queued", "completed"], fail_retrieve_times=2
    )
    client = FakeAsyncClient(fake)

    result = asyncio.run(create_and_poll_async(client, model="gpt-5", input=[]))

    assert result.status == "completed"
    assert len(fake.retrieve_calls) == 3


def test_create_and_poll_async_raises_on_failed(monkeypatch):
    _patch_sleep(monkeypatch)
    fake = FakeAsyncResponses(statuses=["queued", "failed"])
    client = FakeAsyncClient(fake)

    with pytest.raises(PollFailedError):
        asyncio.run(create_and_poll_async(client, model="gpt-5", input=[]))


def test_create_and_poll_async_returns_incomplete(monkeypatch):
    _patch_sleep(monkeypatch)
    fake = FakeAsyncResponses(statuses=["queued", "incomplete"])
    client = FakeAsyncClient(fake)

    result = asyncio.run(create_and_poll_async(client, model="gpt-5", input=[]))

    assert result.status == "incomplete"


def test_create_and_poll_async_concurrent_calls(monkeypatch):
    _patch_sleep(monkeypatch)

    async def _drive() -> List[str]:
        # Each fake client maintains its own state; this proves the helper
        # is safe to invoke from multiple in-flight tasks.
        a = FakeAsyncResponses(statuses=["queued", "completed"])
        b = FakeAsyncResponses(statuses=["queued", "in_progress", "completed"])
        results = await asyncio.gather(
            create_and_poll_async(FakeAsyncClient(a), model="m", input=[]),
            create_and_poll_async(FakeAsyncClient(b), model="m", input=[]),
        )
        return [r.status for r in results]

    statuses = asyncio.run(_drive())
    assert statuses == ["completed", "completed"]

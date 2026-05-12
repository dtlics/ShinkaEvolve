"""Unit tests for ``BackgroundOpenAIResponsesModel``.

The polling loop is the only interesting behavior; everything else is
delegated to the parent ``OpenAIResponsesModel``. Tests use a small mock
``AsyncOpenAI`` shim with ``responses.create`` / ``retrieve`` / ``cancel``
configured via ``AsyncMock`` side_effect sequences.

Async tests follow the repo convention of using ``asyncio.run`` rather
than ``pytest-asyncio`` to avoid pulling a new test-only dependency.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shinka.llm.agent.background_model import (
    BackgroundOpenAIResponsesModel,
    BackgroundPollTimeout,
)


def _make_response(status: str, response_id: str = "resp_test_001") -> SimpleNamespace:
    """Build a minimal stand-in for an OpenAI ``Response`` object."""
    return SimpleNamespace(id=response_id, status=status, output=[], usage=None)


def _make_mock_client(
    create_returns: SimpleNamespace,
    retrieve_returns: list[SimpleNamespace] | None = None,
) -> MagicMock:
    """Build a mock ``AsyncOpenAI`` exposing ``responses.{create,retrieve,cancel}``.

    ``create`` always returns ``create_returns``; ``retrieve`` walks
    through ``retrieve_returns`` once per call. ``cancel`` is a no-op
    ``AsyncMock``.
    """
    client = MagicMock()
    client.responses = MagicMock()
    client.responses.create = AsyncMock(return_value=create_returns)
    client.responses.retrieve = AsyncMock(side_effect=retrieve_returns or [])
    client.responses.cancel = AsyncMock(return_value=None)
    return client


class _StubModel(BackgroundOpenAIResponsesModel):
    """Test subclass that stubs out ``_build_response_create_kwargs``.

    The parent's implementation builds a complex dict from
    ``ModelSettings`` and tool definitions; we don't need to exercise it
    here because the create call is mocked anyway. We just want
    something the polling loop receives.
    """

    def _build_response_create_kwargs(self, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        return {"model": self.model, "input": kwargs.get("input", "")}


def _stub_args() -> dict[str, Any]:
    """Args that ``_fetch_response`` always receives but doesn't care about here."""
    return dict(
        system_instructions=None,
        input="hello",
        model_settings=MagicMock(),
        tools=[],
        output_schema=None,
        handoffs=[],
        previous_response_id=None,
        conversation_id=None,
        stream=False,
        prompt=None,
    )


def test_returns_immediately_when_create_returns_terminal_status() -> None:
    completed = _make_response(status="completed")
    client = _make_mock_client(create_returns=completed, retrieve_returns=[])

    model = _StubModel(
        model="gpt-test",
        openai_client=client,
        poll_interval_sec=0.001,
        poll_timeout_sec=1.0,
    )

    result = asyncio.run(model._fetch_response(**_stub_args()))

    assert result is completed
    assert client.responses.create.await_count == 1
    assert client.responses.retrieve.await_count == 0
    # background=True must be injected.
    create_kwargs = client.responses.create.await_args.kwargs
    assert create_kwargs.get("background") is True


def test_polls_until_terminal() -> None:
    queued = _make_response(status="queued")
    in_progress = _make_response(status="in_progress")
    completed = _make_response(status="completed")

    client = _make_mock_client(
        create_returns=queued,
        retrieve_returns=[in_progress, in_progress, completed],
    )

    model = _StubModel(
        model="gpt-test",
        openai_client=client,
        poll_interval_sec=0.001,
        poll_timeout_sec=1.0,
    )

    result = asyncio.run(model._fetch_response(**_stub_args()))

    assert result is completed
    assert client.responses.create.await_count == 1
    assert client.responses.retrieve.await_count == 3


def test_stuck_in_queued_raises_timeout() -> None:
    queued = _make_response(status="queued")
    client = _make_mock_client(
        create_returns=queued,
        # Always queued -> stuck.
        retrieve_returns=[queued] * 1000,
    )

    model = _StubModel(
        model="gpt-test",
        openai_client=client,
        poll_interval_sec=0.001,
        poll_timeout_sec=10.0,  # wall clock isn't what trips us here.
        max_queued_wait_sec=0.05,  # stuck-queued trips first.
    )

    with pytest.raises(BackgroundPollTimeout) as exc_info:
        asyncio.run(model._fetch_response(**_stub_args()))
    assert exc_info.value.last_status == "queued"
    client.responses.cancel.assert_awaited_once_with("resp_test_001")


def test_wall_clock_timeout_raises() -> None:
    in_progress = _make_response(status="in_progress")
    client = _make_mock_client(
        create_returns=in_progress,
        retrieve_returns=[in_progress] * 1000,
    )

    model = _StubModel(
        model="gpt-test",
        openai_client=client,
        poll_interval_sec=0.001,
        poll_timeout_sec=0.05,  # trip wall clock fast
        max_queued_wait_sec=10.0,
    )

    with pytest.raises(BackgroundPollTimeout) as exc_info:
        asyncio.run(model._fetch_response(**_stub_args()))
    assert exc_info.value.last_status == "in_progress"
    client.responses.cancel.assert_awaited_once()


def test_in_progress_resets_queued_clock() -> None:
    """If we move out of ``queued`` into ``in_progress``, the queued-wait
    timer is reset; only wall clock should bound us thereafter."""
    queued = _make_response(status="queued")
    in_progress = _make_response(status="in_progress")
    completed = _make_response(status="completed")

    # Sequence: queued -> in_progress -> completed.
    client = _make_mock_client(
        create_returns=queued,
        retrieve_returns=[in_progress, completed],
    )

    model = _StubModel(
        model="gpt-test",
        openai_client=client,
        poll_interval_sec=0.001,
        poll_timeout_sec=5.0,
        # max_queued_wait_sec is short but we should never trip it
        # because we leave queued on the first retrieve.
        max_queued_wait_sec=0.05,
    )

    result = asyncio.run(model._fetch_response(**_stub_args()))

    assert result is completed


def test_streaming_delegates_to_parent() -> None:
    """Streaming requests should NOT use background polling — they
    delegate to the parent ``_fetch_response``."""
    sentinel = object()
    client = _make_mock_client(create_returns=_make_response("queued"))

    model = _StubModel(
        model="gpt-test",
        openai_client=client,
        poll_interval_sec=0.001,
        poll_timeout_sec=1.0,
    )

    # Patch the parent's _fetch_response to a tracking AsyncMock so we
    # verify the delegation without exercising actual streaming.
    parent_fetch = AsyncMock(return_value=sentinel)
    from agents.models.openai_responses import OpenAIResponsesModel

    original = OpenAIResponsesModel._fetch_response
    OpenAIResponsesModel._fetch_response = parent_fetch  # type: ignore[assignment]
    try:
        streaming_args = dict(_stub_args())
        streaming_args["stream"] = True
        result = asyncio.run(model._fetch_response(**streaming_args))
    finally:
        OpenAIResponsesModel._fetch_response = original  # type: ignore[assignment]

    assert result is sentinel
    parent_fetch.assert_awaited_once()
    # background polling code path should not have been touched.
    assert client.responses.create.await_count == 0


def test_unexpected_status_returns_without_infinite_loop() -> None:
    """If the API returns a status we don't recognize, return rather
    than poll forever. Lets the parent's error handling surface the
    issue rather than us silently hanging."""
    queued = _make_response(status="queued")
    weird = _make_response(status="some_unknown_state")
    client = _make_mock_client(
        create_returns=queued,
        retrieve_returns=[weird],
    )

    model = _StubModel(
        model="gpt-test",
        openai_client=client,
        poll_interval_sec=0.001,
        poll_timeout_sec=1.0,
    )

    result = asyncio.run(model._fetch_response(**_stub_args()))
    assert result is weird
    assert client.responses.retrieve.await_count == 1


def test_cancel_failure_does_not_mask_timeout() -> None:
    """If ``cancel`` raises, we should still surface the
    ``BackgroundPollTimeout`` to the outer retry layer rather than the
    cancel error."""
    queued = _make_response(status="queued")
    client = _make_mock_client(
        create_returns=queued,
        retrieve_returns=[queued] * 100,
    )
    client.responses.cancel = AsyncMock(side_effect=RuntimeError("cancel busted"))

    model = _StubModel(
        model="gpt-test",
        openai_client=client,
        poll_interval_sec=0.001,
        poll_timeout_sec=10.0,
        max_queued_wait_sec=0.01,
    )

    with pytest.raises(BackgroundPollTimeout):
        asyncio.run(model._fetch_response(**_stub_args()))

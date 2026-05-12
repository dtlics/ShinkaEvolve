"""Unit tests for ``AgentLLMClient``.

We test in two slices:

1. The ``_runresult_to_queryresult`` adapter — pure function from a
   RunResult-shaped object to a ``QueryResult``. Pure math; no async.

2. The ``AgentLLMClient.query`` / ``.run_agent`` routing — provider
   resolution decides whether the call goes through the agents SDK
   (``agents.Runner.run``) or falls back to legacy ``query_async``.
   Both code paths are monkeypatched so tests run offline without
   API credentials.

Note on monkeypatching ``Runner.run``: the SDK's ``Runner.run`` is a
classmethod. ``monkeypatch.setattr(Runner, "run", AsyncMock(...))``
replaces the descriptor with a plain attribute, so calls to
``Runner.run(agent, input, **kwargs)`` pass ``agent`` as the first
positional arg (rather than ``cls``). Tests that capture args/kwargs
account for this.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shinka.llm.agent.client import (
    AgentLLMClient,
    _runresult_to_queryresult,
)
from shinka.llm.providers import QueryResult


# ----------------------------------------------------------------------
# _runresult_to_queryresult — adapter unit tests
# ----------------------------------------------------------------------


def _make_raw_response(
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
) -> SimpleNamespace:
    """Build a stand-in for ``agents.items.ModelResponse``."""
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )
    return SimpleNamespace(usage=usage)


def _make_run_result(
    raw_responses: list[SimpleNamespace],
    final_output: str = "hello world",
    new_items: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        raw_responses=raw_responses,
        final_output=final_output,
        new_items=new_items or [],
    )


def test_adapter_basic_single_response() -> None:
    run = _make_run_result(
        raw_responses=[_make_raw_response(input_tokens=100, output_tokens=50)],
        final_output="hi",
    )
    qr = _runresult_to_queryresult(
        run,
        msg="user msg",
        system_msg="sys",
        msg_history=[],
        shinka_model_name="azure-gpt-5.4-mini",
        api_model_name="gpt-5.4-mini",  # priced in shinka's pricing table
        llm_kwargs={"model_name": "azure-gpt-5.4-mini", "temperature": 0.5},
        model_posteriors=None,
        verbose=False,
    )
    assert isinstance(qr, QueryResult)
    assert qr.content == "hi"
    assert qr.msg == "user msg"
    assert qr.system_msg == "sys"
    assert qr.input_tokens == 100
    assert qr.output_tokens == 50
    assert qr.thinking_tokens == 0
    assert qr.cost > 0  # priced model -> real cost
    assert qr.num_tool_calls == 0
    assert qr.num_total_queries == 1
    # Message history wrapped correctly.
    assert qr.new_msg_history == [
        {"role": "user", "content": "user msg"},
        {"role": "assistant", "content": "hi"},
    ]


def test_adapter_sums_usage_across_multiple_responses() -> None:
    """Agent runs that call tools produce multiple ModelResponses; we
    sum input/output tokens across them."""
    run = _make_run_result(
        raw_responses=[
            _make_raw_response(input_tokens=10, output_tokens=20),
            _make_raw_response(input_tokens=30, output_tokens=40),
            _make_raw_response(input_tokens=50, output_tokens=60),
        ],
        final_output="final",
    )
    qr = _runresult_to_queryresult(
        run,
        msg="m",
        system_msg="s",
        msg_history=[],
        shinka_model_name="azure-gpt-5.4-mini",
        api_model_name="gpt-5.4-mini",
        llm_kwargs={"model_name": "azure-gpt-5.4-mini"},
        model_posteriors=None,
        verbose=False,
    )
    assert qr.input_tokens == 90
    assert qr.output_tokens == 120
    assert qr.num_total_queries == 3


def test_adapter_separates_reasoning_tokens() -> None:
    """``output_tokens`` from the API includes reasoning; shinka's
    convention surfaces visible-output and reasoning separately."""
    run = _make_run_result(
        raw_responses=[
            _make_raw_response(
                input_tokens=100, output_tokens=500, reasoning_tokens=400
            ),
        ],
        final_output="final",
    )
    qr = _runresult_to_queryresult(
        run,
        msg="m",
        system_msg="s",
        msg_history=[],
        shinka_model_name="azure-gpt-5.4-mini",
        api_model_name="gpt-5.4-mini",
        llm_kwargs={"model_name": "azure-gpt-5.4-mini"},
        model_posteriors=None,
        verbose=False,
    )
    # 500 total output - 400 reasoning = 100 visible.
    assert qr.output_tokens == 100
    assert qr.thinking_tokens == 400


def test_adapter_counts_tool_calls_via_item_type() -> None:
    """Tool-call counts come from ``new_items`` filtered by ``.type``."""
    new_items = [
        SimpleNamespace(type="message_output_item"),
        SimpleNamespace(type="tool_call_item"),
        SimpleNamespace(type="function_call_item"),
        SimpleNamespace(type="tool_call_output_item"),  # not a tool-call request
        SimpleNamespace(type="reasoning_item"),
    ]
    run = _make_run_result(
        raw_responses=[_make_raw_response(input_tokens=10, output_tokens=10)],
        final_output="done",
        new_items=new_items,
    )
    qr = _runresult_to_queryresult(
        run,
        msg="m",
        system_msg="s",
        msg_history=[],
        shinka_model_name="azure-gpt-5.4-mini",
        api_model_name="gpt-5.4-mini",
        llm_kwargs={"model_name": "azure-gpt-5.4-mini"},
        model_posteriors=None,
        verbose=False,
    )
    # tool_call_item + function_call_item + tool_call_output_item all
    # contain "tool_call" or "function_call". This is wider than just
    # outgoing calls but acceptable for telemetry (the adapter docs
    # call this out — finer-grained counting is a Phase C follow-up).
    assert qr.num_tool_calls == 3


def test_adapter_unknown_model_defaults_cost_to_zero() -> None:
    run = _make_run_result(
        raw_responses=[_make_raw_response(input_tokens=100, output_tokens=200)],
    )
    qr = _runresult_to_queryresult(
        run,
        msg="m",
        system_msg="s",
        msg_history=[],
        shinka_model_name="unknown-model-xyz",
        api_model_name="unknown-model-xyz",
        llm_kwargs={"model_name": "unknown-model-xyz"},
        model_posteriors=None,
        verbose=False,
    )
    assert qr.cost == 0.0
    assert qr.input_cost == 0.0
    assert qr.output_cost == 0.0


def test_adapter_preserves_existing_msg_history() -> None:
    """If caller passes prior history, the new history appends the
    current turn on top of it."""
    prior_history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first-reply"},
    ]
    run = _make_run_result(
        raw_responses=[_make_raw_response(input_tokens=1, output_tokens=1)],
        final_output="second-reply",
    )
    qr = _runresult_to_queryresult(
        run,
        msg="second",
        system_msg="s",
        msg_history=prior_history,
        shinka_model_name="azure-gpt-5.4-mini",
        api_model_name="gpt-5.4-mini",
        llm_kwargs={"model_name": "azure-gpt-5.4-mini"},
        model_posteriors=None,
        verbose=False,
    )
    assert qr.new_msg_history == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first-reply"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "second-reply"},
    ]


# ----------------------------------------------------------------------
# AgentLLMClient.query — routing tests
# ----------------------------------------------------------------------


def _make_resolved_model(provider: str, api_model_name: str = "test-model") -> SimpleNamespace:
    return SimpleNamespace(
        provider=provider,
        api_model_name=api_model_name,
        api_key_env_name=None,
        base_url=None,
    )


def _stub_async_client(monkeypatch: pytest.MonkeyPatch, *, provider: str = "azure_openai") -> None:
    """Replace ``get_async_client_llm`` so tests don't need real Azure
    credentials. The agent factory in ``_query_via_agents`` calls
    this before constructing the BackgroundOpenAIResponsesModel; we
    want a MagicMock client there so construction succeeds without
    AZURE_OPENAI_API_KEY / AZURE_API_ENDPOINT."""
    import shinka.llm.agent.client as agent_client_mod

    monkeypatch.setattr(
        agent_client_mod,
        "get_async_client_llm",
        lambda model_name, structured_output=False: (
            MagicMock(name="stub_async_client"),
            "gpt-5.4-mini",
            provider,
        ),
    )


def test_query_routes_azure_through_agents_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For Azure/OpenAI, the query must go through ``Runner.run``
    rather than ``query_async``."""
    # Stub provider resolution -> azure_openai, AsyncOpenAI client.
    import shinka.llm.agent.client as agent_client_mod

    monkeypatch.setattr(
        agent_client_mod,
        "resolve_model_backend",
        lambda name: _make_resolved_model("azure_openai", api_model_name="gpt-5.4-mini"),
    )
    _stub_async_client(monkeypatch)

    # Stub the Runner.run to return a canned RunResult.
    fake_run = _make_run_result(
        raw_responses=[_make_raw_response(input_tokens=10, output_tokens=20)],
        final_output="agent answer",
    )
    from agents import Runner

    run_mock = AsyncMock(return_value=fake_run)
    monkeypatch.setattr(Runner, "run", run_mock)

    # Prevent the agent factory from actually building a client. The
    # factory is only invoked by the real Runner; since Runner.run
    # is mocked, the factory is never called — but the agents SDK Agent
    # constructor would attempt imports. We don't enter the factory in
    # this test, so it's safe.

    # Legacy path must NOT be called.
    legacy_mock = AsyncMock()
    monkeypatch.setattr(agent_client_mod, "query_async", legacy_mock)

    client = AgentLLMClient(
        model_names=["azure-gpt-5.4-mini"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
    )

    result = asyncio.run(client.query(msg="hi", system_msg="sys"))

    assert isinstance(result, QueryResult)
    assert result.content == "agent answer"
    run_mock.assert_awaited_once()
    legacy_mock.assert_not_awaited()


def test_query_routes_non_openai_through_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic / Gemini / etc. must use the legacy ``query_async``.

    We use a real shinka model name (``claude-3-5-haiku-20241022``) so
    ``sample_model_kwargs`` resolves cleanly; we don't need to patch
    ``resolve_model_backend`` at all.
    """
    import shinka.llm.agent.client as agent_client_mod

    fake_qr = QueryResult(
        content="claude said hi",
        msg="hi",
        system_msg="sys",
        new_msg_history=[],
        model_name="claude-3-5-haiku-20241022",
        kwargs={},
        input_tokens=1,
        output_tokens=2,
    )
    legacy_mock = AsyncMock(return_value=fake_qr)
    monkeypatch.setattr(agent_client_mod, "query_async", legacy_mock)

    from agents import Runner

    run_mock = AsyncMock()
    monkeypatch.setattr(Runner, "run", run_mock)

    client = AgentLLMClient(
        model_names=["claude-3-5-haiku-20241022"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
    )
    result = asyncio.run(client.query(msg="hi", system_msg="sys"))

    assert result is fake_qr
    legacy_mock.assert_awaited_once()
    run_mock.assert_not_awaited()


def test_structured_output_falls_back_to_legacy_even_for_azure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``output_model`` is set, structured output must go through
    the legacy ``instructor``-augmented path; the agents SDK doesn't
    support our pydantic structured-output flow in Phase B."""
    import shinka.llm.agent.client as agent_client_mod

    monkeypatch.setattr(
        agent_client_mod,
        "resolve_model_backend",
        lambda name: _make_resolved_model("azure_openai", api_model_name="gpt-5.4-mini"),
    )

    from pydantic import BaseModel

    class MyOutput(BaseModel):
        answer: str

    fake_qr = QueryResult(
        content="x",
        msg="m",
        system_msg="s",
        new_msg_history=[],
        model_name="m",
        kwargs={},
        input_tokens=1,
        output_tokens=1,
    )
    legacy_mock = AsyncMock(return_value=fake_qr)
    monkeypatch.setattr(agent_client_mod, "query_async", legacy_mock)

    from agents import Runner

    run_mock = AsyncMock()
    monkeypatch.setattr(Runner, "run", run_mock)

    client = AgentLLMClient(
        model_names=["azure-gpt-5.4-mini"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        output_model=MyOutput,
        verbose=False,
    )
    asyncio.run(client.query(msg="m", system_msg="s"))
    legacy_mock.assert_awaited_once()
    run_mock.assert_not_awaited()


def test_query_returns_none_after_repeated_legacy_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shinka.llm.agent.client as agent_client_mod

    # Make asyncio.sleep instant for fast test.
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    legacy_mock = AsyncMock(side_effect=RuntimeError("transport boom"))
    monkeypatch.setattr(agent_client_mod, "query_async", legacy_mock)

    client = AgentLLMClient(
        model_names=["claude-3-5-haiku-20241022"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
        max_attempts=2,
    )
    result = asyncio.run(client.query(msg="m", system_msg="s"))
    assert result is None
    assert legacy_mock.await_count == 2


def test_query_returns_none_after_agent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Runner.run raises, AgentLLMClient surfaces ``None`` rather
    than propagating — matches AsyncLLMClient contract."""
    import shinka.llm.agent.client as agent_client_mod

    monkeypatch.setattr(
        agent_client_mod,
        "resolve_model_backend",
        lambda name: _make_resolved_model("azure_openai", api_model_name="gpt-5.4-mini"),
    )
    _stub_async_client(monkeypatch)
    from agents import Runner

    run_mock = AsyncMock(side_effect=RuntimeError("agent boom"))
    monkeypatch.setattr(Runner, "run", run_mock)
    legacy_mock = AsyncMock()
    monkeypatch.setattr(agent_client_mod, "query_async", legacy_mock)

    client = AgentLLMClient(
        model_names=["azure-gpt-5.4-mini"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
    )
    result = asyncio.run(client.query(msg="m", system_msg="s"))
    assert result is None


def test_get_kwargs_matches_sample_model_kwargs_shape() -> None:
    """``get_kwargs`` is a thin wrapper; assert the returned dict has
    the keys downstream callers depend on (``model_name``,
    ``temperature``, and a max-tokens field).

    Note: reasoning models on OpenAI/Azure providers have a fixed
    temperature override (see kwargs.py:113), so we don't pin the
    temperature value here.
    """
    client = AgentLLMClient(
        model_names=["azure-gpt-5.4-mini"],
        temperatures=[0.3, 0.7],
        max_tokens=[1000, 2000],
        reasoning_efforts=["disabled"],
        verbose=False,
    )
    kwargs = client.get_kwargs()
    assert kwargs["model_name"] == "azure-gpt-5.4-mini"
    assert "temperature" in kwargs
    # azure-gpt-5.4-mini is a reasoning model -> uses max_output_tokens.
    assert "max_output_tokens" in kwargs or "max_tokens" in kwargs


# ----------------------------------------------------------------------
# AgentLLMClient.run_agent — tool-using runs
# ----------------------------------------------------------------------


def test_run_agent_passes_tools_and_context_to_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_agent on Azure should reach Runner.run with the
    per-call tools and context wired through."""
    import shinka.llm.agent.client as agent_client_mod

    _stub_async_client(monkeypatch)

    fake_run = _make_run_result(
        raw_responses=[_make_raw_response(input_tokens=5, output_tokens=10)],
        final_output="done",
    )
    from agents import Runner

    captured_args: tuple = ()
    captured_kwargs: dict = {}

    async def capture_run(*args: Any, **kwargs: Any) -> Any:
        nonlocal captured_args
        captured_args = args
        captured_kwargs.update(kwargs)
        return fake_run

    monkeypatch.setattr(Runner, "run", capture_run)

    sentinel_ctx = MagicMock(name="ShinkaToolContext")
    sentinel_tool_a = MagicMock(name="apply_patch_tool")
    sentinel_tool_b = MagicMock(name="evaluate_tool")

    client = AgentLLMClient(
        model_names=["azure-gpt-5.4-mini"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
    )

    result = asyncio.run(
        client.run_agent(
            msg="please patch this",
            system_msg="you are shinka",
            tool_context=sentinel_ctx,
            tools=[sentinel_tool_a, sentinel_tool_b],
            max_turns=7,
        )
    )

    assert isinstance(result, QueryResult)
    assert result.content == "done"
    assert captured_kwargs.get("context") is sentinel_ctx
    assert captured_kwargs.get("max_turns") == 7


def test_run_agent_default_max_turns_falls_back_to_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shinka.llm.agent.client as agent_client_mod

    _stub_async_client(monkeypatch)

    fake_run = _make_run_result(
        raw_responses=[_make_raw_response(input_tokens=1, output_tokens=1)],
        final_output="done",
    )
    from agents import Runner

    captured_kwargs: dict = {}

    async def capture(*args: Any, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return fake_run

    monkeypatch.setattr(Runner, "run", capture)

    client = AgentLLMClient(
        model_names=["azure-gpt-5.4-mini"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
        max_tool_steps=15,
    )

    asyncio.run(
        client.run_agent(
            msg="hi",
            system_msg="sys",
            tool_context=MagicMock(),
            tools=[],
            # max_turns not provided -> fall back to constructor's 15
        )
    )
    assert captured_kwargs.get("max_turns") == 15


def test_run_agent_returns_none_on_agent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_async_client(monkeypatch)
    from agents import Runner

    async def boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("agent exhausted retries")

    monkeypatch.setattr(Runner, "run", boom)

    client = AgentLLMClient(
        model_names=["azure-gpt-5.4-mini"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
    )

    result = asyncio.run(
        client.run_agent(
            msg="hi",
            system_msg="sys",
            tool_context=MagicMock(),
            tools=[],
        )
    )
    assert result is None


def test_run_agent_falls_back_to_legacy_on_non_openai_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For Anthropic/Gemini/etc., run_agent ignores tools and falls
    back to single-turn legacy query (and logs a warning)."""
    import shinka.llm.agent.client as agent_client_mod

    fake_qr = QueryResult(
        content="claude said hi",
        msg="m",
        system_msg="s",
        new_msg_history=[],
        model_name="claude-3-5-haiku-20241022",
        kwargs={},
        input_tokens=1,
        output_tokens=2,
    )
    legacy_mock = AsyncMock(return_value=fake_qr)
    monkeypatch.setattr(agent_client_mod, "query_async", legacy_mock)

    from agents import Runner

    async def should_not_run(self, *a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "Runner.run should NOT be called for non-OpenAI providers"
        )

    monkeypatch.setattr(Runner, "run", should_not_run)

    client = AgentLLMClient(
        model_names=["claude-3-5-haiku-20241022"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
    )

    result = asyncio.run(
        client.run_agent(
            msg="hi",
            system_msg="sys",
            tool_context=MagicMock(),
            tools=[MagicMock(name="ignored_tool")],
        )
    )
    assert result is fake_qr
    legacy_mock.assert_awaited_once()


def test_run_agent_uses_per_call_tools_not_constructor_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When run_agent passes ``tools=`` explicitly, the constructor-
    time self._tools (which may be empty) must be ignored.

    Because Agent is constructed inline inside ``_query_via_agents``
    before the ``Runner.run`` call, we just monkeypatch
    ``Agent.__init__`` to capture the tools it sees and stub
    ``get_async_client_llm`` so it doesn't need AZURE_OPENAI_API_KEY.
    """
    captured_agent_tools: list = []

    from agents import Agent

    original_init = Agent.__init__

    def capturing_init(self, *args: Any, **kwargs: Any) -> None:
        captured_agent_tools.append(list(kwargs.get("tools") or []))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capturing_init)

    # Stub the client constructor so the factory doesn't need
    # AZURE_OPENAI_API_KEY.
    import shinka.llm.agent.client as agent_client_mod

    monkeypatch.setattr(
        agent_client_mod,
        "get_async_client_llm",
        lambda model_name, structured_output=False: (
            MagicMock(name="stub_async_client"),
            "gpt-5.4-mini",
            "azure_openai",
        ),
    )

    from agents import Runner

    fake_run = _make_run_result(
        raw_responses=[_make_raw_response(input_tokens=1, output_tokens=1)],
    )

    # By the time Runner.run is called, Agent.__init__ has already
    # fired (it happens earlier in _query_via_agents). Return canned
    # RunResult so the QueryResult adapter has something to work with.
    monkeypatch.setattr(Runner, "run", AsyncMock(return_value=fake_run))

    constructor_tool = MagicMock(name="constructor_tool")
    per_call_tool = MagicMock(name="per_call_tool")
    client = AgentLLMClient(
        model_names=["azure-gpt-5.4-mini"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
        tools=[constructor_tool],
    )

    asyncio.run(
        client.run_agent(
            msg="hi",
            system_msg="sys",
            tool_context=MagicMock(),
            tools=[per_call_tool],
        )
    )

    # The Agent was constructed with per_call_tool, not constructor_tool.
    assert per_call_tool in captured_agent_tools[-1]
    assert constructor_tool not in captured_agent_tools[-1]


def test_batch_query_runs_concurrently_and_filters_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """batch_query gathers concurrent .query() calls; failures (returns
    None or raises) are filtered, successes are kept."""
    import shinka.llm.agent.client as agent_client_mod

    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    success_qr = QueryResult(
        content="ok",
        msg="m",
        system_msg="s",
        new_msg_history=[],
        model_name="claude-3-5-haiku-20241022",
        kwargs={},
        input_tokens=1,
        output_tokens=1,
    )
    call_count = {"n": 0}

    async def side_effect(*args: Any, **kwargs: Any) -> QueryResult:
        call_count["n"] += 1
        # Odd calls succeed, even calls fail. With max_attempts=1 and
        # 2 samples, this gives us exactly one success and one failure.
        if call_count["n"] % 2 == 1:
            return success_qr
        raise RuntimeError("boom")

    monkeypatch.setattr(
        agent_client_mod, "query_async", AsyncMock(side_effect=side_effect)
    )

    client = AgentLLMClient(
        model_names=["claude-3-5-haiku-20241022"],
        temperatures=0.5,
        max_tokens=1000,
        reasoning_efforts="disabled",
        verbose=False,
        max_attempts=1,
    )
    results = asyncio.run(
        client.batch_query(num_samples=2, msg="hi", system_msg="sys")
    )
    # The failed task returns None from .query() and is filtered out.
    assert len(results) == 1
    assert results[0] is success_qr

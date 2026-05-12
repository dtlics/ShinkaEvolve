"""Phase 3a tests: tool primitives + requires_action dispatch loop.

The Responses API is stubbed; we drive it through canned status sequences
and verify the bg+poll helper invokes the right tool, respects the budget,
and coalesces concurrent fetches.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from shinka.llm.poll import create_and_poll, create_and_poll_async
from shinka.llm.tools import (
    ShellTool,
    ToolBudget,
    ToolBudgetExceeded,
    URLFetchCache,
    WebFetchTool,
    WebSearchTool,
    lookup_tool_by_name,
    serialize_tools,
)


# ---------------------------------------------------------------------------
# ToolBudget
# ---------------------------------------------------------------------------


def test_tool_budget_decrements_per_tool():
    b = ToolBudget(max_searches=2, max_fetches=1, max_shell=0, max_turns=10)
    assert b.can_spend("web_search")
    b.spend("web_search")
    b.spend("web_search")
    assert not b.can_spend("web_search")
    with pytest.raises(ToolBudgetExceeded):
        b.spend("web_search")


def test_tool_budget_blocks_after_turn_exhaustion():
    b = ToolBudget(max_searches=3, max_fetches=3, max_shell=3, max_turns=1)
    b.consume_turn()
    assert not b.can_spend("web_search")
    with pytest.raises(ToolBudgetExceeded):
        b.spend("web_search")


# ---------------------------------------------------------------------------
# Tool specs
# ---------------------------------------------------------------------------


def test_serialize_tools_emits_openai_shapes():
    serialized = serialize_tools([WebSearchTool(), WebFetchTool(), ShellTool()])
    assert serialized is not None
    assert {item["type"] for item in serialized} == {
        "web_search",
        "function",
        "shell",
    }
    fetch_spec = next(item for item in serialized if item["type"] == "function")
    assert fetch_spec["name"] == "web_fetch"
    assert "url" in fetch_spec["parameters"]["properties"]


def test_server_side_tools_reject_client_dispatch():
    for tool in (WebSearchTool(), ShellTool()):
        with pytest.raises(NotImplementedError):
            tool.dispatch({"url": "x"}, {})


# ---------------------------------------------------------------------------
# URLFetchCache
# ---------------------------------------------------------------------------


def _fake_fetcher_factory(content: str = "hello"):
    counter = {"calls": 0}

    def _fetch(url: str) -> Dict[str, Any]:
        counter["calls"] += 1
        return {
            "url": url,
            "content": content,
            "content_type": "text/plain",
            "http_status": 200,
            "from_cache": False,
        }

    return _fetch, counter


def test_url_fetch_cache_caches_repeat_hits(tmp_path: Path):
    db_path = tmp_path / "cache.db"
    fetch_fn, counter = _fake_fetcher_factory()
    cache = URLFetchCache(db_path=str(db_path), fetcher=fetch_fn)

    first = cache.get("https://example.com")
    second = cache.get("https://example.com")

    assert counter["calls"] == 1
    assert second["from_cache"] is True
    assert second["hits"] == 1


def test_url_fetch_cache_respects_ttl(tmp_path: Path):
    db_path = tmp_path / "cache.db"
    fetch_fn, counter = _fake_fetcher_factory()
    cache = URLFetchCache(db_path=str(db_path), fetcher=fetch_fn, ttl_seconds=0)

    cache.get("https://example.com")
    cache.get("https://example.com")
    # TTL=0 should force a re-fetch every time.
    assert counter["calls"] == 2


def test_url_fetch_cache_coalesces_async_calls(tmp_path: Path):
    db_path = tmp_path / "cache.db"
    fetch_lock = threading.Lock()
    in_flight = {"max": 0, "current": 0}
    counter = {"calls": 0}

    def _slow_fetch(url: str) -> Dict[str, Any]:
        with fetch_lock:
            in_flight["current"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["current"])
        try:
            counter["calls"] += 1
            time.sleep(0.05)
        finally:
            with fetch_lock:
                in_flight["current"] -= 1
        return {
            "url": url,
            "content": "x",
            "content_type": "text/plain",
            "http_status": 200,
            "from_cache": False,
        }

    cache = URLFetchCache(db_path=str(db_path), fetcher=_slow_fetch)

    async def _drive():
        return await asyncio.gather(
            *[cache.get_async("https://example.com") for _ in range(5)]
        )

    asyncio.run(_drive())
    # Only ONE underlying fetch should have run; the other 4 waited on the
    # per-URL asyncio.Lock and read from the cache after the first completed.
    assert counter["calls"] == 1
    assert in_flight["max"] == 1


# ---------------------------------------------------------------------------
# Dispatch loop: integration with create_and_poll
# ---------------------------------------------------------------------------


def _response(
    response_id: str = "resp_test",
    status: str = "queued",
    *,
    tool_calls: List[Dict[str, Any]] | None = None,
):
    required_action = None
    if tool_calls:
        wrapped = []
        for tc in tool_calls:
            wrapped.append(
                SimpleNamespace(
                    id=tc.get("id"),
                    function=SimpleNamespace(
                        name=tc["name"],
                        arguments=json.dumps(tc.get("arguments", {})),
                    ),
                )
            )
        required_action = SimpleNamespace(
            submit_tool_outputs=SimpleNamespace(tool_calls=wrapped)
        )
    return SimpleNamespace(
        id=response_id,
        status=status,
        output=None,
        error=None,
        required_action=required_action,
    )


class _StubResponses:
    def __init__(
        self,
        sequence: List[Any],
    ):
        # Each element is either a status string (terminal/intermediate) or
        # a list of tool_calls dicts for ``requires_action``. The first
        # element is consumed by ``create``; subsequent by ``retrieve``.
        self._sequence = list(sequence)
        self.create_calls: List[dict] = []
        self.retrieve_calls: List[str] = []
        self.submit_calls: List[dict] = []
        self.delete_calls: List[str] = []

    def _next_response(self) -> Any:
        if not self._sequence:
            raise AssertionError("stub ran out of canned responses")
        spec = self._sequence.pop(0)
        if isinstance(spec, str):
            return _response(status=spec)
        if isinstance(spec, list):
            return _response(status="requires_action", tool_calls=spec)
        return spec  # already a SimpleNamespace

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self._next_response()

    def retrieve(self, response_id: str):
        self.retrieve_calls.append(response_id)
        return self._next_response()

    def submit_tool_outputs(self, response_id: str, tool_outputs):
        self.submit_calls.append(
            {"response_id": response_id, "outputs": tool_outputs}
        )

    def delete(self, response_id: str):
        self.delete_calls.append(response_id)


class _StubClient:
    def __init__(self, responses: _StubResponses):
        self.responses = responses


def _patch_sleep(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    async def _async_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _async_noop)


def test_create_and_poll_dispatches_requires_action(monkeypatch, tmp_path: Path):
    _patch_sleep(monkeypatch)
    fetch_fn, counter = _fake_fetcher_factory(content="page content")
    cache = URLFetchCache(db_path=str(tmp_path / "c.db"), fetcher=fetch_fn)
    tools = [WebFetchTool()]
    budget = ToolBudget(max_fetches=2, max_turns=3)
    trace: List[Dict[str, Any]] = []

    def dispatcher(pending):
        outputs = []
        for call in pending:
            tool = lookup_tool_by_name(tools, call["name"])
            budget.spend(call["name"])
            payload = tool.dispatch(call["arguments"], {"url_fetch_cache": cache})
            trace.append({"name": call["name"], "args": call["arguments"], "output": payload})
            outputs.append(
                {"tool_call_id": call["call_id"], "output": json.dumps(payload)}
            )
        budget.consume_turn()
        return outputs

    stub = _StubResponses(
        sequence=[
            "queued",  # create returns queued
            [  # retrieve #1: model wants to call web_fetch
                {
                    "id": "call_1",
                    "name": "web_fetch",
                    "arguments": {"url": "https://example.com"},
                }
            ],
            "completed",  # retrieve #2: after submit_tool_outputs, terminal
        ]
    )
    client = _StubClient(stub)

    result = create_and_poll(client, tool_dispatcher=dispatcher, model="gpt-5", input=[])

    assert result.status == "completed"
    assert counter["calls"] == 1  # web_fetch ran exactly once
    assert len(stub.submit_calls) == 1
    submitted = stub.submit_calls[0]["outputs"][0]
    assert submitted["tool_call_id"] == "call_1"
    decoded = json.loads(submitted["output"])
    assert decoded["content"] == "page content"
    assert len(trace) == 1
    assert trace[0]["args"]["url"] == "https://example.com"


def test_create_and_poll_dispatch_records_budget_error_payload(monkeypatch):
    _patch_sleep(monkeypatch)
    tools = [WebFetchTool()]
    # zero fetches allowed -> first dispatch should surface ToolBudgetExceeded
    budget = ToolBudget(max_fetches=0, max_turns=3)
    cache = URLFetchCache()  # in-memory only

    def dispatcher(pending):
        outputs = []
        for call in pending:
            try:
                budget.spend(call["name"])
                tool = lookup_tool_by_name(tools, call["name"])
                payload = tool.dispatch(call["arguments"], {"url_fetch_cache": cache})
            except ToolBudgetExceeded as exc:
                payload = {"error": str(exc)}
            outputs.append(
                {"tool_call_id": call["call_id"], "output": json.dumps(payload)}
            )
        budget.consume_turn()
        return outputs

    stub = _StubResponses(
        sequence=[
            "queued",
            [
                {
                    "id": "call_x",
                    "name": "web_fetch",
                    "arguments": {"url": "https://example.com"},
                }
            ],
            "completed",
        ]
    )
    client = _StubClient(stub)

    result = create_and_poll(client, tool_dispatcher=dispatcher, model="gpt-5", input=[])

    assert result.status == "completed"
    assert len(stub.submit_calls) == 1
    submitted_payload = json.loads(stub.submit_calls[0]["outputs"][0]["output"])
    assert "Budget exhausted" in submitted_payload["error"]


def test_create_and_poll_handles_unknown_tool_name(monkeypatch):
    _patch_sleep(monkeypatch)
    tools = [WebFetchTool()]

    def dispatcher(pending):
        outputs = []
        for call in pending:
            tool = lookup_tool_by_name(tools, call["name"])
            if tool is None:
                payload = {"error": f"unknown tool {call['name']!r}"}
            else:
                payload = tool.dispatch(call["arguments"], {"url_fetch_cache": URLFetchCache()})
            outputs.append(
                {"tool_call_id": call["call_id"], "output": json.dumps(payload)}
            )
        return outputs

    stub = _StubResponses(
        sequence=[
            "queued",
            [
                {
                    "id": "call_y",
                    "name": "totally_made_up",
                    "arguments": {},
                }
            ],
            "completed",
        ]
    )
    client = _StubClient(stub)

    result = create_and_poll(client, tool_dispatcher=dispatcher, model="gpt-5", input=[])
    assert result.status == "completed"
    submitted = json.loads(stub.submit_calls[0]["outputs"][0]["output"])
    assert "unknown tool" in submitted["error"]


# ---------------------------------------------------------------------------
# Phase 3b: prompt_cache_key + metadata + safety_identifier
# ---------------------------------------------------------------------------


def test_query_openai_passes_prompt_cache_key_and_metadata(monkeypatch):
    """Phase 3b: cache_static_prompt and call_metadata reach the API client."""
    _patch_sleep(monkeypatch)

    from shinka.llm.providers.openai import query_openai

    captured: Dict[str, Any] = {}

    class _CapturingResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _response(status="completed", tool_calls=None)

        def retrieve(self, response_id):  # pragma: no cover -- inline completed
            return _response(response_id=response_id, status="completed")

        def delete(self, response_id):
            pass

    class _CapturingClient:
        def __init__(self):
            self.responses = _CapturingResponses()

    # Build a minimal response with usage info so get_openai_costs works.
    def _patch_response_for_cost(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id="resp_test",
            status="completed",
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(text="hello")],
                )
            ],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            error=None,
            required_action=None,
        )

    client = _CapturingClient()
    client.responses.create = _patch_response_for_cost  # type: ignore[assignment]

    result = query_openai(
        client=client,
        model="gpt-5-mini",
        msg="user prompt",
        system_msg="STATIC SYSTEM PROMPT",
        msg_history=[],
        output_model=None,
        cache_static_prompt=True,
        call_metadata={"run_id": "abc123", "purpose": "proposer"},
        safety_identifier="user_abc",
    )

    assert result is not None
    assert "prompt_cache_key" in captured
    # SHA-256 hash truncated to 32 chars -- deterministic per identical system_msg
    assert len(captured["prompt_cache_key"]) == 32
    assert all(c in "0123456789abcdef" for c in captured["prompt_cache_key"])
    assert captured["metadata"] == {"run_id": "abc123", "purpose": "proposer"}
    assert captured["safety_identifier"] == "user_abc"
    # bg+poll mode forces these:
    assert captured["background"] is True
    assert captured["store"] is True


def test_query_openai_skips_cache_key_when_disabled(monkeypatch):
    _patch_sleep(monkeypatch)
    from shinka.llm.providers.openai import query_openai

    captured: Dict[str, Any] = {}

    def _patch_response(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id="r",
            status="completed",
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(text="ok")],
                )
            ],
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            error=None,
            required_action=None,
        )

    class _Client:
        def __init__(self):
            self.responses = SimpleNamespace(
                create=_patch_response,
                retrieve=lambda response_id: _patch_response(),
                delete=lambda response_id: None,
            )

    query_openai(
        client=_Client(),
        model="gpt-5-mini",
        msg="x",
        system_msg="some system",
        msg_history=[],
        output_model=None,
        cache_static_prompt=False,
    )
    assert "prompt_cache_key" not in captured


def test_async_llm_client_merges_per_call_metadata(monkeypatch):
    """Client-level metadata + per-call call_metadata MERGE before dispatch."""
    _patch_sleep(monkeypatch)
    import shinka.llm.llm as llm_module
    from shinka.llm import AsyncLLMClient

    captured_kwargs: Dict[str, Any] = {}

    async def _fake_query_async(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            cost=0.0, content="x", to_dict=lambda: {}
        )

    # AsyncLLMClient binds query_async at import time; patch the local ref.
    monkeypatch.setattr(llm_module, "query_async", _fake_query_async)

    client = AsyncLLMClient(
        model_names=["gpt-5-mini"],
        max_tokens=128,
        call_metadata={"run_id": "abc", "purpose": "proposer"},
    )

    asyncio.run(
        client.query(
            msg="hi",
            system_msg="sys",
            call_metadata={"generation": "7", "island_idx": "2"},
        )
    )

    assert captured_kwargs["call_metadata"] == {
        "run_id": "abc",
        "purpose": "proposer",
        "generation": "7",
        "island_idx": "2",
    }


def test_async_dispatch_handles_web_fetch(monkeypatch, tmp_path: Path):
    _patch_sleep(monkeypatch)
    fetch_fn, counter = _fake_fetcher_factory(content="async-page")
    cache = URLFetchCache(db_path=str(tmp_path / "cache.db"), fetcher=fetch_fn)

    class _AsyncStub:
        def __init__(self, sequence):
            self._sequence = list(sequence)
            self.create_calls: List[dict] = []
            self.retrieve_calls: List[str] = []
            self.submit_calls: List[dict] = []
            self.delete_calls: List[str] = []

        def _next(self):
            spec = self._sequence.pop(0)
            if isinstance(spec, str):
                return _response(status=spec)
            if isinstance(spec, list):
                return _response(status="requires_action", tool_calls=spec)
            return spec

        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            return self._next()

        async def retrieve(self, response_id: str):
            self.retrieve_calls.append(response_id)
            return self._next()

        async def submit_tool_outputs(self, response_id: str, tool_outputs):
            self.submit_calls.append({"response_id": response_id, "outputs": tool_outputs})

        async def delete(self, response_id: str):
            self.delete_calls.append(response_id)

    class _AsyncClient:
        def __init__(self, responses):
            self.responses = responses

    tools = [WebFetchTool()]

    async def dispatcher(pending):
        outputs = []
        for call in pending:
            tool = lookup_tool_by_name(tools, call["name"])
            payload = await tool.dispatch_async(call["arguments"], {"url_fetch_cache": cache})
            outputs.append(
                {"tool_call_id": call["call_id"], "output": json.dumps(payload)}
            )
        return outputs

    stub = _AsyncStub(
        sequence=[
            "queued",
            [
                {
                    "id": "call_async",
                    "name": "web_fetch",
                    "arguments": {"url": "https://example.org"},
                }
            ],
            "completed",
        ]
    )
    client = _AsyncClient(stub)

    result = asyncio.run(
        create_and_poll_async(client, tool_dispatcher=dispatcher, model="gpt-5", input=[])
    )
    assert result.status == "completed"
    assert counter["calls"] == 1
    assert len(stub.submit_calls) == 1
    decoded = json.loads(stub.submit_calls[0]["outputs"][0]["output"])
    assert decoded["content"] == "async-page"

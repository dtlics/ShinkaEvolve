"""Tests for ``ShinkaAgentHooks``.

The hook class is exercised in isolation: we manufacture stub
``RunContextWrapper``-shaped objects and a fake ``tool`` (just an
object with a ``name``) and feed them through ``on_tool_start`` /
``on_tool_end``. Integration with the SDK happens inside the agent
loop, which is covered by the smoke run.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from shinka.llm.agent.hooks import ShinkaAgentHooks, _classify_result
from shinka.llm.agent.tools import ShinkaToolContext


def _ctx_wrapper(ctx: ShinkaToolContext) -> SimpleNamespace:
    """Mimic the SDK's ``RunContextWrapper`` — only the ``.context``
    attribute matters for the hook's reads."""
    return SimpleNamespace(context=ctx)


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def test_tool_end_records_success_from_ok_prefix() -> None:
    hooks = ShinkaAgentHooks()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    wrap = _ctx_wrapper(ctx)

    asyncio.run(hooks.on_tool_start(wrap, agent=None, tool=_tool("apply_patch")))
    asyncio.run(
        hooks.on_tool_end(
            wrap, agent=None, tool=_tool("apply_patch"), result="OK: applied 1 change"
        )
    )

    assert len(ctx.tool_call_trace) == 1
    entry = ctx.tool_call_trace[0]
    assert entry["name"] == "apply_patch"
    assert entry["success"] is True
    assert "error" not in entry
    assert isinstance(entry["latency_sec"], float)


def test_tool_end_records_failure_from_error_prefix() -> None:
    hooks = ShinkaAgentHooks()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    wrap = _ctx_wrapper(ctx)

    asyncio.run(hooks.on_tool_start(wrap, agent=None, tool=_tool("evaluate")))
    asyncio.run(
        hooks.on_tool_end(
            wrap, agent=None, tool=_tool("evaluate"), result="Error: no evaluator bound"
        )
    )

    entry = ctx.tool_call_trace[0]
    assert entry["success"] is False
    assert entry["error"] == "no evaluator bound"


def test_tool_end_records_failure_from_failed_prefix() -> None:
    """``evaluate_tool`` returns ``"FAILED: ...; partial_metrics=..."``
    when the program ran but validation failed. The hook recognizes
    both ``FAILED:`` and ``Error:`` as failure prefixes."""
    hooks = ShinkaAgentHooks()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    wrap = _ctx_wrapper(ctx)

    asyncio.run(hooks.on_tool_start(wrap, agent=None, tool=_tool("evaluate")))
    asyncio.run(
        hooks.on_tool_end(
            wrap,
            agent=None,
            tool=_tool("evaluate"),
            result="FAILED: circle out of bounds; partial_metrics={}",
        )
    )

    entry = ctx.tool_call_trace[0]
    assert entry["success"] is False
    assert entry["error"] == "circle out of bounds; partial_metrics={}"


def test_tool_end_merges_last_tool_extras() -> None:
    """Tools that set ``ctx.last_tool_extras`` get those fields merged
    into the trace entry."""
    hooks = ShinkaAgentHooks()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    wrap = _ctx_wrapper(ctx)

    asyncio.run(hooks.on_tool_start(wrap, agent=None, tool=_tool("apply_patch")))
    ctx.last_tool_extras = {
        "patch_type": "diff",
        "num_applied": 2,
    }
    asyncio.run(
        hooks.on_tool_end(
            wrap,
            agent=None,
            tool=_tool("apply_patch"),
            result="OK: applied 2 changes",
        )
    )

    entry = ctx.tool_call_trace[0]
    assert entry["patch_type"] == "diff"
    assert entry["num_applied"] == 2
    # And extras is cleared so the next tool doesn't reuse them.
    assert ctx.last_tool_extras is None


def test_tool_start_clears_stale_extras() -> None:
    """A stale ``last_tool_extras`` from a previous error path must not
    leak into the next tool's trace entry."""
    hooks = ShinkaAgentHooks()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    ctx.last_tool_extras = {"stale": True}
    wrap = _ctx_wrapper(ctx)

    asyncio.run(hooks.on_tool_start(wrap, agent=None, tool=_tool("apply_patch")))
    # The second tool didn't set extras.
    asyncio.run(
        hooks.on_tool_end(
            wrap, agent=None, tool=_tool("apply_patch"), result="OK: ok"
        )
    )

    entry = ctx.tool_call_trace[0]
    assert "stale" not in entry


def test_sequential_calls_match_starts_to_ends_fifo() -> None:
    hooks = ShinkaAgentHooks()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    wrap = _ctx_wrapper(ctx)

    asyncio.run(hooks.on_tool_start(wrap, agent=None, tool=_tool("apply_patch")))
    asyncio.run(
        hooks.on_tool_end(
            wrap, agent=None, tool=_tool("apply_patch"), result="OK: 1"
        )
    )
    asyncio.run(hooks.on_tool_start(wrap, agent=None, tool=_tool("evaluate")))
    asyncio.run(
        hooks.on_tool_end(
            wrap,
            agent=None,
            tool=_tool("evaluate"),
            result="OK: combined_score=0.5; correct=True; details={}",
        )
    )

    assert [e["name"] for e in ctx.tool_call_trace] == ["apply_patch", "evaluate"]
    assert all(e["success"] for e in ctx.tool_call_trace)


def test_non_string_result_defaults_to_success() -> None:
    """A tool that returns something other than a string is treated as
    success by the classifier — the per-tool semantics live in the
    tool, not the hook."""
    hooks = ShinkaAgentHooks()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    wrap = _ctx_wrapper(ctx)

    asyncio.run(hooks.on_tool_start(wrap, agent=None, tool=_tool("weird")))
    asyncio.run(
        hooks.on_tool_end(
            wrap, agent=None, tool=_tool("weird"), result={"k": "v"}
        )
    )
    entry = ctx.tool_call_trace[0]
    assert entry["success"] is True


def test_unclassified_string_assumes_success() -> None:
    """JSON return values from ``query_evolution_db_tool`` start with
    ``{`` — not ``OK``. The classifier's escape hatch treats them as
    success rather than failure."""
    success, error = _classify_result('{"rows": []}')
    assert success is True
    assert error is None


def test_ctx_not_a_shinka_context_is_ignored() -> None:
    """If the SDK's context isn't a ShinkaToolContext (e.g. someone
    re-uses the hooks on a different agent), the hook is a no-op
    rather than crashing."""
    hooks = ShinkaAgentHooks()
    wrap = SimpleNamespace(context="not a ShinkaToolContext")

    # Should not raise.
    asyncio.run(hooks.on_tool_start(wrap, agent=None, tool=_tool("apply_patch")))
    asyncio.run(
        hooks.on_tool_end(
            wrap, agent=None, tool=_tool("apply_patch"), result="OK"
        )
    )

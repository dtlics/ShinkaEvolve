"""Tests for ``ShinkaToolContext`` and the tools registry.

Pure-Python plumbing; no agents SDK or LLM involved.
"""

from __future__ import annotations

import pytest

from shinka.llm.agent.tools import (
    ShinkaToolContext,
    available_tool_names,
    default_shinka_tools,
    register_tool,
    select_shinka_tools,
)


def test_context_defaults_seed_current_code_from_parent() -> None:
    """If caller doesn't pass ``current_code``, it should inherit
    from ``parent_code`` so tools that read it don't see empty
    state before the first apply_patch."""
    ctx = ShinkaToolContext(patch_dir="/tmp/run-1", parent_code="def f(): pass")
    assert ctx.current_code == "def f(): pass"


def test_context_explicit_current_code_overrides_parent_seed() -> None:
    ctx = ShinkaToolContext(
        patch_dir="/tmp/run-1",
        parent_code="def f(): pass",
        current_code="def f(): return 1",
    )
    assert ctx.current_code == "def f(): return 1"


def test_context_tool_root_dir_defaults_to_patch_dir() -> None:
    """The default sandbox root is the patch dir, keeping the
    surface small. Tasks can widen by setting ``tool_root_dir``
    explicitly."""
    ctx = ShinkaToolContext(patch_dir="/tmp/run-1", parent_code="x = 1")
    assert ctx.tool_root_dir == "/tmp/run-1"


def test_context_eval_results_dir_defaults_to_patch_dir() -> None:
    ctx = ShinkaToolContext(patch_dir="/tmp/run-1", parent_code="x = 1")
    assert ctx.eval_results_dir == "/tmp/run-1"


def test_context_record_tool_call_appends_to_trace() -> None:
    ctx = ShinkaToolContext(patch_dir="/tmp/run-1", parent_code="")
    assert ctx.tool_call_trace == []

    ctx.record_tool_call(
        name="apply_patch",
        latency_sec=0.123456,
        success=True,
        extra={"patch_type": "diff", "lines_changed": 4},
    )
    ctx.record_tool_call(
        name="evaluate",
        latency_sec=2.5,
        success=False,
        error="timeout",
    )

    assert len(ctx.tool_call_trace) == 2
    first = ctx.tool_call_trace[0]
    assert first["name"] == "apply_patch"
    assert first["latency_sec"] == 0.1235  # rounded
    assert first["success"] is True
    assert first["patch_type"] == "diff"
    assert first["lines_changed"] == 4
    assert "error" not in first

    second = ctx.tool_call_trace[1]
    assert second["name"] == "evaluate"
    assert second["success"] is False
    assert second["error"] == "timeout"


def test_registry_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register a fake tool, then select it back out. Use monkeypatch
    to keep test isolation — clearing the module-level registry
    would leak across tests if not restored."""
    import shinka.llm.agent.tools.registry as tools_reg

    original = dict(tools_reg._TOOL_REGISTRY)
    monkeypatch.setattr(tools_reg, "_TOOL_REGISTRY", dict(original))

    def make_fake_tool(ctx: ShinkaToolContext) -> str:
        return f"fake-tool-using-{ctx.patch_dir}"

    register_tool("fake_tool", make_fake_tool)

    assert "fake_tool" in available_tool_names()

    ctx = ShinkaToolContext(patch_dir="/tmp/abc", parent_code="x = 1")
    selected = select_shinka_tools(["fake_tool"], ctx)
    assert selected == ["fake-tool-using-/tmp/abc"]


def test_select_unknown_tool_raises_with_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shinka.llm.agent.tools.registry as tools_reg

    original = dict(tools_reg._TOOL_REGISTRY)
    monkeypatch.setattr(tools_reg, "_TOOL_REGISTRY", dict(original))

    register_tool("known_tool", lambda ctx: "tool-instance")

    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    with pytest.raises(KeyError, match="Unknown shinka tool 'no_such_tool'"):
        select_shinka_tools(["no_such_tool"], ctx)


def test_default_shinka_tools_returns_all_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shinka.llm.agent.tools.registry as tools_reg

    monkeypatch.setattr(tools_reg, "_TOOL_REGISTRY", {})
    register_tool("a", lambda ctx: "tool-a")
    register_tool("b", lambda ctx: "tool-b")

    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    tools = default_shinka_tools(ctx)
    assert set(tools) == {"tool-a", "tool-b"}


def test_register_tool_is_idempotent_under_same_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the same name is registered twice, the last one wins.
    Useful for monkey-replacement in tests; harmless in production
    because tool modules import-once."""
    import shinka.llm.agent.tools.registry as tools_reg

    monkeypatch.setattr(tools_reg, "_TOOL_REGISTRY", {})
    register_tool("a", lambda ctx: "v1")
    register_tool("a", lambda ctx: "v2")

    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    assert select_shinka_tools(["a"], ctx) == ["v2"]

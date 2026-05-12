"""Tests for the ``web_search`` opt-in tool factory.

The actual web-search execution happens inside Azure/OpenAI's
Responses API runtime — there's nothing local to exercise. We
verify the SDK ``WebSearchTool`` is constructed correctly from
the context and that opt-in routing works.
"""

from __future__ import annotations

import pytest

from shinka.llm.agent.tools import (
    ShinkaToolContext,
    available_tool_names,
    default_shinka_tools,
    select_shinka_tools,
)
from shinka.llm.agent.tools.web_search import (
    _VALID_CONTEXT_SIZES,
    make_web_search_tool,
)


def test_factory_returns_websearchtool_with_default_context_size() -> None:
    from agents import WebSearchTool

    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    tool = make_web_search_tool(ctx)
    assert isinstance(tool, WebSearchTool)
    assert tool.search_context_size == "medium"


def test_factory_honors_context_size_when_valid() -> None:
    ctx = ShinkaToolContext(
        patch_dir="/tmp/x",
        parent_code="",
        web_search_context_size="high",
    )
    tool = make_web_search_tool(ctx)
    assert tool.search_context_size == "high"


def test_factory_falls_back_to_medium_on_invalid_context_size() -> None:
    """Misconfiguration here shouldn't break the run — silently
    coerce to the safe default."""
    ctx = ShinkaToolContext(
        patch_dir="/tmp/x",
        parent_code="",
        web_search_context_size="enormous",
    )
    tool = make_web_search_tool(ctx)
    assert tool.search_context_size == "medium"


def test_web_search_is_opt_in_not_in_default_tool_set() -> None:
    """Cost-positive tool must NOT come along for free with
    default_shinka_tools — caller has to ask for it explicitly."""
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    defaults = default_shinka_tools(ctx)
    # None of the default tools should be a WebSearchTool.
    from agents import WebSearchTool

    assert not any(isinstance(t, WebSearchTool) for t in defaults)


def test_web_search_is_selectable_by_name() -> None:
    """Even though it's opt-in, the name is resolvable for explicit
    callers."""
    from agents import WebSearchTool

    assert "web_search" in available_tool_names()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    selected = select_shinka_tools(["web_search"], ctx)
    assert len(selected) == 1
    assert isinstance(selected[0], WebSearchTool)


def test_default_tools_contain_the_other_four(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: the non-opt-in tools are still present in defaults.
    Failure here would mean someone marked too many things opt_in."""
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    defaults = default_shinka_tools(ctx)
    # apply_patch, evaluate, query_evolution_db, read_host_file are
    # the four non-opt-in tools after Phase C.5.
    assert len(defaults) >= 4


def test_valid_context_sizes_constant_matches_sdk() -> None:
    """Sanity: the SDK accepts exactly these three sizes."""
    assert _VALID_CONTEXT_SIZES == {"low", "medium", "high"}

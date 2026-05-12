"""Tests for ``apply_patch_tool``.

We exercise ``_apply_patch_impl`` directly with a mocked
``apply_patch_async`` so we don't need a real working directory or
diff parser. The decorated ``_apply_patch_tool`` is verified
separately (1 smoke test) to confirm it correctly threads the
context through ``RunContextWrapper``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from shinka.llm.agent.tools import ShinkaToolContext
from shinka.llm.agent.tools.apply_patch import (
    _apply_patch_impl,
    _apply_patch_tool,
    _VALID_PATCH_TYPES,
)


def _ctx() -> ShinkaToolContext:
    return ShinkaToolContext(
        patch_dir="/tmp/gen-0",
        parent_code="def f():\n    return 1\n",
    )


def test_success_updates_current_code_and_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _ctx()
    new_code = "def f():\n    return 2\n"
    mock_apply = AsyncMock(
        return_value=(new_code, 1, "/tmp/gen-0/evolve.py", None, "patch", "/tmp/gen-0/patch.diff")
    )
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    result = asyncio.run(_apply_patch_impl(state, "diff content here"))

    assert result.startswith("OK: applied 1 change")
    assert state.current_code == new_code
    assert len(state.tool_call_trace) == 1
    trace = state.tool_call_trace[0]
    assert trace["name"] == "apply_patch"
    assert trace["success"] is True
    assert trace["patch_type"] == "diff"
    assert trace["num_applied"] == 1
    assert trace["output_path"] == "/tmp/gen-0/evolve.py"


def test_invalid_patch_type_short_circuits_without_calling_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _ctx()
    mock_apply = AsyncMock()
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    result = asyncio.run(
        _apply_patch_impl(state, "some content", patch_type="wat")
    )

    assert result.startswith("Error: Invalid patch_type")
    # Validation happens before we touch the underlying function.
    mock_apply.assert_not_awaited()
    # Original code unchanged.
    assert state.current_code == "def f():\n    return 1\n"
    # Failure was recorded.
    assert state.tool_call_trace[0]["success"] is False
    assert "Invalid patch_type" in state.tool_call_trace[0]["error"]


def test_patch_application_returns_error_keeps_current_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``apply_patch_async`` reports an error_msg (e.g. malformed
    diff), the tool returns the error and leaves ``current_code``
    unchanged so the agent can fix the patch on next turn."""
    state = _ctx()
    original = state.current_code
    mock_apply = AsyncMock(
        return_value=(None, 0, None, "could not parse diff hunk", None, None)
    )
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    result = asyncio.run(_apply_patch_impl(state, "garbage diff"))

    assert result == "Error: could not parse diff hunk"
    assert state.current_code == original
    assert state.tool_call_trace[0]["success"] is False
    assert state.tool_call_trace[0]["error"] == "could not parse diff hunk"


def test_apply_patch_raises_is_caught_and_returned_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the underlying call throws (e.g. filesystem error), we
    catch and return as a tool error rather than letting the
    exception bubble up into the agent loop."""
    state = _ctx()
    original = state.current_code
    mock_apply = AsyncMock(side_effect=OSError("disk full"))
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    result = asyncio.run(_apply_patch_impl(state, "diff"))

    assert result == "Error: disk full"
    assert state.current_code == original
    assert state.tool_call_trace[0]["success"] is False
    assert "disk full" in state.tool_call_trace[0]["error"]


def test_multiple_successful_applies_chain_current_code_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent typically calls apply_patch multiple times. The second
    call should operate on the code produced by the first."""
    state = _ctx()
    state.current_code = "v1"

    # Each call updates current_code; the mock simulates by returning
    # whatever code we want.
    sequence = [
        ("v2", 1, "/tmp/gen-0/evolve.py", None, "p1", "/tmp/gen-0/patch1.diff"),
        ("v3", 2, "/tmp/gen-0/evolve.py", None, "p2", "/tmp/gen-0/patch2.diff"),
    ]
    mock_apply = AsyncMock(side_effect=sequence)
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    asyncio.run(_apply_patch_impl(state, "first patch"))
    assert state.current_code == "v2"

    asyncio.run(_apply_patch_impl(state, "second patch"))
    assert state.current_code == "v3"

    # Each call should have received the previous current_code as
    # original_str (chained).
    first_call = mock_apply.await_args_list[0]
    second_call = mock_apply.await_args_list[1]
    assert first_call.kwargs["original_str"] == "v1"
    assert second_call.kwargs["original_str"] == "v2"

    # Trace has two entries, both successful.
    assert len(state.tool_call_trace) == 2
    assert all(t["success"] for t in state.tool_call_trace)


def test_valid_patch_types_constant() -> None:
    """Sanity: the validation accepts exactly the types apply_patch_async
    knows how to handle."""
    assert _VALID_PATCH_TYPES == {"diff", "full", "cross"}


def test_decorated_tool_is_registered_under_apply_patch_name() -> None:
    """The module's top-level register_tool call should have made
    apply_patch_tool selectable by name."""
    from shinka.llm.agent.tools import (
        available_tool_names,
        select_shinka_tools,
        ShinkaToolContext,
    )

    assert "apply_patch" in available_tool_names()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    selected = select_shinka_tools(["apply_patch"], ctx)
    assert len(selected) == 1
    assert selected[0] is _apply_patch_tool


def test_function_tool_schema_excludes_ctx_param() -> None:
    """The agents-SDK decorator should auto-strip the ctx parameter
    from the JSON schema, exposing only ``patch_text`` and
    ``patch_type`` to the LLM."""
    schema = _apply_patch_tool.params_json_schema
    properties = schema.get("properties", {})
    assert set(properties.keys()) == {"patch_text", "patch_type"}
    assert "ctx" not in properties

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
    # Tool surfaces structured per-call data on last_tool_extras;
    # ShinkaAgentHooks.on_tool_end merges this into tool_call_trace
    # when called inside the agent loop (not exercised in unit tests).
    extras = state.last_tool_extras
    assert extras is not None
    assert extras["patch_type"] == "diff"
    assert extras["num_applied"] == 1
    assert extras["output_path"] == "/tmp/gen-0/evolve.py"
    # All last_successful_* fields are set on success so the
    # orchestrator can read them after the agent run.
    assert state.last_successful_patch_text == "patch"
    assert state.last_successful_patch_type == "diff"
    assert state.last_successful_num_applied == 1
    assert state.last_successful_patch_path == "/tmp/gen-0/patch.diff"


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
    # No structured extras for the invalid-input early return.
    assert state.last_tool_extras is None


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
    # Structured failure metadata for the hooks layer to attach.
    assert state.last_tool_extras == {"patch_type": "diff"}


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
    # No extras when the underlying call raises before we have any
    # structured data to surface.
    assert state.last_tool_extras is None


def test_no_changes_applied_is_treated_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A patch that parses cleanly but produces no actual change
    (num_applied == 0, no error_msg) must surface as an error so the
    agent retries — matching _run_patch_async's contract. Without
    this, an no-op diff would falsely flip ctx.last_successful_*
    and the orchestrator would persist an empty proposal."""
    state = _ctx()
    original = state.current_code
    # apply_patch_async returns clean (no error) but 0 changes and no
    # modified_code — the "patch parsed but did nothing" case.
    mock_apply = AsyncMock(return_value=(None, 0, None, None, None, None))
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    result = asyncio.run(_apply_patch_impl(state, "no-op diff"))

    assert result.startswith("Error:")
    assert "No changes applied" in result
    # current_code untouched.
    assert state.current_code == original
    # last_successful_* must NOT be set.
    assert state.last_successful_patch_text is None
    assert state.last_successful_num_applied == 0
    # Failure metadata exposed for the hooks layer.
    assert state.last_tool_extras == {"patch_type": "diff", "num_applied": 0}


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

    # The most recent call's extras win (second apply's data).
    assert state.last_tool_extras is not None
    assert state.last_tool_extras["num_applied"] == 2


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

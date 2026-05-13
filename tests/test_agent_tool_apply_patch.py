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


# ----------------------------------------------------------------------
# Doom-remediation Fix 1: auto-eval inside apply_patch.
# Every successful apply runs the evaluator and the result is appended
# to the tool return so the agent sees apply + eval in a single
# response, never has to call ``evaluate`` itself, and the
# ``last_eval_result`` field is structurally guaranteed fresh for the
# orchestrator's cache-and-skip path.
# ----------------------------------------------------------------------


def test_auto_eval_after_successful_apply_appends_eval_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful apply + successful eval → tool return contains both
    the apply message AND the EVAL section. ``ctx.last_eval_result``
    is populated by the evaluator path."""
    state = _ctx()
    mock_apply = AsyncMock(
        return_value=(
            "def f():\n    return 2\n",
            1,
            "/tmp/gen-0/evolve.py",
            None,
            "patch",
            "/tmp/gen-0/patch.diff",
        )
    )
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    # Wire an evaluator that returns (metrics, correct=True, first_error=None).
    eval_metrics = {"combined_score": 0.87, "execution_time_mean": 0.42}
    eval_results = {"correct": {"correct": True}, "metrics": eval_metrics}

    async def fake_evaluator():
        # Production _make_agent_evaluator sets last_eval_result before
        # returning; mirror that contract here.
        state.last_eval_result = eval_results
        state.last_eval_rtime = 0.5
        return eval_metrics, True, None

    state.evaluator = fake_evaluator

    result = asyncio.run(_apply_patch_impl(state, "diff content"))

    # Both apply OK and EVAL section present.
    assert result.startswith("OK: applied 1 change")
    assert "EVAL: " in result
    assert "OK: combined_score=0.87" in result
    assert "correct=True" in result

    # ctx.last_eval_result is set (fresh — orchestrator can trust the cache).
    assert state.last_eval_result is eval_results

    # extras carry the eval signal for fix_telemetry to consume.
    extras = state.last_tool_extras
    assert extras is not None
    assert extras["patch_type"] == "diff"
    assert extras["num_applied"] == 1
    assert extras["eval_correct"] is True
    assert extras["eval_combined_score"] == 0.87


def test_auto_eval_failed_eval_marks_eval_correct_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful apply + failed eval (correct=False) → tool return
    is ``"OK: applied ... \\nEVAL: FAILED: ..."``; the hook will
    classify the apply itself as successful (prefix=OK), but the
    extras record ``eval_correct=False`` so fix_telemetry sees the
    failure correctly."""
    state = _ctx()
    mock_apply = AsyncMock(
        return_value=(
            "def f():\n    raise ValueError('bad')\n",
            1,
            "/tmp/gen-0/evolve.py",
            None,
            "patch",
            "/tmp/gen-0/patch.diff",
        )
    )
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    async def fake_evaluator():
        state.last_eval_result = {
            "correct": {"correct": False},
            "metrics": {"combined_score": 0.0},
        }
        state.last_eval_rtime = 0.3
        return {"combined_score": 0.0}, False, "Validation failed: bad"

    state.evaluator = fake_evaluator

    result = asyncio.run(_apply_patch_impl(state, "broken diff"))

    assert result.startswith("OK: applied 1 change")  # apply still succeeded
    assert "EVAL: FAILED: " in result
    assert "Validation failed: bad" in result

    extras = state.last_tool_extras
    assert extras is not None
    assert extras["eval_correct"] is False
    assert extras["eval_combined_score"] == 0.0


def test_failed_apply_does_not_run_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When apply itself fails (parse error, etc.), no eval runs —
    there's no new code on disk to evaluate. Verify the evaluator
    callable is never invoked."""
    state = _ctx()
    mock_apply = AsyncMock(
        return_value=(None, 0, None, "Could not parse diff hunk", None, None)
    )
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    fake_evaluator = AsyncMock()
    state.evaluator = fake_evaluator

    result = asyncio.run(_apply_patch_impl(state, "malformed"))

    assert result.startswith("Error:")
    assert "Could not parse diff hunk" in result
    fake_evaluator.assert_not_awaited()
    # current_code is unchanged on apply failure.
    assert state.current_code == "def f():\n    return 1\n"
    # last_eval_result stays None — no eval ran.
    assert state.last_eval_result is None


def test_apply_without_evaluator_returns_only_apply_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``ctx.evaluator is None`` (some unit-test contexts, or any
    runner path that doesn't wire one), the tool returns just the
    apply success message — no EVAL section, no crash. Graceful
    degradation."""
    state = _ctx()
    # state.evaluator stays None (the default).
    mock_apply = AsyncMock(
        return_value=(
            "def f():\n    return 2\n",
            1,
            "/tmp/gen-0/evolve.py",
            None,
            "patch",
            "/tmp/gen-0/patch.diff",
        )
    )
    monkeypatch.setattr(
        "shinka.llm.agent.tools.apply_patch.apply_patch_async", mock_apply
    )

    result = asyncio.run(_apply_patch_impl(state, "diff content"))

    assert result.startswith("OK: applied 1 change")
    assert "EVAL:" not in result  # no eval section appended
    # No eval extras either; the apply-only path doesn't fabricate them.
    extras = state.last_tool_extras
    assert extras is not None
    assert "eval_correct" not in extras
    assert "eval_combined_score" not in extras


def test_fix_telemetry_reads_eval_outcomes_from_apply_patch_entries() -> None:
    """After Fix 1 the trace has only ``apply_patch`` entries (no
    separate ``evaluate``). fix_telemetry must read ``eval_correct``
    from the extras merged into each apply entry."""
    from shinka.core.async_runner import _summarize_fix_telemetry

    # Apply 1 succeeded but eval failed; apply 2 succeeded and eval
    # passed. Classic "fixed within the loop" pattern, expressed in
    # the new trace shape (no evaluate entries).
    trace = [
        {
            "name": "apply_patch",
            "success": True,
            "patch_type": "diff",
            "num_applied": 1,
            "eval_correct": False,
            "eval_combined_score": 0.0,
        },
        {
            "name": "apply_patch",
            "success": True,
            "patch_type": "diff",
            "num_applied": 1,
            "eval_correct": True,
            "eval_combined_score": 0.85,
        },
    ]
    summary = _summarize_fix_telemetry(trace)
    assert summary == {
        "apply_attempts": 2,
        "eval_attempts": 2,
        "had_failure_then_success": True,
        "final_correct": True,
    }


def test_fix_telemetry_apply_without_eval_does_not_count_as_eval_attempt() -> None:
    """When apply failed (no eval ran), the trace entry has no
    ``eval_correct`` key. fix_telemetry must NOT count it as an
    eval attempt."""
    from shinka.core.async_runner import _summarize_fix_telemetry

    trace = [
        # Apply failed → eval never ran → no eval_correct field.
        {"name": "apply_patch", "success": False, "error": "parse failure"},
        # Apply succeeded, eval ran and passed.
        {
            "name": "apply_patch",
            "success": True,
            "patch_type": "diff",
            "num_applied": 1,
            "eval_correct": True,
            "eval_combined_score": 0.9,
        },
    ]
    summary = _summarize_fix_telemetry(trace)
    assert summary == {
        "apply_attempts": 2,  # both apply calls count
        "eval_attempts": 1,   # only the second produced an eval result
        "had_failure_then_success": False,  # no failing eval to fix
        "final_correct": True,
    }

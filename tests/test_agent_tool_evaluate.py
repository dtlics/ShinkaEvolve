"""Tests for ``evaluate_tool``.

The tool delegates to a pre-bound evaluator callable on
``ShinkaToolContext.evaluator``; we don't exercise the real
``run_shinka_eval`` here. Tests cover the formatting of the agent-
facing return string for success, validation failure, infrastructure
failure (evaluator raises), missing evaluator (None), and
metrics-JSON truncation.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Tuple
from unittest.mock import AsyncMock

import pytest

from shinka.llm.agent.tools import ShinkaToolContext
from shinka.llm.agent.tools.evaluate import (
    _MAX_METRICS_JSON_CHARS,
    _compact_metrics_json,
    _evaluate_impl,
    _evaluate_tool,
)


def _ctx(evaluator: Optional[Any] = None) -> ShinkaToolContext:
    return ShinkaToolContext(
        patch_dir="/tmp/run-1",
        parent_code="x = 1\n",
        evaluator=evaluator,
    )


def test_returns_error_when_evaluator_not_bound() -> None:
    """A run without an evaluator shouldn't crash the agent loop;
    the tool returns a clear error message instead."""
    state = _ctx(evaluator=None)
    result = asyncio.run(_evaluate_impl(state))
    assert result.startswith("Error:")
    assert "not configured" in result
    assert state.tool_call_trace[0]["success"] is False
    assert state.tool_call_trace[0]["error"] == "no_evaluator_bound"


def test_success_path_returns_score_and_metrics_json() -> None:
    metrics: Dict[str, Any] = {
        "combined_score": 0.85,
        "execution_time_mean": 0.42,
        "num_successful_runs": 3,
    }
    evaluator = AsyncMock(return_value=(metrics, True, None))
    state = _ctx(evaluator=evaluator)

    result = asyncio.run(_evaluate_impl(state))

    assert result.startswith("OK:")
    assert "combined_score=0.85" in result
    assert "correct=True" in result
    # Verify details JSON is embedded and parseable from the prefix.
    details_str = result.split("details=", 1)[1]
    parsed = json.loads(details_str)
    assert parsed["combined_score"] == 0.85
    assert parsed["num_successful_runs"] == 3

    trace = state.tool_call_trace[0]
    assert trace["name"] == "evaluate"
    assert trace["success"] is True
    assert trace["combined_score"] == 0.85
    assert "metrics_keys" in trace


def test_failed_validation_returns_failed_prefix() -> None:
    """When the program ran but ``correct=False``, surface the error
    so the agent can decide whether to patch further."""
    metrics: Dict[str, Any] = {
        "combined_score": 0.1,
        "num_invalid_runs": 5,
    }
    evaluator = AsyncMock(return_value=(metrics, False, "circle out of bounds"))
    state = _ctx(evaluator=evaluator)

    result = asyncio.run(_evaluate_impl(state))

    assert result.startswith("FAILED:")
    assert "circle out of bounds" in result
    assert "partial_metrics=" in result
    # The trace records success=False (because correct=False).
    assert state.tool_call_trace[0]["success"] is False
    assert state.tool_call_trace[0]["error"] == "circle out of bounds"


def test_evaluator_raises_is_caught_and_returned_as_error() -> None:
    """If the bound evaluator itself raises (e.g. ProcessPoolExecutor
    crash, OSError), the tool must not propagate — it returns the
    error string for the agent to read."""
    evaluator = AsyncMock(side_effect=OSError("eval subprocess died"))
    state = _ctx(evaluator=evaluator)

    result = asyncio.run(_evaluate_impl(state))

    assert result == "Error: eval subprocess died"
    assert state.tool_call_trace[0]["success"] is False
    assert "eval subprocess died" in state.tool_call_trace[0]["error"]


def test_non_dict_metrics_does_not_crash_serializer() -> None:
    """If the evaluator returns something weird in the metrics slot,
    we still produce a valid response rather than crash."""
    evaluator = AsyncMock(return_value=("not a dict", True, None))
    state = _ctx(evaluator=evaluator)

    result = asyncio.run(_evaluate_impl(state))
    # Should not raise. combined_score is None when metrics isn't a dict.
    assert result.startswith("OK:")
    assert "combined_score=None" in result


def test_failed_with_none_error_falls_back_to_validation_failed() -> None:
    """correct=False + first_error=None should still surface a
    sensible error string."""
    evaluator = AsyncMock(return_value=({"combined_score": 0.0}, False, None))
    state = _ctx(evaluator=evaluator)

    result = asyncio.run(_evaluate_impl(state))
    assert result.startswith("FAILED:")
    assert "validation failed" in result


def test_metrics_json_truncation_when_too_large() -> None:
    """Pathologically large metrics dicts should be truncated to keep
    LLM context manageable."""
    huge: Dict[str, Any] = {"k_%d" % i: i for i in range(10000)}
    encoded = _compact_metrics_json(huge)
    assert len(encoded) <= _MAX_METRICS_JSON_CHARS
    assert encoded.endswith("...truncated")


def test_metrics_json_handles_non_serializable_values() -> None:
    """Non-JSON values (e.g. numpy arrays in real eval output) become
    their repr instead of crashing."""

    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    encoded = _compact_metrics_json({"x": Weird(), "y": 1.5})
    parsed = json.loads(encoded)
    assert parsed["y"] == 1.5
    assert parsed["x"] == "<weird>"


def test_decorated_evaluate_tool_registered() -> None:
    from shinka.llm.agent.tools import available_tool_names, select_shinka_tools

    assert "evaluate" in available_tool_names()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    selected = select_shinka_tools(["evaluate"], ctx)
    assert selected == [_evaluate_tool]


def test_evaluate_tool_schema_only_exposes_no_args() -> None:
    """The evaluate tool takes no agent-facing args; the schema should
    have no properties to fill in."""
    schema = _evaluate_tool.params_json_schema
    properties = schema.get("properties", {})
    # No agent-facing args required beyond the implicit ctx.
    assert properties == {} or set(properties.keys()) == set()

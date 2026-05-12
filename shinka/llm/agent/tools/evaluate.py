"""``evaluate_tool`` — run the task's evaluator against the current program.

The agent calls this after ``apply_patch_tool`` has produced a
candidate solution. The tool invokes a pre-bound evaluator callable
that the orchestrator stashed on ``ShinkaToolContext.evaluator`` (the
orchestrator already knows the task's program path, results dir,
``aggregate_metrics_fn``, ``validate_fn``, etc.). The agent doesn't
need to know any of that — it just calls ``evaluate()`` and reads
back the score.

This is where the cost-per-generation can climb: evaluations may take
seconds to many minutes depending on the task. The agent should call
this sparingly — typically once per generation, after it's confident
in a candidate. ``max_turns`` in the runner config bounds runaway
loops.

Return format
-------------
Always a single string the LLM can read on its next turn:

* On success:
  ``"OK: combined_score=<float>; correct=<bool>; details=<json>"``
  where ``details`` is a compact JSON of public metrics (e.g.
  ``{"execution_time_mean": 0.42, "num_successful_runs": 3}``).

* On evaluation error (validation failed, program crashed):
  ``"FAILED: <error message>; partial_metrics=<json>"``

* On infrastructure error (no evaluator bound, tool exception):
  ``"Error: <message>"``

The agent should use combined_score to decide whether further patches
are warranted. A high score with ``correct=true`` signals the
generation is ready to be reported as the final state; a low score
or ``correct=false`` is feedback for the next ``apply_patch_tool``
call.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

from agents import RunContextWrapper, function_tool

from .context import ShinkaToolContext
from .registry import register_tool

logger = logging.getLogger(__name__)


# Cap on how much metrics JSON we surface to the LLM. Large metric
# dicts (e.g., per-run breakdowns) can blow up context cheaply.
_MAX_METRICS_JSON_CHARS = 2000


async def _evaluate_impl(state: ShinkaToolContext) -> str:
    """Pure tool body; unit-test target."""
    start = time.monotonic()

    if state.evaluator is None:
        latency = time.monotonic() - start
        msg = (
            "Evaluator is not configured for this run. Apply your patch "
            "and report your reasoning; scoring will be performed by "
            "the orchestrator."
        )
        state.record_tool_call(
            "evaluate", latency, success=False, error="no_evaluator_bound"
        )
        return f"Error: {msg}"

    try:
        metrics, correct, first_error = await state.evaluator()
    except Exception as exc:
        latency = time.monotonic() - start
        logger.info("evaluate tool raised: %s", exc)
        state.record_tool_call(
            "evaluate", latency, success=False, error=str(exc)
        )
        return f"Error: {exc}"

    latency = time.monotonic() - start
    combined_score = metrics.get("combined_score") if isinstance(metrics, dict) else None

    state.record_tool_call(
        "evaluate",
        latency,
        success=correct,
        error=first_error,
        extra={
            "combined_score": combined_score,
            "metrics_keys": (
                sorted(metrics.keys()) if isinstance(metrics, dict) else None
            ),
        },
    )

    metrics_json = _compact_metrics_json(metrics)

    if not correct:
        err = first_error or "validation failed"
        return f"FAILED: {err}; partial_metrics={metrics_json}"

    return f"OK: combined_score={combined_score}; correct=True; details={metrics_json}"


def _compact_metrics_json(metrics: Any) -> str:
    """Render metrics as a one-line JSON, truncating to keep the agent's
    context manageable. Non-serializable values become their repr."""
    if not isinstance(metrics, dict):
        return json.dumps({"_raw": repr(metrics)[:200]})

    def _default(obj: Any) -> Any:
        return repr(obj)

    try:
        encoded = json.dumps(metrics, default=_default, separators=(",", ":"))
    except Exception:  # belt-and-suspenders; default= should cover most
        encoded = json.dumps({"_raw": repr(metrics)[:200]})

    if len(encoded) > _MAX_METRICS_JSON_CHARS:
        encoded = encoded[: _MAX_METRICS_JSON_CHARS - 12] + "...truncated"
    return encoded


@function_tool
async def _evaluate_tool(
    ctx: RunContextWrapper[ShinkaToolContext],
) -> str:
    """Run the task's evaluator against the currently-patched program.

    Call this after you've applied a patch you believe is an
    improvement. The evaluator runs the patched program (possibly
    multiple times depending on the task config), validates the
    output, computes a combined_score, and returns the result.

    Use the score to decide whether more patches are warranted. Be
    judicious — each evaluation can take from seconds to many
    minutes depending on the task. One or two calls per generation
    is typical.

    Returns:
        ``"OK: combined_score=...; correct=True; details=..."`` on
        success, ``"FAILED: <error>; partial_metrics=..."`` if the
        program ran but failed validation, or ``"Error: <message>"``
        on infrastructure failure.
    """
    return await _evaluate_impl(ctx.context)


def make_evaluate_tool(ctx: ShinkaToolContext) -> Any:
    """Factory returns the shared decorated tool. Context is read at
    call time via ``RunContextWrapper``."""
    return _evaluate_tool


register_tool("evaluate", make_evaluate_tool)

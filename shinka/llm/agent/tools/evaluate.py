"""``evaluate_tool`` — manual evaluator invocation. Rarely needed.

**Doom-remediation Fix 1** made every successful ``apply_patch_tool``
auto-evaluate the resulting code and return the eval result in its
own response. The agent therefore does not need to call ``evaluate``
explicitly during a normal apply→fix→apply loop.

This tool remains registered for edge cases — re-evaluating the same
code with different seeds, validating intermediate state without
applying another patch, etc. It is dropped from the default
``agentic_tools`` list in ``EvolutionConfig``; tasks that want manual
control can opt it back in via config.

The tool body lives on ``_evaluate_impl`` and is invoked from
``apply_patch_tool`` (auto-eval) and from the ``@function_tool``
wrapper below (manual). Both paths share the same per-call telemetry
contract.

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
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from agents import RunContextWrapper, function_tool

from .context import ShinkaToolContext
from .registry import register_tool

logger = logging.getLogger(__name__)


# Cap on how much metrics JSON we surface to the LLM. Large metric
# dicts (e.g., per-run breakdowns) can blow up context cheaply.
_MAX_METRICS_JSON_CHARS = 2000


async def _evaluate_impl(state: ShinkaToolContext) -> str:
    """Pure tool body; unit-test target.

    Telemetry: name + latency + success are recorded by
    ``ShinkaAgentHooks.on_tool_end``. The hook treats ``"FAILED:"``
    and ``"Error:"`` prefixes as failure. Structured per-call data
    (combined_score, metrics_keys) goes on ``state.last_tool_extras``.
    """
    if state.evaluator is None:
        return (
            "Error: Evaluator is not configured for this run. Apply your "
            "patch and report your reasoning; scoring will be performed "
            "by the orchestrator."
        )

    try:
        metrics, correct, first_error = await state.evaluator()
    except Exception as exc:
        logger.info("evaluate tool raised: %s", exc)
        return f"Error: {exc}"

    combined_score = metrics.get("combined_score") if isinstance(metrics, dict) else None
    state.last_tool_extras = {
        "combined_score": combined_score,
        "metrics_keys": (
            sorted(metrics.keys()) if isinstance(metrics, dict) else None
        ),
    }

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
    """Manually re-evaluate the current program. Rarely needed.

    ``apply_patch`` already auto-evaluates after every successful
    apply, so use this tool ONLY for edge cases like re-running the
    evaluator with different seeds or validating intermediate state
    without applying a new patch.

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

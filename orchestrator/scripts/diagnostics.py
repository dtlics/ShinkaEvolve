"""diagnostics.py — assemble the window-end diagnostics JSON.

MUTABILITY: IMMUTABLE PLUMBING (cell B). Do not modify as part of a strategy
rewrite. This is the orchestrator's only sensor; if it lies, every decision is
wrong. It embeds NO LLM call.

It reads the archive (best score, per-island health) via immutable plumbing,
calls the MUTABLE ``stagnation_detector.py`` for the J-score and stagnation flag,
and merges those with the per-window counters the harness accumulated
(novelty accept/reject, eval failures, bandit weights, exhausted retry slots).
The clean split: the *sensor* (this file) is fixed; the *J/flag computation* it
embeds is the one mutable piece, and it lives in its own file.

INPUT (stdin JSON): db_path/db_config/embedding_model + the per-window fields the
  harness accumulates: window_index, iters_completed, best_score_start, window_size,
  strategy_fingerprint, stagnation_abs_floor/stagnation_rel_frac (``tau`` is a
  DEPRECATED alias), prior_low_streak, consecutive_required, trigger_metric,
  novelty_accepts/novelty_rejects/novelty_rejected_cost, eval_failures/eval_total,
  fix_count/fix_success/needs_fix_count, llm_bandit_weights/llm_bandit_counts,
  exhausted_retry_slots (generation ids) and exhausted_retry_count.

OUTPUT (stdout JSON, "ok": true): window_index, iters_completed, best_score_start,
  best_score_end, delta, J_score (INFORMATIONAL only — rollback uses rollback_decision),
  threshold, strategy_fingerprint, novelty_acceptance_rate (NULL when no novelty events),
  novelty_rejected_cost, evaluation_failure_rate (post-repair), fix_rate,
  fix_success_rate, needs_fix_rate, llm_bandit_weights, llm_bandit_counts,
  island_health [{id, best, diversity, stagnation_count, count}], stagnation_flag,
  low_streak, exhausted_retry_slots, exhausted_retry_count, trigger_metric,
  total_programs, correct_programs. (run_window additionally attaches window_cost,
  total_cost, budget_remaining, budget_hit, windows_run, return_reason.)
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from . import _common
    from . import stagnation_detector
    from . import archive_query
    from . import island_policy
except ImportError:
    import _common  # type: ignore
    import stagnation_detector  # type: ignore
    import archive_query  # type: ignore
    import island_policy  # type: ignore


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    db_path = payload["db_path"]
    db_config = payload.get("db_config", {})
    embedding_model = payload.get("embedding_model", "text-embedding-3-small")

    # Read current archive state (best score + per-island health).
    summary = archive_query.main(
        {
            "db_path": db_path,
            "db_config": db_config,
            "embedding_model": embedding_model,
            "query_type": "summary",
        }
    )["result"]

    best_end = summary.get("best_score") or 0.0
    best_start = float(payload.get("best_score_start", 0.0) or 0.0)
    window_size = int(payload.get("window_size", 1) or 1)

    stag = stagnation_detector.main(
        {
            "best_score_start": best_start,
            "best_score_end": best_end,
            "window_size": window_size,
            "tau": payload.get("tau", 0.0),  # deprecated abs_floor alias
            "stagnation_abs_floor": payload.get("stagnation_abs_floor"),
            "stagnation_rel_frac": payload.get("stagnation_rel_frac"),
            "prior_low_streak": payload.get("prior_low_streak", 0),
            "consecutive_required": payload.get("consecutive_required", 2),
        }
    )

    accepts = int(payload.get("novelty_accepts", 0) or 0)
    rejects = int(payload.get("novelty_rejects", 0) or 0)
    nov_total = accepts + rejects
    # O10/K15: None when NO novelty events occurred (UNKNOWN), so rollback_decision
    # doesn't mistake "no data" for "perfectly diverse" (the F13 flood-detection
    # inversion). A real rate only when there were events.
    novelty_acceptance_rate = (accepts / nov_total) if nov_total else None

    eval_total = int(payload.get("eval_total", 0) or 0)
    eval_failures = int(payload.get("eval_failures", 0) or 0)
    evaluation_failure_rate = (eval_failures / eval_total) if eval_total else 0.0

    iters = int(payload.get("iters_completed", 0) or 0)
    fix_count = int(payload.get("fix_count", 0) or 0)
    fix_rate = (fix_count / iters) if iters else 0.0
    # WS1: with the IMMEDIATE-fix mechanism, fix_count = repair attempts made this
    # window and fix_success = attempts that recovered correctness. fix_success_rate
    # tells the orchestrator whether fixes actually WORK (SKILL ladder rung 5: a high
    # fix_rate with a low fix_success_rate => rewrite the fix concern). None when no
    # fix was attempted, so the orchestrator can distinguish "no fixes" from "0% worked".
    fix_success = int(payload.get("fix_success", 0) or 0)
    fix_success_rate = (fix_success / fix_count) if fix_count else None

    # M9: needs_fix parents (sampled INCORRECT parents routed to repair mode) are
    # counted separately from immediate-fix ATTEMPTS, so fix_success_rate stays a
    # coherent "immediate repairs that worked / immediate attempts" for ladder rung 5.
    needs_fix_count = int(payload.get("needs_fix_count", 0) or 0)
    needs_fix_rate = (needs_fix_count / iters) if iters else 0.0

    # Island health. The metric DEFINITION lives in the MUTABLE island_policy
    # (F10) so the orchestrator can later evolve "diversity"/"stagnation_count"
    # beyond the toy count default — the sensor (this file) just calls it, the
    # same way it delegates J/flag to the mutable stagnation_detector.
    island_health = island_policy.island_health(
        summary.get("islands", []),
        db_path=db_path,
        db_config=db_config,
        embedding_model=embedding_model,
    )

    return {
        "window_index": int(payload.get("window_index", 0) or 0),
        "iters_completed": int(payload.get("iters_completed", 0) or 0),
        "best_score_start": best_start,
        "best_score_end": float(best_end),
        "delta": stag["delta"],
        "J_score": stag["J_score"],
        "threshold": stag.get("threshold"),
        "current_strategy_hash": payload.get("current_strategy_hash"),  # deprecated
        "strategy_fingerprint": payload.get("strategy_fingerprint", {}),
        "novelty_acceptance_rate": novelty_acceptance_rate,
        "novelty_rejected_cost": float(payload.get("novelty_rejected_cost", 0.0) or 0.0),
        "evaluation_failure_rate": evaluation_failure_rate,
        "fix_rate": fix_rate,
        "fix_success_rate": fix_success_rate,
        "needs_fix_rate": needs_fix_rate,
        "llm_bandit_weights": payload.get("llm_bandit_weights", {}),
        "llm_bandit_counts": payload.get("llm_bandit_counts", {}),
        "island_health": island_health,
        "stagnation_flag": stag["stagnation_flag"],
        "low_streak": stag["low_streak"],
        "exhausted_retry_slots": payload.get("exhausted_retry_slots", []),
        "exhausted_retry_count": int(payload.get("exhausted_retry_count", 0) or 0),
        # echo the active trigger metric (was threaded in but never emitted/read).
        "trigger_metric": payload.get("trigger_metric", "hybrid"),
        "total_programs": summary.get("total"),
        "correct_programs": summary.get("correct"),
    }


if __name__ == "__main__":
    _common.run_main(main)

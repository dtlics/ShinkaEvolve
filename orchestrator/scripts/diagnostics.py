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

INPUT (stdin JSON):
  {
    "db_path": str, "db_config": {..}, "embedding_model": str,
    "window_index": int, "iters_completed": int,
    "best_score_start": float, "window_size": int,
    "current_strategy_hash": str,
    "tau": 0.0, "prior_low_streak": 0, "consecutive_required": 2,
    # per-window counters supplied by the harness:
    "novelty_accepts": int, "novelty_rejects": int,
    "eval_failures": int, "eval_total": int,
    "llm_bandit_weights": {model: weight},
    "exhausted_retry_slots": [candidate_id, ...]
  }

OUTPUT (stdout JSON): the brief's window diagnostics shape, with "ok": true.
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
    novelty_acceptance_rate = (accepts / nov_total) if nov_total else 1.0

    eval_total = int(payload.get("eval_total", 0) or 0)
    eval_failures = int(payload.get("eval_failures", 0) or 0)
    evaluation_failure_rate = (eval_failures / eval_total) if eval_total else 0.0

    iters = int(payload.get("iters_completed", 0) or 0)
    fix_count = int(payload.get("fix_count", 0) or 0)
    fix_rate = (fix_count / iters) if iters else 0.0

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
        "llm_bandit_weights": payload.get("llm_bandit_weights", {}),
        "llm_bandit_counts": payload.get("llm_bandit_counts", {}),
        "island_health": island_health,
        "stagnation_flag": stag["stagnation_flag"],
        "low_streak": stag["low_streak"],
        "exhausted_retry_slots": payload.get("exhausted_retry_slots", []),
        "total_programs": summary.get("total"),
        "correct_programs": summary.get("correct"),
    }


if __name__ == "__main__":
    _common.run_main(main)

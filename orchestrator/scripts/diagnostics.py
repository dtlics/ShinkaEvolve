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
  low_streak, exhausted_retry_slots, exhausted_retry_count, apply_exhausted_count,
  apply_failure_rate, timeout_count, wrong_answer_count, errored_fraction (cumulative,
  over all NON-tombstoned programs — distinct from the per-window evaluation_failure_rate),
  model_collapse {top_arm, top_share, n_arms_active, collapsed} (counts-share, SURFACED
  for the framework-audit check, never auto-corrected in steady-state), repair_mode_on,
  repair_fail_count, repair_tombstoned_count, trigger_metric,
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


def _model_collapse(counts: Dict[str, Any], frac: float = 0.85, min_pulls: int = 8) -> Dict[str, Any]:
    """Surfaced model-picker collapse signal from per-arm SUBMITTED counts (fall back
    to completed). Counts-based on purpose: a single arm's posterior WEIGHT caps at
    1-epsilon, so weights can never reveal a collapse. Read-only and SURFACED for the
    agent's cadenced framework-audit check; never auto-corrected in steady-state (the
    only automatic use is judging a just-deployed rewrite, in rollback_decision)."""
    subs: Dict[str, float] = {}
    for arm, c in (counts or {}).items():
        if isinstance(c, dict):
            subs[arm] = float(c.get("submitted", c.get("completed", 0)) or 0)
        else:
            subs[arm] = float(c or 0)
    total = sum(subs.values())
    n_active = sum(1 for v in subs.values() if v > 0)
    if not subs or total <= 0:
        return {"top_arm": None, "top_share": 0.0, "n_arms_active": n_active, "collapsed": False}
    top_arm = max(subs, key=lambda a: subs[a])
    top_share = subs[top_arm] / total
    collapsed = bool(top_share >= frac and n_active >= 2 and total >= min_pulls)
    return {"top_arm": top_arm, "top_share": top_share,
            "n_arms_active": n_active, "collapsed": collapsed}


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
            "tau": payload.get("tau"),  # M27: default None (NOT 0.0) so the detector's 1e-3 abs_floor fallback engages when stagnation_abs_floor is omitted; literal 0.0 would shadow it
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

    # P2-T3: errored_fraction over ALL NON-tombstoned programs (errored programs are
    # never archived). Tombstoned (repair-removed) programs are EXCLUDED from both
    # numerator and denominator so the repair latch RELEASES once they are removed.
    total_progs = int(summary.get("total", 0) or 0)
    correct_progs = int(summary.get("correct", 0) or 0)
    tombstoned = int(summary.get("tombstoned_count", 0) or 0)
    live_total = max(0, total_progs - tombstoned)
    errored_fraction = (
        max(0, (total_progs - correct_progs) - tombstoned) / live_total
    ) if live_total else 0.0

    # Failure-type echoes (counters CREATED by run_window: apply_exhausted [P1-T1],
    # timeout/wrong [P2-T4]). apply_failure_rate is over attempted-or-apply-failed
    # slots — distinct from evaluation_failure_rate (post-repair, over EVALUATED slots).
    apply_exhausted_count = int(payload.get("apply_exhausted", 0) or 0)
    timeout_count = int(payload.get("timeout_count", 0) or 0)
    wrong_answer_count = int(payload.get("wrong_answer_count", 0) or 0)
    _apply_denom = eval_total + apply_exhausted_count
    apply_failure_rate = (apply_exhausted_count / _apply_denom) if _apply_denom else 0.0

    model_collapse = _model_collapse(
        payload.get("llm_bandit_counts", {}),
        frac=float(payload.get("model_collapse_frac", 0.85) or 0.85),
        min_pulls=int(payload.get("model_collapse_min_pulls", 8) or 8),
    )
    repair_mode_on = errored_fraction >= float(payload.get("repair_trigger_fraction", 0.20) or 0.20)

    return {
        "window_index": int(payload.get("window_index", 0) or 0),
        "iters_completed": int(payload.get("iters_completed", 0) or 0),
        "best_score_start": best_start,
        "best_score_end": float(best_end),
        "delta": stag["delta"],
        "J_score": stag["J_score"],
        "threshold": stag.get("threshold"),
        "strategy_fingerprint": payload.get("strategy_fingerprint", {}),
        "novelty_acceptance_rate": novelty_acceptance_rate,
        "novelty_rejected_cost": float(payload.get("novelty_rejected_cost", 0.0) or 0.0),
        "evaluation_failure_rate": evaluation_failure_rate,
        "eval_total": eval_total,  # H4: distinguishes "evaluated and all passed" from "nothing evaluated"
        "fix_rate": fix_rate,
        "fix_success_rate": fix_success_rate,
        "needs_fix_rate": needs_fix_rate,
        "llm_bandit_weights": payload.get("llm_bandit_weights", {}),
        "llm_bandit_counts": payload.get("llm_bandit_counts", {}),
        # H5: THIS window's submitted counts, the source rollback arm 4a reads (the cumulative
        # llm_bandit_counts above stays for the steady-state model_collapse sensor).
        "llm_bandit_window_counts": payload.get("llm_bandit_window_counts", {}),
        "island_health": island_health,
        "stagnation_flag": stag["stagnation_flag"],
        "low_streak": stag["low_streak"],
        "exhausted_retry_slots": payload.get("exhausted_retry_slots", []),
        "exhausted_retry_count": int(payload.get("exhausted_retry_count", 0) or 0),
        # P2-T3 failure-type + structural sensor fields.
        "apply_exhausted_count": apply_exhausted_count,
        "apply_failure_rate": apply_failure_rate,
        "timeout_count": timeout_count,
        "wrong_answer_count": wrong_answer_count,
        "errored_fraction": errored_fraction,
        "model_collapse": model_collapse,
        "repair_mode_on": repair_mode_on,
        "repair_fail_count": int(payload.get("repair_fail_count", 0) or 0),
        "repair_tombstoned_count": int(payload.get("repair_tombstoned_count", 0) or 0),
        # echo the active trigger metric (was threaded in but never emitted/read).
        "trigger_metric": payload.get("trigger_metric", "hybrid"),
        "total_programs": summary.get("total"),
        "correct_programs": summary.get("correct"),
    }


if __name__ == "__main__":
    _common.run_main(main)

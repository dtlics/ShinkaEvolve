"""cadence_policy.py — decide WHEN the inner loop returns control to you.

MUTABILITY: FOUNDATION — NOT orchestrator-rewritable (S1 ruling, 2026-06-13). The wake-decay
schedule and run termination are part of the contract: an orchestrator must not be able to
change how often it wakes or when its own run ends (it could inadvertently keep itself
asleep/awake or extend its own run). It is therefore REMOVED from strategy_store.MUTABLE_TARGETS,
so the rewrite cycle (snapshot/deploy) refuses it. The cadence/termination KNOBS
(`cadence.early_phase_windows` / `base_low` / `low_threshold` / `max_windows_per_call` /
`termination_streak`) remain tunable but BOOT-ONLY (set in run.json before the run, never edited
mid-run). It embeds NO LLM call.

In `--until-decision` mode the harness runs windows autonomously and, after each
window, asks this policy whether to hand control back. The cadence is TWO-STAGE:

  STAGE 1 — EARLY PHASE: for the first `early_phase_windows` windows (default 5)
  control returns EVERY window, regardless of the work score. The framework is
  least proven early, so you inspect every window. This stage is purely a function
  of the global `window_index` and ignores the work score.

  STAGE 2 — WORK-SCORE TAPER: once past the early phase, the cluster size follows
  the work score. High recent work (you just intervened) → return every window so
  you stay close. As work stays low the cluster grows — base_low, then doubling per
  consecutive low-work return (5, 10, 20, 40 …) — so a stable run is left to run
  with ever-rarer waking. Crucially, the low-streak is counted FROM THE END OF THE
  EARLY PHASE: the early per-window returns must NOT inflate the first steady-state
  cluster (without this, 5 early low-work returns would jump the first taper cluster
  to base_low·2^4 ≈ 80). The taper is UNCAPPED; a cluster is bounded only by the
  budget hard-stop, stagnation, and the termination criteria.

Return is ALSO immediate on stagnation (a decision is needed). No work score
recorded yet → return every window (the safe no-signal default). Set
`early_phase_windows` to 0 to disable Stage 1 and restore the pure work-score taper.

NOT controlled here: the **budget railguard**. The harness hard-stops on budget
regardless of this policy — you cannot disable the spend cap by rewriting cadence.

INPUT (stdin JSON):
  {
    "stagnation_flag": bool,
    "windows_run": int,                  # windows run so far in THIS run_window call
    "window_index": int,                 # global windows completed so far (drives the early phase)
    "early_phase_windows": int,          # per-window-return prefix length (default 5; 0 disables)
    "recent_work_score": float | null,   # last control-return's work score (None = no signal)
    "work_low_streak": int,              # consecutive recent low-work control-returns
    "base_low": float,                   # cluster size at the first low-work return past early (default 5)
    "low_threshold": float,              # work_score <= this counts as "low" (default 1)
    "max_windows_per_call": int | null,  # OPTIONAL explicit ceiling (default: none / no cap)
    "low_streak": int,                   # carried (informational)
    "evaluation_failure_rate": float     # carried (informational)
  }

OUTPUT (stdout JSON):
  { "ok": true, "return": bool, "reason": str, "target_cluster_size": int }
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    stagnation = bool(payload.get("stagnation_flag"))
    windows_run = int(payload.get("windows_run", 0) or 0)

    # A decision is needed now → return immediately.
    if stagnation:
        return {"return": True, "reason": "stagnation", "target_cluster_size": 1}

    recent_work = payload.get("recent_work_score")
    low_threshold = float(payload.get("low_threshold", 1) or 0.0)
    base_low = float(payload.get("base_low", 5) or 5)
    low_streak = int(payload.get("work_low_streak", 0) or 0)
    window_index = int(payload.get("window_index", 0) or 0)
    early_phase_windows = int(payload.get("early_phase_windows", 5) or 0)

    if early_phase_windows > 0 and window_index <= early_phase_windows:
        # STAGE 1 — early phase: inspect every window regardless of work score.
        # window_index is the count of windows completed so far, so `<= K` covers the
        # first K windows (indices 0..K-1), each getting its own control-return.
        target = 1
    elif recent_work is None:
        target = 1  # no work-score signal → wake every window (safe no-signal default)
    elif float(recent_work) > low_threshold:
        target = 1  # you just did real work → stay close, check every window
    else:
        # STAGE 2 — low recent work past the early phase → escalate the next cluster,
        # UNCAPPED: base_low, then doubling. Subtract `early_phase_windows` from the
        # low-streak exponent so the early per-window returns don't inflate the first
        # steady-state cluster (5, 10, 20, 40 …). With early_phase_windows=0 this is
        # exactly the legacy base_low·2^(low_streak-1) ramp.
        exponent = max(0, low_streak - 1 - early_phase_windows)
        target = max(1, int(round(base_low * (2 ** exponent))))

    # OPTIONAL explicit ceiling — honored only if the user set one (default: no cap).
    _ceiling = payload.get("max_windows_per_call")
    if _ceiling is not None:
        target = min(target, int(_ceiling))

    if windows_run >= target:
        return {"return": True, "reason": "taper", "target_cluster_size": target}
    return {"return": False, "reason": "continue", "target_cluster_size": target}


if __name__ == "__main__":
    _common.run_main(main)

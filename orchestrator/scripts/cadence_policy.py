"""cadence_policy.py — decide WHEN the inner loop returns control to you.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite this when the
journal shows it is being woken too often (churning, not letting strategies prove
themselves) or too rarely (slow to react to stagnation). It embeds NO LLM call.

In `--until-decision` mode the harness runs windows autonomously and, after each
window, asks this policy whether to hand control back. The DEFAULT returns on
stagnation (a decision is needed) or once the window-cluster reaches the size the
WORK-SCORE TAPER computes. The taper is UNCAPPED and escalating: high recent work
(you just intervened) → return every window so you stay close; as work stays low it
stretches the next cluster (base_low, then doubling per consecutive low-work return —
5, 10, 20, 40 …) so a stable run is left to run with ever-rarer waking. There is NO
max-window ceiling — a cluster is bounded only by the budget hard-stop, stagnation,
and the termination criteria. No work score recorded yet → return every window (the
safe no-signal default; without a cap, the unsafe direction is waking LESS).

NOT controlled here: the **budget railguard**. The harness hard-stops on budget
regardless of this policy — you cannot disable the spend cap by rewriting cadence.

INPUT (stdin JSON):
  {
    "stagnation_flag": bool,
    "windows_run": int,                  # windows run so far in THIS run_window call
    "recent_work_score": float | null,   # last control-return's work score (None = no signal)
    "work_low_streak": int,              # consecutive recent low-work control-returns
    "base_low": float,                   # cluster size when work first goes low (default 5)
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

    if recent_work is None:
        target = 1  # no work-score signal → wake every window (safe no-signal default)
    elif float(recent_work) > low_threshold:
        target = 1  # you just did real work → stay close, check every window
    else:
        # Low recent work → escalate the next cluster, UNCAPPED: base_low·2^(streak-1).
        target = max(1, int(round(base_low * (2 ** max(0, low_streak - 1)))))

    # OPTIONAL explicit ceiling — honored only if the user set one (default: no cap).
    _ceiling = payload.get("max_windows_per_call")
    if _ceiling is not None:
        target = min(target, int(_ceiling))

    if windows_run >= target:
        return {"return": True, "reason": "taper", "target_cluster_size": target}
    return {"return": False, "reason": "continue", "target_cluster_size": target}


if __name__ == "__main__":
    _common.run_main(main)

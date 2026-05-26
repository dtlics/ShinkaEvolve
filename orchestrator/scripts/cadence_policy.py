"""cadence_policy.py — decide WHEN the inner loop returns control to you.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite this when the
journal shows it is being woken too often (churning, not letting strategies prove
themselves) or too rarely (slow to react to stagnation). It embeds NO LLM call.

In `--until-decision` mode the harness runs windows autonomously and, after each
window, asks this policy whether to hand control back. The DEFAULT returns on
stagnation (a decision is needed) or after `max_windows_per_call` windows (a
periodic sanity check — tuned so max_windows_per_call × window_size ≈ 50 gens).

NOT controlled here: the **budget railguard**. The harness hard-stops on budget
regardless of this policy — you cannot disable the spend cap by rewriting cadence.

INPUT (stdin JSON):
  {
    "stagnation_flag": bool,
    "windows_run": int,            # windows run so far in THIS run_window call
    "max_windows_per_call": int,
    "low_streak": int,             # consecutive low-trigger windows
    "J_score": float,
    "evaluation_failure_rate": float
  }

OUTPUT (stdout JSON):
  { "ok": true, "return": bool, "reason": str }
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
    max_per_call = int(payload.get("max_windows_per_call", 3) or 3)

    # Return immediately when there's a decision to make.
    if stagnation:
        return {"return": True, "reason": "stagnation"}
    # Otherwise return for a periodic sanity check at the window cap.
    if windows_run >= max_per_call:
        return {"return": True, "reason": "window_cap"}
    # Healthy and under the cap → keep running autonomously.
    return {"return": False, "reason": "continue"}


if __name__ == "__main__":
    _common.run_main(main)

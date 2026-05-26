"""stagnation_detector.py — compute the window J-score and the stagnation flag.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite this file —
the J formula and the threshold logic are tuning surfaces. It embeds NO LLM call.
This is NEW code: shinka has no window/J-score concept (its only "stagnation" is
per-island generations-since-best-improved → island spawn, a different thing).

Two distinct quantities (EvoX, arXiv:2602.23413 §6 / Eq. 2):
  * J = (s_end − s_start) · log(1 + s_start) / √W — the strategy-EVALUATION scalar.
    Δ is the best-score gain across the window; √W normalizes for window length;
    the log term upweights gains from higher starting scores. J is what the
    orchestrator compares for ROLLBACK (a new strategy's window-J vs the prior
    strategy's). When s_start ≤ 0 the log term is not meaningful → Δ/√W.
  * The intervention TRIGGER is Δ < τ (EvoX: "If Δ falls below a stagnation
    threshold τ, EvoX triggers a strategy update"). τ is on the raw Δ, so set it
    relative to the task's score scale. ``trigger_metric`` may be switched to
    "J" (scale-free) since this file is mutable.

Stagnation fires when the trigger metric is below τ for ``consecutive_required``
(default 2) consecutive windows — demand-driven, not on a fixed schedule.

INPUT (stdin JSON):
  {
    "best_score_start": float,
    "best_score_end": float,
    "window_size": int,             # EvoX uses W ≈ 10% of the total iteration budget
    "tau": 0.0,                     # threshold on Δ (default) — task-scale-relative
    "trigger_metric": "delta",      # "delta" (EvoX default) | "J" (scale-free)
    "prior_low_streak": 0,          # consecutive low windows BEFORE this one
    "consecutive_required": 2
  }

OUTPUT (stdout JSON):
  {
    "ok": true, "J_score": float, "delta": float,
    "stagnation_flag": bool, "low_streak": int,
    "tau": float, "trigger_metric": str, "formula": str
  }
"""

from __future__ import annotations

import math
from typing import Any, Dict

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def compute_J(best_score_start: float, best_score_end: float, window_size: int) -> float:
    delta = float(best_score_end) - float(best_score_start)
    w = max(int(window_size), 1)
    sqrt_w = math.sqrt(w)
    if best_score_start > 0:
        scale = math.log1p(best_score_start)
    else:
        scale = 1.0  # log term undefined/zero for non-positive scores; use Δ/√W
    return delta * scale / sqrt_w


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    best_start = float(payload.get("best_score_start", 0.0) or 0.0)
    best_end = float(payload.get("best_score_end", 0.0) or 0.0)
    window_size = int(payload.get("window_size", 1) or 1)
    tau = float(payload.get("tau", 0.0) or 0.0)
    prior_low_streak = int(payload.get("prior_low_streak", 0) or 0)
    consecutive_required = int(payload.get("consecutive_required", 2) or 2)

    delta = best_end - best_start
    j = compute_J(best_start, best_end, window_size)

    # EvoX faithfulness: the intervention TRIGGER is Δ < τ (raw best-score gain
    # over the window — EvoX §"If Δ falls below a stagnation threshold τ..."),
    # while J (Eq. 2) is the strategy-EVALUATION scalar the orchestrator compares
    # for rollback (new strategy's window-J vs the prior strategy's). They differ:
    # τ on Δ is relative to the task's score scale; J is scale-normalized. The
    # trigger metric is a knob since this file is mutable.
    trigger_metric = payload.get("trigger_metric", "delta")
    trigger_value = delta if trigger_metric == "delta" else j
    low_streak = prior_low_streak + 1 if trigger_value < tau else 0
    stagnation_flag = low_streak >= consecutive_required

    return {
        "J_score": j,
        "delta": delta,
        "stagnation_flag": bool(stagnation_flag),
        "low_streak": low_streak,
        "tau": tau,
        "trigger_metric": trigger_metric,
        "formula": "J = delta * log1p(max(s_start,0) or 1) / sqrt(W)  [EvoX Eq.2]; trigger: Δ < τ",
    }


if __name__ == "__main__":
    _common.run_main(main)

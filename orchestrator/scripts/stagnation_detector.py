"""stagnation_detector.py — compute the window J-score and the stagnation flag.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite this file —
the J formula and the threshold logic are tuning surfaces. It embeds NO LLM call.
This is NEW code: shinka has no window/J-score concept (its only "stagnation" is
per-island generations-since-best-improved → island spawn, a different thing).

Two distinct quantities:
  * J = Δ / √W — a monotone, continuous progress scalar (Δ = best-score gain over
    the window; √W normalizes for window length). This REPLACES the earlier
    EvoX `Δ·log1p(s_start)/√W` form, whose `log1p(s_start)` scale term was
    discontinuous and non-monotonic around s_start=0 (for the same Δ, J could
    collapse ~200× the instant the score went positive) and dominated Δ on
    small-score tasks — making J useless for cross-window comparison (was F16).
    Rollback no longer keys on J (see ``rollback_decision.py``); J is now purely
    an informational progress reading.
  * The intervention TRIGGER is a **hybrid threshold**: a window is "low" when

        Δ ≤ max(abs_floor, rel_frac · max(s_start, 0))

    The ``rel_frac`` term makes the trigger SCALE-FREE once a score exists
    (equivalent to "relative improvement < rel_frac"), while ``abs_floor`` gives
    a sensible absolute bar during the opening phase when s_start ≈ 0 (where a
    pure relative test would divide by ~0). This fixes the old `Δ < τ=0.05`
    default that flagged stagnation even on a window that tripled the score
    (gains here are ~0.01 ≪ 0.05) — was F12.

Stagnation fires when the trigger is "low" for ``consecutive_required`` (default
2) consecutive windows — demand-driven, not on a fixed schedule.

INPUT (stdin JSON):
  {
    "best_score_start": float,
    "best_score_end": float,
    "window_size": int,
    "stagnation_abs_floor": float,  # absolute min gain to count as progress
    "stagnation_rel_frac": float,   # relative fraction of best_start (default 0.05)
    "tau": float,                   # DEPRECATED alias for stagnation_abs_floor
    "prior_low_streak": 0,
    "consecutive_required": 2
  }

OUTPUT (stdout JSON):
  {
    "ok": true, "J_score": float, "delta": float,
    "stagnation_flag": bool, "low_streak": int,
    "threshold": float, "abs_floor": float, "rel_frac": float,
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

_DEFAULT_ABS_FLOOR = 1e-3
_DEFAULT_REL_FRAC = 0.05


def compute_J(best_score_start: float, best_score_end: float, window_size: int) -> float:
    """Monotone, continuous progress scalar: Δ / √W (no log scale term)."""
    delta = float(best_score_end) - float(best_score_start)
    w = max(int(window_size), 1)
    return delta / math.sqrt(w)


def stagnation_threshold(best_score_start: float, abs_floor: float, rel_frac: float) -> float:
    """The hybrid 'is this window low' bar: max(abs_floor, rel_frac·max(s_start,0))."""
    return max(float(abs_floor), float(rel_frac) * max(float(best_score_start), 0.0))


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    best_start = float(payload.get("best_score_start", 0.0) or 0.0)
    best_end = float(payload.get("best_score_end", 0.0) or 0.0)
    window_size = int(payload.get("window_size", 1) or 1)
    # abs_floor: prefer the explicit knob; fall back to the deprecated `tau`
    # alias; else a small default. (Old configs that set tau still work.)
    abs_floor = payload.get("stagnation_abs_floor")
    if abs_floor is None:
        abs_floor = payload.get("tau")
    abs_floor = _DEFAULT_ABS_FLOOR if abs_floor is None else float(abs_floor)
    rel_frac = payload.get("stagnation_rel_frac")
    rel_frac = _DEFAULT_REL_FRAC if rel_frac is None else float(rel_frac)
    prior_low_streak = int(payload.get("prior_low_streak", 0) or 0)
    consecutive_required = int(payload.get("consecutive_required", 2) or 2)

    delta = best_end - best_start
    j = compute_J(best_start, best_end, window_size)
    threshold = stagnation_threshold(best_start, abs_floor, rel_frac)

    # "Low" window: best-score gain did not clear the hybrid bar.
    low = delta <= threshold
    low_streak = prior_low_streak + 1 if low else 0
    stagnation_flag = low_streak >= consecutive_required

    return {
        "J_score": j,
        "delta": delta,
        "stagnation_flag": bool(stagnation_flag),
        "low_streak": low_streak,
        "threshold": threshold,
        "abs_floor": abs_floor,
        "rel_frac": rel_frac,
        "tau": abs_floor,  # back-compat echo (now == abs_floor)
        "trigger_metric": "hybrid",
        "formula": "J = Δ/√W; trigger low when Δ ≤ max(abs_floor, rel_frac·max(s_start,0))",
    }


if __name__ == "__main__":
    _common.run_main(main)

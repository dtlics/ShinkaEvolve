"""compute_reward.py — turn an evaluation into the learning signal for selection.

MUTABILITY: MUTABLE STRATEGY (cell A), part of the **scoring concern**. The
orchestrator MAY rewrite this when the run journal shows the reward signal is
miscalibrated (e.g. the bandit chases models that produce high-variance noise, or
improvement stops correlating with reward). It embeds NO LLM call.

IMPORTANT — the scoring concern spans multiple files. If you change *how reward is
generated* here, also review *how it is consumed*:
  - `select_llm.py`   — feeds (reward, baseline) into the bandit posterior/update.
  - `sample_parent.py`— weights parents by `combined_score` (the raw score).
Change them together, compatibly (see the concern map in SKILL.md). The raw task
score itself comes from the user's `evaluate.py` and is NOT mutable.

Default policy (identical to the prior hardcoded harness behavior, so the bandit
is unchanged until you deliberately evolve it):
  reward  = candidate.combined_score   (None when incorrect → bandit imputes worst)
  baseline= parent.combined_score      (AsymmetricUCB learns on reward - baseline)

INPUT (stdin JSON):
  {
    "candidate": {"combined_score": float, "correct": bool, "public_metrics": {..}},
    "parent": {"combined_score": float} | null,
    "mode": "absolute" | "relative",   # rewrite lever; default "absolute"
    "reward_validity_floor": float,    # O6 reward-scale floor; default 0.001
    "context": {..}                    # free-form (window stats, etc.)
  }

OUTPUT (stdout JSON):
  { "ok": true, "reward": float | null, "baseline": float, "mode": str }
"""

from __future__ import annotations

import math
from typing import Any, Dict

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def _finite(x: Any, default: float = 0.0) -> float:
    """Coerce to a finite float; return ``default`` for None / NaN / inf / non-numeric."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    candidate = payload.get("candidate", {}) or {}
    parent = payload.get("parent") or {}
    mode = payload.get("mode", "absolute")

    correct = bool(candidate.get("correct", False))
    parent_score = _finite(parent.get("combined_score"), 0.0)
    _raw = candidate.get("combined_score")
    _score_finite = isinstance(_raw, (int, float)) and math.isfinite(float(_raw))

    # P10-T1: incorrect OR a NON-FINITE/missing candidate score (NaN / inf / None — a
    # buggy evaluator or a loss/regret task gone wrong) → no reward; the bandit imputes a
    # worst-case value, so one bad number can't poison an arm. (Failures are penalized,
    # not invisible.) NEGATIVE FINITE scores pass through to the floor logic — the reward
    # math is relative, so negative-score tasks are fully supported.
    if not correct or not _score_finite:
        # M23: return the sign-aware baseline too (the bandit max()es it with 0 anyway, so this is
        # behavior-identical — but keeps the two branches consistent).
        return {"reward": None, "baseline": max(parent_score, 0.0), "mode": mode}
    score = float(_raw)

    # HYBRID (H3 / O6 reward scale): floor the correct candidate's reward CONTRIBUTION
    # so a correct-but-below-parent candidate is STRICTLY better than a failed one
    # (reward=None → bandit imputes worst), instead of collapsing to the same
    # near-worst contribution under the bandit's asymmetric clamp (the bug H3 named).
    # The penalty SHAPE is the mutable lever `reward_validity_floor` (default 0.001).
    # The parent-selection SCORE scale has its own separate `validity_floor`
    # (sample_parent) — two distinct levers per open question O6.
    #
    # M23: SIGN-AWARE baseline. The bandit resolves the effective baseline as
    # max(passed_baseline, self._baseline=0) and then asymmetric-clamps r = max(reward -
    # baseline, 0). With a NEGATIVE parent_score the old `reward = parent_score + max(delta,
    # floor)` gave the bandit r = (parent_score + max(delta,floor)) - 0 = parent_score +
    # max(delta,floor), which clamps to 0 for a sufficiently negative parent — i.e. a
    # correct-but-low candidate becomes INDISTINGUISHABLE from a failure (also r=0). Build the
    # reward against b = max(parent_score, 0) so the bandit's r = reward - b = max(delta, floor)
    # >= floor > 0 for ANY parent sign. For parent_score >= 0 this is byte-identical to before.
    floor = float(payload.get("reward_validity_floor", 0.001) or 0.0)
    baseline = max(parent_score, 0.0)
    delta = score - parent_score
    if mode == "relative":
        # baseline 0 so the bandit sees the (floored) delta directly.
        return {"reward": max(delta, floor), "baseline": 0.0, "mode": mode}
    # absolute (default): bandit learns reward - baseline; floor that gap, sign-aware baseline.
    return {"reward": baseline + max(delta, floor), "baseline": baseline, "mode": mode}


if __name__ == "__main__":
    _common.run_main(main)

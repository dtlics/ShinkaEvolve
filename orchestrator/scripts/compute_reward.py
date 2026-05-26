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
    "context": {..}                    # free-form (window stats, etc.)
  }

OUTPUT (stdout JSON):
  { "ok": true, "reward": float | null, "baseline": float, "mode": str }
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    candidate = payload.get("candidate", {}) or {}
    parent = payload.get("parent") or {}
    mode = payload.get("mode", "absolute")

    correct = bool(candidate.get("correct", False))
    score = float(candidate.get("combined_score", 0.0) or 0.0)
    parent_score = float(parent.get("combined_score", 0.0) or 0.0)

    if not correct:
        # Incorrect → no reward; the bandit imputes a worst-case value. This
        # preserves the "failures are penalized, not invisible" invariant.
        return {"reward": None, "baseline": parent_score, "mode": mode}

    if mode == "relative":
        # Reward the improvement directly; baseline 0 so the bandit sees the delta.
        return {"reward": score - parent_score, "baseline": 0.0, "mode": mode}

    # absolute (default): bandit subtracts baseline internally.
    return {"reward": score, "baseline": parent_score, "mode": mode}


if __name__ == "__main__":
    _common.run_main(main)

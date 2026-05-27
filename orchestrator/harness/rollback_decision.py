"""rollback_decision.py — judge whether a deployed strategy rewrite REGRESSED.

This is the decision half of the strategy-rewrite protocol's "measure → roll back"
step. The orchestrator deploys a candidate, runs ONE measure window, then calls
this with the prior window's diagnostics and the measure window's diagnostics; it
returns whether to roll back (and why).

WHY a basket instead of `new_J < prior_J·0.8`: the old J-only guard was inert
during the opening phase. Best score is monotonic non-decreasing, so window J is
≥ 0, and early on prior_J ≈ 0 → the guard could never fire (was F14) — exactly
when a careless rewrite does the most damage. This judges a basket of
search-health signals (correctness, diversity, score), so a rewrite that breaks
mutation correctness or floods near-duplicates is caught even when the score is
still pinned at its opening value.

A rewrite is a REGRESSION (→ roll back) if ANY of:
  1. Correctness collapse — eval-success rate fell below ``min_eval_success`` (and
     the prior window was above it), OR dropped by ≥ ``eval_drop`` vs prior.
     (The rewrite started producing broken candidates.)
  2. Diversity collapse — novelty-acceptance rate dropped by ≥ ``nov_drop`` vs
     prior. (The rewrite floods near-duplicates — wasted spend, see F13.)
  3. Score regression — the prior window was making real progress
     (prior Δ > prior threshold) AND the measure window's Δ is < ``score_ratio`` ×
     prior Δ AND search health did not improve to compensate.

Otherwise accept. Thresholds are kwargs so a future tuning pass can adjust them.

INPUT (stdin JSON):
  { "prior": <window diag>, "measure": <window diag>,
    "eval_drop": 0.25, "nov_drop": 0.25, "min_eval_success": 0.5, "score_ratio": 0.5 }

OUTPUT (stdout JSON):
  { "ok": true, "regressed": bool, "accept": bool, "reasons": [str], "signals": {..} }
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

_HARNESS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _HARNESS_DIR.parent / "scripts"
for _p in (str(_SCRIPTS_DIR),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _common  # noqa: E402


def _eval_success(diag: Dict[str, Any]) -> float:
    return 1.0 - float(diag.get("evaluation_failure_rate", 0.0) or 0.0)


def _nov(diag: Dict[str, Any]) -> float:
    # default 1.0 (fully accepting) when absent, matching diagnostics' convention
    v = diag.get("novelty_acceptance_rate")
    return 1.0 if v is None else float(v)


def decide(
    prior: Dict[str, Any],
    measure: Dict[str, Any],
    *,
    eval_drop: float = 0.25,
    nov_drop: float = 0.25,
    min_eval_success: float = 0.5,
    score_ratio: float = 0.5,
) -> Dict[str, Any]:
    reasons: List[str] = []

    p_eval, m_eval = _eval_success(prior), _eval_success(measure)
    p_nov, m_nov = _nov(prior), _nov(measure)
    p_delta = float(prior.get("delta", 0.0) or 0.0)
    m_delta = float(measure.get("delta", 0.0) or 0.0)
    p_threshold = float(prior.get("threshold", 0.0) or 0.0)

    # 1. Correctness collapse
    if m_eval < min_eval_success <= p_eval:
        reasons.append(
            f"correctness collapse: eval-success {m_eval:.2f} < {min_eval_success} (prior {p_eval:.2f})"
        )
    elif (p_eval - m_eval) >= eval_drop:
        reasons.append(
            f"correctness drop: eval-success {p_eval:.2f} → {m_eval:.2f} (≥{eval_drop})"
        )

    # 2. Diversity collapse
    if (p_nov - m_nov) >= nov_drop:
        reasons.append(
            f"diversity drop: novelty-acceptance {p_nov:.2f} → {m_nov:.2f} (≥{nov_drop})"
        )

    # 3. Score regression (only when the prior window was genuinely progressing)
    if p_delta > p_threshold and m_delta < score_ratio * p_delta:
        health_improved = (m_nov >= p_nov) and (m_eval >= p_eval)
        if not health_improved:
            reasons.append(
                f"score regression: Δ {p_delta:.5f} → {m_delta:.5f} (< {score_ratio}× prior) with no health gain"
            )

    regressed = len(reasons) > 0
    return {
        "regressed": regressed,
        "accept": not regressed,
        "reasons": reasons,
        "signals": {
            "prior": {"delta": p_delta, "eval_success": p_eval, "novelty_accept": p_nov, "threshold": p_threshold},
            "measure": {"delta": m_delta, "eval_success": m_eval, "novelty_accept": m_nov},
        },
    }


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    return decide(
        payload.get("prior", {}) or {},
        payload.get("measure", {}) or {},
        eval_drop=float(payload.get("eval_drop", 0.25)),
        nov_drop=float(payload.get("nov_drop", 0.25)),
        min_eval_success=float(payload.get("min_eval_success", 0.5)),
        score_ratio=float(payload.get("score_ratio", 0.5)),
    )


if __name__ == "__main__":
    _common.run_main(main)

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
  1. Correctness collapse — eval-success fell below the ABSOLUTE ``abs_eval_floor``
     (near-total breakage; fires regardless of prior, so it is caught even in the
     flat early phase), OR fell below ``min_eval_success`` while the prior was above
     it, OR dropped by ≥ ``eval_drop`` vs prior. ``abs_eval_floor`` (0.05) sits FAR
     below a genuinely-hard task's healthy floor, so a hard task that simply has low
     correctness is NOT auto-rolled-back every rewrite (K14).
  2. Diversity collapse — novelty-acceptance dropped by ≥ ``nov_drop`` vs prior.
     SKIPPED when novelty is UNKNOWN (absent), not treated as maximal (O10/K15).
  3. Score regression — the prior window was making real progress
     (prior Δ > prior threshold) AND the measure window's Δ is < ``score_ratio`` ×
     prior Δ AND search health did not improve to compensate.
  4. Bandit collapse — selection collapsed onto ONE arm (measure top-arm weight ≥
     ``bandit_collapse_frac`` AND that arm ROSE ≥ ``bandit_collapse_rise`` vs prior).
     Fires INDEPENDENTLY of Δ — the reward/bandit-regression class the old basket was
     blind to in the flat early phase, i.e. the exact outer-loop trigger (H4).

Otherwise accept. Thresholds are kwargs so a future tuning pass can adjust them.

INPUT (stdin JSON):
  { "prior": <window diag>, "measure": <window diag>,
    "eval_drop": 0.25, "nov_drop": 0.25, "min_eval_success": 0.5, "score_ratio": 0.5,
    "abs_eval_floor": 0.05, "bandit_collapse_frac": 0.85, "bandit_collapse_rise": 0.25 }

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


def _nov(diag: Dict[str, Any]):
    # O10/K15: return None when novelty is UNKNOWN (absent) — do NOT default to 1.0
    # ("perfectly diverse"), which would mask a rewrite that records zero novelty
    # events. Callers skip the diversity arm when this is None.
    v = diag.get("novelty_acceptance_rate")
    return None if v is None else float(v)


def decide(
    prior: Dict[str, Any],
    measure: Dict[str, Any],
    *,
    eval_drop: float = 0.25,
    nov_drop: float = 0.25,
    min_eval_success: float = 0.5,
    score_ratio: float = 0.5,
    abs_eval_floor: float = 0.05,
    bandit_collapse_frac: float = 0.85,
    bandit_collapse_rise: float = 0.25,
) -> Dict[str, Any]:
    reasons: List[str] = []

    p_eval, m_eval = _eval_success(prior), _eval_success(measure)
    p_nov, m_nov = _nov(prior), _nov(measure)
    p_delta = float(prior.get("delta", 0.0) or 0.0)
    m_delta = float(measure.get("delta", 0.0) or 0.0)
    p_threshold = float(prior.get("threshold", 0.0) or 0.0)

    # 1. Correctness collapse.
    if m_eval < abs_eval_floor:
        # ABSOLUTE near-total collapse — fires regardless of prior, so a rewrite that
        # breaks almost everything is caught even in the early/flat phase. abs_eval_floor
        # (0.05) sits FAR below a genuinely-hard task's healthy floor (K14), so a hard
        # task that simply has low correctness is NOT auto-rolled-back every rewrite.
        reasons.append(
            f"correctness collapse: eval-success {m_eval:.2f} < abs_eval_floor {abs_eval_floor}"
        )
    elif m_eval < min_eval_success <= p_eval:
        reasons.append(
            f"correctness drop below {min_eval_success}: eval-success {m_eval:.2f} (prior {p_eval:.2f})"
        )
    elif (p_eval - m_eval) >= eval_drop:
        reasons.append(
            f"correctness drop: eval-success {p_eval:.2f} → {m_eval:.2f} (≥{eval_drop})"
        )

    # 2. Diversity collapse (skip when novelty is UNKNOWN — O10/K15).
    if p_nov is not None and m_nov is not None and (p_nov - m_nov) >= nov_drop:
        reasons.append(
            f"diversity drop: novelty-acceptance {p_nov:.2f} → {m_nov:.2f} (≥{nov_drop})"
        )

    # 3. Score regression (only when the prior window was genuinely progressing).
    if p_delta > p_threshold and m_delta < score_ratio * p_delta:
        eval_ok = m_eval >= p_eval
        nov_ok = (m_nov >= p_nov) if (p_nov is not None and m_nov is not None) else True
        if not (eval_ok and nov_ok):
            reasons.append(
                f"score regression: Δ {p_delta:.5f} → {m_delta:.5f} (< {score_ratio}× prior) with no health gain"
            )

    # 4. Bandit collapse (H4 core): selection collapsed onto a single arm — fires
    # INDEPENDENTLY of Δ, so it is caught in the flat early phase the old basket was
    # blind to (the reward/bandit-regression class the outer loop exists to catch).
    # Uses llm_bandit_weights already in the diagnostics (contract-free).
    pw = prior.get("llm_bandit_weights") or {}
    mw = measure.get("llm_bandit_weights") or {}
    if mw:
        top_arm = max(mw, key=lambda k: float(mw[k] or 0.0))
        m_top = float(mw.get(top_arm, 0.0) or 0.0)
        p_top = float(pw.get(top_arm, 0.0) or 0.0)
        if m_top >= bandit_collapse_frac and (m_top - p_top) >= bandit_collapse_rise:
            reasons.append(
                f"bandit collapse: arm {top_arm} weight {p_top:.2f} → {m_top:.2f} "
                f"(≥{bandit_collapse_frac}, rose ≥{bandit_collapse_rise})"
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
        abs_eval_floor=float(payload.get("abs_eval_floor", 0.05)),
        bandit_collapse_frac=float(payload.get("bandit_collapse_frac", 0.85)),
        bandit_collapse_rise=float(payload.get("bandit_collapse_rise", 0.25)),
    )


if __name__ == "__main__":
    _common.run_main(main)

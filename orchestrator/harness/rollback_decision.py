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
  4. Bandit collapse — selection collapsed onto ONE arm THIS measure window. PRIMARY arm
     4a is COUNTS-share: the top arm's submitted-share (from ``llm_bandit_window_counts``,
     this window's counts — H5) ≥ ``bandit_collapse_count_frac`` AND rose vs prior, with
     ≥ ``bandit_collapse_min_pulls`` total pulls. Fires INDEPENDENTLY of Δ — the
     reward/bandit-regression class the old basket was blind to (H5). Arm 4b (weights-share)
     is LEGACY/near-unreachable (a single arm's weight caps at 1−epsilon).

Otherwise accept. Thresholds are kwargs so a future tuning pass can adjust them.

INPUT (stdin JSON):
  { "prior": <window diag>, "measure": <window diag>,
    "eval_drop": 0.25, "nov_drop": 0.25, "min_eval_success": 0.5, "score_ratio": 0.5,
    "abs_eval_floor": 0.05,
    "bandit_collapse_count_frac": 0.85, "bandit_collapse_min_pulls": 8,  # PRIMARY counts-share arm (4a)
    "bandit_collapse_frac": 0.85, "bandit_collapse_rise": 0.25,          # LEGACY weights arm (4b, near-unreachable)
    "measure_crashed": false }                                          # caller-forced fail-closed

OUTPUT (stdout JSON):
  { "ok": true, "regressed": bool, "accept": bool, "reasons": [str], "signals": {..} }
"""

from __future__ import annotations

import math
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
    bandit_collapse_count_frac: float = 0.85,
    bandit_collapse_min_pulls: int = 8,
    measure_crashed: bool = False,
) -> Dict[str, Any]:
    reasons: List[str] = []

    # P7-T2: FAIL CLOSED on no usable measure data. A measure window that crashed,
    # exited non-zero, returned empty, or produced NaN in a core sensor is treated as a
    # REGRESSION (revert) — never silently accepted. That is the precise worst case the
    # safety net exists to catch. A VALID flat window (delta 0 with a present, non-NaN
    # eval-failure-rate) is NOT caught here.
    def _is_nan(x: Any) -> bool:
        return isinstance(x, float) and math.isnan(x)

    _core_absent = (
        measure.get("best_score_end") is None
        and "delta" not in measure
        and "evaluation_failure_rate" not in measure
    )
    _core_nan = any(_is_nan(measure.get(k)) for k in ("delta", "evaluation_failure_rate", "best_score_end"))
    # H4: a ZERO-EVALUATION measure window (every slot apply-exhausted — exactly what a
    # patch-format-breaking construct_mutation_prompt rewrite, or an Azure outage, produces)
    # completes with evaluation_failure_rate=0.0 PRESENT (the 0/0 branch) + delta 0, so the
    # absent/NaN guards above do NOT catch it and the gate would ACCEPT the poisoned rewrite
    # with zero evidence. apply_failure_rate==1.0 is the structural "nothing was evaluated"
    # signal; a VALID flat window has apply_failure_rate<1.0 (some slots evaluated) so it is
    # NOT caught (preserves the K14 valid-flat-window contract).
    _no_evals = float(measure.get("apply_failure_rate", 0.0) or 0.0) >= 1.0
    if measure_crashed or (not measure) or _core_absent or _core_nan or _no_evals:
        return {
            "regressed": True,
            "accept": False,
            "reasons": ["measure window produced no usable data (crash / empty / NaN / zero "
                        "evaluations — all slots apply-exhausted) — fail closed"],
            "signals": {"measure_crashed": bool(measure_crashed),
                        "apply_failure_rate": float(measure.get("apply_failure_rate", 0.0) or 0.0)},
        }

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

    # 4. Bandit collapse — selection collapsed onto a single arm while judging this
    # just-deployed rewrite (NOT steady-state; this module is only invoked to judge a
    # measure window — steady-state collapse is the SURFACED model_collapse diag flag,
    # never auto-corrected).
    # 4a. PRIMARY — COUNTS-share. A single arm's WEIGHT caps at 1-epsilon, so weights
    # can never reveal a collapse; submitted-COUNTS can. Fires when the top arm's
    # submitted-share >= bandit_collapse_count_frac AND rose vs prior AND there were
    # enough total pulls (min_pulls floor avoids firing on a 2-pull window).
    def _submitted(counts: Any) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for arm, c in (counts or {}).items():
            out[arm] = float(c.get("submitted", c.get("completed", 0)) or 0) if isinstance(c, dict) else float(c or 0)
        return out

    # H5: read THIS window's submitted counts (llm_bandit_window_counts), falling back to
    # the run-CUMULATIVE llm_bandit_counts only when absent. The cumulative pkl counts can
    # never move the top-share past the threshold mid-run (a 10-pull collapsed window barely
    # shifts an N-pull lifetime total), so a mid-run select_llm/compute_reward rewrite that
    # collapses selection was previously auto-accepted. The per-window source makes arm 4a
    # actually reachable; the fallback keeps older priors / tests non-crashing.
    pc = _submitted(prior.get("llm_bandit_window_counts") or prior.get("llm_bandit_counts"))
    mc = _submitted(measure.get("llm_bandit_window_counts") or measure.get("llm_bandit_counts"))
    m_total = sum(mc.values())
    if mc and len(mc) >= 2 and m_total >= bandit_collapse_min_pulls:
        top = max(mc, key=lambda k: mc[k])
        m_share = mc[top] / m_total if m_total else 0.0
        p_total = sum(pc.values())
        p_share = (pc.get(top, 0.0) / p_total) if p_total else 0.0
        if m_share >= bandit_collapse_count_frac and m_share > p_share:
            reasons.append(
                f"bandit collapse (counts): arm {top} submitted-share {p_share:.2f} → "
                f"{m_share:.2f} (≥{bandit_collapse_count_frac}, rose)"
            )

    # 4b. LEGACY weights arm — kept for back-compat but near-unreachable (the
    # 1-epsilon single-arm weight ceiling means bandit_collapse_frac=0.85 rarely fires);
    # the counts-share arm above is the real signal.
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
        bandit_collapse_count_frac=float(payload.get("bandit_collapse_count_frac", 0.85)),
        bandit_collapse_min_pulls=int(payload.get("bandit_collapse_min_pulls", 8)),
        measure_crashed=bool(payload.get("measure_crashed", False)),
    )


if __name__ == "__main__":
    _common.run_main(main)

"""select_llm.py — pick which LLM to use for the next mutation; learn from results.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite the SELECTION
policy here. It embeds NO LLM call.

Design note: shinka's ``AsymmetricUCB`` is the reward/cost-aware bandit; this file
WRAPS it (parity + safe state) and exposes a thin, rewritable policy layer on top.

LATENCY-AWARE REWRITE (post-run-20260527, sanctioned foundation work)
--------------------------------------------------------------------
WHY: the bandit optimizes reward (and observed $-cost via cost_aware_coef) but is
**blind to wallclock**. The inner loop is SEQUENTIAL, so a model that takes 25-40 min
per mutation (measured: azure-gpt-5.5 and azure-gpt-5.4-pro at reasoning_effort=medium)
freezes the whole window — while codex (~180s) and mini (~255s) finish fast at the SAME
effort. Worse, novelty-rejected / timed-out candidates skip the bandit update
(run_window reject path), so the bandit never learns a model is slow. This made
`medium` reasoning look unusable when it is in fact fine for the fast models.

FIX: track a per-arm latency EWMA (seconds), seeded from the measured values, and blend
an inverse-latency penalty into selection: ``p_i ∝ ucb_posterior_i · (1/latency_i)^k``,
floored at ``_MIN_PROB`` so every model stays reachable (honours "use all models"). Slow
arms get demoted to ~1-2% automatically; the bandit's reward/cost learning is untouched.
With this, a `medium`-reasoning, all-models run self-routes to codex/mini (fast + deep)
and only rarely probes gpt-5.5/pro. Live `latency_sec` from run_window refines the EWMA.

Modes:
  * select  : choose a model (marks it submitted). Blends UCB posterior × latency prior.
  * update  : fold an eval result back into the bandit; if `latency_sec` is present,
              update that arm's latency EWMA.
  * weights : read-only posterior + per-arm tallies (diagnostics; unchanged).

INPUT (stdin JSON): {mode, models, state_path, bandit_kwargs, force_explore, subset,
  seed, [update:] arm, reward, baseline, cost, latency_sec}
OUTPUT: select {model_name, probs, models}; update {updated}; weights {weights, counts, models}
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


# Per-arm latency prior (seconds) — measured 2026-05-27 with a realistic ~16KB mutation
# prompt at reasoning_effort=medium (codex/mini complete fast; gpt-5.5/pro run 25-40min).
# Used only to SEED the EWMA the first time; live `latency_sec` updates refine it.
_SEED_LATENCY_SEC: Dict[str, float] = {
    "azure-gpt-5.3-codex": 180.0,
    "azure-gpt-5.4-mini": 255.0,
    "azure-gpt-5.5": 1800.0,
    "azure-gpt-5.4-pro": 1800.0,
}
_DEFAULT_LATENCY_SEC = 600.0   # unknown arm -> mid-high (explored cautiously)
_LAT_PENALTY_EXP = 2.0         # inverse-latency exponent; 2.0 => strong demotion of slow arms
_LAT_EWMA_ALPHA = 0.4          # weight on the newest observation
_MIN_PROB = 0.01               # floor so every arm stays reachable (use-all-models)


def _lat_state_path(state_path) -> str:
    base = os.path.dirname(state_path) if state_path else "."
    return os.path.join(base, "latency_state.json")


def _load_latency(state_path) -> Dict[str, float]:
    p = _lat_state_path(state_path)
    if os.path.exists(p):
        try:
            with open(p) as f:
                d = json.load(f)
            return {str(k): float(v) for k, v in d.items()}
        except Exception:
            pass
    return dict(_SEED_LATENCY_SEC)  # seed on first use


def _save_latency(state_path, lat: Dict[str, float]) -> None:
    try:
        with open(_lat_state_path(state_path), "w") as f:
            json.dump(lat, f, indent=2)
    except Exception:
        pass


def _make_bandit(models: List[str], bandit_kwargs: Dict[str, Any], state_path):
    from shinka.llm import AsymmetricUCB

    bandit = AsymmetricUCB(arm_names=list(models), **(bandit_kwargs or {}))
    if state_path and os.path.exists(state_path):
        try:
            bandit.load_state(state_path)
        except Exception:
            pass
    return bandit


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    import numpy as np

    mode = payload.get("mode", "select")
    models: List[str] = list(payload["models"])
    state_path = payload.get("state_path")
    bandit_kwargs = payload.get("bandit_kwargs", {})
    seed = payload.get("seed")
    if seed is not None:
        np.random.seed(int(seed))

    bandit = _make_bandit(models, bandit_kwargs, state_path)

    if mode == "weights":
        try:
            probs = np.asarray(bandit.posterior(subset=payload.get("subset")), dtype=float)
            weights = {m: float(probs[i]) for i, m in enumerate(models)}
        except Exception:
            weights = {m: 1.0 / len(models) for m in models} if models else {}
        counts: Dict[str, Any] = {}
        try:
            st = bandit.get_state()
            names = list(st.get("arm_names", models))
            sub = st.get("n_submitted"); comp = st.get("n_completed"); tc = st.get("total_costs")
            for i, m in enumerate(names):
                counts[m] = {
                    "submitted": float(sub[i]) if sub is not None else None,
                    "completed": float(comp[i]) if comp is not None else None,
                    "cost": float(tc[i]) if tc is not None else None,
                }
        except Exception:
            pass
        # surface the latency EWMA too, so the orchestrator can see why an arm is demoted.
        return {"weights": weights, "counts": counts, "latency_sec": _load_latency(state_path), "models": models}

    if mode == "update":
        arm = payload["arm"]
        reward = payload.get("reward")
        baseline = payload.get("baseline")
        bandit.update(arm=arm, reward=reward, baseline=baseline)
        cost = payload.get("cost")
        if cost is not None:
            try:
                bandit.update_cost(arm=arm, cost=float(cost))
            except Exception:
                pass
        if state_path:
            bandit.save_state(state_path)
        # latency EWMA update (the wallclock the bandit itself can't see)
        lat_sec = payload.get("latency_sec")
        if lat_sec is not None:
            try:
                lat = _load_latency(state_path)
                prev = lat.get(arm, float(lat_sec))
                lat[arm] = _LAT_EWMA_ALPHA * float(lat_sec) + (1 - _LAT_EWMA_ALPHA) * prev
                _save_latency(state_path, lat)
            except Exception:
                pass
        return {"updated": True}

    # mode == "select"
    if payload.get("force_explore"):
        pool = payload.get("subset") or models
        model_name = pool[int(np.random.randint(len(pool)))]
        probs = [1.0 / len(pool)] * len(pool)
        bandit.update_submitted(model_name)
        if state_path:
            bandit.save_state(state_path)
        return {"model_name": model_name, "probs": probs, "models": list(pool), "explored": True}

    pool = payload.get("subset") or models
    try:
        post = np.asarray(bandit.posterior(subset=payload.get("subset")), dtype=float)
    except Exception:
        post = np.ones(len(pool), dtype=float)
    if post.shape[0] != len(pool) or not np.isfinite(post).all() or post.sum() <= 0:
        post = np.ones(len(pool), dtype=float)

    # latency prior: favour fast arms. (1/latency)^k, normalised.
    lat = _load_latency(state_path)
    lat_w = np.array(
        [(1.0 / max(lat.get(m, _DEFAULT_LATENCY_SEC), 1.0)) ** _LAT_PENALTY_EXP for m in pool],
        dtype=float,
    )
    if not np.isfinite(lat_w).all() or lat_w.sum() <= 0:
        lat_w = np.ones(len(pool), dtype=float)

    blended = post * lat_w
    if not np.isfinite(blended).all() or blended.sum() <= 0:
        blended = lat_w.copy()
    blended = blended / blended.sum()
    # Strict floor (clip-and-redistribute): every arm ends with p >= _MIN_PROB AND
    # sum(p)==1. A simple `maximum(p,_MIN_PROB); /=sum` would normalise the floor
    # back below _MIN_PROB whenever the posterior collapses to one arm (then sum
    # ~= 1 + (N-1)·_MIN_PROB), violating the "every model stays reachable" contract.
    below = blended < _MIN_PROB
    if below.any():
        n_above = int((~below).sum())
        if n_above == 0:
            blended = np.ones_like(blended) / len(blended)
        else:
            deficit = float((_MIN_PROB - blended[below]).sum())
            above_mass = float(blended[~below].sum())
            blended[below] = _MIN_PROB
            if above_mass > deficit:
                blended[~below] -= (blended[~below] / above_mass) * deficit
            else:  # not enough above-floor mass to cover; fall back to uniform.
                blended = np.ones_like(blended) / len(blended)
    idx = int(np.random.choice(len(pool), p=blended))
    model_name = pool[idx]
    bandit.update_submitted(model_name)
    if state_path:
        bandit.save_state(state_path)
    return {"model_name": model_name, "probs": [float(p) for p in blended.tolist()], "models": list(pool)}


if __name__ == "__main__":
    _common.run_main(main)

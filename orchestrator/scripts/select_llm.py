"""select_llm.py — pick which LLM to use for the next mutation; learn from results.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite the SELECTION
policy here (e.g. force re-exploration when the bandit collapses to one model and
J is flat). It embeds NO LLM call.

Design note (justified deviation from a pure port): shinka's ``AsymmetricUCB`` is
1400+ lines with asymmetric scaling, cost-aware blending, decay, and pickle state
that MUST stay compatible across runs. Re-porting it would be error-prone, so this
file WRAPS the bandit (parity + safe state) and exposes a thin, rewritable policy
layer on top — the ``force_explore`` override and arm ``subset`` are the levers a
rewrite uses. The bandit's state file is plumbing; a rewrite must not break it.

Two modes:
  * mode="select": choose a model for the next mutation (and mark it submitted).
  * mode="update": fold an evaluation result back into the bandit.

INPUT (stdin JSON):
  {
    "mode": "select" | "update",
    "models": [str],
    "state_path": str | null,        # pickle of bandit state; created if absent
    "bandit_kwargs": {..},           # e.g. {"cost_aware_coef": 0.5}
    "force_explore": false,          # rewrite lever: uniform instead of UCB
    "subset": [str] | null,          # restrict selection to these arms
    "seed": int | null,
    # update mode:
    "arm": str, "reward": float | null, "baseline": float | null, "cost": float | null
  }

OUTPUT (stdout JSON):
  select:  { "ok": true, "model_name": str, "probs": [float], "models": [str] }
  update:  { "ok": true, "updated": true }
  weights: { "ok": true, "weights": {model: prob}, "counts": {model: {...}}, "models": [str] }
           # read-only snapshot of the posterior + per-arm tallies for diagnostics
           # (no selection, no update, no state write) — fixes the dead
           # llm_bandit_weights sensor.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def _make_bandit(models: List[str], bandit_kwargs: Dict[str, Any], state_path):
    from shinka.llm import AsymmetricUCB

    bandit = AsymmetricUCB(arm_names=list(models), **(bandit_kwargs or {}))
    if state_path and os.path.exists(state_path):
        try:
            bandit.load_state(state_path)
        except Exception:
            pass  # incompatible/old state -> start fresh (logged by caller if needed)
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
        # Read-only posterior snapshot for diagnostics. `posterior()` is
        # deterministic (no RNG, unlike `select_llm`) and has no side effects, so
        # this never perturbs the bandit. Returns per-arm selection probabilities
        # plus cumulative submitted/completed/cost tallies (the better
        # collapse-detector). Loads state but never writes it.
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
        return {"weights": weights, "counts": counts, "models": models}

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
        return {"updated": True}

    # mode == "select"
    if payload.get("force_explore"):
        # Rewrite lever: ignore the (collapsed) posterior, explore uniformly.
        pool = payload.get("subset") or models
        model_name = pool[int(np.random.randint(len(pool)))]
        probs = [1.0 / len(pool)] * len(pool)
        bandit.update_submitted(model_name)
        if state_path:
            bandit.save_state(state_path)
        return {"model_name": model_name, "probs": probs, "models": list(pool), "explored": True}

    one_hot, probs = bandit.select_llm(subset=payload.get("subset"))
    one_hot = np.asarray(one_hot, dtype=float)
    model_name = models[int(one_hot.argmax())]
    bandit.update_submitted(model_name)
    if state_path:
        bandit.save_state(state_path)
    return {
        "model_name": model_name,
        "probs": [float(p) for p in np.asarray(probs, dtype=float).tolist()],
        "models": models,
    }


if __name__ == "__main__":
    _common.run_main(main)

"""record_policy.py — decide what derived signals to persist per candidate.

MUTABILITY: MUTABLE STRATEGY (cell A), the **memory concern**. The orchestrator
MAY rewrite this to log additional derived signals when it needs visibility the
current metadata doesn't give (e.g. to diagnose a reward problem it must first be
able to SEE reward-vs-improvement per candidate). It embeds NO LLM call.

It writes ONLY into the program's free-form ``metadata`` JSON blob — never the
sqlite schema (that is immutable foundation). Whatever you log here becomes
queryable via ``archive_query`` (which surfaces metadata) and is what
``diagnostics``/``run_journal`` can aggregate. If you add a signal here that a
consumer relies on, that consumer is part of the same concern — change both.

Default policy: passthrough the mutation facts + a few cheap derived signals
(improvement over parent, whether it improved, the reward that was used, novelty
similarity, transport/attempts). These are exactly the signals that let the
orchestrator spot cross-cutting issues from the journal.

INPUT (stdin JSON):
  {
    "eval": {"combined_score","correct","error_traceback", ...},
    "parent": {"combined_score"} | null,
    "mutation": {"patch_type","patch_name","num_applied","cost","model_name","transport","attempts"},
    "sample": {"parent_id","archive_inspiration_ids","top_k_inspiration_ids","needs_fix"},
    "novelty": {"max_similarity","n_compared"} | null,
    "reward": {"reward","baseline"} | null
  }

OUTPUT (stdout JSON):
  { "ok": true, "metadata": { ...fields to merge into program.metadata... } }
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    ev = payload.get("eval", {}) or {}
    parent = payload.get("parent") or {}
    mut = payload.get("mutation", {}) or {}
    sample = payload.get("sample", {}) or {}
    novelty = payload.get("novelty") or {}
    reward = payload.get("reward") or {}

    score = float(ev.get("combined_score", 0.0) or 0.0)
    parent_score = float(parent.get("combined_score", 0.0) or 0.0)
    improvement = score - parent_score

    # C2: persist the per-eval runtime + a timed_out flag so a slow-but-correct (or
    # timed-out) candidate is VISIBLE to downstream prompt builders. construct_mutation_prompt
    # uses these (vs task.eval_time) to surface a bounded runtime-budget caution to the LLM so
    # future candidates finish within the budget. These are NUMERIC/boolean (not evaluator text),
    # so they survive use_text_feedback:false and never echo a traceback.
    _runtime = ev.get("runtime_sec")

    metadata: Dict[str, Any] = {
        # mutation facts (passthrough)
        "patch_type": mut.get("patch_type"),
        "patch_name": mut.get("patch_name"),
        "num_applied": mut.get("num_applied"),
        "api_cost": mut.get("cost", 0.0),
        "model_name": mut.get("model_name"),
        "transport": mut.get("transport"),
        "mutation_attempts": mut.get("attempts"),
        # derived signals (the evolvable part — add more here when needed)
        "improvement_over_parent": improvement,
        "is_improvement": improvement > 0,
        "fix_mode": bool(sample.get("needs_fix", False)),
        "reward_used": reward.get("reward"),
        "reward_baseline": reward.get("baseline"),
        "novelty_max_similarity": novelty.get("max_similarity"),
        "novelty_n_compared": novelty.get("n_compared"),
        # runtime signals (C2) — runtime always recorded; timed_out only when True (compact)
        "runtime_sec": (float(_runtime) if _runtime is not None else None),
        "timed_out": (True if ev.get("timed_out") else None),
    }
    # drop Nones so metadata stays compact
    metadata = {k: v for k, v in metadata.items() if v is not None}
    return {"metadata": metadata}


if __name__ == "__main__":
    _common.run_main(main)

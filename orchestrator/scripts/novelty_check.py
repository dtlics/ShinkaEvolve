"""novelty_check.py — reject candidates too similar to existing programs.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite this file —
the similarity threshold and accept/reject logic throttle diversity, a classic
plateau knob. It embeds NO LLM call in this default port (shinka's optional
LLM-as-judge is gated and rare; if a rewrite enables it, do not change response
parsing — see taxonomy.md).

This is a port of shinka's default novelty path: compare the candidate's code
embedding against the (optionally island-filtered) correct archive by cosine
similarity, and REJECT when the max similarity ≥ ``code_embed_sim_threshold``
(default 0.99 — i.e. a near-duplicate). Accept otherwise, or when there is
nothing to compare against. test_parity.py checks the cosine + threshold
decision matches a direct computation.

INPUT (stdin JSON):
  {
    "db_path": str, "db_config": {..}, "embedding_model": str,
    "candidate_embedding": [float],
    "code_embed_sim_threshold": 0.99,
    "island_idx": int | null        # restrict comparison to one island
  }

OUTPUT (stdout JSON):
  { "ok": true, "accept": bool, "max_similarity": float,
    "most_similar_id": str | null, "n_compared": int }
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from . import _common
    from . import archive_query
except ImportError:
    import _common  # type: ignore
    import archive_query  # type: ignore


def _cosine(a: List[float], b: List[float]) -> float:
    import numpy as np

    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    cand = payload.get("candidate_embedding") or []
    threshold = float(payload.get("code_embed_sim_threshold", 0.99))
    island_idx = payload.get("island_idx")

    if not cand:
        # No embedding to judge -> accept (matches shinka's "no embedding" skip).
        return {"accept": True, "max_similarity": 0.0, "most_similar_id": None, "n_compared": 0}

    programs = archive_query.main(
        {
            "db_path": payload["db_path"],
            "db_config": payload.get("db_config", {}),
            "embedding_model": payload.get("embedding_model", "text-embedding-3-small"),
            "query_type": "all",
            "include_embedding": True,
        }
    )["result"]

    max_sim = 0.0
    most_similar_id: Optional[str] = None
    n = 0
    for p in programs:
        if not p.get("correct"):
            continue
        if island_idx is not None and p.get("island_idx") != island_idx:
            continue
        emb = p.get("embedding") or []
        if not emb:
            continue
        n += 1
        sim = _cosine(cand, emb)
        if sim > max_sim:
            max_sim = sim
            most_similar_id = p.get("id")

    accept = max_sim < threshold
    return {
        "accept": bool(accept),
        "max_similarity": max_sim,
        "most_similar_id": most_similar_id,
        "n_compared": n,
    }


if __name__ == "__main__":
    _common.run_main(main)

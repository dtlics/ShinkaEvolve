"""novelty_check.py — reject candidates too similar to existing programs.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite this file —
the similarity threshold and accept/reject logic throttle diversity, a classic
plateau knob. It embeds NO LLM call in this default port (shinka's optional
LLM-as-judge is gated and rare; if a rewrite enables it, do not change response
parsing — see taxonomy.md).

This computes the near-duplicate decision: compare the candidate's code embedding
against the (optionally island-filtered, non-tombstoned) correct archive by cosine
similarity; ``accept=False`` when the max similarity ≥ ``code_embed_sim_threshold``
(default 0.99 — a near-duplicate), and it also returns the most-similar program's id
AND score so the CALLER can KEEP THE BETTER of the pair (H5): run_window evaluates a
near-duplicate, compares scores, and either drops the worse newcomer or archives it
and tombstones the worse incumbent. (This file only DECIDES near-duplication + surfaces
the comparison data; the keep-better eviction is wired in run_window.) test_parity.py
checks the cosine + threshold decision matches a direct computation. The per-candidate
audit trail of these decisions (one row per evaluated-correct candidate: the four-way
decision + max_similarity + most_similar_id + diff_lines) is written to journal/novelty.jsonl
by run_window (the caller), and the per-window aggregate rate to window diagnostics.

INPUT (stdin JSON):
  {
    "db_path": str, "db_config": {..}, "embedding_model": str,
    "candidate_embedding": [float],
    "code_embed_sim_threshold": 0.99,
    "island_idx": int | null        # restrict comparison to one island
  }

OUTPUT (stdout JSON):
  { "ok": true, "accept": bool, "max_similarity": float,
    "most_similar_id": str | null, "most_similar_score": float | null,  # H5: keep-the-better
    "n_compared": int }
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
        # N6: include most_similar_score (None) so this early return honors the same
        # OUTPUT contract as the main return — a caller that reads it for keep-the-better
        # gets a consistent shape on every path.
        return {"accept": True, "max_similarity": 0.0, "most_similar_id": None,
                "most_similar_score": None, "n_compared": 0}

    programs = archive_query.main(
        {
            "db_path": payload["db_path"],
            "db_config": payload.get("db_config", {}),
            "embedding_model": payload.get("embedding_model", "azure-text-embedding-3-small"),
            "query_type": "all",
            "include_embedding": True,
            "include_metadata": True,
        }
    )["result"]

    max_sim = 0.0
    most_similar_id: Optional[str] = None
    most_similar_score: Optional[float] = None
    n = 0
    for p in programs:
        if not p.get("correct"):
            continue
        # H5: a tombstoned program (de-archived — e.g. EVICTED as the worse of a
        # near-duplicate pair by keep-the-better, or repair-removed) must NOT keep
        # blocking new candidates.
        if (p.get("metadata") or {}).get("repair_tombstoned") is True:
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
            most_similar_score = p.get("combined_score")

    accept = max_sim < threshold
    return {
        "accept": bool(accept),
        "max_similarity": max_sim,
        "most_similar_id": most_similar_id,
        # H5: the incumbent's score so the caller can KEEP THE BETTER of a near-dup pair.
        "most_similar_score": most_similar_score,
        "n_compared": n,
    }


if __name__ == "__main__":
    _common.run_main(main)

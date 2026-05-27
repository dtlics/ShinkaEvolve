"""island_policy.py — decide island fork / migrate / retire at window boundaries.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite this file to
repair population structure when an island's diversity collapses. It embeds NO
LLM call, and it only DECIDES — the actual island_idx mutations are executed by
immutable plumbing (archive_record / shinka's island manager).

This is a port of shinka's default island decisions:
  * spawn  — dynamic-island stagnation rule: enable_dynamic_islands AND
             (current_gen - generation_of_best) ≥ stagnation_threshold
             (cf. ProgramDatabase.is_stagnant).
  * migrate— elitist migration cadence: migration_rate > 0 AND num_islands ≥ 2
             AND current_gen % migration_interval == 0.
  * retire — not part of shinka's default (it spawns rather than retires); left
             as a rewrite-extensible recommendation (default null).

INPUT (stdin JSON):
  {
    "db_path": str, "db_config": {..}, "embedding_model": str,
    "current_generation": int | null   # if null, taken as max generation in archive
  }

OUTPUT (stdout JSON):
  {
    "ok": true,
    "actions": {"spawn": bool, "migrate": bool, "retire_island": int | null},
    "reasons": {..}, "current_generation": int, "best_generation": int,
    "gens_since_best": int
  }
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from . import _common
    from . import archive_query
except ImportError:
    import _common  # type: ignore
    import archive_query  # type: ignore


def island_health(
    islands_summary,
    db_path=None,
    db_config=None,
    embedding_model=None,
):
    """Per-island health rows for the window diagnostics. MUTABLE POLICY.

    This is deliberately a **toy** default so the metric DEFINITION lives in a
    mutable policy file rather than baked into the immutable sensor: `diversity`
    is just the island's program count and `stagnation_count` is left None. The
    orchestrator MAY rewrite this (e.g. compute real diversity as the embedding
    spread within each island — the ``db_*`` params are threaded through so a
    future version can query embeddings — and track a genuine per-island
    generations-since-best stagnation count). Until then, downstream readers must
    treat `diversity` as a population count, not a spread.

    ``islands_summary`` is the per-island list from archive_query's "summary"
    ({island_idx, best, count}). Returns a list of
    {id, best, diversity, stagnation_count}.
    """
    out = []
    for isl in islands_summary or []:
        out.append(
            {
                "id": isl.get("island_idx"),
                "best": isl.get("best"),
                "diversity": isl.get("count"),  # TOY: count, not a spread metric
                "stagnation_count": None,
            }
        )
    return out


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    db_config = payload.get("db_config", {})
    programs = archive_query.main(
        {
            "db_path": payload["db_path"],
            "db_config": db_config,
            "embedding_model": payload.get("embedding_model", "text-embedding-3-small"),
            "query_type": "all",
        }
    )["result"]

    gens = [int(p.get("generation", 0) or 0) for p in programs]
    current_generation = payload.get("current_generation")
    if current_generation is None:
        current_generation = max(gens) if gens else 0
    current_generation = int(current_generation)

    correct = [p for p in programs if p.get("correct")]
    if correct:
        best = max(correct, key=lambda p: p.get("combined_score", 0.0))
        best_generation = int(best.get("generation", 0) or 0)
    else:
        best_generation = 0
    gens_since_best = current_generation - best_generation

    # Config knobs (the evolvable thresholds).
    enable_dynamic = bool(db_config.get("enable_dynamic_islands", False))
    stagnation_threshold = int(db_config.get("stagnation_threshold", 100))
    migration_rate = float(db_config.get("migration_rate", 0.0))
    migration_interval = int(db_config.get("migration_interval", 10))
    num_islands = int(db_config.get("num_islands", 2))

    spawn = enable_dynamic and gens_since_best >= stagnation_threshold
    migrate = (
        migration_rate > 0.0
        and num_islands >= 2
        and migration_interval > 0
        and current_generation % migration_interval == 0
        and current_generation > 0
    )

    return {
        "actions": {"spawn": bool(spawn), "migrate": bool(migrate), "retire_island": None},
        "reasons": {
            "enable_dynamic_islands": enable_dynamic,
            "stagnation_threshold": stagnation_threshold,
            "gens_since_best": gens_since_best,
            "migration_rate": migration_rate,
            "migration_interval": migration_interval,
        },
        "current_generation": current_generation,
        "best_generation": best_generation,
        "gens_since_best": gens_since_best,
    }


if __name__ == "__main__":
    _common.run_main(main)

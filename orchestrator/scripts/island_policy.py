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
    "current_generation": int | null,  # if null, taken as max generation in archive
    "apply": false                      # opt-in: EXECUTE the decided actions (H8/O3)
  }

OUTPUT (stdout JSON):
  {
    "ok": true,
    "actions": {"spawn": bool, "migrate": bool, "retire_island": int | null},
    "executed": {"migrated": bool, "spawned": bool, "retired": int | null} | null,
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


def _embedding_spread(members):
    """Mean pairwise cosine DISTANCE (1 - cosine sim) among members carrying an
    embedding. None when < 2 have embeddings (caller falls back to the count)."""
    embs = [p.get("embedding") for p in members if p.get("embedding")]
    if len(embs) < 2:
        return None
    import numpy as np

    M = np.asarray(embs, dtype=float)
    norms = np.linalg.norm(M, axis=1)
    norms[norms == 0] = 1.0
    M = M / norms[:, None]
    sims = M @ M.T
    iu = np.triu_indices(len(embs), k=1)
    dists = 1.0 - sims[iu]
    return float(dists.mean()) if dists.size else None


def _gens_since_island_best(members, current_generation):
    """current_generation minus the generation of the island's best CORRECT
    member. None when the island has no correct member."""
    correct = [p for p in members if p.get("correct")]
    if not correct:
        return None
    best = max(correct, key=lambda p: p.get("combined_score", 0.0) or 0.0)
    return int(current_generation) - int(best.get("generation", 0) or 0)


def island_health(
    islands_summary,
    db_path=None,
    db_config=None,
    embedding_model=None,
):
    """Per-island health rows for the window diagnostics. MUTABLE POLICY.

    Real metrics (M12 fix): ``diversity`` is the mean pairwise cosine DISTANCE of
    the island's program embeddings — a genuine spread, so two islands collapsed
    onto one genome read LOW even with many members (the toy count could not). And
    ``stagnation_count`` is generations since the island's best correct program.
    Both fall back gracefully (diversity -> population count when < 2 embeddings;
    stagnation_count -> None when no correct member), and this NEVER raises — the
    diagnostics sensor depends on it. ``count`` is kept as an additive field.

    The orchestrator reads ``diversity``/``stagnation_count`` to decide WHEN to
    author a per-island brief (``island_brief.py``) to re-differentiate a
    converging island. ``islands_summary`` is archive_query "summary"'s per-island
    list ({island_idx, best, count}).
    """
    rows_by_island: Dict[Any, list] = {}
    current_generation = 0
    if db_path is not None:
        try:
            progs = archive_query.main(
                {
                    "db_path": db_path,
                    "db_config": db_config or {},
                    "embedding_model": embedding_model or "text-embedding-3-small",
                    "query_type": "all",
                    "include_embedding": True,
                }
            )["result"]
            for p in progs:
                current_generation = max(current_generation, int(p.get("generation", 0) or 0))
                rows_by_island.setdefault(p.get("island_idx"), []).append(p)
        except Exception:
            rows_by_island = {}

    out = []
    for isl in islands_summary or []:
        idx = isl.get("island_idx")
        members = rows_by_island.get(idx, [])
        spread = _embedding_spread(members)
        out.append(
            {
                "id": idx,
                "best": isl.get("best"),
                # real spread when embeddings exist; else fall back to the count.
                "diversity": spread if spread is not None else isl.get("count"),
                "stagnation_count": _gens_since_island_best(members, current_generation),
                "count": isl.get("count"),  # additive: population size kept separately
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

    # H8/O3 (opt-in): when the caller asks to APPLY, execute the decided actions via
    # the FOUNDATION executor (db.apply_island_actions) — this file only DECIDES; the
    # mutation lives in immutable plumbing. Default (no apply) is decision-only, so a
    # rewrite of THIS policy's spawn/migrate logic now actually TAKES EFFECT when the
    # orchestrator runs with evo.island_policy_driven (fixes H8's dead-code lever).
    executed = None
    if payload.get("apply"):
        from shinka.database import ProgramDatabase, DatabaseConfig

        _cfg_kwargs = dict(db_config)
        _cfg_kwargs["db_path"] = payload["db_path"]
        _db = ProgramDatabase(
            DatabaseConfig(**_cfg_kwargs),
            embedding_model=payload.get("embedding_model", "text-embedding-3-small"),
            read_only=False,
        )
        try:
            executed = _db.apply_island_actions(
                {"spawn": bool(spawn), "migrate": bool(migrate), "retire_island": None},
                current_generation,
            )
        finally:
            _db.close()

    return {
        "actions": {"spawn": bool(spawn), "migrate": bool(migrate), "retire_island": None},
        "executed": executed,
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

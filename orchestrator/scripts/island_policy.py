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
                    "embedding_model": embedding_model or "azure-text-embedding-3-small",
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
                # M28: disambiguate the `diversity` UNITS so a reader never compares a cosine
                # spread (≈0..2, real embedding diversity) against a raw member count. The typed
                # `cosine_spread` is None when <2 members carry an embedding (the fallback case),
                # and `diversity_kind` says which basis `diversity` actually used this window.
                "diversity_kind": "cosine_spread" if spread is not None else "member_count",
                "cosine_spread": spread,
                "member_count": isl.get("count"),
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
            "embedding_model": payload.get("embedding_model", "azure-text-embedding-3-small"),
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

    # H10: decouple the POLICY's spawn/migrate decision from the db_config AUTO-TRIGGER knobs
    # (enable_dynamic_islands / migration_rate>0) that ALSO drive the foundation add()-time
    # maintenance. Keying on the SAME knobs made island_policy_driven a no-op under its
    # documented prerequisite (auto-triggers OFF → both decisions always False, result
    # discarded) and a DOUBLE-execution when the knobs are flipped ON. The policy now reads its
    # OWN payload keys, defaulting to the db_config values for back-compat — so the correct way
    # to drive island_policy_driven is: add()-time triggers OFF (enable_dynamic_islands=false,
    # migration_rate=0) AND these policy_* keys set.
    _policy_spawn_enabled = bool(payload.get("policy_spawn_enabled", enable_dynamic))
    _policy_spawn_stag = int(payload.get("policy_spawn_stagnation", stagnation_threshold))
    _policy_migrate_enabled = bool(payload.get("policy_migrate_enabled", migration_rate > 0.0))
    _policy_migrate_interval = int(payload.get("policy_migrate_interval", migration_interval))
    spawn = _policy_spawn_enabled and gens_since_best >= _policy_spawn_stag

    # M15: spawn-ONCE-per-stagnation-episode. The raw rule (gens_since_best >= threshold) stays
    # TRUE every window while the island is stuck, so without a durable marker the policy would
    # spawn a NEW island EVERY window it stays stagnant — flooding the population. The harness
    # carries `last_policy_spawn_generation` across windows (in the window diag) and passes it
    # back here; this is the PRIMARY guard (archive-derived best_generation alone can't say
    # "already spawned THIS episode"). Suppress a repeat spawn while the marker is at/after the
    # current best generation (we already spawned since the last improvement), unless an optional
    # cooldown of generations has elapsed since that spawn. A new improvement (best_generation
    # advancing past the marker) re-arms spawning automatically.
    _last_spawn_gen = payload.get("last_policy_spawn_generation")
    _spawn_cooldown = int(payload.get("policy_spawn_cooldown", 0) or 0)
    _spawn_suppressed = False
    if spawn and _last_spawn_gen is not None:
        _last_spawn_gen = int(_last_spawn_gen)
        if _last_spawn_gen >= best_generation and (
            _spawn_cooldown <= 0 or (current_generation - _last_spawn_gen) < _spawn_cooldown
        ):
            spawn = False
            _spawn_suppressed = True
    migrate = (
        _policy_migrate_enabled
        and num_islands >= 2
        and _policy_migrate_interval > 0
        and current_generation % _policy_migrate_interval == 0
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
            embedding_model=payload.get("embedding_model", "azure-text-embedding-3-small"),
            read_only=False,
        )
        try:
            executed = _db.apply_island_actions(
                {"spawn": bool(spawn), "migrate": bool(migrate), "retire_island": None},
                current_generation,
            )
        finally:
            _db.close()

    # M15: advance the durable marker ONLY when a spawn actually EXECUTED this window; otherwise
    # carry the prior marker forward (suppressed / decision-only windows must not advance it, or
    # the cooldown would never elapse). The harness stamps this back into the window diag.
    _spawned = bool((executed or {}).get("spawned"))
    if _spawned:
        _new_spawn_marker = current_generation
    elif _last_spawn_gen is not None:
        _new_spawn_marker = int(_last_spawn_gen)
    else:
        _new_spawn_marker = None

    return {
        "actions": {"spawn": bool(spawn), "migrate": bool(migrate), "retire_island": None},
        "executed": executed,
        "last_policy_spawn_generation": _new_spawn_marker,
        "spawn_suppressed_this_episode": _spawn_suppressed,
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

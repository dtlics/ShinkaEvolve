"""archive_query.py — read from the archive (by id, score, lineage, failures).

MUTABILITY: IMMUTABLE PLUMBING. Do not modify as part of a strategy rewrite.
Read-only SQLite access via shinka's ``ProgramDatabase`` (opened read_only).
Embeds NO LLM call. The orchestrator and harness depend on this shape being
stable.

INPUT (stdin JSON):
  {
    "db_path": str,
    "db_config": {..},
    "embedding_model": str,
    "query_type": "get" | "ancestry" | "best" | "top_n" | "by_generation"
                | "recent_failures" | "all" | "count" | "summary"
                | "island_brief",
    # query-specific params:
    "program_id": str,              # get / ancestry
    "island_idx": int,              # island_brief (latest per-island direction)
    "max_ancestors": 10,            # ancestry
    "metric": str | null,           # best
    "n": 10,                        # top_n / recent_failures / all (cap)
    "generation": int,              # by_generation
    "correct_only": true,           # top_n
    "include_code": false,
    "include_embedding": false,
    "include_metadata": false,   # surface the per-program metadata blob (the role-2 lock-out read leans on this)
    "code_preview_chars": 0
  }

OUTPUT (stdout JSON): { "ok": true, "result": <program summary | [summaries] | dict> }
"""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def _summ(payload, program):
    return _common.program_summary(
        program,
        include_code=bool(payload.get("include_code", False)),
        include_embedding=bool(payload.get("include_embedding", False)),
        include_metadata=bool(payload.get("include_metadata", False)),
        code_preview_chars=int(payload.get("code_preview_chars", 0) or 0),
    )


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    from shinka.database import ProgramDatabase, DatabaseConfig

    db_path = payload["db_path"]
    db_config_kwargs = dict(payload.get("db_config", {}))
    db_config_kwargs["db_path"] = db_path
    embedding_model = payload.get("embedding_model", "text-embedding-3-small")
    query_type = payload.get("query_type", "summary")

    config = DatabaseConfig(**db_config_kwargs)
    db = ProgramDatabase(config, embedding_model=embedding_model, read_only=True)
    try:
        result = _dispatch(db, query_type, payload)
    finally:
        db.close()
    return {"result": result}


def _dispatch(db, query_type: str, payload: Dict[str, Any]):
    if query_type == "get":
        return _summ(payload, db.get(payload["program_id"]))

    if query_type == "ancestry":
        chain = db.get_ancestry(
            payload["program_id"], int(payload.get("max_ancestors", 10))
        )
        return [_summ(payload, p) for p in chain]

    if query_type == "best":
        return _summ(payload, db.get_best_program(payload.get("metric")))

    if query_type == "island_brief":
        # Latest per-island DIRECTION the orchestrator authored (None if none).
        # Calls the DB API (never raw SQL) — keeps archive_query API-only.
        return db.get_latest_meta_brief(int(payload["island_idx"]))

    # The remaining query types derive from the full program list. The archive
    # is small in practice; an efficiency note is in NOTES.md for long runs.
    programs: List[Any] = db.get_all_programs()

    if query_type == "all":
        n = int(payload.get("n", 0) or 0)
        chosen = programs[:n] if n > 0 else programs
        return [_summ(payload, p) for p in chosen]

    if query_type == "count":
        correct = sum(1 for p in programs if getattr(p, "correct", False))
        return {
            "total": len(programs),
            "correct": correct,
            "incorrect": len(programs) - correct,
        }

    if query_type == "top_n":
        correct_only = bool(payload.get("correct_only", True))
        pool = [p for p in programs if (not correct_only or getattr(p, "correct", False))]
        pool.sort(key=lambda p: getattr(p, "combined_score", 0.0), reverse=True)
        n = int(payload.get("n", 10))
        return [_summ(payload, p) for p in pool[:n]]

    if query_type == "by_generation":
        gen = int(payload["generation"])
        sel = [p for p in programs if getattr(p, "generation", None) == gen]
        return [_summ(payload, p) for p in sel]

    if query_type == "recent_failures":
        fails = [p for p in programs if not getattr(p, "correct", False)]
        fails.sort(key=lambda p: getattr(p, "generation", 0), reverse=True)
        n = int(payload.get("n", 10))
        return [_summ(payload, p) for p in fails[:n]]

    if query_type == "summary":
        correct = [p for p in programs if getattr(p, "correct", False)]
        best = max(
            (getattr(p, "combined_score", 0.0) for p in correct), default=None
        )
        per_island: Dict[int, Dict[str, Any]] = {}
        for p in programs:
            isl = getattr(p, "island_idx", None)
            if isl is None:
                continue
            bucket = per_island.setdefault(
                isl, {"island_idx": isl, "count": 0, "best": None}
            )
            bucket["count"] += 1
            if getattr(p, "correct", False):
                sc = getattr(p, "combined_score", 0.0)
                if bucket["best"] is None or sc > bucket["best"]:
                    bucket["best"] = sc
        tombstoned = sum(
            1 for p in programs
            if ((getattr(p, "metadata", None) or {}).get("repair_tombstoned") is True)
        )
        # H3: of the tombstoned rows, count ONLY the repair-removed INCORRECT ones for the
        # errored_fraction numerator. A keep-the-better evictee is a CORRECT program, so
        # `not correct` excludes it robustly (old + new DBs); tombstone_reason makes the
        # distinction explicit where present (never count a "novelty_evict").
        errored_tombstoned = sum(
            1 for p in programs
            if ((getattr(p, "metadata", None) or {}).get("repair_tombstoned") is True)
            and not getattr(p, "correct", False)
            and ((getattr(p, "metadata", None) or {}).get("tombstone_reason") != "novelty_evict")
        )
        return {
            "total": len(programs),
            "correct": len(correct),
            # P5: repair-tombstoned programs, EXCLUDED from the errored_fraction trigger
            # (diagnostics) so repair mode releases once dead programs are removed.
            "tombstoned_count": tombstoned,
            # H3: only the INCORRECT (repair-removed) tombstones — a CORRECT keep-the-better
            # evictee is NOT subtracted from the errored numerator (it was never errored).
            "errored_tombstoned_count": errored_tombstoned,
            "best_score": best,
            "islands": sorted(per_island.values(), key=lambda b: b["island_idx"]),
        }

    raise ValueError(f"unknown query_type: {query_type!r}")


if __name__ == "__main__":
    _common.run_main(main)

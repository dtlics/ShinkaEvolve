"""archive_record.py — persist a candidate program into the archive.

MUTABILITY: IMMUTABLE PLUMBING. Do not modify as part of a strategy rewrite.
This wraps shinka's ``ProgramDatabase.add`` (which assigns the island, computes
complexity, updates the elite archive, schedules migration, and may spawn a new
island). It embeds NO LLM call. Corrupting it corrupts the whole search.

Note: the island-policy *decisions* (when to migrate/spawn) are evolvable and
live in ``island_policy.py``; this file only executes the persistence + the
maintenance shinka already does on insert.

INPUT (stdin JSON):
  {
    "db_path": str,
    "db_config": {..},              # DatabaseConfig kwargs (db_path injected here)
    "embedding_model": str,
    "program": {                    # Program dataclass fields; unknown keys ignored
       "id": str | null,            # generated if null
       "code": str,                 # required
       "generation": int,
       "parent_id": str | null,
       "combined_score": float,
       "correct": bool,
       "public_metrics": {..}, "private_metrics": {..},
       "error_traceback": str | null,
       "code_diff": str | null,
       "embedding": [float], "archive_inspiration_ids": [str],
       "top_k_inspiration_ids": [str], "metadata": {..}, ...
    },
    "defer_maintenance": false,
    "verbose": false
  }

OUTPUT (stdout JSON):
  { "ok": true, "program_id": str, "combined_score": float, "correct": bool,
    "island_idx": int | null, "in_archive": bool }
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, Dict

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def _build_program(program_fields: Dict[str, Any]):
    from shinka.database import Program

    valid = {f.name for f in dataclasses.fields(Program)}
    kwargs = {k: v for k, v in program_fields.items() if k in valid}
    if not kwargs.get("id"):
        kwargs["id"] = str(uuid.uuid4())
    if "code" not in kwargs:
        raise ValueError("program.code is required")
    return Program(**kwargs)


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    from shinka.database import ProgramDatabase, DatabaseConfig

    db_path = payload["db_path"]
    db_config_kwargs = dict(payload.get("db_config", {}))
    db_config_kwargs["db_path"] = db_path
    embedding_model = payload.get("embedding_model", "azure-text-embedding-3-small")

    config = DatabaseConfig(**db_config_kwargs)
    db = ProgramDatabase(config, embedding_model=embedding_model, read_only=False)
    try:
        program = _build_program(payload["program"])
        program_id = db.add(
            program,
            verbose=bool(payload.get("verbose", False)),
            defer_maintenance=bool(payload.get("defer_maintenance", False)),
        )
        # Re-read so the island assignment + archive membership decided during
        # add() are reflected in the response.
        persisted = db.get(program_id)
    finally:
        db.close()

    return {
        "program_id": program_id,
        "combined_score": float(getattr(persisted, "combined_score", 0.0) or 0.0),
        "correct": bool(getattr(persisted, "correct", False)),
        "island_idx": getattr(persisted, "island_idx", None),
        "in_archive": bool(getattr(persisted, "in_archive", False)),
    }


if __name__ == "__main__":
    _common.run_main(main)

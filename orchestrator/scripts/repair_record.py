"""repair_record.py — record the outcome of a FAILED repair attempt (P5 repair mode).

MUTABILITY: IMMUTABLE PLUMBING. A thin stdin/stdout wrapper over the foundation DB
ops ``db.append_program_error`` / ``db.tombstone_program``. It embeds NO LLM call. The
repair POLICY (when repair mode is on, the trigger fraction, the attempt cap) lives in
the mutable ``sample_parent`` selection + the ``run_window`` wiring; this script just
persists the result.

When a repair generation fails to fix an errored program, NO new child is added;
instead the (truncated) error is appended to that errored program's OWN record and its
repair-attempt count is bumped. If the program has now failed repair ``attempt_cap``
times (default 2), it is also TOMBSTONED in the SAME call — de-archived
non-destructively (row + island_idx + lineage preserved, just removed from the
sampling pool), AFTER the strike-N error is recorded.

INPUT (stdin JSON):
  {
    "db_path": str, "db_config": {..}, "embedding_model": str,
    "program_id": str,
    "action": "append_fail" | "tombstone",
    "traceback_chunk": str | null,   # the failed repair's error (action=append_fail)
    "attempt_cap": int,              # default 2; also tombstone once attempts >= cap
    "reason": "repair" | "novelty_evict"  # H3: WHY (action=tombstone); default "repair"
  }

OUTPUT (stdout JSON):
  { "ok": true, "program_id": str, "repair_attempts": int, "tombstoned": bool }
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from . import _common
except ImportError:  # when run as a script, not a package
    import _common  # type: ignore


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    from shinka.database import ProgramDatabase, DatabaseConfig

    db_path = payload["db_path"]
    cfg_kwargs = dict(payload.get("db_config", {}))
    cfg_kwargs["db_path"] = db_path
    config = DatabaseConfig(**cfg_kwargs)
    embedding_model = payload.get("embedding_model", "azure-text-embedding-3-small")
    program_id = str(payload["program_id"])
    action = str(payload.get("action", "append_fail"))
    attempt_cap = int(payload.get("attempt_cap", 2) or 2)
    # H3: WHY this row is tombstoned. The keep-the-better caller (run_window novelty
    # resolve) passes reason="novelty_evict" (a CORRECT incumbent); the repair path uses
    # the default "repair" (an INCORRECT program). errored_fraction counts only "repair".
    reason = str(payload.get("reason", "repair"))

    db = ProgramDatabase(config, embedding_model=embedding_model, read_only=False)
    try:
        repair_attempts = 0
        tombstoned = False
        if action == "tombstone":
            tombstoned = bool(db.tombstone_program(program_id, reason=reason))
            _p = db.get(program_id)
            repair_attempts = int(((_p.metadata if _p else None) or {}).get("repair_attempts", 0) or 0)
        else:  # "append_fail": record the failure, and tombstone once the cap is hit
            repair_attempts = int(
                db.append_program_error(program_id, payload.get("traceback_chunk", "") or "")
            )
            if repair_attempts >= attempt_cap:
                # The strike-N error is already on the record; now remove it from the
                # sampling pool (append-then-tombstone in one call) — a repair removal.
                tombstoned = bool(db.tombstone_program(program_id, reason="repair"))
    finally:
        db.close()
    return {"program_id": program_id, "repair_attempts": repair_attempts,
            "tombstoned": tombstoned}


if __name__ == "__main__":
    _common.run_main(main)

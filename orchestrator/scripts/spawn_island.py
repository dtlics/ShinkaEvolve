"""spawn_island.py — on-demand spawn a new island seeded from an existing program.

FOUNDATION capability (shinka ``ProgramDatabase.spawn_island_from_program``): give a
novel deep-research direction its OWN island so it isn't out-competed before it
matures. This is the executable form of the DR-triage "novel → new island" branch
(SKILL.md Boot/DR): the orchestrator first GROUNDS the direction (a normal,
web-grounded pro mutation that produces a correct program), then calls THIS to copy
that program onto a fresh island as its root.

Honors ``db_config.max_islands``: at the cap, the worst island is retired
NON-DESTRUCTIVELY (de-archived + island_idx nulled, rows preserved) and its index
reused; island 0 and the global-best island are protected. ``max_islands=0``
(default) = unbounded (always a fresh index).

It embeds NO LLM call — pure DB op. NOT a strategy file; it's a thin wrapper over the
foundation method the orchestrator invokes deliberately.

INPUT (stdin JSON):
  {
    "db_path": str,
    "db_config": {.. incl. num_islands, max_islands, island_evict_strategy ..},
    "embedding_model": str,
    "program_id": str          # the grounded program to seed the new island with
  }

OUTPUT (stdout JSON):
  { "ok": true, "island_idx": int | null, "program_id": str }
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    from shinka.database import ProgramDatabase, DatabaseConfig

    db_path = payload["db_path"]
    cfg_kwargs = dict(payload.get("db_config", {}))
    cfg_kwargs["db_path"] = db_path
    config = DatabaseConfig(**cfg_kwargs)
    embedding_model = payload.get("embedding_model", "azure-text-embedding-3-small")

    db = ProgramDatabase(config, embedding_model=embedding_model, read_only=False)
    try:
        island_idx = db.spawn_island_from_program(payload["program_id"])
    finally:
        db.close()
    return {"island_idx": island_idx, "program_id": payload["program_id"]}


if __name__ == "__main__":
    _common.run_main(main)

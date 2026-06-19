"""spawn_island.py — on-demand spawn a new island seeded from an existing program.

FOUNDATION capability (shinka ``ProgramDatabase.spawn_island_from_program``): give a
novel discovery direction its OWN island so it isn't out-competed before it
matures. This is the executable form of the discovery-triage "novel → new island"
branch (SKILL.md Boot/triage): the orchestrator first GROUNDS the direction (a normal,
web-grounded pro mutation that produces a correct program), then calls THIS to copy
that program onto a fresh island as its root.

The technique seeded here MUST originate from an in-interval discovery round (DR
round) — EXACTLY ONE OF R1 (Azure deep research, ``deep_research.py``, stub
``kind="dr"``) OR R2 (archive-analyst subagent, stub ``kind="archive_analyst"``) —
logged THIS control-return interval. The PRIMARY fail-closed gate (DEC-7) at the top
of ``main`` enforces this: it refuses to seed any island unless
``journal.discovery_in_interval(results_dir)`` returns a non-empty list of usable
in-interval stubs. A brainstormed/tournament-over-own-ideas technique has no stub and
is refused by construction.

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
    "program_id": str,         # the grounded program to seed the new island with
    "results_dir": str,        # REQUIRED — run journal dir; the PRIMARY gate reads
                               #   journal.discovery_in_interval(results_dir) and
                               #   refuses to seed an island if it is empty (DEC-7).
    "discovery_provenance": str  # OPTIONAL exact-match tightener — a reference to the
                               #   in-interval R1/R2 discovery stub this grounding
                               #   came from (e.g. the stub file path or summary).
                               #   Informational/provenance; the gate already fails
                               #   closed on a missing in-interval stub without it.
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
    # ------------------------------------------------------------------
    # PRIMARY fail-closed gate (DEC-7). Run BEFORE opening the DB so a
    # refused grounding seeds NO island. The technique must come from an
    # in-interval R1/R2 discovery stub (kind in {dr, archive_analyst});
    # journal.discovery_in_interval is the single source of truth for the
    # recency rule. Fail CLOSED on a missing results_dir, an unavailable
    # journal bridge, or an empty in-interval stub list.
    # ------------------------------------------------------------------
    results_dir = payload.get("results_dir")
    if not results_dir:
        return {
            "ok": False,
            "error": "results_dir required for the discovery gate; grounding refused (DEC-7)",
        }
    journal = _common._lazy_journal()
    if journal is None or not hasattr(journal, "discovery_in_interval"):
        return {
            "ok": False,
            "error": "journal.discovery_in_interval unavailable; grounding refused (DEC-7)",
        }
    if not journal.discovery_in_interval(results_dir):
        return {
            "ok": False,
            "error": "no in-interval discovery stub; grounding refused (DEC-7)",
        }

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

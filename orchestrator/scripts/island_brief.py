"""island_brief.py — record a per-island DIRECTION ("brief") the orchestrator
authored at runtime, so different islands evolve toward DIFFERENT goals.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator calls this when it wants
to steer ONE island in a distinct direction (e.g. after a meta round produced a
per-island idea, or to ground a deep-research brief in a fresh island). It embeds
NO LLM call — it just persists the text the orchestrator (or meta round) produced.

The per-island brief is the mechanism that keeps islands genuinely DIFFERENTIATED
rather than all evolving under one global direction (audit finding H1). It is read
back per island by ``archive_query`` (``query_type="island_brief"``) and rendered
into that island's mutation prompt by ``construct_mutation_prompt`` /
``sampler.sample`` (preferred over, or augmenting, the global direction per
``evo.brief_compose_mode``). The persistent failure caution is rendered separately
(``failure_note``) and is never dropped, so a brief never clobbers it.

INPUT (stdin JSON):
  {
    "db_path": str, "db_config": {..}, "embedding_model": str,
    "island_idx": int,              # which island this direction is for
    "generation": int,              # the generation it was authored at (latest-wins)
    "content": str,                 # the direction text for THIS island
    "stage": "orchestrator",        # provenance tag (free-form)
    "structured_json": str | null,  # optional structured payload (e.g. a DR brief)
    "model_used": str | null,       # provenance (if a model produced the text)
    "cost": float                   # any cost to fold into provenance (ledger is separate)
  }

OUTPUT (stdout JSON): { "ok": true, "recorded": true, "island_idx": int }
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

    db = ProgramDatabase(config, embedding_model=embedding_model, read_only=False)
    try:
        db.record_meta_brief(
            island_idx=int(payload["island_idx"]),
            generation=int(payload.get("generation", 0) or 0),
            content=str(payload.get("content", "") or ""),
            stage=str(payload.get("stage", "orchestrator")),
            structured_json=payload.get("structured_json"),
            model_used=payload.get("model_used"),
            cost=float(payload.get("cost", 0.0) or 0.0),
        )
    finally:
        db.close()
    return {"recorded": True, "island_idx": int(payload["island_idx"])}


if __name__ == "__main__":
    _common.run_main(main)

"""Phase 2 of research-grounding — DR meta pipeline.

Tests cover the Stage A drift gate, the Stage B novelty cache lookup,
SQLite table round-trips, and the per-island accumulator on
``MetaSummarizer``. The Stage C/D placeholder implementations are
stand-ins until phase 2c lands the real calls; tests here use the
placeholders directly (they're part of the contract — when DR isn't
configured, the meta cycle still runs and emits a placeholder).

No live LLM, no live Azure DR, no embeddings — all stubbed via
AsyncMock.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, List, Tuple
from unittest.mock import AsyncMock

import pytest

from shinka.core.deep_research_summarizer import (
    BriefItem,
    DRBrief,
    DeepResearchSummarizer,
    StageAOutput,
    _cosine_similarity,
    _render_brief_markdown,
    cache_brief,
    lookup_cached_brief,
    persist_brief,
)
from shinka.core.summarizer import MetaSummarizer
from shinka.database import DatabaseConfig, Program, ProgramDatabase


def _program(
    pid: str, island_idx: int | None = 0, gen: int = 1, correct: bool = True
) -> Program:
    return Program(
        id=pid,
        code="def f(): return 1\n",
        correct=correct,
        combined_score=0.5,
        generation=gen,
        island_idx=island_idx,
        metadata={"patch_name": f"step-{pid}", "patch_description": "stub"},
    )


# ----------------------------------------------------------------------
# Per-island accumulator on MetaSummarizer
# ----------------------------------------------------------------------


def test_meta_summarizer_per_island_accumulator_separates_islands() -> None:
    """add_evaluated_program must push into both the flat list (for
    freeform meta) and the per-island bucket (for DR Stage A)."""
    summ = MetaSummarizer(meta_llm_client=None, async_mode=True)
    summ.add_evaluated_program(_program("p0", island_idx=0))
    summ.add_evaluated_program(_program("p1", island_idx=1))
    summ.add_evaluated_program(_program("p2", island_idx=0))
    # Flat list keeps order, all three.
    assert [p.id for p in summ.evaluated_since_last_meta] == ["p0", "p1", "p2"]
    # Per-island buckets are partitioned.
    assert [p.id for p in summ.evaluated_since_last_meta_by_island[0]] == [
        "p0",
        "p2",
    ]
    assert [p.id for p in summ.evaluated_since_last_meta_by_island[1]] == ["p1"]


def test_consume_island_programs_returns_and_clears() -> None:
    """consume_island_programs returns the list and resets the bucket."""
    summ = MetaSummarizer(meta_llm_client=None, async_mode=True)
    summ.add_evaluated_program(_program("p0", island_idx=2))
    summ.add_evaluated_program(_program("p1", island_idx=2))
    captured = summ.consume_island_programs(2)
    assert [p.id for p in captured] == ["p0", "p1"]
    assert summ.evaluated_since_last_meta_by_island[2] == []
    # The flat list is NOT cleared by consume — freeform meta still
    # owns that. (It clears itself after update_meta_memory.)
    assert len(summ.evaluated_since_last_meta) == 2


# ----------------------------------------------------------------------
# SQLite tables: meta_briefs and dr_brief_cache
# ----------------------------------------------------------------------


def test_meta_briefs_table_exists_after_db_init() -> None:
    """Migration 6 must create meta_briefs and dr_brief_cache so the
    DR summarizer can persist without raising."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ProgramDatabase(
            config=DatabaseConfig(
                db_path=str(Path(tmpdir) / "db.sqlite"), num_islands=1
            ),
            embedding_model="",
        )
        try:
            assert db.cursor is not None
            tables = {
                row[0]
                for row in db.cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "meta_briefs" in tables
            assert "dr_brief_cache" in tables

            # The shapes must include the columns the summarizer writes.
            mb_cols = {
                row[1]
                for row in db.cursor.execute("PRAGMA table_info(meta_briefs)")
            }
            assert {
                "island_idx",
                "generation",
                "stage",
                "structured_json",
                "model_used",
                "cost",
                "created_at",
            }.issubset(mb_cols)

            cache_cols = {
                row[1]
                for row in db.cursor.execute(
                    "PRAGMA table_info(dr_brief_cache)"
                )
            }
            assert {
                "query_text",
                "query_embedding",
                "brief_json",
                "model_used",
                "hits",
            }.issubset(cache_cols)
        finally:
            db.close()


def test_persist_brief_roundtrip() -> None:
    """persist_brief inserts a row whose structured_json round-trips
    back to the original BriefItem list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ProgramDatabase(
            config=DatabaseConfig(
                db_path=str(Path(tmpdir) / "db.sqlite"), num_islands=1
            ),
            embedding_model="",
        )
        try:
            assert db.conn is not None
            brief = DRBrief(
                island_idx=0,
                generation=5,
                items=[
                    BriefItem(
                        idea="cache-oblivious tiling",
                        rationale="reduces L1 misses",
                        reference_source="https://example.com/paper.pdf",
                        reference_snippet="The tile size T should satisfy T^2 < L1.",
                        gotchas="Doesn't apply for sparse matrices.",
                    )
                ],
                candidate_question="How do recent papers tile small GEMMs?",
                drift_score=0.7,
                source="fresh",
                rendered_markdown="**Idea 1**: cache-oblivious tiling",
                cost=4.2,
            )
            row_id = persist_brief(db.conn, brief, model_used="dr-test")
            assert row_id > 0

            row = db.cursor.execute(
                "SELECT structured_json, island_idx, generation, "
                "stage, model_used, cost FROM meta_briefs WHERE id = ?",
                (row_id,),
            ).fetchone()
            assert row["island_idx"] == 0
            assert row["generation"] == 5
            assert row["stage"] == "fresh"
            assert row["model_used"] == "dr-test"
            assert abs(row["cost"] - 4.2) < 1e-9
            items_data = json.loads(row["structured_json"])
            assert items_data[0]["idea"] == "cache-oblivious tiling"
        finally:
            db.close()


def test_cache_brief_and_lookup_above_threshold() -> None:
    """A cached brief embedded with a known vector must be retrieved
    by a near-identical query embedding, but not by a far one."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ProgramDatabase(
            config=DatabaseConfig(
                db_path=str(Path(tmpdir) / "db.sqlite"), num_islands=1
            ),
            embedding_model="",
        )
        try:
            assert db.conn is not None
            embedding = [1.0, 0.0, 0.0]
            brief = DRBrief(
                island_idx=None,
                generation=0,
                items=[BriefItem(idea="x")],
                rendered_markdown="x",
            )
            cache_brief(
                db.conn, "How to tile GEMMs?", embedding, brief, "dr-test"
            )

            # Near-identical query embedding → cache hit at threshold 0.99.
            near = [0.999, 0.001, 0.0]
            hit = lookup_cached_brief(db.conn, near, threshold=0.99)
            assert hit is not None
            row_id, sim, items = hit
            assert sim > 0.99
            assert items[0].idea == "x"

            # Orthogonal embedding → cache miss.
            far = [0.0, 1.0, 0.0]
            miss = lookup_cached_brief(db.conn, far, threshold=0.5)
            assert miss is None
        finally:
            db.close()


# ----------------------------------------------------------------------
# DR Summarizer pipeline (Stage A + Stage B; C/D placeholders)
# ----------------------------------------------------------------------


def _make_summarizer(
    db_conn: Any = None,
    *,
    stage_a_raw: str = '{"drift_score": 1.0, "justification": "x", "candidate_question": "Q?"}',
    embedder: Any = None,
    drift_threshold: float = 0.5,
    brief_cache_threshold: float = 0.95,
) -> Tuple[DeepResearchSummarizer, MetaSummarizer]:
    """Build a DR summarizer with stubbed dependencies for testing."""
    meta = MetaSummarizer(meta_llm_client=None, async_mode=True)

    async def fake_judge(*args: Any, **kwargs: Any) -> Tuple[str, float]:
        return stage_a_raw, 0.001

    dr = DeepResearchSummarizer(
        meta_summarizer=meta,
        stage_a_judge=fake_judge,
        embedder=embedder,
        db_conn=db_conn,
        drift_threshold=drift_threshold,
        brief_cache_threshold=brief_cache_threshold,
    )
    return dr, meta


def test_dr_returns_none_for_empty_island() -> None:
    """An island with no recent programs is skipped silently."""
    dr, meta = _make_summarizer()
    result = asyncio.run(dr.update_async(generation=10, island_indices=[0, 1]))
    assert result == {}


def test_dr_drift_skip_keeps_previous_brief() -> None:
    """When Stage A's drift_score < threshold and a previous brief
    exists, the prior brief is reused with source='drift_skip'."""
    # First pass: high drift → placeholder brief lands as previous.
    dr, meta = _make_summarizer(
        stage_a_raw='{"drift_score": 0.9, "justification": "shift", "candidate_question": "Q?"}'
    )
    meta.add_evaluated_program(_program("p0", island_idx=0))
    first = asyncio.run(dr.update_async(generation=20, island_indices=[0]))
    assert 0 in first
    # The Stage C placeholder fired, so source is "placeholder".
    assert first[0].source in {"placeholder", "fresh"}
    # Brief is stored on the summarizer as the per-island "previous".
    assert 0 in dr._previous_brief_by_island

    # Second pass: low drift → drift_skip with same items as before.
    dr.stage_a_judge = AsyncMock(
        return_value=(
            '{"drift_score": 0.1, "justification": "stable", "candidate_question": "Q?"}',
            0.0,
        )
    )
    meta.add_evaluated_program(_program("p1", island_idx=0))
    second = asyncio.run(dr.update_async(generation=40, island_indices=[0]))
    assert 0 in second
    assert second[0].source == "drift_skip"
    # And the items match the prior brief.
    assert second[0].items == dr._previous_brief_by_island[0].items


def test_dr_cache_hit_short_circuits_stage_c() -> None:
    """When a near-identical brief exists in dr_brief_cache, Stage B
    surfaces it and Stage C is NOT called."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ProgramDatabase(
            config=DatabaseConfig(
                db_path=str(Path(tmpdir) / "db.sqlite"), num_islands=1
            ),
            embedding_model="",
        )
        try:
            assert db.conn is not None
            # Seed a cached brief.
            seed_emb = [1.0, 0.0, 0.0]
            seed_brief = DRBrief(
                island_idx=None,
                generation=0,
                items=[BriefItem(idea="cached technique")],
                rendered_markdown="**Idea 1**: cached technique",
            )
            cache_brief(db.conn, "Q?", seed_emb, seed_brief, "dr-test")

            async def fake_embed(*args: Any, **kwargs: Any) -> Tuple[List[float], float]:
                return [0.999, 0.001, 0.0], 0.0001  # near-identical

            dr, meta = _make_summarizer(
                db_conn=db.conn, embedder=fake_embed, brief_cache_threshold=0.95
            )

            # Spy on Stage C — it must NOT be called.
            dr._run_stage_c = AsyncMock(  # type: ignore[assignment]
                side_effect=AssertionError("Stage C must not fire on cache hit")
            )

            meta.add_evaluated_program(_program("p0", island_idx=0))
            result = asyncio.run(
                dr.update_async(generation=10, island_indices=[0])
            )
            assert 0 in result
            brief = result[0]
            assert brief.source == "cache_hit"
            assert brief.items[0].idea == "cached technique"

            # And a meta_briefs row was persisted with stage="cache_hit".
            row = db.cursor.execute(
                "SELECT stage, model_used FROM meta_briefs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row["stage"] == "cache_hit"
            assert row["model_used"] == "cache_hit"

            # Cache hit count incremented.
            hits = db.cursor.execute(
                "SELECT hits FROM dr_brief_cache LIMIT 1"
            ).fetchone()[0]
            assert hits == 1
        finally:
            db.close()


def test_dr_stage_c_placeholder_fires_when_drift_high_and_no_cache() -> None:
    """High drift + no cache hit + default placeholders ⇒ a
    placeholder brief is emitted and persisted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ProgramDatabase(
            config=DatabaseConfig(
                db_path=str(Path(tmpdir) / "db.sqlite"), num_islands=1
            ),
            embedding_model="",
        )
        try:
            assert db.conn is not None

            async def fake_embed(*args: Any, **kwargs: Any) -> Tuple[List[float], float]:
                return [0.5, 0.5, 0.5], 0.0001

            dr, meta = _make_summarizer(db_conn=db.conn, embedder=fake_embed)
            meta.add_evaluated_program(_program("p0", island_idx=3))
            result = asyncio.run(
                dr.update_async(generation=20, island_indices=[3])
            )
            assert 3 in result
            assert result[3].source == "placeholder"
            assert result[3].items == []  # placeholder has no items

            # Stage C call count did NOT advance (we treat the
            # placeholder as "DR did not run").
            assert dr._stage_c_call_count == 0

            # Placeholder is persisted to meta_briefs so the user can
            # see *why* no items are present.
            row = db.cursor.execute(
                "SELECT stage, model_used FROM meta_briefs WHERE island_idx = 3"
            ).fetchone()
            assert row["stage"] == "placeholder"
            assert row["model_used"] == "placeholder"
        finally:
            db.close()


def test_dr_budget_exhaustion_skips_stage_c() -> None:
    """Once dr_max_calls_per_run is hit, subsequent islands get
    placeholder briefs with reason='dr_budget_exhausted' instead of
    invoking Stage C."""
    dr, meta = _make_summarizer()
    dr.dr_max_calls_per_run = 0  # already exhausted
    # Override placeholder Stage C to assert it isn't called.
    sentinel = AsyncMock(return_value=([], 0.0, "dr_placeholder"))
    dr._run_stage_c = sentinel  # type: ignore[assignment]

    meta.add_evaluated_program(_program("p0", island_idx=0))
    result = asyncio.run(dr.update_async(generation=20, island_indices=[0]))
    assert 0 in result
    assert result[0].source == "placeholder"
    sentinel.assert_not_called()


# ----------------------------------------------------------------------
# Pure helpers (formatters, parsers, cosine sim)
# ----------------------------------------------------------------------


def test_cosine_similarity_basic_cases() -> None:
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine_similarity([], [1.0]) == 0.0  # empty / mismatch → 0
    assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero mag → 0


def test_render_brief_markdown_includes_each_item_with_present_fields() -> None:
    md = _render_brief_markdown(
        [
            BriefItem(
                idea="hierarchical tiling",
                rationale="cuts misses",
                reference_source="paper.pdf",
                reference_snippet="T < L1/2",
                gotchas="dense only",
            )
        ]
    )
    assert "Idea 1" in md
    assert "hierarchical tiling" in md
    assert "Rationale" in md
    assert "T < L1/2" in md
    assert "paper.pdf" in md
    assert "Gotchas" in md


def test_stage_a_output_parses_json_with_code_fence_wrapping() -> None:
    """Real models occasionally wrap their JSON in ```json fences. The
    parser must strip them rather than crashing the drift gate."""
    fenced = (
        "```json\n"
        '{"drift_score": 0.42, "justification": "x", "candidate_question": "Q?"}\n'
        "```"
    )
    out = StageAOutput.parse(fenced)
    assert out.drift_score == pytest.approx(0.42)
    assert out.justification == "x"
    assert out.candidate_question == "Q?"


def test_stage_a_output_tolerant_of_garbage() -> None:
    """Malformed input becomes a zero-drift permissive default rather
    than crashing the meta cycle."""
    out = StageAOutput.parse("not even close to JSON")
    assert out.drift_score == 0.0
    assert out.justification == ""
    assert out.candidate_question == ""

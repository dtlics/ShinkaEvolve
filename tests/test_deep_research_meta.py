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


# ----------------------------------------------------------------------
# Phase 2c: real Stage C/D wiring via stage_c_fn / stage_d_fn injection,
# and the sampler island_brief plumbing.
# ----------------------------------------------------------------------


def test_dr_stage_c_fn_replaces_placeholder_when_injected() -> None:
    """When ``stage_c_fn`` is wired, the summarizer calls it and treats
    the returned items as a fresh brief (not a placeholder)."""

    async def fake_stage_c(*, candidate_question, programs):
        return (
            [BriefItem(idea="injected-technique", rationale="r", reference_source="s")],
            0.5,
            "o3-deep-research",
        )

    async def fake_stage_d(items):
        # Stage D in real flow can confirm/extend; here just pass through.
        return list(items), 0.0

    with tempfile.TemporaryDirectory() as tmpdir:
        db = ProgramDatabase(
            config=DatabaseConfig(
                db_path=str(Path(tmpdir) / "db.sqlite"), num_islands=1
            ),
            embedding_model="",
        )
        try:
            assert db.conn is not None
            meta = MetaSummarizer(meta_llm_client=None, async_mode=True)

            async def fake_judge(*, system_msg, user_msg):
                return (
                    '{"drift_score": 0.9, "justification": "drift", "candidate_question": "Q?"}',
                    0.0,
                )

            async def fake_embed(text):
                return [0.5, 0.5, 0.5], 0.0

            dr = DeepResearchSummarizer(
                meta_summarizer=meta,
                stage_a_judge=fake_judge,
                embedder=fake_embed,
                stage_c_fn=fake_stage_c,
                stage_d_fn=fake_stage_d,
                db_conn=db.conn,
                drift_threshold=0.5,
                brief_cache_threshold=0.99,
            )

            meta.add_evaluated_program(_program("p0", island_idx=0))
            result = asyncio.run(
                dr.update_async(generation=20, island_indices=[0])
            )
            assert 0 in result
            brief = result[0]
            assert brief.source == "fresh"
            assert len(brief.items) == 1
            assert brief.items[0].idea == "injected-technique"
            assert "injected-technique" in brief.rendered_markdown
            # The Stage C budget counter incremented (this consumed a real call).
            assert dr._stage_c_call_count == 1

            # And a meta_briefs row with stage="fresh" + model_used was persisted.
            row = db.cursor.execute(
                "SELECT stage, model_used FROM meta_briefs WHERE island_idx = 0"
            ).fetchone()
            assert row["stage"] == "fresh"
            assert row["model_used"] == "o3-deep-research"

            # And the brief landed in dr_brief_cache so a future Stage B
            # lookup can short-circuit.
            cached = db.cursor.execute(
                "SELECT brief_json FROM dr_brief_cache"
            ).fetchone()
            assert cached is not None
        finally:
            db.close()


def test_dr_stage_c_exception_falls_back_to_placeholder() -> None:
    """If stage_c_fn raises, the summarizer must emit a placeholder
    rather than crashing the meta cycle."""

    async def raising_stage_c(**_kwargs):
        raise RuntimeError("DR endpoint exploded")

    meta = MetaSummarizer(meta_llm_client=None, async_mode=True)

    async def fake_judge(*, system_msg, user_msg):
        return (
            '{"drift_score": 0.9, "justification": "drift", "candidate_question": "Q?"}',
            0.0,
        )

    dr = DeepResearchSummarizer(
        meta_summarizer=meta,
        stage_a_judge=fake_judge,
        stage_c_fn=raising_stage_c,
    )
    meta.add_evaluated_program(_program("p0", island_idx=0))
    result = asyncio.run(dr.update_async(generation=20, island_indices=[0]))
    assert 0 in result
    assert result[0].source == "placeholder"
    assert "stage_c_failed" in result[0].rendered_markdown
    # Budget NOT consumed on failure.
    assert dr._stage_c_call_count == 0


def test_sampler_prefers_island_brief_over_meta_recommendations() -> None:
    """When island_brief is provided, the sampler injects it into the
    # Potential Recommendations slot in place of meta_recommendations."""
    from shinka.core.sampler import PromptSampler

    sampler = PromptSampler(
        task_sys_msg=None,
        patch_types=["diff"],
        patch_type_probs=[1.0],
        language="python",
    )

    parent = _program("parent", island_idx=0, correct=True)
    sys_msg, _user_msg, patch_type = sampler.sample(
        parent=parent,
        archive_inspirations=[],
        top_k_inspirations=[],
        meta_recommendations="GENERIC FREEFORM REC",
        island_brief="ISLAND-SPECIFIC BRIEF",
    )
    assert patch_type == "diff"
    # The island brief landed in the slot; the freeform rec was
    # overridden (it does NOT appear separately).
    assert "ISLAND-SPECIFIC BRIEF" in sys_msg
    assert "GENERIC FREEFORM REC" not in sys_msg


def test_sampler_falls_back_to_meta_recommendations_without_island_brief() -> None:
    """No island_brief ⇒ the freeform meta_recommendations are still
    injected (existing behavior preserved for islands DR didn't touch)."""
    from shinka.core.sampler import PromptSampler

    sampler = PromptSampler(
        task_sys_msg=None,
        patch_types=["diff"],
        patch_type_probs=[1.0],
        language="python",
    )
    parent = _program("parent", island_idx=0, correct=True)
    sys_msg, _user_msg, patch_type = sampler.sample(
        parent=parent,
        archive_inspirations=[],
        top_k_inspirations=[],
        meta_recommendations="GENERIC FREEFORM REC",
        island_brief=None,
    )
    assert "GENERIC FREEFORM REC" in sys_msg


# ----------------------------------------------------------------------
# Doom-remediation Fix 2: DR pipeline cost is summed into total_api_cost.
# Stage A LLM judge cost lives on DRBrief.stage_a_cost; Stage C + Stage D
# costs live on DRBrief.cost. DRBrief.total_cost sums them. The
# orchestrator's DR firing block adds this into self.total_api_cost so
# max_api_costs budget enforcement covers DR spend.
# ----------------------------------------------------------------------


def test_stage_a_output_carries_cost() -> None:
    """StageAOutput.parse accepts a cost kwarg and preserves it. This
    is the foundation for Stage A cost flowing through to DRBrief."""
    raw = '{"drift_score": 0.7, "justification": "x", "candidate_question": "Q?"}'
    out = StageAOutput.parse(raw, cost=0.0123)
    assert out.drift_score == pytest.approx(0.7)
    assert out.cost == pytest.approx(0.0123)
    # Default cost when not supplied is 0.0 (backward compat).
    out_no_cost = StageAOutput.parse(raw)
    assert out_no_cost.cost == 0.0


def test_dr_brief_total_cost_sums_stage_a_and_main() -> None:
    """DRBrief.total_cost = stage_a_cost + cost. The orchestrator reads
    only this composite value, so internal cost partitioning is an
    implementation detail."""
    brief = DRBrief(island_idx=0, generation=5, cost=4.5, stage_a_cost=0.02)
    assert brief.total_cost == pytest.approx(4.52)
    # Default values give zero — empty briefs don't accidentally
    # advance the budget.
    empty = DRBrief(island_idx=0, generation=5)
    assert empty.total_cost == 0.0


def test_dr_brief_stage_a_cost_set_from_judge_in_drift_skip_path() -> None:
    """The drift-skip branch (drift_score < threshold) must still
    record stage_a_cost — Stage A ran, the LLM judge was paid for,
    even though Stage C/D never fired."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ProgramDatabase(
            config=DatabaseConfig(
                db_path=str(Path(tmpdir) / "db.sqlite"), num_islands=1
            ),
            embedding_model="",
        )
        try:
            # Seed a previous brief so drift_skip can fire.
            meta = MetaSummarizer(meta_llm_client=None, async_mode=True)

            async def cheap_judge(*args: Any, **kwargs: Any) -> Tuple[str, float]:
                # Below threshold → drift_skip path.
                return (
                    '{"drift_score": 0.1, "justification": "stable", "candidate_question": "Q?"}',
                    0.005,
                )

            dr = DeepResearchSummarizer(
                meta_summarizer=meta,
                stage_a_judge=cheap_judge,
                db_conn=db.conn,
                drift_threshold=0.5,
            )
            # Pre-populate previous brief so the drift_skip branch is reachable.
            dr._previous_brief_by_island[0] = DRBrief(
                island_idx=0,
                generation=0,
                items=[BriefItem(idea="x")],
                rendered_markdown="**Idea 1**: x",
            )
            meta.add_evaluated_program(_program("p0", island_idx=0))
            result = asyncio.run(
                dr.update_async(generation=20, island_indices=[0])
            )
            assert 0 in result
            brief = result[0]
            assert brief.source == "drift_skip"
            assert brief.cost == 0.0  # Stage C/D didn't run
            assert brief.stage_a_cost == pytest.approx(0.005)
            assert brief.total_cost == pytest.approx(0.005)
        finally:
            db.close()


def test_dr_brief_total_cost_aggregates_stage_a_plus_c_plus_d() -> None:
    """End-to-end fresh-brief path: stage_a (judge) + stage_c (DR) +
    stage_d (per-item agent grounding) all show up in total_cost."""

    async def cheap_judge(*args: Any, **kwargs: Any) -> Tuple[str, float]:
        return (
            '{"drift_score": 0.9, "justification": "shift", "candidate_question": "Q?"}',
            0.01,  # Stage A cost
        )

    async def fake_stage_c(*, candidate_question, programs):
        return (
            [BriefItem(idea="t1", reference_source="s", reference_snippet="snip")],
            5.0,  # Stage C cost
            "o3-deep-research",
        )

    async def fake_stage_d(items):
        return list(items), 0.25  # Stage D cost

    with tempfile.TemporaryDirectory() as tmpdir:
        db = ProgramDatabase(
            config=DatabaseConfig(
                db_path=str(Path(tmpdir) / "db.sqlite"), num_islands=1
            ),
            embedding_model="",
        )
        try:
            meta = MetaSummarizer(meta_llm_client=None, async_mode=True)
            dr = DeepResearchSummarizer(
                meta_summarizer=meta,
                stage_a_judge=cheap_judge,
                stage_c_fn=fake_stage_c,
                stage_d_fn=fake_stage_d,
                db_conn=db.conn,
                drift_threshold=0.5,
            )
            meta.add_evaluated_program(_program("p0", island_idx=0))
            result = asyncio.run(
                dr.update_async(generation=20, island_indices=[0])
            )
            brief = result[0]
            assert brief.source == "fresh"
            assert brief.cost == pytest.approx(5.25)  # cost_c + cost_d
            assert brief.stage_a_cost == pytest.approx(0.01)
            assert brief.total_cost == pytest.approx(5.26)
        finally:
            db.close()


# ----------------------------------------------------------------------
# Doom-remediation Fix 3: placeholder briefs do NOT replace freeform meta.
# The DR firing block at the orchestrator stashes brief.rendered_markdown
# into _latest_island_briefs ONLY when the brief is real (has items AND
# source != "placeholder"). Placeholder briefs still persist to the
# meta_briefs SQLite table for diagnostic visibility but they don't
# poison the sampler's prompt slot.
# ----------------------------------------------------------------------


def test_placeholder_brief_does_not_overwrite_latest_island_briefs() -> None:
    """Simulate the orchestrator's DR firing block. Pre-seed
    _latest_island_briefs[0] with a real brief (a prior cycle's
    success). Run the DR cycle and get back a placeholder brief
    (Stage C failed this time). Assert the prior brief is preserved —
    the placeholder did NOT overwrite it."""
    from types import SimpleNamespace

    # Pre-seed: a real brief is already in place from a prior cycle.
    prior_real_markdown = "**Idea 1**: real and valuable"
    latest_island_briefs = {0: prior_real_markdown}
    latest_island_brief_obj: dict = {}
    dr_cycle_cost = 0.0

    # New cycle returns a placeholder brief (Stage C failed). This is
    # the shape DeepResearchSummarizer._placeholder_brief produces.
    placeholder_brief = DRBrief(
        island_idx=0,
        generation=20,
        items=[],
        candidate_question="Q?",
        drift_score=0.9,
        source="placeholder",
        rendered_markdown=(
            "_DR pipeline did not produce items this cycle: stage_c_failed: <exc>._"
            "\n\n_Candidate question:_ Q?"
        ),
        cost=0.0,
        stage_a_cost=0.005,
    )
    briefs = {0: placeholder_brief}

    # Replicate the orchestrator's DR firing block stash logic.
    # (The actual block is in async_runner.py; this test exercises
    # the contract independently to keep the test focused.)
    for island_idx, brief in briefs.items():
        is_real_brief = (
            getattr(brief, "source", "") != "placeholder"
            and bool(getattr(brief, "items", None))
        )
        if is_real_brief and brief.rendered_markdown:
            latest_island_briefs[island_idx] = brief.rendered_markdown
        latest_island_brief_obj[island_idx] = brief
        dr_cycle_cost += float(getattr(brief, "total_cost", 0.0) or 0.0)

    # Prior real brief still in place.
    assert latest_island_briefs[0] == prior_real_markdown
    # Placeholder brief NOT promoted to _latest_island_briefs[0].
    assert "DR pipeline did not produce items" not in latest_island_briefs[0]
    # But the structured brief object IS recorded for diagnostics +
    # the lit_grounded picker (which filters on item.confirmed and
    # non-empty reference_snippet separately).
    assert latest_island_brief_obj[0] is placeholder_brief
    # Stage A cost still surfaces in the budget tally (Fix 2 contract).
    assert dr_cycle_cost == pytest.approx(0.005)


def test_real_brief_does_overwrite_latest_island_briefs() -> None:
    """The flip side of the placeholder gate: a real brief (with items
    and source="fresh" or "cache_hit") MUST overwrite the prior
    entry. Otherwise the gate would also block valid updates."""
    latest_island_briefs = {0: "old prior brief markdown"}
    latest_island_brief_obj: dict = {}

    fresh_brief = DRBrief(
        island_idx=0,
        generation=40,
        items=[BriefItem(idea="new technique", reference_snippet="snip")],
        candidate_question="Q?",
        drift_score=0.9,
        source="fresh",
        rendered_markdown="**Idea 1**: new technique",
        cost=5.0,
        stage_a_cost=0.01,
    )
    briefs = {0: fresh_brief}

    for island_idx, brief in briefs.items():
        is_real_brief = (
            getattr(brief, "source", "") != "placeholder"
            and bool(getattr(brief, "items", None))
        )
        if is_real_brief and brief.rendered_markdown:
            latest_island_briefs[island_idx] = brief.rendered_markdown
        latest_island_brief_obj[island_idx] = brief

    assert latest_island_briefs[0] == "**Idea 1**: new technique"
    assert latest_island_brief_obj[0] is fresh_brief


def test_dr_brief_placeholder_carries_stage_a_cost() -> None:
    """Even when DR aborts to a placeholder (Stage C failure, budget
    exhausted, etc.), the Stage A judge call already happened and was
    billed. The placeholder brief must record stage_a_cost so the
    orchestrator can sum it into the budget — otherwise repeated
    Stage C failures silently leak Stage A spend."""

    async def cheap_judge(*args: Any, **kwargs: Any) -> Tuple[str, float]:
        return (
            '{"drift_score": 0.9, "justification": "shift", "candidate_question": "Q?"}',
            0.003,
        )

    async def stage_c_explodes(**_kwargs):
        raise RuntimeError("DR endpoint unreachable")

    meta = MetaSummarizer(meta_llm_client=None, async_mode=True)
    dr = DeepResearchSummarizer(
        meta_summarizer=meta,
        stage_a_judge=cheap_judge,
        stage_c_fn=stage_c_explodes,
    )
    meta.add_evaluated_program(_program("p0", island_idx=0))
    result = asyncio.run(dr.update_async(generation=20, island_indices=[0]))
    brief = result[0]
    assert brief.source == "placeholder"
    assert brief.stage_a_cost == pytest.approx(0.003)
    assert brief.total_cost == pytest.approx(0.003)

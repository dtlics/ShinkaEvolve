"""Phase 5 tests for the deep-research meta pipeline.

Phase 5a coverage: config flags, dr_client construction + env validation,
and the meta_briefs / dr_brief_cache SQLite tables. Phase 5b/c/d extend
this file as the summarizer is layered on.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import openai
import pytest

from shinka.core.config import EvolutionConfig
from shinka.database import DatabaseConfig, ProgramDatabase
from shinka.llm.dr_client import (
    DRConfigurationError,
    _normalize_endpoint,
    get_async_dr_client,
    get_dr_client,
)


# ---------------------------------------------------------------------------
# Config flags
# ---------------------------------------------------------------------------


def test_dr_config_defaults():
    cfg = EvolutionConfig()
    assert cfg.enable_deep_research is False
    assert cfg.dr_meta_interval == 20
    assert cfg.dr_model == "o3-deep-research"
    assert cfg.dr_endpoint_env == "AZURE_DR_ENDPOINT"
    assert cfg.dr_api_key_env == "AZURE_DR_API_KEY"
    assert cfg.dr_reasoning_effort == "medium"
    assert cfg.dr_max_tool_calls == 20
    assert cfg.dr_background is True
    assert cfg.dr_max_calls_per_run == 30
    assert cfg.dr_brief_cache_threshold == pytest.approx(0.95)
    assert cfg.dr_drift_threshold == pytest.approx(0.5)
    assert "github.com" in cfg.dr_code_grounding_domains
    assert "arxiv.org" in cfg.dr_code_grounding_domains


# ---------------------------------------------------------------------------
# DR client factory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "https://x.services.ai.azure.com/api/projects/p/openai/v1/responses",
            "https://x.services.ai.azure.com/api/projects/p",
        ),
        (
            "https://x.services.ai.azure.com/api/projects/p/openai/v1/responses/",
            "https://x.services.ai.azure.com/api/projects/p",
        ),
        (
            "https://x.services.ai.azure.com/api/projects/p/openai/v1",
            "https://x.services.ai.azure.com/api/projects/p",
        ),
        (
            "https://x.services.ai.azure.com/api/projects/p",
            "https://x.services.ai.azure.com/api/projects/p",
        ),
        ("", ""),
    ],
)
def test_normalize_endpoint_strips_responses_path(raw: str, expected: str):
    assert _normalize_endpoint(raw) == expected


def test_get_async_dr_client_raises_when_endpoint_missing(monkeypatch):
    monkeypatch.delenv("AZURE_DR_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_DR_API_KEY", raising=False)
    with pytest.raises(DRConfigurationError) as excinfo:
        get_async_dr_client()
    msg = str(excinfo.value)
    assert "AZURE_DR_ENDPOINT" in msg
    assert "AZURE_DR_API_KEY" in msg


def test_get_async_dr_client_raises_when_only_one_var_set(monkeypatch):
    monkeypatch.setenv("AZURE_DR_ENDPOINT", "https://example.invalid/")
    monkeypatch.delenv("AZURE_DR_API_KEY", raising=False)
    with pytest.raises(DRConfigurationError) as excinfo:
        get_async_dr_client()
    msg = str(excinfo.value)
    assert "AZURE_DR_API_KEY" in msg
    assert "AZURE_DR_ENDPOINT" not in msg  # not in the missing list


def test_get_async_dr_client_constructs_when_both_vars_set(monkeypatch):
    monkeypatch.setenv(
        "AZURE_DR_ENDPOINT",
        "https://r.services.ai.azure.com/api/projects/p/openai/v1/responses",
    )
    monkeypatch.setenv("AZURE_DR_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_API_VERSION", "preview")

    client = get_async_dr_client()
    assert isinstance(client, openai.AsyncAzureOpenAI)


def test_get_sync_dr_client_constructs_when_both_vars_set(monkeypatch):
    monkeypatch.setenv("AZURE_DR_ENDPOINT", "https://example.invalid/")
    monkeypatch.setenv("AZURE_DR_API_KEY", "test-key")
    client = get_dr_client()
    assert isinstance(client, openai.AzureOpenAI)


def test_get_async_dr_client_respects_custom_env_names(monkeypatch):
    monkeypatch.setenv("MY_DR_ENDPOINT", "https://example.invalid/")
    monkeypatch.setenv("MY_DR_KEY", "k")
    monkeypatch.delenv("AZURE_DR_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_DR_API_KEY", raising=False)
    client = get_async_dr_client(
        endpoint_env="MY_DR_ENDPOINT", api_key_env="MY_DR_KEY"
    )
    assert isinstance(client, openai.AsyncAzureOpenAI)


# ---------------------------------------------------------------------------
# meta_briefs + dr_brief_cache tables
# ---------------------------------------------------------------------------


def test_db_creates_meta_briefs_table(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "dr.db"
        db = ProgramDatabase(
            config=DatabaseConfig(db_path=str(db_path), num_islands=1),
            embedding_model="",
        )
        try:
            cursor = db.conn.cursor()
            tables = {
                row[0]
                for row in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "meta_briefs" in tables
            assert "dr_brief_cache" in tables

            # Schema sanity-check.
            meta_cols = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(meta_briefs)")
            }
            assert {
                "id",
                "island_idx",
                "generation",
                "stage",
                "content",
                "structured_json",
                "model_used",
                "cost",
                "created_at",
            }.issubset(meta_cols)

            cache_cols = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(dr_brief_cache)")
            }
            assert {
                "id",
                "query_text",
                "query_embedding",
                "brief_json",
                "model_used",
                "cost",
                "hits",
                "created_at",
            }.issubset(cache_cols)

            # Indices.
            idx_names = {
                row[0]
                for row in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name IN ('meta_briefs', 'dr_brief_cache')"
                )
            }
            assert "idx_meta_briefs_island_generation" in idx_names
            assert "idx_dr_brief_cache_created_at" in idx_names

            # Round-trip: insert + read.
            cursor.execute(
                "INSERT INTO meta_briefs "
                "(island_idx, generation, stage, content, structured_json, "
                "model_used, cost, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (0, 5, "A", "drift score 0.62", "{}", "gpt-5-mini", 0.001, 100.0),
            )
            db.conn.commit()
            row = cursor.execute(
                "SELECT island_idx, generation, stage, model_used FROM meta_briefs"
            ).fetchone()
            assert tuple(row) == (0, 5, "A", "gpt-5-mini")
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Phase 5b: DeepResearchSummarizer Stage A/B + helpers
# ---------------------------------------------------------------------------


from shinka.core.deep_research_summarizer import (  # noqa: E402
    BriefItem,
    DeepResearchSummarizer,
    DriftCheckResult,
    IslandBrief,
    _parse_drift_response,
    _summarize_recent_programs,
    cosine_similarity,
)


def test_brief_item_to_dict_round_trips_fields():
    item = BriefItem(
        idea="LR warmup",
        rationale="Reduce gradient spikes early",
        reference_snippet="for step in range(warmup): ...",
        source="https://arxiv.org/abs/...",
        gotchas="Skip when batch_size < 32",
    )
    assert item.to_dict() == {
        "idea": "LR warmup",
        "rationale": "Reduce gradient spikes early",
        "reference_snippet": "for step in range(warmup): ...",
        "source": "https://arxiv.org/abs/...",
        "gotchas": "Skip when batch_size < 32",
    }


def test_island_brief_serializes_items_recursively():
    brief = IslandBrief(
        island_idx=1,
        summary="LR scheduling family",
        items=[
            BriefItem(idea="cosine", rationale="smooth decay"),
            BriefItem(idea="warmup", rationale="anneal in"),
        ],
        direction_summary="learning-rate scheduling",
        drift_score=0.7,
        generation=42,
        model_used="o3-deep-research",
        cost=4.2,
        cached=False,
    )
    payload = brief.to_dict()
    assert payload["summary"] == "LR scheduling family"
    assert payload["drift_score"] == 0.7
    assert payload["generation"] == 42
    assert len(payload["items"]) == 2
    assert payload["items"][0]["idea"] == "cosine"
    assert payload["cached"] is False


def test_summarize_recent_programs_includes_intent_and_score():
    from types import SimpleNamespace

    progs = [
        SimpleNamespace(
            generation=5,
            mutation_type="diff",
            combined_score=0.42,
            mutation_intent="Tune LR | technique: cosine | expected: smoother loss",
        ),
        SimpleNamespace(
            generation=6,
            mutation_type="full",
            combined_score=0.51,
            mutation_intent=None,
        ),
    ]
    summary = _summarize_recent_programs(progs)
    assert "gen=5" in summary
    assert "diff" in summary
    assert "0.4200" in summary
    assert "cosine" in summary
    assert "gen=6" in summary
    assert "(no intent)" in summary


def test_summarize_recent_programs_truncates():
    from types import SimpleNamespace

    progs = [
        SimpleNamespace(
            generation=i,
            mutation_type="diff",
            combined_score=0.5,
            mutation_intent="x" * 200,
        )
        for i in range(200)
    ]
    summary = _summarize_recent_programs(progs)
    assert len(summary) <= 1500 + 50  # 50-char wiggle for the truncate suffix
    assert "[truncated]" in summary


def test_summarize_recent_programs_handles_empty():
    summary = _summarize_recent_programs([])
    assert "no programs" in summary


@pytest.mark.parametrize(
    "raw, expected_score",
    [
        ('{"drift_score": 0.4, "justification": "small variation"}', 0.4),
        # Score is clamped into [0, 1].
        ('{"drift_score": 2.5, "justification": "off-scale"}', 1.0),
        ('{"drift_score": -0.5, "justification": "negative"}', 0.0),
        # Stray prose around the JSON is tolerated.
        (
            "Here is my answer:\n"
            '{"drift_score": 0.8, "justification": "big shift"}\n'
            "End.",
            0.8,
        ),
        # Markdown code fences are stripped.
        (
            "```json\n"
            '{"drift_score": 0.2, "justification": "tiny"}\n'
            "```",
            0.2,
        ),
    ],
)
def test_parse_drift_response_clamps_and_locates_json(raw, expected_score):
    result = _parse_drift_response(raw)
    assert isinstance(result, DriftCheckResult)
    assert result.drift_score == pytest.approx(expected_score)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "no json at all",
        "{",
        '{"drift_score": "not-a-number"}',
    ],
)
def test_parse_drift_response_falls_back_to_zero_on_garbage(raw):
    result = _parse_drift_response(raw)
    assert result.drift_score == 0.0
    # justification is short and explanatory; never raises.
    assert isinstance(result.justification, str)


def test_cosine_similarity_basic_cases():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([1.0, 0.0], []) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_dr_summarizer_drift_check_calls_llm_and_parses(monkeypatch):
    from types import SimpleNamespace

    captured: Dict[str, Any] = {}

    async def _fake_drift_llm(sys_msg, user_msg):
        captured["sys"] = sys_msg
        captured["user"] = user_msg
        return (
            '{"drift_score": 0.62, "justification": "moved from LR sched to '
            'momentum tuning"}',
            0.012,
        )

    summ = DeepResearchSummarizer(
        drift_llm=_fake_drift_llm, drift_threshold=0.5, cache_threshold=0.95
    )
    programs = [
        SimpleNamespace(
            generation=10,
            mutation_type="diff",
            combined_score=0.5,
            mutation_intent="Tune momentum | technique: nesterov | expected: x",
        ),
    ]
    previous = IslandBrief(
        island_idx=0,
        summary="LR scheduling techniques family",
        items=[BriefItem(idea="cosine", rationale="x")],
    )

    import asyncio as _asyncio

    result, cost = _asyncio.run(
        summ.drift_check(
            island_idx=0,
            recent_programs=programs,
            previous_brief=previous,
        )
    )
    assert result.drift_score == pytest.approx(0.62)
    assert "momentum" in result.justification
    assert cost == pytest.approx(0.012)
    assert "Tune momentum" in captured["user"]
    assert "LR scheduling techniques family" in captured["user"]


def test_dr_summarizer_drift_check_handles_no_previous_brief(monkeypatch):
    captured: Dict[str, Any] = {}

    async def _fake_drift_llm(sys_msg, user_msg):
        captured["user"] = user_msg
        return ('{"drift_score": 0.1, "justification": "stable"}', 0.0)

    summ = DeepResearchSummarizer(drift_llm=_fake_drift_llm)
    import asyncio as _asyncio

    result, _ = _asyncio.run(
        summ.drift_check(
            island_idx=None, recent_programs=[], previous_brief=None
        )
    )
    assert result.drift_score == pytest.approx(0.1)
    # The Stage A user prompt clearly tells the model there's no prior brief.
    assert "no previous brief" in captured["user"]


def test_dr_summarizer_novelty_check_hits_cache(monkeypatch):
    import asyncio as _asyncio

    async def _embed(text):
        return ([1.0, 0.0, 0.0], 0.001)

    matched_brief = IslandBrief(
        island_idx=0,
        summary="Cached LR family",
        items=[BriefItem(idea="cosine", rationale="x")],
    )

    def _cache_lookup(embedding):
        # Trivial lookup: return the canned brief whenever the embedding
        # matches what we returned above. In Phase 5c the runner-side
        # cache will do a proper cosine NN against dr_brief_cache.
        if embedding[0] == 1.0:
            return matched_brief
        return None

    async def _drift_llm(*_args, **_kwargs):
        return ("{}", 0.0)

    summ = DeepResearchSummarizer(
        drift_llm=_drift_llm,
        embed=_embed,
        cache_lookup=_cache_lookup,
    )
    match, embedding, cost = _asyncio.run(
        summ.novelty_check(direction_summary="learning-rate scheduling family")
    )
    assert match is matched_brief
    assert embedding == [1.0, 0.0, 0.0]
    assert cost == pytest.approx(0.001)


def test_dr_summarizer_novelty_check_misses_when_no_cache_match():
    import asyncio as _asyncio

    async def _embed(text):
        return ([0.0, 1.0], 0.002)

    def _cache_lookup(embedding):
        return None

    async def _drift_llm(*_args, **_kwargs):
        return ("{}", 0.0)

    summ = DeepResearchSummarizer(
        drift_llm=_drift_llm, embed=_embed, cache_lookup=_cache_lookup
    )
    match, embedding, _cost = _asyncio.run(
        summ.novelty_check(direction_summary="unique direction")
    )
    assert match is None
    assert embedding == [0.0, 1.0]


def test_dr_summarizer_novelty_check_skips_without_embed_hook():
    import asyncio as _asyncio

    async def _drift_llm(*_args, **_kwargs):
        return ("{}", 0.0)

    summ = DeepResearchSummarizer(drift_llm=_drift_llm)
    match, embedding, cost = _asyncio.run(
        summ.novelty_check(direction_summary="x")
    )
    assert match is None
    assert embedding == []
    assert cost == 0.0


def test_db_migration_creates_dr_tables_on_legacy_open(monkeypatch):
    """An on-disk DB created without the DR tables should grow them on
    next open without crashing or duplicating."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "legacy_dr.db"

        # Build a minimal pre-Phase-5 DB by hand.
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                """
                CREATE TABLE programs (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    language TEXT NOT NULL,
                    parent_id TEXT,
                    archive_inspiration_ids TEXT,
                    top_k_inspiration_ids TEXT,
                    generation INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    code_diff TEXT,
                    combined_score REAL,
                    public_metrics TEXT,
                    private_metrics TEXT,
                    text_feedback TEXT,
                    complexity REAL,
                    embedding TEXT,
                    embedding_pca_2d TEXT,
                    embedding_pca_3d TEXT,
                    embedding_cluster_id INTEGER,
                    correct BOOLEAN DEFAULT 0,
                    children_count INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT,
                    migration_history TEXT,
                    island_idx INTEGER,
                    system_prompt_id TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        # First open: migrations should fire and create the tables.
        db = ProgramDatabase(
            config=DatabaseConfig(db_path=str(db_path), num_islands=1),
            embedding_model="",
        )
        try:
            cursor = db.conn.cursor()
            tables = {
                row[0]
                for row in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "meta_briefs" in tables
            assert "dr_brief_cache" in tables
        finally:
            db.close()

        # Second open should be a no-op (idempotent).
        db2 = ProgramDatabase(
            config=DatabaseConfig(db_path=str(db_path), num_islands=1),
            embedding_model="",
        )
        try:
            cursor = db2.conn.cursor()
            row = cursor.execute(
                "SELECT COUNT(*) FROM meta_briefs"
            ).fetchone()
            assert row[0] == 0
        finally:
            db2.close()

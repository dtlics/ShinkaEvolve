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


# ---------------------------------------------------------------------------
# Phase 5c: BriefCache + Stage C + Stage D + run_pipeline
# ---------------------------------------------------------------------------


from shinka.core.deep_research_summarizer import BriefCache, _parse_brief_response  # noqa: E402


def _build_dr_db(tmp_path: Path) -> str:
    """Create a DR-table-ready DB and return its path."""
    db_path = tmp_path / "dr.db"
    db = ProgramDatabase(
        config=DatabaseConfig(db_path=str(db_path), num_islands=1),
        embedding_model="",
    )
    try:
        return str(db_path)
    finally:
        db.close()


def test_parse_brief_response_returns_normalized_dict():
    raw = """```json
    {
      "summary": "lr scheduling family",
      "items": [
        {
          "idea": "cosine",
          "rationale": "smooth decay",
          "reference_snippet": "for step in range(...)",
          "source": "https://arxiv.org/abs/x",
          "gotchas": ""
        }
      ]
    }
    ```"""
    parsed = _parse_brief_response(raw)
    assert parsed["summary"] == "lr scheduling family"
    assert len(parsed["items"]) == 1
    assert parsed["items"][0]["idea"] == "cosine"
    assert parsed["items"][0]["source"].startswith("https://")


@pytest.mark.parametrize(
    "raw",
    [None, "", "no json", "{not valid"],
)
def test_parse_brief_response_returns_empty_on_garbage(raw):
    parsed = _parse_brief_response(raw)
    assert parsed == {"summary": "", "items": []}


def test_brief_cache_store_and_lookup_round_trip(tmp_path: Path):
    db_path = _build_dr_db(tmp_path)
    cache = BriefCache(db_path=db_path, similarity_threshold=0.95)

    brief = IslandBrief(
        island_idx=2,
        summary="LR scheduling family",
        items=[BriefItem(idea="cosine", rationale="smooth decay")],
        direction_summary="learning-rate scheduling",
        drift_score=0.7,
        source_query_embedding=[1.0, 0.0, 0.0],
        generation=10,
        model_used="o3-deep-research",
        cost=4.2,
    )
    cache.store(brief)

    # Exact-match lookup
    hit = cache.lookup([1.0, 0.0, 0.0])
    assert hit is not None
    assert hit.summary == "LR scheduling family"
    assert hit.cached is True
    assert hit.items[0].idea == "cosine"

    # Below-threshold lookup misses
    miss = cache.lookup([0.0, 1.0, 0.0])
    assert miss is None


def test_brief_cache_lookup_returns_closest_above_threshold(tmp_path: Path):
    db_path = _build_dr_db(tmp_path)
    cache = BriefCache(db_path=db_path, similarity_threshold=0.95)

    near = IslandBrief(
        island_idx=0,
        summary="near",
        direction_summary="x",
        source_query_embedding=[1.0, 0.05, 0.0],
        items=[BriefItem(idea="near", rationale="x")],
    )
    far = IslandBrief(
        island_idx=1,
        summary="far",
        direction_summary="y",
        source_query_embedding=[0.0, 1.0, 0.0],
        items=[BriefItem(idea="far", rationale="x")],
    )
    cache.store(near)
    cache.store(far)

    # Query close to ``near`` -> picks near
    hit = cache.lookup([1.0, 0.04, 0.0])
    assert hit is not None
    assert hit.summary == "near"


def test_brief_cache_handles_missing_db(tmp_path: Path):
    cache = BriefCache(db_path=None, similarity_threshold=0.95)
    brief = IslandBrief(
        island_idx=0,
        summary="x",
        direction_summary="y",
        source_query_embedding=[1.0],
        items=[],
    )
    # No-op store / lookup; doesn't raise.
    cache.store(brief)
    assert cache.lookup([1.0]) is None
    cache.increment_hits([1.0])


def test_brief_cache_increment_hits_bumps_counter(tmp_path: Path):
    db_path = _build_dr_db(tmp_path)
    cache = BriefCache(db_path=db_path, similarity_threshold=0.95)
    cache.store(
        IslandBrief(
            island_idx=0,
            summary="x",
            direction_summary="y",
            source_query_embedding=[1.0, 0.0],
            items=[],
        )
    )
    cache.increment_hits([1.0, 0.0])
    cache.increment_hits([0.99, 0.05])  # within threshold
    cache.increment_hits([0.0, 1.0])  # orthogonal -> below threshold

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT hits FROM dr_brief_cache LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 2


def test_summarizer_deep_research_parses_and_returns_brief():
    import asyncio as _asyncio

    async def _drift_llm(*_args, **_kwargs):
        return ("{}", 0.0)

    captured: Dict[str, Any] = {}

    async def _dr_call(sys_msg, user_msg):
        captured["user"] = user_msg
        return (
            '{"summary":"x","items":[{"idea":"a","rationale":"b"}]}',
            5.5,
            "o3-deep-research",
        )

    summ = DeepResearchSummarizer(
        drift_llm=_drift_llm,
        dr_call=_dr_call,
        allowed_domains=["github.com"],
    )
    brief, cost, model_used = _asyncio.run(
        summ.deep_research(
            task_description="evolve circle packing",
            direction_summary="LR scheduling",
        )
    )
    assert brief["summary"] == "x"
    assert len(brief["items"]) == 1
    assert brief["items"][0]["idea"] == "a"
    assert cost == pytest.approx(5.5)
    assert model_used == "o3-deep-research"
    assert "evolve circle packing" in captured["user"]
    assert "LR scheduling" in captured["user"]
    assert "github.com" in captured["user"]


def test_summarizer_deep_research_returns_empty_when_no_hook():
    import asyncio as _asyncio

    async def _drift_llm(*_args, **_kwargs):
        return ("{}", 0.0)

    summ = DeepResearchSummarizer(drift_llm=_drift_llm)
    brief, cost, model_used = _asyncio.run(
        summ.deep_research(task_description="t", direction_summary="d")
    )
    assert brief == {"summary": "", "items": []}
    assert cost == 0.0
    assert model_used is None


def test_summarizer_code_ground_fills_sources(monkeypatch):
    import asyncio as _asyncio

    async def _drift_llm(*_args, **_kwargs):
        return ("{}", 0.0)

    async def _ground_call(sys_msg, user_msg, items):
        # Grounder fills in source + snippet.
        return (
            '{"summary":"sum","items":[{"idea":"cosine","rationale":"r",'
            '"reference_snippet":"snip","source":"https://arxiv.org/abs/x"}]}',
            0.3,
            "gpt-5",
        )

    summ = DeepResearchSummarizer(
        drift_llm=_drift_llm, ground_call=_ground_call
    )
    grounded, cost, model_used = _asyncio.run(
        summ.code_ground(
            brief={
                "summary": "sum",
                "items": [
                    {
                        "idea": "cosine",
                        "rationale": "r",
                        "reference_snippet": "",
                        "source": "",
                        "gotchas": "",
                    }
                ],
            }
        )
    )
    assert grounded["items"][0]["source"].startswith("https://")
    assert grounded["items"][0]["reference_snippet"] == "snip"
    assert cost == pytest.approx(0.3)


def test_summarizer_code_ground_falls_back_to_input_on_empty_response():
    import asyncio as _asyncio

    async def _drift_llm(*_args, **_kwargs):
        return ("{}", 0.0)

    async def _ground_call(sys_msg, user_msg, items):
        # Empty payload from the grounder -> caller keeps original brief
        return ("{}", 0.1, "gpt-5")

    summ = DeepResearchSummarizer(
        drift_llm=_drift_llm, ground_call=_ground_call
    )
    original = {
        "summary": "sum",
        "items": [{"idea": "cosine", "rationale": "r"}],
    }
    grounded, cost, _ = _asyncio.run(summ.code_ground(brief=original))
    assert grounded == original
    assert cost == pytest.approx(0.1)


# --- run_pipeline orchestration --------------------------------------------


def _pipeline_stubs(*, drift_score: float):
    """Build a set of stubs that return canned content at each stage."""
    calls = {"stage_a": 0, "stage_b_lookup": 0, "stage_c": 0, "stage_d": 0}

    async def drift_llm(sys_msg, user_msg):
        calls["stage_a"] += 1
        return (
            f'{{"drift_score": {drift_score}, "justification": "test"}}',
            0.001,
        )

    async def embed(text):
        return [1.0, 0.0, 0.0], 0.002

    def cache_lookup(emb):
        calls["stage_b_lookup"] += 1
        return None

    async def dr_call(sys_msg, user_msg):
        calls["stage_c"] += 1
        return (
            '{"summary":"sum","items":[{"idea":"a","rationale":"b"}]}',
            5.0,
            "o3-deep-research",
        )

    async def ground_call(sys_msg, user_msg, items):
        calls["stage_d"] += 1
        return (
            '{"summary":"sum","items":[{"idea":"a","rationale":"b",'
            '"reference_snippet":"snip","source":"https://arxiv.org/abs/x"}]}',
            0.5,
            "gpt-5",
        )

    return calls, drift_llm, embed, cache_lookup, dr_call, ground_call


def test_run_pipeline_short_circuits_when_drift_below_threshold():
    import asyncio as _asyncio

    calls, drift_llm, embed, cache_lookup, dr_call, ground_call = _pipeline_stubs(
        drift_score=0.1
    )

    summ = DeepResearchSummarizer(
        drift_llm=drift_llm,
        embed=embed,
        cache_lookup=cache_lookup,
        dr_call=dr_call,
        ground_call=ground_call,
        drift_threshold=0.5,
    )
    previous = IslandBrief(
        island_idx=0,
        summary="previous",
        items=[BriefItem(idea="old", rationale="x")],
    )
    brief, costs = _asyncio.run(
        summ.run_pipeline(
            island_idx=0,
            recent_programs=[],
            previous_brief=previous,
            task_description="t",
        )
    )
    assert brief is previous
    # Only stage A ran -- no embeddings, no DR, no grounding.
    assert calls == {"stage_a": 1, "stage_b_lookup": 0, "stage_c": 0, "stage_d": 0}
    assert costs == {"stage_a": 0.001, "stage_b": 0.0, "stage_c": 0.0, "stage_d": 0.0}


def test_run_pipeline_uses_cache_hit_to_skip_stage_c():
    import asyncio as _asyncio

    cached_brief = IslandBrief(
        island_idx=0,
        summary="cached",
        items=[BriefItem(idea="cached", rationale="x")],
    )

    calls, drift_llm, embed, _miss_lookup, dr_call, ground_call = _pipeline_stubs(
        drift_score=0.9
    )

    def hit_lookup(emb):
        calls["stage_b_lookup"] += 1
        return cached_brief

    summ = DeepResearchSummarizer(
        drift_llm=drift_llm,
        embed=embed,
        cache_lookup=hit_lookup,
        dr_call=dr_call,
        ground_call=ground_call,
        drift_threshold=0.5,
    )
    brief, costs = _asyncio.run(
        summ.run_pipeline(
            island_idx=0,
            recent_programs=[],
            previous_brief=None,
            task_description="t",
        )
    )
    assert brief.cached is True
    assert brief.summary == "cached"
    assert brief.items[0].idea == "cached"
    assert brief.island_idx == 0  # retagged to the requesting island
    # Stage C/D should NOT have run.
    assert calls["stage_c"] == 0
    assert calls["stage_d"] == 0
    assert costs["stage_c"] == 0.0
    assert costs["stage_d"] == 0.0


def test_run_pipeline_runs_full_stages_when_novel_drift(tmp_path: Path):
    import asyncio as _asyncio

    db_path = _build_dr_db(tmp_path)
    cache = BriefCache(db_path=db_path, similarity_threshold=0.95)
    calls, drift_llm, embed, _, dr_call, ground_call = _pipeline_stubs(
        drift_score=0.9
    )

    summ = DeepResearchSummarizer(
        drift_llm=drift_llm,
        embed=embed,
        cache=cache,
        dr_call=dr_call,
        ground_call=ground_call,
        drift_threshold=0.5,
    )
    brief, costs = _asyncio.run(
        summ.run_pipeline(
            island_idx=3,
            recent_programs=[],
            previous_brief=None,
            task_description="evolve circle packing",
            generation=42,
        )
    )
    assert brief.cached is False
    assert brief.island_idx == 3
    assert brief.summary == "sum"
    assert len(brief.items) == 1
    assert brief.items[0].source.startswith("https://")
    assert brief.drift_score == pytest.approx(0.9)
    assert brief.model_used == "o3-deep-research"
    assert brief.cost == pytest.approx(5.5)  # 5.0 + 0.5
    # All four stages fired.
    assert calls["stage_a"] == 1
    assert calls["stage_c"] == 1
    assert calls["stage_d"] == 1
    # And the brief was persisted to the cache for future islands.
    cached = cache.lookup(brief.source_query_embedding)
    assert cached is not None
    assert cached.summary == "sum"


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

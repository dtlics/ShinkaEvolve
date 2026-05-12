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

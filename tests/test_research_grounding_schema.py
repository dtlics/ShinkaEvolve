"""Phase 1 schema tests: research-grounding fields + migration.

These tests cover the Phase 1 additions in isolation:

- New Program dataclass fields (``error_traceback``, ``attempt_round``,
  ``mutation_type``, ``mutation_intent``, ``model_used``) survive a
  round-trip through SQLite.
- An old-schema DB without the new columns has Migration 5 fire exactly
  once on first open, after which round-trips work normally.
- The MutationIntent Pydantic contract accepts well-formed inputs and
  falls back to ``NO_INTENT_RECORDED`` on parse failure.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from shinka.core.mutation_intent import (
    NO_INTENT_RECORDED,
    MutationIntent,
    validate_mutation_intent,
)
from shinka.database import DatabaseConfig, Program, ProgramDatabase


_NEW_COLUMNS = (
    "error_traceback",
    "attempt_round",
    "mutation_type",
    "mutation_intent",
    "model_used",
)


def _make_program(
    program_id: str,
    *,
    error_traceback: str | None = None,
    attempt_round: int = 0,
    mutation_type: str | None = None,
    mutation_intent: str | None = None,
    model_used: str | None = None,
) -> Program:
    return Program(
        id=program_id,
        code="def f():\n    return 1\n",
        correct=True,
        combined_score=1.0,
        generation=0,
        island_idx=0,
        error_traceback=error_traceback,
        attempt_round=attempt_round,
        mutation_type=mutation_type,
        mutation_intent=mutation_intent,
        model_used=model_used,
    )


def test_program_dataclass_defaults_new_fields_to_null():
    program = _make_program("p-defaults")
    assert program.error_traceback is None
    assert program.attempt_round == 0
    assert program.mutation_type is None
    assert program.mutation_intent is None
    assert program.model_used is None


def test_new_columns_round_trip_through_sqlite(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "round_trip.db"
        db = ProgramDatabase(
            config=DatabaseConfig(db_path=str(db_path), num_islands=1),
            embedding_model="",
        )
        try:
            db.add(
                _make_program(
                    "round-trip",
                    error_traceback="Traceback (most recent call last)\nValueError: x",
                    attempt_round=2,
                    mutation_type="error_fix",
                    mutation_intent=(
                        "Tighten loop bound | technique: clamp i to len(arr) "
                        "| expected: avoid IndexError on empty input"
                    ),
                    model_used="gpt-5-codex",
                )
            )
            loaded = db.get("round-trip")
            assert loaded is not None
            assert loaded.error_traceback is not None
            assert "ValueError: x" in loaded.error_traceback
            assert loaded.attempt_round == 2
            assert loaded.mutation_type == "error_fix"
            assert loaded.mutation_intent is not None
            assert "clamp i to len(arr)" in loaded.mutation_intent
            assert loaded.model_used == "gpt-5-codex"
        finally:
            db.close()


def test_migration_adds_new_columns_to_legacy_schema(monkeypatch, caplog):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "legacy.db"

        # Hand-craft a pre-Phase-1 schema (only the pre-existing columns).
        legacy_conn = sqlite3.connect(str(db_path))
        try:
            legacy_conn.execute(
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
            legacy_conn.commit()

            # Sanity: legacy schema has none of the new columns.
            legacy_cols = {row[1] for row in legacy_conn.execute("PRAGMA table_info(programs)")}
            assert legacy_cols.isdisjoint(_NEW_COLUMNS)
        finally:
            legacy_conn.close()

        # Opening through the live code should auto-migrate.
        db = ProgramDatabase(
            config=DatabaseConfig(db_path=str(db_path), num_islands=1),
            embedding_model="",
        )
        try:
            cursor = db.conn.cursor()
            migrated_cols = {row[1] for row in cursor.execute("PRAGMA table_info(programs)")}
            assert set(_NEW_COLUMNS).issubset(migrated_cols)

            # Round-trip on the migrated DB to verify NOT NULL DEFAULT 0 on
            # attempt_round.
            db.add(_make_program("post-migration"))
            loaded = db.get("post-migration")
            assert loaded is not None
            assert loaded.attempt_round == 0
        finally:
            db.close()

        # Reopening must not re-run ALTER (idempotency).
        db_again = ProgramDatabase(
            config=DatabaseConfig(db_path=str(db_path), num_islands=1),
            embedding_model="",
        )
        try:
            cursor = db_again.conn.cursor()
            cols = {row[1] for row in cursor.execute("PRAGMA table_info(programs)")}
            assert set(_NEW_COLUMNS).issubset(cols)
        finally:
            db_again.close()


def test_validate_mutation_intent_accepts_well_formed_dict():
    rendered = validate_mutation_intent(
        {
            "name": "Bias init scale",
            "primary_technique": "Initialize bias to small positive value",
            "expected_effect": "Avoids dead-ReLU on the first few mini-batches.",
        }
    )
    assert rendered != NO_INTENT_RECORDED
    assert "Bias init scale" in rendered
    assert "Initialize bias to small positive value" in rendered
    assert "Avoids dead-ReLU on the first few mini-batches." in rendered


def test_validate_mutation_intent_accepts_pydantic_instance():
    instance = MutationIntent(
        name="Tighten loop bound",
        primary_technique="Clamp i to len(arr)",
        expected_effect="Avoids IndexError on empty input.",
    )
    rendered = validate_mutation_intent(instance)
    assert rendered == instance.render()


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "freeform text that should never reach the DB",
        {"name": "Way too many words here exceeds the limit",
         "primary_technique": "x", "expected_effect": "y"},
        {"name": "ok", "primary_technique": "x\nwith newline",
         "expected_effect": "ok"},
        {"name": "ok", "primary_technique": "x",
         "expected_effect": "two\nlines"},
        {"name": "", "primary_technique": "x", "expected_effect": "y"},
        {"name": "ok"},  # missing keys
        42,
        ["list", "of", "strings"],
    ],
)
def test_validate_mutation_intent_falls_back_on_invalid_input(raw):
    assert validate_mutation_intent(raw) == NO_INTENT_RECORDED


def test_mutation_intent_truncates_oversized_technique():
    too_long = "x" * 200  # > _MAX_TECHNIQUE_CHARS=140
    assert (
        validate_mutation_intent(
            {"name": "ok", "primary_technique": too_long, "expected_effect": "y"}
        )
        == NO_INTENT_RECORDED
    )

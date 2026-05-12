"""Tests for ``query_evolution_db_tool``.

Uses a real sqlite file (via ``tmp_path``) populated with a minimal
``programs`` table that mirrors the production schema for the columns
the tool reads. This is faster than mocking sqlite3 and exercises the
real SQL queries.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from shinka.llm.agent.tools import ShinkaToolContext
from shinka.llm.agent.tools.query_db import (
    _MAX_RESULT_ROWS,
    _VALID_QUERY_TYPES,
    _query_evolution_db_impl,
    _query_evolution_db_tool,
)


def _make_db(
    tmp_path: Path,
    programs: List[Tuple],
) -> str:
    """Build a tiny programs table at ``tmp_path/evolution_db.sqlite``.

    ``programs`` is a list of tuples
        (id, generation, parent_id, combined_score, correct,
         code, metadata_json_str, text_feedback)
    """
    db_path = tmp_path / "evolution_db.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE programs (
                id TEXT PRIMARY KEY,
                generation INTEGER,
                parent_id TEXT,
                combined_score REAL,
                correct INTEGER,
                code TEXT,
                metadata TEXT,
                text_feedback TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO programs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            programs,
        )
        conn.commit()
    finally:
        conn.close()
    return str(db_path)


def _ctx(db_path: Optional[str] = None) -> ShinkaToolContext:
    return ShinkaToolContext(
        patch_dir="/tmp/run-1",
        parent_code="",
        db_path=db_path,
    )


def test_returns_error_when_db_path_unset() -> None:
    state = _ctx(db_path=None)
    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="top_n_by_score")
    )
    assert result.startswith("Error:")
    assert "not configured" in result
    assert state.tool_call_trace[0]["error"] == "no_db_path"


def test_returns_error_for_unknown_query_type(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path, [])
    state = _ctx(db_path=db_path)
    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="wat")
    )
    assert result.startswith("Error: Invalid query_type")


def test_top_n_by_score_returns_correct_programs_descending(
    tmp_path: Path,
) -> None:
    rows = [
        ("p1", 1, None, 0.5, 1, "code1", None, None),
        ("p2", 2, "p1", 0.9, 1, "code2", None, None),
        ("p3", 3, "p2", 0.3, 1, "code3", None, None),
        ("p_bad", 4, "p3", 0.99, 0, "bad", None, None),  # excluded (correct=0)
    ]
    db_path = _make_db(tmp_path, rows)
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="top_n_by_score", limit=10)
    )
    payload = json.loads(result)
    assert payload["query_type"] == "top_n_by_score"
    ids = [r["id"] for r in payload["rows"]]
    # Sorted by combined_score DESC, p_bad excluded as correct=0.
    assert ids == ["p2", "p1", "p3"]
    assert payload["count"] == 3


def test_top_n_respects_limit(tmp_path: Path) -> None:
    rows = [
        (f"p{i}", i, None, float(i), 1, "code", None, None) for i in range(20)
    ]
    db_path = _make_db(tmp_path, rows)
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="top_n_by_score", limit=5)
    )
    payload = json.loads(result)
    assert payload["count"] == 5


def test_limit_is_hard_capped_at_max_result_rows(tmp_path: Path) -> None:
    rows = [
        (f"p{i}", i, None, float(i), 1, "code", None, None)
        for i in range(_MAX_RESULT_ROWS + 20)
    ]
    db_path = _make_db(tmp_path, rows)
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(
            state, query_type="top_n_by_score", limit=_MAX_RESULT_ROWS + 100
        )
    )
    payload = json.loads(result)
    # Even when the agent requests more, the cap holds.
    assert payload["count"] == _MAX_RESULT_ROWS


def test_recent_failures_returns_correct_zero_descending(tmp_path: Path) -> None:
    rows = [
        ("ok", 1, None, 0.5, 1, "good", None, None),
        ("f1", 2, None, 0.1, 0, "bad1", '{"error":"timeout"}', None),
        ("f2", 3, None, 0.0, 0, "bad2", '{"error":"oob"}', None),
        ("f3", 4, None, 0.05, 0, "bad3", '{}', None),
    ]
    db_path = _make_db(tmp_path, rows)
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="recent_failures", limit=10)
    )
    payload = json.loads(result)
    # ORDER BY generation DESC, correct=0 only.
    ids = [r["id"] for r in payload["rows"]]
    assert ids == ["f3", "f2", "f1"]
    # Error info should be surfaced from metadata when correct=False.
    f1 = next(r for r in payload["rows"] if r["id"] == "f1")
    assert f1.get("error") == "timeout"


def test_by_generation_filters_and_requires_generation_arg(
    tmp_path: Path,
) -> None:
    rows = [
        ("a", 1, None, 0.5, 1, "code", None, None),
        ("b", 1, None, 0.8, 1, "code", None, None),
        ("c", 2, None, 0.9, 1, "code", None, None),
    ]
    db_path = _make_db(tmp_path, rows)
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(
            state, query_type="by_generation", generation=1, limit=10
        )
    )
    payload = json.loads(result)
    ids = sorted(r["id"] for r in payload["rows"])
    assert ids == ["a", "b"]

    # Without the generation arg, it should error.
    state2 = _ctx(db_path=db_path)
    err = asyncio.run(
        _query_evolution_db_impl(state2, query_type="by_generation", limit=10)
    )
    assert err.startswith("Error:")
    assert "generation" in err.lower()


def test_lineage_walks_parent_ids_up_to_limit(tmp_path: Path) -> None:
    rows = [
        ("g1", 1, None, 0.5, 1, "code", None, None),
        ("g2", 2, "g1", 0.6, 1, "code", None, None),
        ("g3", 3, "g2", 0.7, 1, "code", None, None),
        ("g4", 4, "g3", 0.8, 1, "code", None, None),
    ]
    db_path = _make_db(tmp_path, rows)
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(
            state, query_type="lineage_of", program_id="g4", limit=10
        )
    )
    payload = json.loads(result)
    ids = [r["id"] for r in payload["rows"]]
    # Newest to oldest, walking parent_id back.
    assert ids == ["g4", "g3", "g2", "g1"]


def test_lineage_respects_limit(tmp_path: Path) -> None:
    rows = [
        ("g1", 1, None, 0.5, 1, "code", None, None),
        ("g2", 2, "g1", 0.6, 1, "code", None, None),
        ("g3", 3, "g2", 0.7, 1, "code", None, None),
        ("g4", 4, "g3", 0.8, 1, "code", None, None),
    ]
    db_path = _make_db(tmp_path, rows)
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(
            state, query_type="lineage_of", program_id="g4", limit=2
        )
    )
    payload = json.loads(result)
    assert [r["id"] for r in payload["rows"]] == ["g4", "g3"]


def test_lineage_handles_missing_id_gracefully(tmp_path: Path) -> None:
    db_path = _make_db(
        tmp_path, [("a", 1, None, 0.5, 1, "code", None, None)]
    )
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(
            state, query_type="lineage_of", program_id="does_not_exist"
        )
    )
    payload = json.loads(result)
    assert payload["count"] == 0
    assert payload["rows"] == []


def test_lineage_requires_program_id() -> None:
    state = _ctx(db_path="/tmp/whatever.sqlite")
    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="lineage_of")
    )
    assert result.startswith("Error:")
    assert "program_id" in result


def test_missing_db_file_is_returned_as_error(tmp_path: Path) -> None:
    state = _ctx(db_path=str(tmp_path / "nope.sqlite"))
    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="top_n_by_score")
    )
    assert result.startswith("Error:")
    assert state.tool_call_trace[0]["success"] is False


def test_code_preview_is_truncated(tmp_path: Path) -> None:
    long_code = "x = 1\n" * 1000  # ~6000 chars
    db_path = _make_db(
        tmp_path, [("p", 1, None, 0.5, 1, long_code, None, None)]
    )
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="top_n_by_score")
    )
    payload = json.loads(result)
    preview = payload["rows"][0]["code_preview"]
    assert "truncated" in preview
    assert len(preview) < len(long_code)


def test_description_extracted_from_metadata_json(tmp_path: Path) -> None:
    md = json.dumps({"description": "swap quicksort for radix sort"})
    db_path = _make_db(
        tmp_path, [("p1", 1, None, 0.5, 1, "code", md, None)]
    )
    state = _ctx(db_path=db_path)

    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="top_n_by_score")
    )
    payload = json.loads(result)
    assert payload["rows"][0]["description"] == "swap quicksort for radix sort"


def test_invalid_metadata_json_does_not_crash(tmp_path: Path) -> None:
    """Malformed metadata JSON should not crash the tool — we skip
    the description extraction and return the row anyway."""
    db_path = _make_db(
        tmp_path, [("p1", 1, None, 0.5, 1, "code", "{not json", None)]
    )
    state = _ctx(db_path=db_path)
    result = asyncio.run(
        _query_evolution_db_impl(state, query_type="top_n_by_score")
    )
    payload = json.loads(result)
    assert payload["count"] == 1
    assert "description" not in payload["rows"][0]


def test_valid_query_types_constant() -> None:
    """Sanity: schema-of-the-API match."""
    assert _VALID_QUERY_TYPES == {
        "top_n_by_score",
        "recent_failures",
        "lineage_of",
        "by_generation",
    }


def test_decorated_tool_registered() -> None:
    from shinka.llm.agent.tools import available_tool_names, select_shinka_tools

    assert "query_evolution_db" in available_tool_names()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    selected = select_shinka_tools(["query_evolution_db"], ctx)
    assert selected == [_query_evolution_db_tool]


def test_tool_schema_has_expected_args() -> None:
    schema = _query_evolution_db_tool.params_json_schema
    properties = schema.get("properties", {})
    assert "query_type" in properties
    assert "limit" in properties
    assert "program_id" in properties
    assert "generation" in properties
    assert "ctx" not in properties

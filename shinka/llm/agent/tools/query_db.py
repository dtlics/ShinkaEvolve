"""``query_evolution_db_tool`` — read past programs from evolution_db.sqlite.

Gives the agent visibility into the evolution history *during* its run,
not just the static snapshot that ``PromptSampler`` chose to inject
into the system prompt. Useful queries:

- ``top_n_by_score``: best programs so far. ``"what's our high-water mark?"``
- ``recent_failures``: programs that failed validation recently, with
  their error messages. ``"what bugs have we been hitting?"``
- ``lineage_of``: ancestry chain of a specific program. ``"how did this
  patch evolve from earlier ancestors?"``

Read-only. We use raw ``sqlite3`` rather than constructing the full
``ProgramDatabase`` because we don't need the write/maintenance/
embedding machinery — just a few selects. Avoids pulling the
embedding client into every agent run.

Sandbox / safety
----------------
* Connection is opened read-only via the SQLite URI mode.
* Queries are parameterized (no string concatenation of user input
  into SQL).
* Returned code snippets are truncated to keep agent context bounded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

from agents import RunContextWrapper, function_tool

from .context import ShinkaToolContext
from .registry import register_tool

logger = logging.getLogger(__name__)


# Cap on how much code we surface per program to keep agent context
# tractable. The agent can request the full code by re-querying with
# a narrower filter, or via ``read_host_file`` against the eval dir.
_CODE_PREVIEW_CHARS = 400
# Hard cap on number of rows returned regardless of requested limit.
_MAX_RESULT_ROWS = 50

_VALID_QUERY_TYPES = frozenset(
    {"top_n_by_score", "recent_failures", "lineage_of", "by_generation"}
)


@contextmanager
def _open_readonly(db_path: str):
    """Open a SQLite read-only connection. Using the URI form ensures
    the underlying file is opened with O_RDONLY, so even a buggy query
    can't corrupt the DB."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def _summarize_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Render a programs-table row into a compact dict the LLM can
    consume. Truncates code, parses JSON columns lazily, and strips
    None-valued fields to keep the payload tight."""
    code = row["code"] if "code" in row.keys() else None
    code_preview: Optional[str] = None
    if code:
        if len(code) > _CODE_PREVIEW_CHARS:
            code_preview = code[:_CODE_PREVIEW_CHARS] + "...(truncated)"
        else:
            code_preview = code

    metadata_raw = row["metadata"] if "metadata" in row.keys() else None
    metadata_obj: Optional[Dict[str, Any]] = None
    if metadata_raw:
        try:
            parsed = json.loads(metadata_raw)
            if isinstance(parsed, dict):
                metadata_obj = parsed
        except (TypeError, ValueError):
            metadata_obj = None

    description = None
    if metadata_obj:
        # Different shinka versions stash the patch description in
        # slightly different keys; prefer the explicit one then fall
        # back to anything with "description" in the name.
        for key in ("description", "patch_description", "summary"):
            if key in metadata_obj and isinstance(metadata_obj[key], str):
                description = metadata_obj[key]
                break

    text_feedback = row["text_feedback"] if "text_feedback" in row.keys() else None
    correct = bool(row["correct"]) if "correct" in row.keys() else None

    summary: Dict[str, Any] = {
        "id": row["id"],
        "generation": row["generation"],
        "parent_id": row["parent_id"],
        "combined_score": row["combined_score"],
        "correct": correct,
    }
    if description:
        summary["description"] = description
    if text_feedback:
        # Truncate text_feedback to keep payload bounded.
        if len(text_feedback) > _CODE_PREVIEW_CHARS:
            summary["text_feedback"] = (
                text_feedback[:_CODE_PREVIEW_CHARS] + "...(truncated)"
            )
        else:
            summary["text_feedback"] = text_feedback
    if code_preview:
        summary["code_preview"] = code_preview
    if metadata_obj and not correct:
        # Surface error info for failures.
        for err_key in ("error", "evaluation_error", "patch_error"):
            val = metadata_obj.get(err_key)
            if isinstance(val, str) and val:
                summary["error"] = val[:_CODE_PREVIEW_CHARS]
                break
    return summary


def _query_top_n_by_score(
    conn: sqlite3.Connection, limit: int
) -> List[sqlite3.Row]:
    """Top ``limit`` correct programs ordered by combined_score DESC."""
    cur = conn.execute(
        """
        SELECT id, generation, parent_id, combined_score, correct,
               code, metadata, text_feedback
          FROM programs
         WHERE correct = 1
      ORDER BY combined_score DESC
         LIMIT ?
        """,
        (limit,),
    )
    return cur.fetchall()


def _query_recent_failures(
    conn: sqlite3.Connection, limit: int
) -> List[sqlite3.Row]:
    """Recent ``limit`` failed programs, newest generation first."""
    cur = conn.execute(
        """
        SELECT id, generation, parent_id, combined_score, correct,
               code, metadata, text_feedback
          FROM programs
         WHERE correct = 0
      ORDER BY generation DESC
         LIMIT ?
        """,
        (limit,),
    )
    return cur.fetchall()


def _query_by_generation(
    conn: sqlite3.Connection, generation: int, limit: int
) -> List[sqlite3.Row]:
    """All programs in the given generation, capped at ``limit``."""
    cur = conn.execute(
        """
        SELECT id, generation, parent_id, combined_score, correct,
               code, metadata, text_feedback
          FROM programs
         WHERE generation = ?
      ORDER BY combined_score DESC
         LIMIT ?
        """,
        (generation, limit),
    )
    return cur.fetchall()


def _query_lineage(
    conn: sqlite3.Connection, program_id: str, max_ancestors: int
) -> List[sqlite3.Row]:
    """Walk parent_id back from ``program_id`` up to ``max_ancestors``."""
    chain: List[sqlite3.Row] = []
    current_id: Optional[str] = program_id
    seen: set[str] = set()
    while current_id and len(chain) < max_ancestors and current_id not in seen:
        seen.add(current_id)
        cur = conn.execute(
            """
            SELECT id, generation, parent_id, combined_score, correct,
                   code, metadata, text_feedback
              FROM programs
             WHERE id = ?
            """,
            (current_id,),
        )
        row = cur.fetchone()
        if row is None:
            break
        chain.append(row)
        current_id = row["parent_id"]
    return chain


def _summarize_rows(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [_summarize_row(r) for r in rows]


async def _query_evolution_db_impl(
    state: ShinkaToolContext,
    query_type: str,
    limit: int = 10,
    program_id: Optional[str] = None,
    generation: Optional[int] = None,
) -> str:
    """Telemetry: name + latency + success are recorded by
    ``ShinkaAgentHooks.on_tool_end``. The JSON success payload doesn't
    start with ``"OK"`` so the hook's success-detection falls through
    its escape hatch (non-error → success); error returns are prefixed
    ``"Error:"`` and detected normally."""
    if state.db_path is None:
        return "Error: evolution database is not configured for this run."

    if query_type not in _VALID_QUERY_TYPES:
        return (
            f"Error: Invalid query_type {query_type!r}. Expected one of "
            f"{sorted(_VALID_QUERY_TYPES)}."
        )

    # Validate required args up front so the error doesn't get masked
    # by the more general database-open error when the path is wrong.
    if query_type == "by_generation" and generation is None:
        return "Error: query_type='by_generation' requires the 'generation' arg."
    if query_type == "lineage_of" and not program_id:
        return "Error: query_type='lineage_of' requires the 'program_id' arg."

    effective_limit = max(1, min(limit, _MAX_RESULT_ROWS))

    try:
        # sqlite3 is sync; offload to a thread so we don't block the
        # event loop on disk I/O.
        rows = await asyncio.to_thread(
            _execute_query,
            state.db_path,
            query_type,
            effective_limit,
            program_id,
            generation,
        )
    except FileNotFoundError:
        return f"Error: evolution_db.sqlite not found at {state.db_path}"
    except sqlite3.Error as exc:
        logger.info("query_evolution_db sqlite error: %s", exc)
        return f"Error: database error: {exc}"
    except ValueError as exc:
        # Raised by _execute_query when required args are missing.
        return f"Error: {exc}"

    summaries = _summarize_rows(rows)
    state.last_tool_extras = {
        "query_type": query_type,
        "result_count": len(summaries),
    }
    payload = {"query_type": query_type, "count": len(summaries), "rows": summaries}
    return json.dumps(payload, default=repr, separators=(",", ":"))


def _execute_query(
    db_path: str,
    query_type: str,
    limit: int,
    program_id: Optional[str],
    generation: Optional[int],
) -> List[sqlite3.Row]:
    """Sync helper offloaded to a thread by the async wrapper.

    Splits per ``query_type`` so each branch can validate its own
    required args explicitly (raises ``ValueError`` on missing
    requirements; the async caller turns this into an Error: ...).
    """
    with _open_readonly(db_path) as conn:
        if query_type == "top_n_by_score":
            return _query_top_n_by_score(conn, limit)
        if query_type == "recent_failures":
            return _query_recent_failures(conn, limit)
        if query_type == "by_generation":
            if generation is None:
                raise ValueError(
                    "query_type='by_generation' requires the 'generation' arg."
                )
            return _query_by_generation(conn, int(generation), limit)
        if query_type == "lineage_of":
            if not program_id:
                raise ValueError(
                    "query_type='lineage_of' requires the 'program_id' arg."
                )
            return _query_lineage(conn, program_id, limit)
        raise ValueError(f"Unhandled query_type {query_type!r}")


@function_tool
async def _query_evolution_db_tool(
    ctx: RunContextWrapper[ShinkaToolContext],
    query_type: str,
    limit: int = 10,
    program_id: Optional[str] = None,
    generation: Optional[int] = None,
) -> str:
    """Query the evolution database for past program information.

    Use this when you want to reason about the run's history beyond
    the context you've been given in the system prompt — e.g. to see
    the current best score, recent failure modes, or trace how a
    specific program was derived.

    Args:
        query_type: One of
            - ``"top_n_by_score"``: top ``limit`` correct programs by
              combined_score (descending).
            - ``"recent_failures"``: last ``limit`` programs that
              failed validation, newest generation first. Includes
              error info when available.
            - ``"by_generation"``: programs from a specific
              ``generation``. Requires the ``generation`` arg.
            - ``"lineage_of"``: ancestry chain of ``program_id``,
              following parent_id back up to ``limit`` ancestors.
              Requires the ``program_id`` arg.
        limit: Result row cap. Defaults to 10, hard-capped at 50.
        program_id: Required for ``"lineage_of"``; ignored otherwise.
        generation: Required for ``"by_generation"``; ignored otherwise.

    Returns:
        JSON-encoded dict ``{"query_type": ..., "count": N, "rows":
        [...]}`` on success. Each row has id, generation, parent_id,
        combined_score, correct, and (when available) description,
        code_preview, text_feedback, error.

        Returns ``"Error: <message>"`` on failure (no DB configured,
        invalid query_type, missing required arg, database error).
    """
    return await _query_evolution_db_impl(
        ctx.context,
        query_type=query_type,
        limit=limit,
        program_id=program_id,
        generation=generation,
    )


def make_query_evolution_db_tool(ctx: ShinkaToolContext) -> Any:
    return _query_evolution_db_tool


register_tool("query_evolution_db", make_query_evolution_db_tool)

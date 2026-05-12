"""Tool primitives for the OpenAI / Azure OpenAI Responses API.

Phase 3 of research-grounding adds a ``tools`` kwarg to the LLM client.
This module defines:

- :class:`ToolSpec` -- a uniform interface every tool implements.
- :class:`WebSearchTool` -- OpenAI's built-in server-side web search.
- :class:`WebFetchTool` -- our own custom function tool that fetches URLs
  through a shared :class:`URLFetchCache` so simultaneous tasks coalesce.
- :class:`ShellTool` -- Codex's first-class server-side shell tool.
- :class:`ToolBudget` -- a counter that callers consult before dispatching;
  exceeded budgets cancel the loop in :func:`shinka.llm.poll.create_and_poll`.
- :class:`URLFetchCache` -- a SQLite-backed cache keyed by URL with a
  per-URL ``asyncio.Lock`` so concurrent dispatchers fetch each URL once.

Design notes
~~~~~~~~~~~~

- Server-side tools (``web_search``, ``shell``) are dispatched by the API;
  the client never sees ``requires_action`` for them. Their ``dispatch``
  raises ``NotImplementedError`` so misuse is caught loudly.
- Client-side tools (``web_fetch``) appear as ``requires_action`` items in
  the Responses API output. :func:`shinka.llm.poll.create_and_poll` calls
  the matching :class:`ToolSpec.dispatch`, then submits the result via
  ``client.responses.submit_tool_outputs``.
- ``tool_choice`` defaults to ``"auto"`` whenever ``tools`` is provided
  (never ``"required"`` -- that risks unbounded loops; see the
  research-grounding plan gotcha #7).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ToolBudget
# ---------------------------------------------------------------------------


@dataclass
class ToolBudget:
    """Hard caps on tool usage per call.

    The ``max_turns`` knob applies to the whole tool-using loop -- one turn
    is one round trip between the model and the tool dispatcher. The other
    counters are decremented inside :meth:`spend` whenever the corresponding
    tool type is dispatched. Once any counter reaches zero further dispatches
    raise :class:`ToolBudgetExceeded`.
    """

    max_searches: int = 3
    max_fetches: int = 2
    max_shell: int = 4
    max_turns: int = 6

    def __post_init__(self) -> None:
        self._remaining = {
            "web_search": int(self.max_searches),
            "web_fetch": int(self.max_fetches),
            "shell": int(self.max_shell),
        }
        self._turns_left = int(self.max_turns)

    def remaining(self, tool_name: str) -> int:
        return self._remaining.get(tool_name, 0)

    @property
    def turns_left(self) -> int:
        return self._turns_left

    def can_spend(self, tool_name: str) -> bool:
        if self._turns_left <= 0:
            return False
        return self._remaining.get(tool_name, 0) > 0

    def consume_turn(self) -> None:
        self._turns_left -= 1

    def spend(self, tool_name: str) -> None:
        if not self.can_spend(tool_name):
            raise ToolBudgetExceeded(
                f"Budget exhausted for {tool_name!r}: "
                f"{self._remaining.get(tool_name)} remaining, "
                f"{self._turns_left} turns left"
            )
        self._remaining[tool_name] -= 1


class ToolBudgetExceeded(RuntimeError):
    """Raised when a tool dispatch would exceed the per-call budget."""


# ---------------------------------------------------------------------------
# URL fetch cache (SQLite + per-URL lock for in-process coalescing)
# ---------------------------------------------------------------------------


_DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60


class URLFetchCache:
    """SQLite-backed cache for ``web_fetch`` results.

    The on-disk table is named ``web_fetch_cache`` so the database migration
    in :mod:`shinka.database.dbase` can create it next to ``programs``.
    Concurrent tasks fetching the same URL coalesce through a per-URL
    :class:`asyncio.Lock` so we never issue two HTTP requests for the same
    URL in flight.
    """

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS web_fetch_cache (
            url TEXT PRIMARY KEY,
            content TEXT,
            content_type TEXT,
            http_status INTEGER,
            fetched_at REAL NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0
        )
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        ttl_seconds: int = _DEFAULT_CACHE_TTL_SECONDS,
        fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
    ) -> None:
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self._async_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sync_lock = threading.Lock()
        self._fetcher = fetcher or _default_http_fetcher
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._ensure_table()

    def _ensure_table(self) -> None:
        if not self.db_path:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(self.SCHEMA)
            conn.commit()

    def _lookup(self, url: str) -> Optional[Dict[str, Any]]:
        if not self.db_path:
            return None
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT content, content_type, http_status, fetched_at, hits "
                "FROM web_fetch_cache WHERE url = ?",
                (url,),
            ).fetchone()
            if not row:
                return None
            content, content_type, http_status, fetched_at, hits = row
            if (time.time() - fetched_at) > self.ttl_seconds:
                return None
            conn.execute(
                "UPDATE web_fetch_cache SET hits = hits + 1 WHERE url = ?",
                (url,),
            )
            conn.commit()
        return {
            "url": url,
            "content": content,
            "content_type": content_type,
            "http_status": http_status,
            "from_cache": True,
            "hits": hits + 1,
        }

    def _store(self, url: str, payload: Dict[str, Any]) -> None:
        if not self.db_path:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO web_fetch_cache "
                "(url, content, content_type, http_status, fetched_at, hits) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (
                    url,
                    payload.get("content"),
                    payload.get("content_type"),
                    payload.get("http_status"),
                    time.time(),
                ),
            )
            conn.commit()

    async def get_async(self, url: str) -> Dict[str, Any]:
        """Async fetch with coalescing and TTL caching."""
        cached = self._lookup(url)
        if cached is not None:
            return cached
        # Coalesce parallel fetches for the same URL.
        lock = self._async_locks[url]
        async with lock:
            cached = self._lookup(url)
            if cached is not None:
                return cached
            loop = asyncio.get_event_loop()
            payload = await loop.run_in_executor(None, self._fetcher, url)
            self._store(url, payload)
            return payload

    def get(self, url: str) -> Dict[str, Any]:
        """Sync fetch with TTL caching (no in-process coalescing)."""
        with self._sync_lock:
            cached = self._lookup(url)
            if cached is not None:
                return cached
            payload = self._fetcher(url)
            self._store(url, payload)
            return payload


def _default_http_fetcher(url: str) -> Dict[str, Any]:
    """Plain GET via httpx; returns a dict with content + metadata."""
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            response = client.get(url)
        return {
            "url": url,
            "content": response.text,
            "content_type": response.headers.get("content-type"),
            "http_status": response.status_code,
            "from_cache": False,
        }
    except Exception as exc:  # noqa: BLE001 -- surface the error to the model
        logger.warning("web_fetch failed for %s: %s", url, exc)
        return {
            "url": url,
            "content": f"web_fetch failed: {exc}",
            "content_type": None,
            "http_status": None,
            "from_cache": False,
        }


# ---------------------------------------------------------------------------
# ToolSpec interface + concrete tools
# ---------------------------------------------------------------------------


@runtime_checkable
class ToolSpec(Protocol):
    """Uniform interface every tool must implement.

    - ``name``: matches the OpenAI tool name as it appears in
      ``response.output[*].name`` for client-side dispatch, and the
      ``"type"`` field for server-side built-ins.
    - ``to_openai_dict()``: emits the dict appended to the ``tools`` kwarg.
    - ``is_server_side``: ``True`` if OpenAI runs the tool itself and never
      surfaces ``requires_action`` for it.
    - ``dispatch(args, ctx)``: client-side handler. For server-side tools
      this raises :class:`NotImplementedError` so misuse is caught loudly.
    """

    name: str
    is_server_side: bool

    def to_openai_dict(self) -> Dict[str, Any]: ...

    def dispatch(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]: ...


@dataclass
class WebSearchTool:
    """OpenAI's built-in server-side web search tool.

    OpenAI runs the search itself during response generation; we just
    surface the spec on the ``tools`` list. Optional allowed-domains list
    is informational only -- the API filter is per-deployment.
    """

    name: str = "web_search"
    allowed_domains: List[str] = field(default_factory=list)
    is_server_side: bool = True

    def to_openai_dict(self) -> Dict[str, Any]:
        # The Responses API accepts ``{"type": "web_search"}`` as the
        # built-in tool. Some deployments use ``web_search_preview``; we
        # default to the stable name and let callers override via a
        # subclass if needed.
        return {"type": "web_search"}

    def dispatch(
        self, args: Dict[str, Any], ctx: Dict[str, Any]
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "WebSearchTool is server-side; OpenAI dispatches it internally."
        )


@dataclass
class WebFetchTool:
    """Custom function tool: fetch a URL through :class:`URLFetchCache`.

    Surfaced to the model as a function named ``web_fetch`` taking a single
    ``url`` argument. The dispatcher reads ``ctx["url_fetch_cache"]`` (a
    :class:`URLFetchCache` instance) and ``ctx.get("tool_budget")``.
    """

    name: str = "web_fetch"
    description: str = (
        "Fetch the textual contents of a single URL. "
        "Use sparingly and only for sources directly relevant to the "
        "reference material you have already been given."
    )
    is_server_side: bool = False

    def to_openai_dict(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Absolute http(s) URL to fetch.",
                    }
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        }

    def dispatch(
        self, args: Dict[str, Any], ctx: Dict[str, Any]
    ) -> Dict[str, Any]:
        url = args.get("url")
        if not url:
            return {"error": "missing 'url' argument"}
        cache = ctx.get("url_fetch_cache")
        if cache is None:
            return {"error": "no URLFetchCache configured"}
        try:
            return cache.get(url)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"fetch failed: {exc}"}

    async def dispatch_async(
        self, args: Dict[str, Any], ctx: Dict[str, Any]
    ) -> Dict[str, Any]:
        url = args.get("url")
        if not url:
            return {"error": "missing 'url' argument"}
        cache = ctx.get("url_fetch_cache")
        if cache is None:
            return {"error": "no URLFetchCache configured"}
        return await cache.get_async(url)


@dataclass
class ShellTool:
    """Codex's first-class server-side shell tool (Phase 4 error-fix only).

    Wiring sits here in Phase 3a so the type is importable; gating on
    ``error_fix_enable_shell`` and adding ``ShellTool()`` to the tools list
    happens in Phase 4.
    """

    name: str = "shell"
    is_server_side: bool = True

    def to_openai_dict(self) -> Dict[str, Any]:
        return {"type": "shell"}

    def dispatch(
        self, args: Dict[str, Any], ctx: Dict[str, Any]
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "ShellTool is server-side; Codex dispatches it internally."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def serialize_tools(tools: Optional[Iterable[ToolSpec]]) -> Optional[List[Dict[str, Any]]]:
    """Convert ToolSpec instances to the OpenAI ``tools`` kwarg shape."""
    if not tools:
        return None
    return [tool.to_openai_dict() for tool in tools]


def lookup_tool_by_name(
    tools: Optional[Iterable[ToolSpec]], name: str
) -> Optional[ToolSpec]:
    if not tools:
        return None
    for tool in tools:
        if tool.name == name:
            return tool
    return None


__all__ = [
    "ShellTool",
    "ToolBudget",
    "ToolBudgetExceeded",
    "ToolSpec",
    "URLFetchCache",
    "WebFetchTool",
    "WebSearchTool",
    "lookup_tool_by_name",
    "serialize_tools",
]

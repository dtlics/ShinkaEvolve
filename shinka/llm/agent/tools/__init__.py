"""Agent tools for shinka.

The toolset is intentionally narrow. Each tool is implemented as a
``function_tool``-decorated async function in its own module, takes
a ``RunContextWrapper[ShinkaToolContext]`` as the first parameter,
and returns a string the LLM can read in its next turn.

Public API
----------
* ``ShinkaToolContext`` — per-generation state.
* ``default_shinka_tools(ctx)`` — return the standard tool list
  (the orchestrator passes this to ``Agent(tools=...)``).
* ``select_shinka_tools(names, ctx)`` — return a filtered subset.

Tools land incrementally; each phase commit adds one entry to
``registry._TOOL_REGISTRY`` by importing the tool module here. See
``AGENTIC_REWRITE.md`` for the planned sequence.

Why the indirection (registry.py + this module): tool modules need
to call ``register_tool`` at import time, and they would otherwise
have to import from ``tools/__init__.py`` — which in turn imports
the tool modules. Splitting the registry out breaks that cycle.
"""

from __future__ import annotations

from .context import (
    DEFAULT_EVAL_TIMEOUT_SEC,
    DEFAULT_PROBE_TIMEOUT_SEC,
    DEFAULT_READ_FILE_MAX_BYTES,
    ShinkaToolContext,
)
from .registry import (
    _TOOL_REGISTRY,  # exposed for tests; not part of stable API
    available_tool_names,
    default_shinka_tools,
    register_tool,
    select_shinka_tools,
)

# Eager-import tool modules so they register themselves. Order
# doesn't matter — each module is independent. Add new tools here as
# they land.
from . import apply_patch as _apply_patch  # noqa: F401
from . import evaluate as _evaluate  # noqa: F401
from . import query_db as _query_db  # noqa: F401

__all__ = [
    "DEFAULT_EVAL_TIMEOUT_SEC",
    "DEFAULT_PROBE_TIMEOUT_SEC",
    "DEFAULT_READ_FILE_MAX_BYTES",
    "ShinkaToolContext",
    "available_tool_names",
    "default_shinka_tools",
    "register_tool",
    "select_shinka_tools",
]

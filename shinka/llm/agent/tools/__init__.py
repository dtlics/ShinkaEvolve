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
``_TOOL_REGISTRY`` below. See ``AGENTIC_REWRITE.md`` for the planned
sequence.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .context import (
    DEFAULT_EVAL_TIMEOUT_SEC,
    DEFAULT_PROBE_TIMEOUT_SEC,
    DEFAULT_READ_FILE_MAX_BYTES,
    ShinkaToolContext,
)


# Mapping of tool name -> factory(ctx) -> Tool. Factories rather than
# tool instances let each tool decide whether it needs the context at
# construction time (e.g. to capture sandbox roots) or only at call
# time (most tools, via the RunContextWrapper).
_TOOL_REGISTRY: Dict[str, Callable[[ShinkaToolContext], Any]] = {}


def register_tool(
    name: str, factory: Callable[[ShinkaToolContext], Any]
) -> Callable[[ShinkaToolContext], Any]:
    """Register a tool factory under ``name``. Returns the factory so
    it can be used as a decorator if convenient. Idempotent on
    re-registration with the same name (last wins)."""
    _TOOL_REGISTRY[name] = factory
    return factory


def available_tool_names() -> List[str]:
    """Names of tools currently registered. Useful for diagnostics."""
    return sorted(_TOOL_REGISTRY.keys())


def select_shinka_tools(
    names: List[str], ctx: ShinkaToolContext
) -> List[Any]:
    """Return the tools matching ``names``. Unknown names raise
    ``KeyError`` rather than silently skip so misconfiguration is
    loud."""
    tools: List[Any] = []
    for name in names:
        try:
            factory = _TOOL_REGISTRY[name]
        except KeyError as exc:
            available = ", ".join(available_tool_names()) or "(none)"
            raise KeyError(
                f"Unknown shinka tool {name!r}. Available: {available}"
            ) from exc
        tools.append(factory(ctx))
    return tools


def default_shinka_tools(ctx: ShinkaToolContext) -> List[Any]:
    """All currently-registered tools. The orchestrator passes this
    to ``Agent(tools=...)`` unless a task config narrows the set via
    ``select_shinka_tools``."""
    return [factory(ctx) for factory in _TOOL_REGISTRY.values()]


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

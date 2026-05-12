"""Tool registry — internal module so individual tool modules can
import ``register_tool`` without depending on ``tools/__init__.py``
(which would import them, creating a cycle)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, NamedTuple

from .context import ShinkaToolContext


class _ToolEntry(NamedTuple):
    """Registry entry pairing a factory with opt-in metadata.

    ``opt_in=True`` means the tool is selectable by name via
    ``select_shinka_tools`` but is excluded from
    ``default_shinka_tools``. Used for tools with non-trivial cost
    (e.g. ``web_search``) or risk (e.g. ``run_probe``) where the
    caller should make an explicit decision rather than getting the
    tool by default.
    """

    factory: Callable[[ShinkaToolContext], Any]
    opt_in: bool


# Mapping of tool name -> registry entry.
_TOOL_REGISTRY: Dict[str, _ToolEntry] = {}


def register_tool(
    name: str,
    factory: Callable[[ShinkaToolContext], Any],
    *,
    opt_in: bool = False,
) -> Callable[[ShinkaToolContext], Any]:
    """Register a tool factory under ``name``.

    Args:
        name: The tool's lookup key.
        factory: Zero-or-one-arg callable that builds the tool from a
            ``ShinkaToolContext``. Most tools ignore the context at
            construction time and read from it at call time via the
            ``RunContextWrapper``.
        opt_in: When True, the tool is selectable by name but
            excluded from ``default_shinka_tools``. Use for tools
            with non-default costs or risks.

    Returns the factory unchanged so the call can be used as a
    decorator. Idempotent on re-registration with the same name
    (last wins).
    """
    _TOOL_REGISTRY[name] = _ToolEntry(factory=factory, opt_in=opt_in)
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
            entry = _TOOL_REGISTRY[name]
        except KeyError as exc:
            available = ", ".join(available_tool_names()) or "(none)"
            raise KeyError(
                f"Unknown shinka tool {name!r}. Available: {available}"
            ) from exc
        tools.append(entry.factory(ctx))
    return tools


def default_shinka_tools(ctx: ShinkaToolContext) -> List[Any]:
    """All non-opt-in registered tools. The orchestrator passes this
    to ``Agent(tools=...)`` unless a task config narrows the set via
    ``select_shinka_tools``. Opt-in tools (``web_search``, etc.)
    are excluded and must be requested explicitly."""
    return [entry.factory(ctx) for entry in _TOOL_REGISTRY.values() if not entry.opt_in]

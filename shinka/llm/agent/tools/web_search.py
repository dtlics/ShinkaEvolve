"""``web_search`` — server-side built-in via the openai-agents SDK.

Unlike the other shinka tools, ``web_search`` is implemented on the
server side: the OpenAI Responses API runs the search-and-synthesize
loop inside Azure's / OpenAI's cluster, and we get back the model's
final message with the integrated search results. There is no
function for us to execute locally.

The agents SDK exposes this as ``agents.WebSearchTool`` — a thin
configuration class the agent passes to ``Agent(tools=[...])``. We
just construct one per-context using ``ctx.web_search_context_size``
to drive the per-call cost / quality tradeoff.

Pricing reminder
----------------
Per OpenAI's official rate card (2026-05): $10 per 1000 calls plus
search content tokens billed at the underlying model's input rate.
``search_context_size`` controls how many tokens of search content
get fed to the model:

* ``"low"``: cheapest, narrow snippets.
* ``"medium"`` (default): balanced.
* ``"high"``: most context, most expensive.

For shinka, ``"medium"`` is the right default — we use reasoning
models that benefit from more context, but per-generation cost
matters too. Tasks can override via per-task config.
"""

from __future__ import annotations

from typing import Any

from agents import WebSearchTool

from .context import ShinkaToolContext
from .registry import register_tool


_VALID_CONTEXT_SIZES = frozenset({"low", "medium", "high"})


def make_web_search_tool(ctx: ShinkaToolContext) -> Any:
    """Return a fresh ``WebSearchTool`` configured for this run.

    Reads ``ctx.web_search_context_size``; falls back to ``"medium"``
    on invalid values rather than raising, since misconfiguration
    here shouldn't break the whole run.
    """
    size = ctx.web_search_context_size or "medium"
    if size not in _VALID_CONTEXT_SIZES:
        size = "medium"
    return WebSearchTool(search_context_size=size)


# Web search is registered but NOT eager-built — it's an opt-in tool
# because (a) it costs real money per call, and (b) it's only
# supported on OpenAI/Azure models. Tasks that want it call
# ``select_shinka_tools(["web_search", ...])`` explicitly rather than
# getting it implicitly via ``default_shinka_tools``.
#
# To enforce that, we register under a sentinel that
# ``default_shinka_tools`` consumers can filter out. The
# orchestrator passes a per-task allowlist anyway, so the registry
# entry just makes ``web_search`` resolvable when asked for by name.
register_tool("web_search", make_web_search_tool, opt_in=True)

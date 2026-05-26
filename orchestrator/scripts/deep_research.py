"""deep_research.py — call the deep-research model; return a grounded brief.

MUTABILITY: IMMUTABLE (cell D — LLM-embedded, wraps a paid external service).
Do NOT rewrite this. It wraps shinka's ``o3-deep-research`` path (a separate
Azure resource, ~$5/call). The orchestrator is free to *call* it and *interpret*
its output, but must not change its body. Per the SKILL.md: call it at problem
onset (to seed the initial program / island count / prompt) and at stuck-
stagnation moments (after ≥2 strategy rewrites failed to break a plateau). Be
deliberate — it is the most expensive single action in the system.

This is the Stage-C call from shinka's DR pipeline, exposed standalone: it sends
the research question + program context to ``o3-deep-research`` (background mode +
polling via ``run_dr_call``) and parses the returned techniques into a brief.
Requires AZURE_DR_ENDPOINT + AZURE_DR_API_KEY (see CLAUDE.md). A ``mock`` mode is
provided only to exercise the parsing/contract offline — it makes no API call.

INPUT (stdin JSON):
  {
    "query": str,                    # the research question
    "program_context": str,          # current best program / what's been tried
    "model": "o3-deep-research",
    "reasoning_effort": "medium",
    "max_tool_calls": 20,
    "mock": false, "mock_text": str | null
  }

OUTPUT (stdout JSON):
  {
    "ok": true,
    "brief": [ {idea, rationale, reference_source, reference_snippet, gotchas} ],
    "raw_text": str, "cost": float, "model": str
  }
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def _parse_brief(text: str) -> List[Dict[str, Any]]:
    """Lenient extraction of the techniques list from the model's text.

    Accepts a bare JSON object/array, or one wrapped in ``` fences or prose.
    """
    if not text:
        return []
    # Strip markdown fences if present.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    blob = fenced.group(1) if fenced else text
    # Find the first JSON object/array in the blob.
    start = min(
        [i for i in (blob.find("{"), blob.find("[")) if i != -1], default=-1
    )
    if start == -1:
        return []
    candidate = blob[start:]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # Try trimming to the last closing brace/bracket.
        for end in range(len(candidate), start, -1):
            try:
                data = json.loads(candidate[:end])
                break
            except json.JSONDecodeError:
                continue
        else:
            return []
    if isinstance(data, dict):
        items = data.get("techniques") or data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "idea": it.get("idea", ""),
                "rationale": it.get("rationale", ""),
                "reference_source": it.get("reference_source", ""),
                "reference_snippet": it.get("reference_snippet", ""),
                "gotchas": it.get("gotchas", ""),
            }
        )
    return out


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    from shinka.prompts import DR_STAGE_C_SYS_MSG, DR_STAGE_C_USER_MSG

    query = payload.get("query", "")
    program_context = payload.get("program_context", "")
    model = payload.get("model", "o3-deep-research")

    system_msg = DR_STAGE_C_SYS_MSG
    user_msg = DR_STAGE_C_USER_MSG.format(
        candidate_question=query, program_context=program_context
    )

    if payload.get("mock"):
        text = payload.get("mock_text", "") or "[]"
        return {"brief": _parse_brief(text), "raw_text": text, "cost": 0.0, "model": model}

    import asyncio
    from shinka.llm.agent.dr_client import get_dr_async_client, run_dr_call

    client, _base = get_dr_async_client()
    text, token_cost = asyncio.run(
        run_dr_call(
            client,
            model=model,
            system_msg=system_msg,
            user_msg=user_msg,
            reasoning_effort=payload.get("reasoning_effort", "medium"),
            max_tool_calls=int(payload.get("max_tool_calls", 20)),
            background=bool(payload.get("background", True)),
            call_metadata={"purpose": "dr_stage_c", "source": "orchestrator"},
        )
    )
    # o3-deep-research always runs web_search internally (~10-30 calls/query at
    # $10/1k = $0.10-0.30), which is NOT in token usage. Add a conservative
    # surcharge so the budget over-estimates rather than under-counts DR spend.
    search_surcharge = float(payload.get("search_surcharge_usd", 0.30))
    cost = float(token_cost) + search_surcharge
    return {
        "brief": _parse_brief(text), "raw_text": text,
        "cost": cost, "token_cost": float(token_cost),
        "search_surcharge": search_surcharge, "model": model,
    }


if __name__ == "__main__":
    _common.run_main(main)

"""deep_research.py — call the deep-research model; return a grounded brief.

MUTABILITY: IMMUTABLE (cell D — LLM-embedded, wraps a paid external service).
Do NOT rewrite this. It wraps shinka's ``o3-deep-research`` path (a separate
Azure resource, ~$5/call). The orchestrator is free to *call* it and *interpret*
its output, but must not change its body. Per the SKILL.md: call it at problem
onset (to seed the initial program / island count / prompt) and at stuck-
stagnation moments (after ≥2 strategy rewrites failed to break a plateau). Be
deliberate — it is the most expensive single action in the system. Before calling,
the orchestrator runs the SKILL.md "pre-flight self-check": the QUERY must target
GENERAL SOTA for the task/sub-task, never "reproduce a specific named paper" (that is
the follow-up pro grounding run's job). The ``reference_snippet`` field below is
requested by the IMMUTABLE Stage-C system prompt, NOT by your query — on a
``content_filter`` refusal, rewrite the QUERY shape, never retry the same one.

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
    "results_dir": str | null,       # WS7: if set, self-log the full call + fold cost into the ledger
    "budget_usd": float | null,      # D3: with results_dir, pre-flight-skip when budget can't cover dr_estimated_cost_usd
    "dr_estimated_cost_usd": 5.0,    # D3 pre-flight estimate
    "search_surcharge_usd": 0.30,    # D6: web-search cost guard (conservative over-estimate)
    "mock": false, "mock_text": str | null
  }

OUTPUT (stdout JSON):
  {
    "ok": true,
    "brief": [ {idea, rationale, reference_source, reference_snippet, gotchas} ],
    "raw_text": str, "cost": float, "model": str
  }
  On a REFUSED/FAILED call (P7-T6): { "refused": true, "degraded": true,
    "reason": "content_filter" | "dr_failed:<...>", "brief": [], "cost": <billed> } —
    the query is preserved in the journal; RESHAPE the query, never re-fire the same one.
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

    # D3 (H5b, opt-in): budget pre-flight — DR is the single most expensive action (~$5).
    # Only fires if the agent threads budget_usd (an opt-in guard, NOT an autonomous
    # guarantee — the harness never calls DR). Skip the spend when budget can't cover it.
    _rd, _bud = payload.get("results_dir"), payload.get("budget_usd")
    if _rd and _bud is not None:
        _rem = _common.budget_remaining(_rd, _bud)
        _est = float(payload.get("dr_estimated_cost_usd", payload.get("estimated_cost_usd", 5.0)))
        if _rem is not None and _rem < _est:
            return {"brief": [], "raw_text": "", "cost": 0.0, "model": model,
                    "skipped": "budget", "budget_remaining": _rem, "estimated_cost": _est}

    import asyncio
    from shinka.llm.agent.dr_client import get_dr_async_client, run_dr_call

    # web-search surcharge (the DR model runs web_search internally; not in token usage).
    search_surcharge = float(payload.get("search_surcharge_usd", 0.30))
    client, _base = get_dr_async_client()
    try:
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
    except Exception as exc:
        # P7-T6: a refused/failed DR call must NOT crash the orchestrator. Classify the
        # reason, fold the billed cost (the transport attached err.cost) into the ledger
        # so the spend isn't lost, and return a DEGRADED result with the query preserved
        # so the agent can RESHAPE the query — a content_filter refusal almost always
        # means a "reproduce paper X" framing; never re-fire the same query shape.
        _txt = str(exc).lower()
        reason = ("content_filter" if ("content_filter" in _txt or "content filter" in _txt)
                  else f"dr_failed:{exc}")
        _billed = float(getattr(exc, "cost", 0.0) or 0.0)
        _submitted = bool(getattr(exc, "submitted", False))
        if _submitted:
            # The job was SUBMITTED and ran (web searches + reasoning compute) before the
            # terminal failure/timeout. usage is typically None on failure, so token cost is
            # 0 — floor the spend at the search surcharge so the ledger reflects that Azure
            # billed us, then add any reported token cost on top. (A content_filter REFUSAL at
            # submit time is NOT `submitted` → it correctly stays free.) This is the fix for
            # "the framework doesn't know it's being billed" on a failed DR call.
            _billed = max(_billed, 0.0) + search_surcharge
        elif _billed > 0:
            _billed += search_surcharge
        _common.log_external_call(
            payload.get("results_dir"), "dr",
            {"query": query, "program_context": program_context, "model": model,
             "reasoning_effort": payload.get("reasoning_effort", "medium")},
            {"refused": True, "reason": reason, "error": str(exc),
             "error_code": getattr(exc, "error_code", None)},
            cost=_billed, summary=f"DR refused/failed: {reason}",
        )
        return {"brief": [], "raw_text": "", "cost": _billed, "model": model,
                "refused": True, "degraded": True, "reason": reason,
                "error_code": getattr(exc, "error_code", None)}
    cost = float(token_cost) + search_surcharge
    brief = _parse_brief(text)
    # WS7: persist the FULL DR call (query + program_context + raw output) to the
    # journal and fold cost into the ledger when results_dir is set. This is the fix
    # for round-1's lost prompt — the query no longer lives only in an ephemeral
    # runner script that the next call can overwrite.
    _common.log_external_call(
        payload.get("results_dir"), "dr",
        {"query": query, "program_context": program_context, "model": model,
         "reasoning_effort": payload.get("reasoning_effort", "medium")},
        {"brief": brief, "raw_text": text, "token_cost": float(token_cost),
         "search_surcharge": search_surcharge},
        cost=cost,
        summary=f"{len(brief)} brief items",
    )
    return {
        "brief": brief, "raw_text": text,
        "cost": cost, "token_cost": float(token_cost),
        "search_surcharge": search_surcharge, "model": model,
    }


if __name__ == "__main__":
    _common.run_main(main)

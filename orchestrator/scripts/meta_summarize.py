"""meta_summarize.py — the cheap "meta round": an external LLM proposes
directions, given context the orchestrator gathers.

MUTABILITY: MUTABLE STRATEGY (cell C — prompt editable, the call is fixed). The
orchestrator MAY rewrite the prompt when recommendations stop being useful.

This exists because the orchestrator must NOT invent new algorithmic directions
itself — that burns its (expensive) turns and is exactly the kind of open-ended
ideation an LLM call should do. So when the search needs *new ideas* (not just a
framework tweak), the orchestrator calls THIS (cheap) or `deep_research.py`
(expensive, web-grounded).

Meta is the AUTOMATIC per-window round run by the HARNESS (not an orchestrator
decision): after each window the harness calls this ONCE → global ``directions`` +
a ``failure_note`` caution + ONE differentiated direction per live island
(``island_directions``), auto-recorded as per-island briefs so islands evolve in
DIFFERENT directions by default. It summarizes what the search has ALREADY tried
(recent attempts, including failures). It is NOT the per-gen idea source — the
per-gen choice is a weighted SAMPLE over the global directions (run_window samples
one per mutation) plus the per-island brief for that island. Default model
``azure-gpt-5.5`` at ``medium`` effort, mutable to a stronger model (e.g. pro@high).

WS2/WS3 OUTPUT CONTRACT (changed from the old single-blob string):
  * ``directions``    — a WEIGHTED list ``[{text, weight}]``. The orchestrator
                        writes these into ``evo.meta_directions`` and run_window
                        SAMPLES ONE per gen (weight = relative promise / "best
                        shots"). This replaces the old global blob that was
                        appended verbatim to every gen.
  * ``failure_note``  — a concise PROSE paragraph: what tended to cause failures
                        (e.g. runtime/timeout vs correctness) and what future
                        attempts should watch for. The orchestrator writes it to
                        ``evo.meta_failure_note`` and run_window feeds it forward
                        into EVERY gen (a persistent caution).

WS3 GUARANTEE (meta must SEE failures): if ``recent_programs`` is not supplied but
``db_path`` is, meta SELF-GATHERS recent attempts from the archive — explicitly
including recent FAILURES (via archive_query ``recent_failures``) plus the current
top performers. So meta can't silently miss the failure signal regardless of what
the caller remembered to pass. Error text is truncated to the MAJOR reason only
(``_ERR_CHARS``); understanding a full non-timeout traceback isn't worth the tokens.

All LLM usage is Azure background-poll (`_azure.bg_query`), never the
orchestrator's own tokens.

Escalation tiers for "new directions": meta_summarize (cheap, frequent) →
deep_research (web-grounded, rare). Both are external-LLM ideation; the
orchestrator only decides WHEN to call and how to use the output.

INPUT (stdin JSON):
  {
    "model_name": "azure-gpt-5.5",          # default; mutable to pro@high when worth it
    "reasoning_effort": "medium" | null,     # default medium (pro rejects "low")
    "goal": "<task goal / system message>",
    # context — supply recent_programs explicitly, OR db_path to self-gather:
    "db_path": str | null, "db_config": {..} | null, "embedding_model": str | null,
    "recent_programs": [ {generation, combined_score, correct, error_traceback, metadata} ] | null,
    "islands": [ {"id": int, "best": float, "count": int} ] | null,  # live islands to differentiate
    "num_islands": int | null,
    "best_program": {"combined_score": float, "code": str} | null,
    "n_recent": 16,
    "prior_recommendations": str | null,
    "max_recommendations": 5,
    "results_dir": str | null,   # WS7: if set, self-log the full call + fold cost into the ledger
    "mock": false, "mock_text": str | null,
    "run_id": str | null
  }

OUTPUT (stdout JSON):
  {
    "directions": [ {"text": str, "weight": float} ],
    "failure_note": str,
    "island_directions": [ {"island_idx": int, "text": str} ],  # headline dir per island (derived; back-compat)
    "islands": [ {"island_idx": int, "directions": [ {"text": str, "weight": float,
                  "assigned_program_ids": [str]} ]} ],  # rich per-island output (H1/M13 mapping)
    "recommendations": str,   # legacy joined string (logging / back-compat)
    "cost": float, "model": str
  }
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

try:
    from . import _common
    from . import _azure
    from . import archive_query
except ImportError:
    import _common  # type: ignore
    import _azure  # type: ignore
    import archive_query  # type: ignore


# Show only the MAJOR failure reason — the first line of the traceback / the
# "EvaluationTerminated: ... time limit ..." banner is enough for meta to classify
# timeout vs correctness. Understanding a full traceback would cost far more tokens
# than the signal is worth (user direction, WS3).
_ERR_CHARS = 160


_SYS = (
    "You are a research strategist for an LLM-driven evolutionary code search with multiple "
    "parallel ISLANDS (sub-populations) that should explore DIFFERENT solution families. You "
    "are given the goal, the current best program, and — grouped PER ISLAND — that island's "
    "recent programs (id, score, ok/FAIL, a CODE preview, and the error of any failures). "
    "Produce STRICT JSON and nothing else:\n"
    '{{\n'
    '  "directions": [ {{"text": "<one concrete, actionable GLOBAL direction — name the '
    'technique/structure/parameters; do NOT write code>", "weight": <number 0..1, relative '
    'promise>}}, ... up to {n} items ],\n'
    '  "failure_note": "<2-4 sentences of PROSE: what tended to cause the recent failures '
    '(e.g. runtime/timeout vs broken correctness) and what future attempts should be careful '
    'about. Lead with the dominant failure class and roughly how common it was (e.g. \'Most '
    'recent failures (~X of N) were timeouts; ...\'). Empty string if there were no '
    'failures.>",\n'
    '  "islands": [ {{"island_idx": <int — one of the live island ids listed below>, '
    '"directions": [ {{"text": "<a direction for THIS island; name the technique, no code>", '
    '"weight": <0..1>, "assigned_program_id": "<the id of an EXISTING program shown for THIS '
    'island that ALREADY realizes this direction, or null if it is a NEW/untried idea>"}}, '
    '... 1 to 3 per island ]}}, ... EXACTLY ONE entry per live island id ]\n'
    '}}\n'
    "Rules: make the islands explore genuinely DIFFERENT families/approaches from each other "
    "and from prior recommendations. Within an island, LABEL a direction that is already "
    "working by setting assigned_program_id to that island's existing program id; use null "
    "for fresh ideas. Weight = your confidence it pays off; weights need not sum to 1. "
    "Output ONLY the JSON object."
)


def _gather_recent(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """WS3: self-gather recent attempts (failures FIRST, then top performers) from
    the archive so meta always sees the failure signal. Falls back to an explicit
    ``recent_programs`` if the caller supplied one, or [] if no db is reachable."""
    explicit = payload.get("recent_programs")
    if explicit:
        return list(explicit)
    db_path = payload.get("db_path")
    if not db_path:
        return []
    db_config = payload.get("db_config", {}) or {}
    embedding_model = payload.get("embedding_model", "azure-text-embedding-3-small")
    n_recent = int(payload.get("n_recent", 16) or 16)
    # F4 (mutable knob): how much of the context is recent FAILURES vs top performers.
    # Default 0.5 reproduces today's even split; raise toward 0.75 when failures
    # dominate and the distilled failure_note keeps coming back vague.
    frac = min(max(float(payload.get("meta_failures_first_frac", 0.5) or 0.5), 0.0), 1.0)
    n_fail = max(1, int(round(n_recent * frac)))
    n_top = max(1, n_recent - n_fail)
    base = {"db_path": db_path, "db_config": db_config, "embedding_model": embedding_model,
            "include_metadata": True,
            # H11: give meta the actual CODE (capped) so directions are code-grounded and
            # it can ASSIGN a direction to the program that realizes it. code_preview_chars
            # (mutable knob) bounds the token cost; 0 disables the code preview.
            "code_preview_chars": int(payload.get("meta_code_preview_chars", 1200) or 0)}
    out: List[Dict[str, Any]] = []
    seen = set()
    try:  # recent FAILURES first — the signal WS3 must not miss.
        fails = archive_query.main({**base, "query_type": "recent_failures", "n": n_fail})["result"]
        for p in fails:
            if p.get("id") not in seen:
                out.append(p); seen.add(p.get("id"))
    except Exception:
        pass
    try:  # then the current top performers (what's working).
        tops = archive_query.main({**base, "query_type": "top_n", "correct_only": True, "n": n_top})["result"]
        for p in tops:
            if p.get("id") not in seen:
                out.append(p); seen.add(p.get("id"))
    except Exception:
        pass
    return out


def _build_user_msg(payload: Dict[str, Any], recents: List[Dict[str, Any]]) -> str:
    best = payload.get("best_program") or {}
    prior = payload.get("prior_recommendations")
    parts = [f"# Goal\n{payload.get('goal', '(none)')}"]
    if best:
        parts.append(
            f"\n# Current best (score {best.get('combined_score')})\n"
            f"```\n{(best.get('code') or '')[:4000]}\n```"
        )
    def _err_reason(p):
        # M7: a format_exc() traceback's FIRST line is the generic banner; the real
        # exception is the LAST line. Synthesized reasons (timeouts, domain failures) put
        # the reason on the first line, so only swap to the last when it's the banner.
        e = p.get("error_traceback") or ""
        if not e:
            return ""
        ls = [ln for ln in e.strip().splitlines() if ln.strip()]
        if not ls:
            return ""
        pick = ls[-1] if ls[0].startswith("Traceback (most recent call last)") else ls[0]
        return f" err={pick[:_ERR_CHARS]}"

    def _prog_block(p):
        tag = "ok" if p.get("correct") else "FAIL"
        patch = ((p.get("metadata") or {}).get("patch_name")) or p.get("patch_name") or ""
        head = (f"  - [id={p.get('id')}] gen {p.get('generation')} [{tag}] "
                f"score={p.get('combined_score')} {patch}{_err_reason(p)}")
        code = (p.get("code") or p.get("code_preview") or "")
        if code:
            head += f"\n    ```\n{code}\n    ```"
        return head

    # H11/M13: render recent attempts GROUPED PER ISLAND (id + score + CODE preview), so meta
    # sees what each island actually did and can give it a DISTINCT direction AND assign a
    # direction to an existing program id it already realizes.
    islands = payload.get("islands") or []
    by_island = {}
    for p in recents[:24]:
        by_island.setdefault(p.get("island_idx"), []).append(p)
    if islands:
        for it in islands:
            iid = it.get("id")
            progs = by_island.get(iid, [])
            body = ("\n".join(_prog_block(p) for p in progs[:4]) if progs
                    else "  (no recent attempts)")
            parts.append(
                f"\n# ISLAND {iid} (best={it.get('best')} members={it.get('count')})\n{body}"
            )
        parts.append(
            "\n# Live island ids: " + ", ".join(str(it.get("id")) for it in islands)
            + "\nGive EXACTLY ONE entry per island id in `islands`, with 1-3 distinct "
            "directions each (genuinely different across islands)."
        )
    elif recents:
        # No island list (single-island / degraded) → one flat block.
        parts.append("\n# Recent attempts\n" + "\n".join(_prog_block(p) for p in recents[:12]))
    if prior:
        parts.append(f"\n# Prior recommendations (avoid repeating)\n{prior}")
    return "\n".join(parts)


def _parse_meta(text: str, max_n: int) -> Dict[str, Any]:
    """Lenient JSON extraction → {directions:[{text,weight}], failure_note:str}.
    Falls back to treating the whole response as one direction so a non-JSON reply
    never crashes the meta cycle (it just yields a single unweighted direction)."""
    directions: List[Dict[str, Any]] = []
    island_directions: List[Dict[str, Any]] = []
    islands_rich: List[Dict[str, Any]] = []
    failure_note = ""
    blob = text or ""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", blob, re.DOTALL)
    if fenced:
        blob = fenced.group(1)
    start = blob.find("{")
    end = blob.rfind("}")
    data = None
    if start != -1 and end > start:
        try:
            data = json.loads(blob[start : end + 1])
        except json.JSONDecodeError:
            data = None
    if isinstance(data, dict):
        raw_dirs = data.get("directions") or []
        for d in raw_dirs[:max_n]:
            if isinstance(d, dict) and d.get("text"):
                try:
                    w = float(d.get("weight", 1.0))
                except (TypeError, ValueError):
                    w = 1.0
                directions.append({"text": str(d["text"]).strip(), "weight": max(0.0, w)})
            elif isinstance(d, str) and d.strip():
                directions.append({"text": d.strip(), "weight": 1.0})
        failure_note = str(data.get("failure_note") or "").strip()
        for d in (data.get("island_directions") or []):
            if isinstance(d, dict) and d.get("text") is not None:
                try:
                    idx = int(d.get("island_idx"))
                except (TypeError, ValueError):
                    continue  # drop a malformed entry without crashing
                txt = str(d.get("text") or "").strip()
                if txt:
                    island_directions.append({"island_idx": idx, "text": txt})
        # M13/H11: the richer per-island output — each island gets 1-3 directions, each
        # optionally ASSIGNED to an existing program id it realizes (the mapping the sampler
        # consumes). assigned_program_id (singular) is normalized to a list; a hallucinated
        # id is harmless (the sampler intersects it with the live pool).
        for isl in (data.get("islands") or []):
            if not isinstance(isl, dict):
                continue
            try:
                iidx = int(isl.get("island_idx"))
            except (TypeError, ValueError):
                continue
            idirs: List[Dict[str, Any]] = []
            for d in (isl.get("directions") or []):
                if isinstance(d, dict) and d.get("text"):
                    try:
                        w = float(d.get("weight", 1.0))
                    except (TypeError, ValueError):
                        w = 1.0
                    _apid = d.get("assigned_program_id")
                    _apid = str(_apid) if _apid not in (None, "", "null") else None
                    idirs.append({"text": str(d["text"]).strip(), "weight": max(0.0, w),
                                  "assigned_program_ids": ([_apid] if _apid else [])})
                elif isinstance(d, str) and d.strip():
                    idirs.append({"text": d.strip(), "weight": 1.0, "assigned_program_ids": []})
            if idirs:
                islands_rich.append({"island_idx": iidx, "directions": idirs})
        # Back-compat: if the model used the richer `islands` schema, DERIVE the flat
        # island_directions (one headline per island) so existing consumers still work.
        if islands_rich and not island_directions:
            for isl in islands_rich:
                ds = sorted(isl["directions"], key=lambda x: x.get("weight", 0.0), reverse=True)
                if ds:
                    island_directions.append(
                        {"island_idx": isl["island_idx"], "text": ds[0]["text"]}
                    )
    if not directions:
        # Fallback: no parseable JSON — keep the raw text as a single direction so
        # the orchestrator still gets *something* usable, and log nothing lost.
        txt = (text or "").strip()
        if txt:
            directions = [{"text": txt[:2000], "weight": 1.0}]
    return {"directions": directions, "failure_note": failure_note,
            "island_directions": island_directions, "islands": islands_rich}


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    n = int(payload.get("max_recommendations", 5))
    model = payload.get("model_name", "azure-gpt-5.5")
    effort = payload.get("reasoning_effort") or "medium"  # default medium (pro rejects "low")
    # H7: meta does NOT parse a "model@effort" arm id (only the bandit's _parse_arm does), so a
    # value like "azure-gpt-5.4-pro@high" resolved to a NONEXISTENT deployment and silently
    # degraded EVERY meta round (no briefs/directions written, the only trace a calls.jsonl
    # line). Split it here — BEFORE the mock branch — so both the two-knob form and a habitual
    # @-suffix work. (The canonical config is evo.meta_model + evo.meta_reasoning_effort.)
    if isinstance(model, str) and "@" in model:
        model, _eff = model.split("@", 1)
        effort = _eff or effort

    if payload.get("mock"):
        text = payload.get("mock_text", "") or ""
        parsed = _parse_meta(text, n)
        return {
            "directions": parsed["directions"],
            "failure_note": parsed["failure_note"],
            "island_directions": parsed["island_directions"],
            "islands": parsed["islands"],
            "recommendations": text,
            "cost": 0.0,
            "model": model,
        }

    # D3 (H5b, opt-in): budget pre-flight — only fires if the agent threads budget_usd
    # (the harness never calls meta; this is agent-invoked, so it is an opt-in guard, not
    # an autonomous guarantee). Skip the spend when the remaining budget can't cover it.
    _rd, _bud = payload.get("results_dir"), payload.get("budget_usd")
    if _rd and _bud is not None:
        _rem = _common.budget_remaining(_rd, _bud)
        _est = float(payload.get("meta_estimated_cost_usd", payload.get("estimated_cost_usd", 1.0)))
        if _rem is not None and _rem < _est:
            return {"directions": [], "failure_note": None, "island_directions": [],
                    "islands": [], "recommendations": "", "cost": 0.0, "model": model,
                    "skipped": "budget", "budget_remaining": _rem, "estimated_cost": _est}
    # F3: auto-populate prior_recommendations from recent meta calls so meta doesn't
    # re-propose directions it already gave (an explicit caller value always wins).
    if not payload.get("prior_recommendations") and _rd:
        _prior = _common.recent_meta_directions(_rd, k=3)
        if _prior:
            payload = {**payload, "prior_recommendations": "; ".join(_prior[:8])}

    recents = _gather_recent(payload)
    system_msg = _SYS.format(n=n)
    user_msg = _build_user_msg(payload, recents)
    call_metadata = {"purpose": "meta", "model_name": model}
    if payload.get("run_id"):
        call_metadata["run_id"] = payload["run_id"]

    try:
        text, cost = _azure.bg_query(
            model_name=model,
            system_msg=system_msg,
            user_msg=user_msg,
            reasoning_effort=effort,
            call_metadata=call_metadata,
        )
    except Exception as exc:
        # H2: meta transport failure must not CRASH the orchestrator. Log the (billed)
        # cost the transport attached and return a GRACEFUL degraded result with a
        # discriminator so the agent can tell "meta crashed" from "meta found nothing".
        _cost = float(getattr(exc, "cost", 0.0) or 0.0)
        _common.log_external_call(
            payload.get("results_dir"), "meta",
            {"system": system_msg, "user": user_msg, "model": model},
            {"error": str(exc)}, cost=_cost, summary="meta transport FAILED",
        )
        return {
            "directions": [], "failure_note": None, "island_directions": [], "islands": [],
            "recommendations": "", "cost": _cost, "model": model,
            "degraded": True, "error": str(exc),
        }
    parsed = _parse_meta(text, n)
    # Legacy joined string — handy for logging and for any caller that still wants
    # a single blob (back-compat); the structured fields are the real output.
    joined = "\n".join(f"{i+1}. {d['text']}" for i, d in enumerate(parsed["directions"]))
    # WS7: persist the full call (prompt + raw output) + fold cost into the ledger
    # when results_dir is provided — automatic, never-overwritten, no manual step.
    _common.log_external_call(
        payload.get("results_dir"), "meta",
        {"system": system_msg, "user": user_msg, "model": model,
         "reasoning_effort": effort},
        {"directions": parsed["directions"], "failure_note": parsed["failure_note"],
         "island_directions": parsed["island_directions"], "raw_text": text},
        cost=float(cost),
        summary=f"{len(parsed['directions'])} directions, "
        f"{len(parsed['island_directions'])} island"
        + ("; +failure_note" if parsed["failure_note"] else ""),
    )
    return {
        "directions": parsed["directions"],
        "failure_note": parsed["failure_note"],
        "island_directions": parsed["island_directions"],
        "islands": parsed["islands"],
        "recommendations": joined,
        "cost": float(cost),
        "model": model,
    }


if __name__ == "__main__":
    _common.run_main(main)

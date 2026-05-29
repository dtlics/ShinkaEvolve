"""meta_summarize.py — the cheap "meta round": an external LLM proposes
directions, given context the orchestrator gathers.

MUTABILITY: MUTABLE STRATEGY (cell C — prompt editable, the call is fixed). The
orchestrator MAY rewrite the prompt when recommendations stop being useful.

This exists because the orchestrator must NOT invent new algorithmic directions
itself — that burns its (expensive) turns and is exactly the kind of open-ended
ideation an LLM call should do. So when the search needs *new ideas* (not just a
framework tweak), the orchestrator calls THIS (cheap) or `deep_research.py`
(expensive, web-grounded).

Meta is REACTIVE / aftermath: it summarizes what the search has ALREADY tried
(recent attempts, including failures) into directions for the next batch. It is
NOT the per-gen idea source — the per-gen choice is a weighted SAMPLE over the
directions it returns (run_window samples one direction per mutation).

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
    "model_name": "azure-gpt-5.4-mini",
    "reasoning_effort": "low" | null,
    "goal": "<task goal / system message>",
    # context — supply recent_programs explicitly, OR db_path to self-gather:
    "db_path": str | null, "db_config": {..} | null, "embedding_model": str | null,
    "recent_programs": [ {generation, combined_score, correct, error_traceback, metadata} ] | null,
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
    "You are a research strategist for an LLM-driven evolutionary code search. "
    "Given the optimization goal, the current best program, and recent attempts "
    "(including FAILURES with their error), produce STRICT JSON and nothing else:\n"
    '{{\n'
    '  "directions": [ {{"text": "<one concrete, actionable direction — name the '
    'technique/structure/parameters; do NOT write code>", "weight": <number 0..1, '
    'relative promise>}}, ... up to {n} items ],\n'
    '  "failure_note": "<2-4 sentences of PROSE: what tended to cause the recent '
    'failures (e.g. runtime/timeout vs broken correctness) and what future attempts '
    'should be careful about. Empty string if there were no failures.>"\n'
    '}}\n'
    "Rules: prioritize directions NOT already tried (see prior recommendations). "
    "Weight = your confidence it pays off ('best shots' get higher weight); weights "
    "need not sum to 1. Output ONLY the JSON object."
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
    embedding_model = payload.get("embedding_model", "text-embedding-3-small")
    n_recent = int(payload.get("n_recent", 16) or 16)
    base = {"db_path": db_path, "db_config": db_config, "embedding_model": embedding_model,
            "include_metadata": True}
    out: List[Dict[str, Any]] = []
    seen = set()
    try:  # recent FAILURES first — the signal WS3 must not miss.
        fails = archive_query.main({**base, "query_type": "recent_failures", "n": max(1, n_recent // 2)})["result"]
        for p in fails:
            if p.get("id") not in seen:
                out.append(p); seen.add(p.get("id"))
    except Exception:
        pass
    try:  # then the current top performers (what's working).
        tops = archive_query.main({**base, "query_type": "top_n", "correct_only": True, "n": max(1, n_recent // 2)})["result"]
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
    if recents:
        lines = []
        for p in recents[:20]:
            tag = "ok" if p.get("correct") else "FAIL"
            err = (p.get("error_traceback") or "")
            # keep only the major reason (first line, capped) — see _ERR_CHARS.
            if err:
                err = err.strip().splitlines()[0][:_ERR_CHARS]
                err = f" err={err}"
            patch = ((p.get("metadata") or {}).get("patch_name")) or p.get("patch_name") or ""
            lines.append(
                f"- gen {p.get('generation')} [{tag}] score={p.get('combined_score')} "
                f"{patch}{err}"
            )
        parts.append("\n# Recent attempts (failures + top performers)\n" + "\n".join(lines))
    if prior:
        parts.append(f"\n# Prior recommendations (avoid repeating)\n{prior}")
    return "\n".join(parts)


def _parse_meta(text: str, max_n: int) -> Dict[str, Any]:
    """Lenient JSON extraction → {directions:[{text,weight}], failure_note:str}.
    Falls back to treating the whole response as one direction so a non-JSON reply
    never crashes the meta cycle (it just yields a single unweighted direction)."""
    directions: List[Dict[str, Any]] = []
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
    if not directions:
        # Fallback: no parseable JSON — keep the raw text as a single direction so
        # the orchestrator still gets *something* usable, and log nothing lost.
        txt = (text or "").strip()
        if txt:
            directions = [{"text": txt[:2000], "weight": 1.0}]
    return {"directions": directions, "failure_note": failure_note}


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    n = int(payload.get("max_recommendations", 5))
    model = payload.get("model_name", "azure-gpt-5.4-mini")

    if payload.get("mock"):
        text = payload.get("mock_text", "") or ""
        parsed = _parse_meta(text, n)
        return {
            "directions": parsed["directions"],
            "failure_note": parsed["failure_note"],
            "recommendations": text,
            "cost": 0.0,
            "model": model,
        }

    recents = _gather_recent(payload)
    system_msg = _SYS.format(n=n)
    user_msg = _build_user_msg(payload, recents)
    call_metadata = {"purpose": "meta", "model_name": model}
    if payload.get("run_id"):
        call_metadata["run_id"] = payload["run_id"]

    text, cost = _azure.bg_query(
        model_name=model,
        system_msg=system_msg,
        user_msg=user_msg,
        reasoning_effort=payload.get("reasoning_effort"),
        call_metadata=call_metadata,
    )
    parsed = _parse_meta(text, n)
    # Legacy joined string — handy for logging and for any caller that still wants
    # a single blob (back-compat); the structured fields are the real output.
    joined = "\n".join(f"{i+1}. {d['text']}" for i, d in enumerate(parsed["directions"]))
    # WS7: persist the full call (prompt + raw output) + fold cost into the ledger
    # when results_dir is provided — automatic, never-overwritten, no manual step.
    _common.log_external_call(
        payload.get("results_dir"), "meta",
        {"system": system_msg, "user": user_msg, "model": model,
         "reasoning_effort": payload.get("reasoning_effort")},
        {"directions": parsed["directions"], "failure_note": parsed["failure_note"],
         "raw_text": text},
        cost=float(cost),
        summary=f"{len(parsed['directions'])} directions"
        + ("; +failure_note" if parsed["failure_note"] else ""),
    )
    return {
        "directions": parsed["directions"],
        "failure_note": parsed["failure_note"],
        "recommendations": joined,
        "cost": float(cost),
        "model": model,
    }


if __name__ == "__main__":
    _common.run_main(main)

"""meta_summarize.py — the cheap "meta round": an external LLM proposes
directions, given context the orchestrator gathers.

MUTABILITY: MUTABLE STRATEGY (cell C — prompt editable, the call is fixed). The
orchestrator MAY rewrite the prompt when recommendations stop being useful.

This exists because the orchestrator must NOT invent new algorithmic directions
itself — that burns its (expensive) turns and is exactly the kind of open-ended
ideation an LLM call should do. So when the search needs *new ideas* (not just a
framework tweak), the orchestrator gathers context (best program, recent
attempts, prior recs) and calls THIS (cheap) or `deep_research.py` (expensive,
web-grounded). The returned recommendations are fed into the next window via the
run config's `evo.meta_recommendations`, which `construct_mutation_prompt.py`
injects into the mutation prompt. All LLM usage is Azure background-poll
(`_azure.bg_query`), never the orchestrator's own tokens.

Escalation tiers for "new directions": meta_summarize (cheap, frequent) →
deep_research (≈$5, web-grounded, rare). Both are external-LLM ideation; the
orchestrator only decides WHEN to call and how to use the output.

INPUT (stdin JSON):
  {
    "model_name": "azure-gpt-5.4-mini",
    "reasoning_effort": "low" | null,
    "goal": "<task goal / system message>",
    "best_program": {"combined_score": float, "code": str} | null,
    "recent_programs": [ {"generation","combined_score","correct","patch_name","error_traceback"} ],
    "prior_recommendations": str | null,
    "max_recommendations": 5,
    "mock": false, "mock_text": str | null,
    "run_id": str | null
  }

OUTPUT (stdout JSON):
  { "ok": true, "recommendations": str, "cost": float, "model": str }
"""

from __future__ import annotations

import json
from typing import Any, Dict

try:
    from . import _common
    from . import _azure
except ImportError:
    import _common  # type: ignore
    import _azure  # type: ignore


_SYS = (
    "You are a research strategist for an LLM-driven evolutionary code search. "
    "Given the optimization goal, the current best program, and recent attempts "
    "(including failures), propose concrete, actionable directions for the next "
    "batch of mutations. Do NOT rewrite code. Output a short numbered list of at "
    "most {n} specific recommendations, each one line, prioritizing ideas not "
    "already tried. Be concrete (name techniques, parameters, structures)."
)


def _build_user_msg(payload: Dict[str, Any]) -> str:
    best = payload.get("best_program") or {}
    recents = payload.get("recent_programs") or []
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
            err = f" err={err[:120]}" if err else ""
            lines.append(
                f"- gen {p.get('generation')} [{tag}] score={p.get('combined_score')} "
                f"{p.get('patch_name') or ''}{err}"
            )
        parts.append("\n# Recent attempts\n" + "\n".join(lines))
    if prior:
        parts.append(f"\n# Prior recommendations (avoid repeating)\n{prior}")
    return "\n".join(parts)


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    n = int(payload.get("max_recommendations", 5))
    model = payload.get("model_name", "azure-gpt-5.4-mini")

    if payload.get("mock"):
        text = payload.get("mock_text", "") or ""
        return {"recommendations": text, "cost": 0.0, "model": model}

    system_msg = _SYS.format(n=n)
    user_msg = _build_user_msg(payload)
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
    return {"recommendations": text.strip(), "cost": float(cost), "model": model}


if __name__ == "__main__":
    _common.run_main(main)

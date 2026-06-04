"""construct_mutation_prompt.py — build the prompt sent to the mutation LLM.

MUTABILITY: MUTABLE STRATEGY (cell C — prompt construction). The orchestrator
MAY rewrite this file when a window shows mutations consistently missing the
point or repeating a failure mode. It builds a string and embeds NO LLM call
itself (the call lives in mutate.py).

Per the brief's resolution: the default is to *just stack* parent code +
inspirations + a precise goal — which is exactly what shinka's ``PromptSampler``
does. So this file delegates the mechanical template-filling to PromptSampler
(parity for free) and exposes the evolvable POLICY: the patch-type weights, the
inspiration ordering, and any extra guidance the orchestrator wants to inject.
A rewrite typically tweaks ``patch_type_probs`` or appends guidance to the goal.

INPUT (stdin JSON):
  {
    "parent": {"id","code","combined_score","public_metrics","text_feedback",...},
    "archive_inspirations": [ {same shape} ],
    "top_k_inspirations": [ {same shape} ],
    "meta_recommendations": str | null,
    "failure_note": str | null,      # persistent caution; ALWAYS rendered (never dropped)
    "island_brief": str | null,      # per-island direction (H1)
    "brief_compose_mode": "replace" | "augment",  # how a brief combines with the global dir
    "task_sys_msg": str | null,
    "patch_types": ["diff","full","cross"],
    "patch_type_probs": [0.6,0.3,0.1],
    "language": "python",
    "use_text_feedback": true,   # default true (repair feedback ON); false on a spoil-risk task
    "inspiration_sort_order": "ascending",
    "extra_guidance": str | null,   # appended to the system prompt (rewrite lever)
    "seed": int | null
  }

OUTPUT (stdout JSON):
  { "ok": true, "patch_sys": str, "patch_msg": str, "patch_type": str }
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def _to_program(d: Optional[Dict[str, Any]]):
    from shinka.database import Program

    if not d:
        return None
    valid = {f.name for f in dataclasses.fields(Program)}
    kwargs = {k: v for k, v in d.items() if k in valid}
    kwargs.setdefault("id", d.get("id") or "unknown")
    kwargs.setdefault("code", d.get("code", "") or "")
    return Program(**kwargs)


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    import numpy as np
    from shinka.core import PromptSampler

    seed = payload.get("seed")
    if seed is not None:
        np.random.seed(int(seed))

    # No-spoil (H9): when use_text_feedback is False, STRIP every evaluator-derived text
    # channel from the parent + ALL inspiration/ancestor programs BEFORE building Program
    # objects — so this prompt builder is a hard gate even if a caller forgets to thread
    # the flag (the exact omission that caused H9). The PromptSampler flag below is then
    # belt-and-suspenders, and meta is gated separately at its own assembly site.
    if not bool(payload.get("use_text_feedback", True)):
        _EVAL_TEXT_KEYS = ("text_feedback", "error", "error_traceback", "stdout_log", "stderr_log")

        def _strip_eval_text(d: Any) -> None:
            if isinstance(d, dict):
                for _k in _EVAL_TEXT_KEYS:
                    d.pop(_k, None)

        _strip_eval_text(payload.get("parent"))
        for _lst in ("archive_inspirations", "top_k_inspirations", "ancestor_inspirations"):
            for _d in (payload.get(_lst) or []):
                _strip_eval_text(_d)

    parent = _to_program(payload["parent"])
    archive_insp = [_to_program(d) for d in payload.get("archive_inspirations", [])]
    top_k_insp = [_to_program(d) for d in payload.get("top_k_inspirations", [])]

    sampler = PromptSampler(
        task_sys_msg=payload.get("task_sys_msg"),
        language=payload.get("language", "python"),
        patch_types=payload.get("patch_types"),
        patch_type_probs=payload.get("patch_type_probs"),
        use_text_feedback=bool(payload.get("use_text_feedback", True)),
        inspiration_sort_order=payload.get("inspiration_sort_order", "ascending"),
    )

    if payload.get("needs_fix"):
        # FIX MODE (part of the fix/repair concern): the parent is an incorrect
        # program; build a repair prompt from its error + its ancestors. The
        # "when to fix" decision lives in sample_parent.needs_fix; this is the
        # "how to fix" prompt half.
        ancestors = [_to_program(d) for d in payload.get("ancestor_inspirations", [])]
        patch_sys, patch_msg, patch_type = sampler.sample_fix(
            incorrect_program=parent, ancestor_inspirations=ancestors,
            failure_note=payload.get("failure_note"),
        )
    else:
        # brief_compose_mode (MUTABLE lever): "replace" (default) lets a per-island
        # brief stand in for the global direction (the FOUNDATION sampler's `prefer`
        # semantic); "augment" layers the brief ON TOP of the global direction. The
        # composition is done HERE (mutable) rather than hardcoded in sampler.py (K7).
        _meta_recs = payload.get("meta_recommendations")
        _island_brief = payload.get("island_brief")
        if (
            _island_brief
            and _meta_recs
            and payload.get("brief_compose_mode", "replace") == "augment"
        ):
            _meta_recs = f"{_island_brief}\n\n{_meta_recs}"
            _island_brief = None
        patch_sys, patch_msg, patch_type = sampler.sample(
            parent=parent,
            archive_inspirations=archive_insp,
            top_k_inspirations=top_k_insp,
            meta_recommendations=_meta_recs,
            island_brief=_island_brief,
            failure_note=payload.get("failure_note"),
        )

    # Rewrite lever: orchestrator-supplied guidance is appended to the system
    # prompt so it rides along with every mutation in the next window.
    extra = payload.get("extra_guidance")
    if extra:
        patch_sys = f"{patch_sys}\n\n# Additional guidance\n{extra}"

    return {
        "patch_sys": patch_sys,
        "patch_msg": patch_msg,
        "patch_type": str(patch_type),
    }


if __name__ == "__main__":
    _common.run_main(main)

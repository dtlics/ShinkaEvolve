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
    "needs_fix": false,              # FIX branch: parent is incorrect; build a repair prompt
    "ancestor_inspirations": [ {same shape} ],  # FIX branch only: correct ancestors to learn from
    "meta_recommendations": str | null,
    "failure_note": str | null,      # persistent caution; ALWAYS rendered (never dropped)
    "island_brief": str | null,      # per-island direction (H1)
    "brief_compose_mode": "replace" | "augment",  # how a brief combines with the global dir
    "task_sys_msg": str | null,
    "patch_types": ["diff","full","cross"],
    "patch_type_probs": [0.6,0.3,0.1],
    "language": "python",
    "forced_patch_type": str | null, # D4: the patch MODE run_window sampled (diff/full/cross); null = sampler samples internally
    "objective_brief": str | null,   # Point 4.3: orchestrator-authored objective/score-shape gloss (what we optimize + constraints)
    "inspiration_sort_order": "ascending",
    "extra_guidance": str | null,   # appended to the system prompt (rewrite lever)
    "eval_budget_sec": float | null,    # C2: per-eval time budget (task.eval_time secs) for the runtime caution
    "slow_caution_frac": 0.8,           # C2: runtime >= this*budget counts as "slow" (default 0.8)
    "parent_runtime_sec": float | null, # C2 (immediate-fix only): the just-failed candidate's runtime
    "parent_timed_out": bool | null,    # C2 (immediate-fix only): did the just-failed candidate time out
    "seed": int | null
  }

OUTPUT (stdout JSON):
  { "ok": true, "patch_sys": str, "patch_msg": str, "patch_type": str }
  # patch_type is "fix" on the FIX branch (needs_fix=True; full-code repair reply),
  # else the sampled "diff"/"full"/"cross".
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

    parent = _to_program(payload["parent"])
    archive_insp = [_to_program(d) for d in payload.get("archive_inspirations", [])]
    top_k_insp = [_to_program(d) for d in payload.get("top_k_inspirations", [])]

    sampler = PromptSampler(
        task_sys_msg=payload.get("task_sys_msg"),
        language=payload.get("language", "python"),
        patch_types=payload.get("patch_types"),
        patch_type_probs=payload.get("patch_type_probs"),
        use_text_feedback=True,  # Point 5: spoil apparatus removed — evaluator feedback always fed
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
            objective_brief=payload.get("objective_brief"),
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
            objective_brief=payload.get("objective_brief"),
            forced_patch_type=payload.get("forced_patch_type"),
        )

    # Rewrite lever: orchestrator-supplied guidance is appended to the system
    # prompt so it rides along with every mutation in the next window.
    extra = payload.get("extra_guidance")
    if extra:
        patch_sys = f"{patch_sys}\n\n# Additional guidance\n{extra}"

    # C2 runtime-budget caution: if the parent, an inspiration, or (immediate-fix) the
    # just-failed candidate ran close to the per-eval time budget — or was timed out —
    # surface a BOUNDED caution so the LLM keeps its synthesis within budget. This does NOT
    # penalize a slow-but-correct candidate (still archived/scored/rewarded normally); it only
    # makes the budget visible so a genuinely-better-but-too-slow synthesis isn't lost to a
    # timeout. runtime_sec/timed_out are numeric/boolean (not evaluator text), so this never
    # echoes a raw traceback. Applies to BOTH branches.
    _budget = payload.get("eval_budget_sec")
    _slow_frac = float(payload.get("slow_caution_frac", 0.8) or 0.8)

    def _runtime_signals(d: Any):
        md = (d or {}).get("metadata") or {}
        _rt = md.get("runtime_sec")
        if _rt is None:
            _rt = (d or {}).get("runtime_sec")
        _to = bool(md.get("timed_out") or (d or {}).get("timed_out"))
        return (float(_rt) if _rt is not None else None), _to

    _any_timeout = bool(payload.get("parent_timed_out"))
    _slow_rt: Optional[float] = None
    _pr = payload.get("parent_runtime_sec")  # immediate-fix: the just-failed candidate isn't archived
    if _pr is not None and _budget and float(_pr) >= _slow_frac * float(_budget):
        _slow_rt = float(_pr)
    for _d in [payload.get("parent"), *(payload.get("archive_inspirations") or []),
               *(payload.get("top_k_inspirations") or [])]:
        _rt, _to = _runtime_signals(_d)
        if _to:
            _any_timeout = True
        if _rt is not None and _budget and _rt >= _slow_frac * float(_budget):
            _slow_rt = _rt if _slow_rt is None else max(_slow_rt, _rt)

    if _any_timeout or _slow_rt is not None:
        _budget_txt = (f"~{float(_budget):.0f}s per evaluation" if _budget
                       else "a fixed per-evaluation time budget")
        _obs = ("A recent candidate was TIMED OUT by the evaluator (it exceeded the budget and "
                "scored 0)." if _any_timeout
                else f"A recent candidate took ~{_slow_rt:.0f}s, close to the budget.")
        patch_sys = (
            f"{patch_sys}\n\n# Runtime budget\n{_obs} Each evaluation has {_budget_txt}; a "
            "candidate that exceeds it is timed out and scores 0 regardless of solution quality. "
            "Keep the algorithmic improvement, but make the synthesis efficient enough to finish "
            "well within the budget — do NOT trade away correctness or depth for raw speed; just "
            "avoid gratuitously slow constructions."
        )

    return {
        "patch_sys": patch_sys,
        "patch_msg": patch_msg,
        "patch_type": str(patch_type),
    }


if __name__ == "__main__":
    _common.run_main(main)

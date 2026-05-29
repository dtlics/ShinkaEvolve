"""mutate.py — call the mutation LLM, parse its response, apply the patch.

MUTABILITY: the BODY is IMMUTABLE (the call transport, the <NAME>/<DESCRIPTION>
parse, the patch application, the retry mechanics). Only the PROMPT is mutable,
and the prompt is produced by ``construct_mutation_prompt.py`` — not here. Do not
change the parsing/apply/transport logic in a strategy rewrite.

This is the stateless, per-candidate mutation operator. **All LLM usage here goes
to Azure, never the orchestrator's own tokens** — the inner loop must run at
API-call speed (the cost asymmetry that makes the whole design work). The real
path uses Azure **background mode + polling** (the same resilient transport
``deep_research`` uses) so long reasoning calls don't hit the documented
long-idle-TCP hang. Non-Azure/OpenAI providers fall back to the legacy
synchronous client (they don't expose background mode).

Retry: on APPLY failure (patch doesn't apply), it re-prompts the same model with
the apply error fed back, up to ``max_attempts`` (default 3). EVAL failures are
not retried here — an applied-but-incorrect candidate is recorded and handled by
the FIX-mode policy on a later generation (see sample_parent.needs_fix +
construct_mutation_prompt fix branch).

A ``mock`` mode makes the harness + offline tests run with no API.

INPUT (stdin JSON):
  {
    "parent_code": str,
    "patch_sys": str, "patch_msg": str, "patch_type": "diff"|"full"|"cross",
    "patch_dir": str, "language": "python",
    "model_name": str,                       # azure-* etc.
    "reasoning_effort": "medium" | null,     # for reasoning models
    "enable_web_search": false,              # WS4: attach web_search_preview (DR-ref grounding / fix)
    "max_attempts": 3,
    "mock": false, "mock_code": str|null, "mock_patch": str|null, "mock_cost": 0.0,
    "run_id": str|null, "generation": int|null, "verbose": false
  }

OUTPUT (stdout JSON):
  {
    "ok": true, "applied": bool, "num_applied": int,
    "candidate_code": str, "candidate_path": str,
    "name": str|null, "description": str|null,
    "cost": float, "attempts": int, "transport": "background"|"legacy"|"mock",
    "error": str|null, "raw_response": str|null
  }
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

try:
    from . import _common
    from . import _azure
except ImportError:
    import _common  # type: ignore
    import _azure  # type: ignore


def _ext(language: str) -> str:
    try:
        from shinka.utils.languages import get_language_extension

        return get_language_extension(language)
    except Exception:
        return {"python": "py", "cpp": "cpp", "c": "c", "rust": "rs"}.get(language, "py")


def _apply(patch_type, patch_str, original, patch_dir, language, verbose) -> Tuple:
    from shinka.edit.apply_diff import apply_diff_patch
    from shinka.edit.apply_full import apply_full_patch

    func = apply_full_patch if patch_type in ("full", "cross") else apply_diff_patch
    return func(
        patch_str=patch_str, original_str=original,
        patch_dir=patch_dir, language=language, verbose=verbose,
    )


def _write_candidate(patch_dir: str, language: str, code: str) -> str:
    os.makedirs(patch_dir, exist_ok=True)
    path = os.path.join(patch_dir, f"main.{_ext(language)}")
    with open(path, "w") as f:
        f.write(code)
    return path


def _mock(payload, parent_code, patch_type, patch_dir, language, verbose) -> Dict[str, Any]:
    mock_code = payload.get("mock_code")
    mock_patch = payload.get("mock_patch")
    name = payload.get("name", "mock_mutation")
    description = payload.get("description", "mock mutation")
    cost = float(payload.get("mock_cost", 0.0))
    if mock_code is not None:
        path = _write_candidate(patch_dir, language, mock_code)
        return {"applied": True, "num_applied": 1, "candidate_code": mock_code,
                "candidate_path": path, "name": name, "description": description,
                "cost": cost, "attempts": 0, "transport": "mock", "error": None, "raw_response": None}
    if mock_patch is not None:
        updated, n, out_path, err, _t, _d = _apply(patch_type, mock_patch, parent_code, patch_dir, language, verbose)
        applied = bool(n) and updated is not None
        candidate = updated if applied else parent_code
        path = out_path or _write_candidate(patch_dir, language, candidate)
        return {"applied": applied, "num_applied": int(n or 0), "candidate_code": candidate,
                "candidate_path": path, "name": name, "description": description,
                "cost": cost, "attempts": 0, "transport": "mock", "error": err, "raw_response": mock_patch}
    path = _write_candidate(patch_dir, language, parent_code)  # identity
    return {"applied": True, "num_applied": 0, "candidate_code": parent_code,
            "candidate_path": path, "name": name, "description": description,
            "cost": cost, "attempts": 0, "transport": "mock", "error": None, "raw_response": None}


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    parent_code = payload["parent_code"]
    patch_type = payload.get("patch_type", "diff")
    patch_dir = payload["patch_dir"]
    language = payload.get("language", "python")
    verbose = bool(payload.get("verbose", False))

    if payload.get("mock"):
        return _mock(payload, parent_code, patch_type, patch_dir, language, verbose)

    from shinka.llm import extract_between
    from shinka.llm.providers.model_resolver import resolve_model_backend

    model_name = payload["model_name"]
    reasoning_effort = payload.get("reasoning_effort")
    max_attempts = int(payload.get("max_attempts", 3))
    # WS4: web search is OFF unless the caller opts in (DR-reference grounding /
    # fix-retry when evo.fix_web_search is set). Only the bg (Azure/OpenAI) path
    # supports it; the legacy sync client ignores it.
    enable_web_search = bool(payload.get("enable_web_search", False))
    call_metadata = {"purpose": "proposer", "model_name": model_name}
    if payload.get("run_id"):
        call_metadata["run_id"] = payload["run_id"]
    if payload.get("generation") is not None:
        call_metadata["generation"] = payload["generation"]

    provider = resolve_model_backend(model_name).provider
    use_bg = provider in ("azure_openai", "openai")

    patch_sys = payload["patch_sys"]
    patch_msg = payload["patch_msg"]
    total_cost = 0.0
    last_error: Optional[str] = None
    raw = None
    name = description = None

    for attempt in range(max_attempts):
        # --- get response text (Azure background mode, or legacy fallback) ---
        if use_bg:
            try:
                raw, cost = _azure.bg_query(
                    model_name, patch_sys, patch_msg, reasoning_effort, call_metadata,
                    enable_web_search=enable_web_search,
                )
                transport = "background"
            except Exception as exc:
                last_error = f"transport error: {exc}"
                # H2: a failed/capped Azure call may still be BILLED — fold the cost the
                # transport attached to the exception so the ledger doesn't drop it.
                total_cost += float(getattr(exc, "cost", 0.0) or 0.0)
                break
        else:
            from shinka.llm import LLMClient

            client = LLMClient(model_names=[model_name], reasoning_efforts=reasoning_effort or "disabled", verbose=verbose)
            resp = client.query(msg=patch_msg, system_msg=patch_sys, msg_history=[])
            if resp is None or not getattr(resp, "content", None):
                last_error = "LLM returned no content"
                break
            raw, cost = resp.content, float(getattr(resp, "cost", 0.0) or 0.0)
            transport = "legacy"
        total_cost += cost

        name = extract_between(raw, "<NAME>", "</NAME>", False)
        description = extract_between(raw, "<DESCRIPTION>", "</DESCRIPTION>", False)

        # --- apply ---
        updated, n, out_path, err, _t, _d = _apply(patch_type, raw, parent_code, patch_dir, language, verbose)
        if n and updated is not None:
            path = out_path or _write_candidate(patch_dir, language, updated)
            return {"applied": True, "num_applied": int(n), "candidate_code": updated,
                    "candidate_path": path, "name": name, "description": description,
                    "cost": total_cost, "attempts": attempt + 1, "transport": transport,
                    "error": None, "raw_response": raw}
        # APPLY failed — feed the error back and retry (bounded).
        last_error = err or "patch did not apply (0 changes)"
        patch_msg = (
            payload["patch_msg"]
            + f"\n\nYour previous attempt failed to apply: {last_error}\n"
            "Re-read the code and emit a patch that applies cleanly."
        )

    # exhausted attempts — return the parent unchanged as a failed slot.
    path = _write_candidate(patch_dir, language, parent_code)
    return {"applied": False, "num_applied": 0, "candidate_code": parent_code,
            "candidate_path": path, "name": name, "description": description,
            "cost": total_cost, "attempts": max_attempts, "transport": "background" if use_bg else "legacy",
            "error": last_error, "raw_response": raw}


if __name__ == "__main__":
    _common.run_main(main)

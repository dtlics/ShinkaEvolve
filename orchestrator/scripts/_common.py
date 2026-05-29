"""Shared JSON stdin/stdout contract for orchestrator subroutines.

Every script in this directory follows the same contract:

  * It exposes ``def main(payload: dict) -> dict``.
  * Running ``python scripts/<name>.py`` reads one JSON object from stdin,
    calls ``main`` with it, and writes one JSON object to stdout.
  * The harness may instead ``import`` the script and call ``main`` directly
    (no subprocess) for speed; the contract is identical either way.

Output envelope: ``main`` returns a plain dict; ``run_main`` wraps it so that
success is ``{"ok": true, ...fields}`` and an uncaught exception becomes
``{"ok": false, "error": "...", "error_type": "...", "traceback": "..."}``.
This lets the orchestrator distinguish a clean result from a crash without
parsing stderr.

This module is IMMUTABLE plumbing. Do not edit it as part of a strategy
rewrite — every other script depends on its contract.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Make ``import shinka`` work whether or not the editable install is active.
# This file lives at orchestrator/scripts/_common.py, so the repo root (which
# contains the ``shinka`` package) is two parents up.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
# FORCE the worktree to the front of sys.path (move it if already present later)
# so `import shinka` resolves HERE, never an editable install pointing at another
# (original) shinka checkout. This is the install-isolation guarantee.
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))


def repo_root() -> Path:
    return _REPO_ROOT


def assert_worktree_shinka(expected_root: Any = None) -> str:
    """Loud-fail if `shinka` resolves outside the repo (e.g. a stray editable
    install of an original checkout). Call this at orchestrator startup so the
    next agent can NEVER silently run a different shinka. ``expected_root`` lets
    a caller (the harness) pass the authoritative repo root — important when this
    module is loaded from a copied scripts/ dir (tests), where the locally
    computed `_REPO_ROOT` would be wrong."""
    import shinka

    root = str(expected_root) if expected_root else str(_REPO_ROOT)
    f = getattr(shinka, "__file__", "") or ""
    if not f.startswith(root):
        raise RuntimeError(
            f"shinka resolved to {f!r}, NOT the expected repo ({root}). The "
            "orchestrator must use this repo's shinka. Run from the repo root, "
            "set PYTHONPATH to the repo root, or remove the conflicting editable "
            "install."
        )
    return f


class _NumpyAwareEncoder(json.JSONEncoder):
    """Serialize numpy scalars/arrays that leak out of shinka internals."""

    def default(self, obj: Any) -> Any:  # noqa: D401
        # Lazy import so the module loads without numpy present.
        try:
            import numpy as np
        except Exception:  # pragma: no cover - numpy always present in env
            np = None
        if np is not None:
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, np.bool_):
                return bool(obj)
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, set):
            return sorted(obj)
        return super().default(obj)


def dumps(obj: Any) -> str:
    return json.dumps(obj, cls=_NumpyAwareEncoder, allow_nan=True)


def read_stdin_json() -> Dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def write_stdout_json(obj: Any) -> None:
    sys.stdout.write(dumps(obj))
    sys.stdout.flush()


def run_main(main_fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    """Entry point for ``if __name__ == '__main__'`` blocks.

    Reads stdin JSON, calls ``main_fn``, writes a success/error envelope to
    stdout. Exits non-zero on crash so callers can detect failure from the
    process return code as well as the envelope.
    """
    try:
        payload = read_stdin_json()
    except Exception as exc:  # malformed input
        write_stdout_json(
            {
                "ok": False,
                "error": f"could not parse stdin JSON: {exc}",
                "error_type": type(exc).__name__,
            }
        )
        sys.exit(2)

    try:
        result = main_fn(payload)
        if not isinstance(result, dict):
            result = {"result": result}
        result.setdefault("ok", True)
        write_stdout_json(result)
    except Exception as exc:
        write_stdout_json(
            {
                "ok": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
            }
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Program (de)serialization helpers shared by archive_record / archive_query /
# sample_parent / diagnostics so they all speak the same program JSON shape.
# ---------------------------------------------------------------------------

# Fields safe to echo without bloating the JSON. ``code`` and ``embedding`` are
# opt-in because they are large.
_PROGRAM_SUMMARY_FIELDS = (
    "id",
    "generation",
    "parent_id",
    "island_idx",
    "combined_score",
    "correct",
    "complexity",
    "children_count",
    "in_archive",
    "public_metrics",
    "error_traceback",
    "archive_inspiration_ids",
    "top_k_inspiration_ids",
    "system_prompt_id",
)


def program_summary(
    program: Any,
    include_code: bool = False,
    include_embedding: bool = False,
    include_metadata: bool = False,
    code_preview_chars: int = 0,
) -> Dict[str, Any]:
    """Turn a shinka ``Program`` (or dict) into a JSON-safe summary dict.

    ``include_metadata`` surfaces the free-form ``metadata`` blob — that is where
    ``record_policy.py`` writes its derived signals, so the orchestrator reads it
    to spot cross-cutting issues (reward-vs-improvement, transport, etc.).
    """
    if program is None:
        return {}
    get = (
        program.get
        if isinstance(program, dict)
        else lambda k, d=None: getattr(program, k, d)
    )
    out: Dict[str, Any] = {}
    for field in _PROGRAM_SUMMARY_FIELDS:
        out[field] = get(field, None)
    code = get("code", "") or ""
    if include_code:
        out["code"] = code
    elif code_preview_chars > 0:
        out["code_preview"] = code[:code_preview_chars]
    if include_embedding:
        out["embedding"] = get("embedding", []) or []
    if include_metadata:
        out["metadata"] = get("metadata", {}) or {}
    return out


def log_external_call(results_dir, kind, request, response, cost=0.0, summary=None):
    """WS7: self-log an external LLM call (meta / deep_research) to the run journal,
    so the prompt + raw output are persisted (never overwritten) and the cost folds
    into the ledger automatically — the orchestrator just passes ``results_dir``.

    A best-effort no-op when ``results_dir`` is falsy or the journal can't be
    imported: LOGGING MUST NEVER BREAK A CALL. Returns the detail file path or None.
    (journal lives in ../harness; add it to sys.path lazily to avoid a hard
    scripts->harness import dependency.)"""
    if not results_dir:
        return None
    try:
        import os as _os
        import sys as _sys

        _harness = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "harness")
        if _harness not in _sys.path:
            _sys.path.insert(0, _harness)
        import journal  # type: ignore

        return journal.log_call(results_dir, kind, request, response, cost=cost, summary=summary)
    except Exception:
        return None

"""validate_strategy.py — Valid(S') check before deploying a rewritten strategy.

This is the EvoX safety net, translated. Before the orchestrator deploys a
candidate strategy file, it runs this to confirm the I/O contract still holds:

  1. PARSE   — the file is syntactically valid Python (ast.parse).
  2. SMOKE   — the file is run as a sandboxed subprocess with a synthetic payload
               (a tiny throwaway archive for db-backed targets) and must emit
               ``{"ok": true, ...}`` containing the target's required output keys.

If validation fails, the orchestrator does NOT deploy (it fixes a mechanical
error and retries, or abandons the rewrite and tries a different intervention).

USAGE:
  python harness/validate_strategy.py <candidate_path> <target_filename>
  (or import and call ``main({"candidate_path":..., "target_filename":...})``)

MUTABILITY: harness plumbing. Not a strategy file; do not rewrite.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_HARNESS_DIR = Path(__file__).resolve().parent
_ORCH_DIR = _HARNESS_DIR.parent
_REPO_ROOT = _ORCH_DIR.parent
_SCRIPTS_DIR = Path(os.environ.get("SHINKA_ORCH_SCRIPTS_DIR", _ORCH_DIR / "scripts"))
for _p in (str(_REPO_ROOT), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _common  # noqa: E402
import archive_record  # noqa: E402
import archive_query  # noqa: E402

_SUBPROC_TIMEOUT = 90


# --- synthetic archive for db-backed targets --------------------------------
def _build_synthetic_archive(tmp: str) -> Dict[str, Any]:
    db_path = os.path.join(tmp, "programs.sqlite")
    db_config = {"num_islands": 2, "archive_size": 10}
    code = "# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n"
    for gen, score in enumerate([1.0, 1.2, 1.5]):
        archive_record.main(
            {
                "db_path": db_path,
                "db_config": db_config,
                "program": {
                    "code": code,
                    "generation": gen,
                    "combined_score": score,
                    "correct": True,
                    "public_metrics": {},
                    "private_metrics": {},
                },
            }
        )
    ids = [
        p["id"]
        for p in archive_query.main(
            {"db_path": db_path, "db_config": db_config, "query_type": "all"}
        )["result"]
    ]
    return {"db_path": db_path, "db_config": db_config, "archive_ids": ids}


# --- per-target contracts ----------------------------------------------------
# Each spec: required output keys, whether it needs a synthetic archive, a
# payload builder, and an optional invariant check over (output, ctx).
def _sample_parent_payload(ctx):
    return {"db_path": ctx["db_path"], "db_config": ctx["db_config"], "seed": 0}


def _sample_parent_invariant(out, ctx):
    if out.get("parent_id") not in ctx["archive_ids"]:
        return "parent_id is not a member of the synthetic archive"
    return None


def _prompt_payload(ctx):
    return {
        "parent": {"id": "p", "code": "x=1", "combined_score": 1.0, "public_metrics": {}},
        "archive_inspirations": [],
        "top_k_inspirations": [],
        "patch_types": ["diff", "full"],
        "patch_type_probs": [0.7, 0.3],
        "language": "python",
        "seed": 0,
    }


CONTRACTS: Dict[str, Dict[str, Any]] = {
    "sample_parent.py": {
        "required_keys": {"parent_id", "needs_fix", "selection_probs"},
        "needs_archive": True,
        "build_payload": _sample_parent_payload,
        "invariant": _sample_parent_invariant,
    },
    "stagnation_detector.py": {
        "required_keys": {"J_score", "stagnation_flag", "delta", "low_streak"},
        "needs_archive": False,
        "build_payload": lambda ctx: {
            "best_score_start": 1.0, "best_score_end": 1.5,
            "window_size": 5, "tau": 0.0, "prior_low_streak": 0,
        },
        "invariant": None,
    },
    "construct_mutation_prompt.py": {
        "required_keys": {"patch_sys", "patch_msg", "patch_type"},
        "needs_archive": False,
        "build_payload": _prompt_payload,
        "invariant": None,
        # L5: also smoke the FIX branch. The normal branch alone lets a rewrite that
        # drops `if payload.get("needs_fix"):` deploy green (it still returns the same
        # key set with patch_type "diff"/"full"/"cross") — re-opening H1 or losing
        # repair prompting entirely. The invariant pins patch_type=="fix" on this path.
        "extra_payloads": [
            {"label": "fix_mode",
             "required_keys": {"patch_sys", "patch_msg", "patch_type"},
             "build_payload": lambda ctx: {
                 "parent": {
                     "id": "p", "correct": False, "combined_score": 0.0,
                     "code": "# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n",
                     "metadata": {"stderr_log": "Traceback: boom", "stdout_log": ""},
                 },
                 "needs_fix": True, "ancestor_inspirations": [],
                 "task_sys_msg": "t", "language": "python",
                 "patch_types": ["diff", "full"], "patch_type_probs": [0.7, 0.3], "seed": 0,
             },
             "invariant": lambda out, ctx: (
                 None if out.get("patch_type") == "fix"
                 else "fix-mode payload must yield patch_type=='fix' "
                      "(the needs_fix branch appears to have been dropped — H1 regression)"
             )},
        ],
    },
    "novelty_check.py": {
        "required_keys": {"accept"},
        "needs_archive": True,
        "build_payload": lambda ctx: {
            "db_path": ctx["db_path"], "db_config": ctx["db_config"],
            "candidate_embedding": [0.1] * 8, "code_embed_sim_threshold": 0.99,
        },
        "invariant": None,
    },
    "select_llm.py": {
        "required_keys": {"model_name"},
        "needs_archive": False,
        "build_payload": lambda ctx: {
            "models": ["m1", "m2"], "state": {}, "seed": 0,
        },
        "invariant": None,
        # P7-T5: also smoke the weights + update modes (the bandit-counts snapshot is the
        # collapse + lock-out data source), so a rewrite that breaks them is caught before
        # deploy. Each uses a FRESH state_path under the temp dir.
        "extra_payloads": [
            {"label": "weights", "required_keys": {"weights", "counts", "models"},
             "build_payload": lambda ctx: {
                 "mode": "weights", "models": ["m1", "m2"], "bandit_kwargs": {},
                 "state_path": os.path.join(ctx["tmp_dir"], "bandit_weights.pkl")}},
            {"label": "update", "required_keys": {"updated"},
             "build_payload": lambda ctx: {
                 "mode": "update", "models": ["m1", "m2"], "bandit_kwargs": {},
                 "arm": "m1", "reward": 1.0, "baseline": 0.0, "cost": 0.1,
                 "state_path": os.path.join(ctx["tmp_dir"], "bandit_update.pkl")}},
        ],
    },
    "island_policy.py": {
        "required_keys": {"actions"},
        "needs_archive": True,
        "build_payload": lambda ctx: {
            "db_path": ctx["db_path"], "db_config": ctx["db_config"],
        },
        "invariant": None,
    },
    "compute_reward.py": {
        "required_keys": {"reward", "baseline"},
        "needs_archive": False,
        "build_payload": lambda ctx: {
            "candidate": {"combined_score": 1.5, "correct": True},
            "parent": {"combined_score": 1.0}, "mode": "absolute",
        },
        "invariant": None,
    },
    "record_policy.py": {
        "required_keys": {"metadata"},
        "needs_archive": False,
        "build_payload": lambda ctx: {
            "eval": {"combined_score": 1.5, "correct": True},
            "parent": {"combined_score": 1.0},
            "mutation": {"patch_type": "diff", "num_applied": 1},
            "sample": {"parent_id": "p", "needs_fix": False},
        },
        "invariant": None,
    },
    "meta_summarize.py": {
        "required_keys": {"recommendations", "directions", "failure_note", "island_directions"},
        "needs_archive": False,
        "build_payload": lambda ctx: {
            "mock": True, "goal": "x",
            "mock_text": (
                '{"directions": [{"text": "try simulated annealing", "weight": 0.7}], '
                '"failure_note": "timeouts dominated", '
                '"island_directions": [{"island_idx": 0, "text": "greedy"}, '
                '{"island_idx": 1, "text": "exact F2 elimination"}]}'
            ),
        },
        "invariant": lambda out, ctx: (
            None if isinstance(out.get("island_directions"), list)
            else "meta must return island_directions as a list"
        ),
    },
    "cadence_policy.py": {
        "required_keys": {"return", "reason"},
        "needs_archive": False,
        "build_payload": lambda ctx: {
            "stagnation_flag": False, "windows_run": 5, "max_windows_per_call": 3,
        },
        "invariant": lambda out, ctx: (
            None if out.get("return") is True else "expected return=True at window cap"
        ),
    },
    # M15: mutate.py IS a mutable target — give it a real output-contract smoke (mock
    # mode makes no LLM/Azure call) so a rewrite that drops candidate_path/applied/cost
    # is caught instead of passing parse-only.
    "mutate.py": {
        "required_keys": {"applied", "candidate_path", "cost"},
        "needs_archive": False,
        "build_payload": lambda ctx: {
            "mock": True, "mock_code": "x = 1\n", "parent_code": "x = 0\n",
            "patch_type": "diff", "patch_dir": ctx.get("tmp_dir", "."),
            "language": "python", "model_name": "mock",
        },
        "invariant": None,
    },
}


def validate_bundle(changes):
    """Validate a SET of (candidate_path, target_filename) before an atomic
    concern-bundle deploy. Returns {valid: bool, results: [...]}. The compose
    check (do the rewritten files still work together) is the rewrite protocol's
    measure-window step; this gates each file's contract first.
    """
    results = []
    all_valid = True
    for ch in changes:
        r = main({"candidate_path": ch["candidate_path"], "target_filename": ch["target"]})
        r["target"] = ch["target"]
        results.append(r)
        all_valid = all_valid and r.get("valid", False)
    return {"valid": all_valid, "results": results}


def _run_candidate(candidate_path: str, payload: Dict[str, Any]):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_SCRIPTS_DIR), str(_REPO_ROOT), env.get("PYTHONPATH", "")]
    )
    proc = subprocess.run(
        [sys.executable, candidate_path],
        input=_common.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=_SUBPROC_TIMEOUT,
    )
    return proc


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    candidate_path = payload["candidate_path"]
    target = payload["target_filename"]
    errors: List[str] = []

    # 1. PARSE
    try:
        src = Path(candidate_path).read_text(encoding="utf-8")
        ast.parse(src)
    except SyntaxError as exc:
        return {"valid": False, "stage": "parse", "errors": [f"SyntaxError: {exc}"]}
    except Exception as exc:
        return {"valid": False, "stage": "parse", "errors": [str(exc)]}

    spec = CONTRACTS.get(target)
    if spec is None:
        # M16: a target that is neither a known contract NOR a MUTABLE strategy file is
        # almost certainly a typo or a FOUNDATION file — refuse it (the rewrite protocol
        # must not green-light writing it).
        try:
            import os as _os
            import sys as _sys

            _hd = _os.path.dirname(_os.path.abspath(__file__))
            if _hd not in _sys.path:
                _sys.path.insert(0, _hd)
            from strategy_store import MUTABLE_TARGETS as _MUT
        except Exception:
            _MUT = (
                "sample_parent.py", "novelty_check.py", "select_llm.py", "compute_reward.py",
                "record_policy.py", "stagnation_detector.py", "island_policy.py",
                "cadence_policy.py", "construct_mutation_prompt.py", "mutate.py", "meta_summarize.py",
                "island_brief.py",  # M3: keep in sync with strategy_store.MUTABLE_TARGETS
            )
        if target not in _MUT:
            return {
                "valid": False, "stage": "parse",
                "errors": [f"{target} is neither a known contract nor a MUTABLE strategy target"],
            }
        # A MUTABLE target without a smoke contract (currently only mutate.py — M15):
        # parse-only for now (confirm a callable main exists).
        if "def main(" not in src:
            return {
                "valid": False, "stage": "parse",
                "errors": [f"no contract for {target} and no main() found"],
            }
        return {
            "valid": True, "stage": "parse",
            "errors": [], "note": f"no smoke contract for {target}; parse-only (M15: add one)",
        }

    # 2. SMOKE
    with tempfile.TemporaryDirectory() as tmp:
        ctx: Dict[str, Any] = {}
        if spec["needs_archive"]:
            ctx = _build_synthetic_archive(tmp)
        ctx["tmp_dir"] = tmp  # M15: a writable dir for targets (e.g. mutate) that need one
        run_payload = spec["build_payload"](ctx)
        try:
            proc = _run_candidate(candidate_path, run_payload)
        except subprocess.TimeoutExpired:
            return {"valid": False, "stage": "smoke", "errors": ["timed out"]}

        if not proc.stdout.strip():
            return {
                "valid": False, "stage": "smoke",
                "errors": [f"no stdout; stderr: {proc.stderr[-800:]}"],
            }
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {
                "valid": False, "stage": "smoke",
                "errors": [f"stdout not JSON: {proc.stdout[:300]}"],
            }

        if not out.get("ok"):
            return {
                "valid": False, "stage": "smoke",
                "errors": [out.get("error", "main returned ok=false")],
            }
        missing = spec["required_keys"] - set(out.keys())
        if missing:
            errors.append(f"missing required output keys: {sorted(missing)}")
        inv = spec.get("invariant")
        if inv is not None and not errors:
            msg = inv(out, ctx)
            if msg:
                errors.append(msg)

        # P7-T5: smoke EXTRA modes (e.g. select_llm weights + update) so a rewrite that
        # breaks the bandit-counts snapshot — the collapse + lock-out data source — is
        # caught BEFORE deploy, not silently at runtime.
        for extra in spec.get("extra_payloads", []):
            _label = extra.get("label", "extra")
            try:
                _eproc = _run_candidate(candidate_path, extra["build_payload"](ctx))
                _eout = json.loads(_eproc.stdout or "{}")
            except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as _exc:
                errors.append(f"{_label} mode failed: {_exc}")
                continue
            if not _eout.get("ok"):
                errors.append(f"{_label} mode returned ok=false")
                continue
            _emiss = set(extra["required_keys"]) - set(_eout.keys())
            if _emiss:
                errors.append(f"{_label} mode missing keys: {sorted(_emiss)}")
                continue
            # L5: an extra payload MAY carry its own invariant (the required_keys check
            # alone can't catch a semantic regression, e.g. a dropped needs_fix branch
            # still returns the same key set with the wrong patch_type).
            _einv = extra.get("invariant")
            if _einv is not None:
                _emsg = _einv(_eout, ctx)
                if _emsg:
                    errors.append(f"{_label} invariant: {_emsg}")

    return {
        "valid": len(errors) == 0,
        "stage": "smoke",
        "errors": errors,
        "output_keys": sorted(out.keys()),
    }


def _cli() -> None:
    if len(sys.argv) >= 3:
        payload = {"candidate_path": sys.argv[1], "target_filename": sys.argv[2]}
        result = main(payload)
        sys.stdout.write(_common.dumps(result))
        sys.stdout.flush()
        sys.exit(0 if result.get("valid") else 1)
    _common.run_main(main)


if __name__ == "__main__":
    _cli()

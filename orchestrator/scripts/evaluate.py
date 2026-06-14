"""evaluate.py — run a candidate program, return score + artifacts.

MUTABILITY: IMMUTABLE PLUMBING. Do not modify as part of a strategy rewrite.
This is the evaluation primitive — the score it returns is ground truth that
everything downstream depends on. It embeds NO LLM call.

Ground-truth guarantee (H13): the candidate's ``results_dir`` is WIPED before every
eval, so a result-less death (timeout SIGKILL / crash / a late grandchild write) yields
an empty dir — ``load_results`` then returns correct=false and the timeout/crash
synthesis fires — rather than reading a reused gen dir's stale metrics/correct as this
candidate's score.

It is a thin wrapper around shinka's existing evaluator path: it builds a
``JobScheduler`` + ``LocalJobConfig`` and calls ``scheduler.run(...)``, which is
the exact synchronous code path the real runner uses (build
``python <evaluate.py> --program_path <prog> --results_dir <dir>``, run it as a
sandboxed subprocess, wait, and load ``metrics.json`` / ``correct.json``). Using
shinka's own scheduler guarantees behavioral parity with the original loop.

INPUT (stdin JSON):
  {
    "program_path": str,            # path to the candidate program file
    "results_dir": str,             # dir to write metrics.json / correct.json
    "eval_program_path": str,       # the task's evaluate.py
    "job_type": "local",            # only "local" supported here
    "time": "00:05:00" | null,      # per-eval wall-clock cap (LocalJobConfig.time)
    "conda_env": str | null,        # optional conda env to run the eval in
    "python_executable": str | null,
    "extra_cmd_args": {..} | null,  # extra --flags forwarded to the evaluator
    "verbose": false
  }

OUTPUT (stdout JSON):
  {
    "ok": true,
    "combined_score": float,
    "correct": bool,
    "public_metrics": {..},
    "private_metrics": {..},
    "error": str | null,            # validation/first error message
    "error_traceback": str | null,  # truncated traceback when correct=false
    "text_feedback": str | null,    # evaluator's human-readable reason (domain-failure detail)
    "stdout_log": str,
    "stderr_log": str,              # already head+tail truncated to ~16KB
    "runtime_sec": float
  }
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from . import _common
except ImportError:  # when run as a script, not a package
    import _common  # type: ignore


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    import time

    from shinka.launch.scheduler import JobScheduler, LocalJobConfig
    from shinka.launch.local import monitor as monitor_local
    from shinka.utils.general import load_results

    program_path = payload["program_path"]
    results_dir = payload["results_dir"]
    eval_program_path = payload.get("eval_program_path", "evaluate.py")
    eval_time = payload.get("time")
    job_type = payload.get("job_type", "local")
    if job_type != "local":
        raise ValueError(
            f"evaluate.py only supports job_type='local', got {job_type!r}"
        )

    job_config = LocalJobConfig(
        eval_program_path=eval_program_path,
        time=eval_time,
        conda_env=payload.get("conda_env"),
        python_executable=payload.get("python_executable"),
        extra_cmd_args=payload.get("extra_cmd_args") or {},
    )
    scheduler = JobScheduler(
        job_type="local",
        config=job_config,
        verbose=bool(payload.get("verbose", False)),
    )

    # NOTE: we deliberately do NOT use scheduler.run() — it monitors WITHOUT
    # passing the timeout, so a hung candidate would block the whole window
    # forever. We submit, then monitor WITH the timeout (which kills on
    # overrun), then inspect the return code to report a clear timeout error
    # the fix policy can act on.
    from shinka.utils import parse_time_to_seconds

    # Make `import shinka` work in the eval subprocess regardless of cwd or
    # whether the editable install is present — the subprocess inherits our env,
    # so put the repo root on PYTHONPATH. This decouples the design from
    # `pip install -e .`.
    import os as _os

    _root = str(_common.repo_root())
    _pp = _os.environ.get("PYTHONPATH", "")
    if _root not in _pp.split(_os.pathsep):
        _os.environ["PYTHONPATH"] = _root + (_os.pathsep + _pp if _pp else "")

    # H13: pre-clean the candidate's results_dir BEFORE the eval runs, so a RESULT-LESS
    # death (timeout SIGKILL, crash, or a late conda-grandchild write — M47) can never let
    # load_results below read a PRIOR occupant's stale metrics.json/correct.json as THIS
    # candidate's ground truth. Generation numbers ARE reused (novelty-drop / apply-exhausted
    # slots archive no row; a strategy revert rewinds the DB but not the on-disk gen dirs),
    # so a reused gen dir can still hold a predecessor's files; a stale correct=true would
    # otherwise BOTH fabricate a score (line ~147) AND suppress the timeout/crash synthesis
    # (gated on `not correct`). Scope: results_dir ONLY (the gen_dir/results subdir) — the
    # candidate program lives in the PARENT gen_dir/main.<ext> and is untouched, and the
    # parent gen_dir survives for L11's monotonic-generation disk scan.
    import shutil as _shutil

    _shutil.rmtree(results_dir, ignore_errors=True)
    _os.makedirs(results_dir, exist_ok=True)

    proc = scheduler.submit_async(program_path, results_dir)
    t0 = time.time()
    try:
        monitor_local(proc, results_dir, verbose=bool(payload.get("verbose", False)), timeout=eval_time)
        # monitor_local kills on overrun but does not reap; wait so the return
        # code is populated (SIGKILL -> negative rc).
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    finally:
        scheduler.shutdown()
    runtime_sec = time.time() - t0

    results = load_results(results_dir)
    correct_blob = results.get("correct", {}) or {}
    metrics = results.get("metrics", {}) or {}
    stderr_log = results.get("stderr_log", "") or ""

    correct = bool(correct_blob.get("correct", False))
    error = correct_blob.get("error")
    error_traceback = correct_blob.get("error_traceback")

    # Detect a timeout (monitor_local kills exactly at the limit, so runtime
    # reaching the limit with no success is the reliable signal) or a hard crash
    # (negative return code). The evaluator never wrote correct.json in these
    # cases, so synthesize a clear, fix-usable error from the limit + stderr tail.
    timeout_seconds = parse_time_to_seconds(eval_time) if eval_time else None
    return_code = getattr(proc, "returncode", None)
    timed_out = (
        timeout_seconds is not None
        and runtime_sec >= timeout_seconds - 0.5
        and not correct
    )
    crashed = return_code is not None and return_code < 0 and not correct
    if (timed_out or crashed) and not error_traceback:
        reason = "exceeded the time limit" if timed_out else "crashed (process killed)"
        tail = stderr_log[-1000:] if stderr_log else "(no stderr captured)"
        error_traceback = (
            f"EvaluationTerminated: {reason} after {runtime_sec:.0f}s "
            f"(limit {eval_time}, return code {return_code}). "
            f"Reduce runtime or fix the hang/crash.\n--- stderr tail ---\n{tail}"
        )
        error = error or "evaluation timed out or crashed"

    return {
        "combined_score": float(metrics.get("combined_score", 0.0) or 0.0),
        "correct": correct,
        "public_metrics": metrics.get("public", {}) or {},
        "private_metrics": metrics.get("private", {}) or {},
        "error": error,
        "error_traceback": error_traceback,
        "text_feedback": metrics.get("text_feedback"),
        "timed_out": bool(timed_out),
        "stdout_log": results.get("stdout_log", "") or "",
        "stderr_log": stderr_log,
        "runtime_sec": float(runtime_sec),
    }


if __name__ == "__main__":
    _common.run_main(main)

"""Evaluator for the pbb_code_discovery task — Campaign 5 (non-CSS PBB codes).

Faithful ShinkaEvolve port of the Campaign-5 evaluation from
"Evolutionary Discovery of Bivariate Bicycle Codes with LLM-Guided Search"
(Cruz-Benito, Cross, Kremer, Faro; arXiv:2606.02418), repo
``qiskit-community/qcode-discovery`` (Apache-2.0). The candidate's evolved
``generate_candidates(ell, m)`` (in initial.py) proposes non-CSS perturbed
bivariate-bicycle (PBB) code 4-tuples ``(A, B, C, D)``; this evaluator builds
each code, screens by logical-qubit count ``k``, and certifies distance ``d``
with the paper's 3-tier adaptive pipeline, then scores FOM = k*d^2/n.

WHAT IS REPLICATED VERBATIM FROM CAMPAIGN 5 (the science):
  * Construction — qcode_eval/pbb_code.py (block-1 z-part [C|D], paper Eq. 2).
  * Distance — qcode_eval/_noncss_distance_worker.py: (1) hash-based EXACT
    low-weight check (exact d<=6 at n<=216, exact d<=4 at n>216), (2) symplectic
    MILP with adaptive per-logical timeouts, (3) BP-OSD only as last-resort
    fallback (it overestimates non-CSS distance, so it is heavily discounted).
  * Scored lattices — (6,6),(9,6),(12,6),(15,6),(30,6),(6,3),(3,6), n=36..360.
  * Trust filter & scoring — EXACT/TRUSTED bounds score at full FOM, PARTIAL at
    0.25x, UNTRUSTED at 0x; combined_score = sum over lattices of the best
    trust-adjusted FOM. Codes with d<=4 contribute fom=0 (a tiny k/n*0.1 floor
    keeps "valid but low-distance" candidates strictly above "no valid code").

OPERATIONAL DEVIATION (does NOT change the science): on a distance-pool TIMEOUT
this keeps the results that finished and abandons the rest (collect-and-drop),
rather than re-running every task sequentially as upstream did — a re-run would
blow the Shinka per-candidate wall-clock budget and get the eval SIGKILLed.

REWARD-HACK / LEAK-PROOFING (why this is robust by construction): the score is
computed from REAL codes — k via exact GF(2) rank, d via exact hash / MILP. A
candidate cannot fake a high FOM: BP-OSD's overestimates are caught by the trust
multiplier (high d/sqrt(n) -> 0x), the A=B / C=D self-dual traps collapse to
d=2, and the hash check is ground truth at small n. There is no held-out answer
key — any candidate that scores well genuinely discovered a good code. Raw
per-code distance internals (d_bposd vs d_milp, untrusted bounds, solve times)
live under ``private``; only the trust-adjusted view reaches the inner loop via
``public`` + ``text_feedback`` (its own discovered codes, reflected back).

RUNTIME: ~2-25 min depending on what the candidate proposes. The hash check is
the workhorse (~3s/code at n=72, up to ~2min at n=216); MILP only fires for
genuinely high-d codes or n=360. Distance runs in parallel across all
lattice x top-k tasks via a spawn ProcessPoolExecutor (per-future isolation, so
one ldpc C-extension segfault cannot sink the batch). Env knobs below tune the
budget; defaults match config_noncss.yaml (1500 s evaluator timeout).

Deps (shinka env): qldpc, galois, ldpc, scipy, sympy, numpy (see README).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from typing import Any, Optional

# --- Make the vendored frozen backbone importable, including by spawn workers ---
# run_shinka_eval loads + runs aggregate_fn IN-PROCESS (num_runs=1, run_workers=1),
# so inserting the task dir here puts qcode_eval on sys.path for this process; spawn
# children inherit sys.path via the multiprocessing prep data, and PYTHONPATH is set
# as a belt-and-suspenders for any re-exec.
_TASK_DIR = os.path.dirname(os.path.abspath(__file__))
if _TASK_DIR not in sys.path:
    sys.path.insert(0, _TASK_DIR)
os.environ["PYTHONPATH"] = _TASK_DIR + os.pathsep + os.environ.get("PYTHONPATH", "")

from qcode_eval.pbb_code import build_pbb_code, get_pbb_params_fast
from qcode_eval._noncss_distance_worker import distance_worker


# --- Campaign-5 benchmark constants (frozen; env-overridable for smoke tests) ---

def _parse_lattices(spec: str | None) -> list[tuple[int, int]]:
    """Parse 'ell,m;ell,m;...' into [(ell,m), ...]; None -> Campaign-5 default."""
    if not spec:
        return [(6, 6), (9, 6), (12, 6), (15, 6), (30, 6), (6, 3), (3, 6)]
    out: list[tuple[int, int]] = []
    for pair in spec.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        a, b = pair.split(",")
        out.append((int(a), int(b)))
    return out


# The 7 scored lattices of Campaign 5 (n = 72, 108, 144, 180, 360, 36, 36).
STAGE2_LATTICES: list[tuple[int, int]] = _parse_lattices(os.environ.get("PBB_LATTICES"))
# d below this contributes fom=0 (the worker already rejects d<=4 by setting fom=0).
MIN_RELEVANT_D: int = 5
# Distance is certified for at most this many diverse candidates per lattice.
MAX_DISTANCE_PER_LATTICE: int = int(os.environ.get("PBB_MAX_DIST_PER_LATTICE", "10"))
# Per-candidate cap before the k-screen (matches upstream).
MAX_CANDIDATES_PER_LATTICE: int = int(os.environ.get("PBB_MAX_CANDIDATES_PER_LATTICE", "3000"))
# BP-OSD trial budget handed to the worker (only used in the fallback path).
NUM_TRIALS: int = int(os.environ.get("PBB_NUM_TRIALS", "1000"))
# Distance-pool worker count (each worker is single-threaded).
NUM_WORKERS: int = int(os.environ.get("PBB_NUM_WORKERS", str(max(1, min((os.cpu_count() or 4) // 2, 16)))))
# Whole-eval wall-clock budget; ~matches config_noncss.yaml evaluator timeout (1500 s).
# The harness task.eval_time MUST exceed this (the shipped config uses 00:32:00 = 1920 s).
EVAL_WALLCLOCK_BUDGET_S: float = float(os.environ.get("PBB_EVAL_WALLCLOCK_BUDGET_S", str(1500)))
# Ceiling on the parallel distance phase. A ProcessPoolExecutor cannot cancel an already-RUNNING
# worker, so the real distance-phase time can overshoot this by up to one max single-task runtime
# (~370 s: <1 s hash + 360 s MILP budget at n=360). Invariant:
#   pool_timeout + max_task  <  EVAL_WALLCLOCK_BUDGET_S  <  eval_time   (1000 + 370 < 1500 < 1920).
DISTANCE_POOL_TIMEOUT_S: float = float(os.environ.get("PBB_DISTANCE_POOL_TIMEOUT_S", str(1000)))
# Per-call wall-clock cap on the candidate's generate_candidates (build-phase backstop: a hung or
# infinite generator becomes a clean per-lattice skip instead of a harness SIGKILL). Realistically
# generate_candidates runs in seconds; this only fires on a pathological mutation.
GENERATE_TIMEOUT_S: float = float(os.environ.get("PBB_GENERATE_TIMEOUT_S", str(120)))


# --- Trust filter (faithful to evolve/openevolve_evaluator_noncss.py) -----------

def _trust_level_for_result(result: dict) -> str:
    """Publication-style trust label for a distance result."""
    if result.get("d_is_exact") or result.get("milp_exact"):
        return "EXACT"
    trust = result.get("trust_level")
    if trust:
        return trust
    d = result.get("d", 0)
    n = result.get("n", 0)
    ratio = d / math.sqrt(n) if d > 0 and n > 0 else 0.0
    if ratio < 1.5:
        return "TRUSTED"
    if ratio < 2.5:
        return "PARTIAL"
    return "UNTRUSTED"


def _trust_multiplier(result: dict) -> float:
    """Discount speculative distance bounds: EXACT/TRUSTED full, PARTIAL 0.25, else 0."""
    trust = _trust_level_for_result(result)
    if trust in {"EXACT", "TRUSTED"}:
        return 1.0
    if trust == "PARTIAL":
        return 0.25
    return 0.0


def _scored_fom(result: dict) -> float:
    return result.get("fom", 0.0) * _trust_multiplier(result)


# --- Build + per-lattice top-k selection (sequential, fast) ---------------------

class _GenTimeout(Exception):
    pass


def _call_with_timeout(fn, args, timeout_s):
    """Run fn(*args) under a wall-clock timeout via a daemon worker thread (Windows-safe,
    no SIGALRM). On timeout the thread is abandoned (daemon -> dies with the process) and
    _GenTimeout is raised. Mirrors the cnot_grid_synth per-trial timeout pattern."""
    import threading

    box: dict = {}

    def _worker() -> None:
        try:
            box["value"] = fn(*args)
        except BaseException as exc:  # propagate into the caller's thread
            box["error"] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(float(timeout_s))
    if t.is_alive():
        raise _GenTimeout(f"generate_candidates exceeded {timeout_s:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _build_and_select(
    generate_fn,
    lattices: list[tuple[int, int]],
    max_per_lattice: int,
    deadline: float,
) -> tuple[list, list, int, list]:
    """For each lattice: call the candidate, build PBB codes, keep k>0, and pick a
    diverse top-k (one per distinct k, then fill) for distance certification.

    Returns (per_lattice_top, per_lattice_rest, total_candidates, errors) where
    per_lattice_top is [((ell,m), [info...])] and per_lattice_rest is the k-only
    remainder [((ell,m), [result...])]. Mirrors upstream `_run_evaluation` phase 1.
    """
    per_lattice_top: list = []
    per_lattice_rest: list = []
    total = 0
    errors: list = []

    for ell, m in lattices:
        if time.monotonic() > deadline:
            errors.append(f"({ell},{m}): skipped — eval wall-clock budget exhausted during build")
            continue
        try:
            cands = _call_with_timeout(generate_fn, (ell, m), GENERATE_TIMEOUT_S)
        except _GenTimeout as e:
            errors.append(f"({ell},{m}): {e}")
            continue
        except Exception as e:
            errors.append(f"({ell},{m}): generate_candidates raised {type(e).__name__}: {e}")
            continue
        if not isinstance(cands, list):
            errors.append(f"({ell},{m}): returned {type(cands).__name__}, not list")
            continue

        total += len(cands)
        if len(cands) > MAX_CANDIDATES_PER_LATTICE:
            errors.append(f"({ell},{m}): {len(cands)} candidates, capped to {MAX_CANDIDATES_PER_LATTICE}")
            cands = cands[:MAX_CANDIDATES_PER_LATTICE]

        built: list[dict] = []
        for cand in cands:
            if not (isinstance(cand, (list, tuple)) and len(cand) == 4):
                continue
            A_terms, B_terms, C_terms, D_terms = cand
            try:
                code = build_pbb_code(ell, m, A_terms, B_terms, C_terms, D_terms)
                n, k = get_pbb_params_fast(code)
            except Exception:
                # Invalid terms / commutativity violation / construction error.
                continue
            if k > 0:
                built.append({
                    "ell": ell, "m": m, "n": n, "k": k,
                    "A_terms": A_terms, "B_terms": B_terms,
                    "C_terms": C_terms, "D_terms": D_terms,
                    "encoding_rate": k / n if n > 0 else 0.0,
                })

        # Diverse top-k: one per distinct k (highest first), then fill remaining slots.
        built.sort(key=lambda x: x["k"], reverse=True)
        seen_k: set[int] = set()
        top: list[dict] = []
        top_ids: set[int] = set()
        for info in built:
            if info["k"] not in seen_k and len(top) < max_per_lattice:
                seen_k.add(info["k"])
                top.append(info)
                top_ids.add(id(info))
        for info in built:
            if len(top) >= max_per_lattice:
                break
            if id(info) not in top_ids:
                top.append(info)
                top_ids.add(id(info))

        per_lattice_top.append(((ell, m), top))
        rest = [
            {**info, "d": 0, "fom": 0.0}
            for info in built if id(info) not in top_ids
        ]
        per_lattice_rest.append(((ell, m), rest))

    return per_lattice_top, per_lattice_rest, total, errors


# --- Parallel 3-tier distance (spawn pool, per-future isolation) ----------------

def _run_distance(tasks: list[tuple], num_workers: int, pool_timeout: float, deadline: float) -> list[dict]:
    """Run distance_worker over all tasks in a spawn ProcessPoolExecutor.

    spawn + individual futures so one worker dying (ldpc's BpOsdDecoder can
    segfault on degenerate non-CSS matrices) does not sink the batch. On pool
    TIMEOUT we collect-and-drop (keep what finished); on pool START failure we
    fall back to sequential, wall-clock guarded by `deadline`.
    """
    if not tasks:
        return []

    import concurrent.futures
    import multiprocessing as mp

    results: list[dict] = []
    nw = max(1, min(num_workers, len(tasks)))
    try:
        ctx = mp.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=nw, mp_context=ctx) as pool:
            futures = {pool.submit(distance_worker, t): t for t in tasks}
            collected: set = set()
            try:
                for fut in concurrent.futures.as_completed(futures, timeout=pool_timeout):
                    collected.add(fut)
                    try:
                        results.append(fut.result())
                    except Exception:
                        pass  # individual worker failure -> drop that one task
            except concurrent.futures.TimeoutError:
                # Pool timed out. `collected` holds every future as_completed already yielded
                # (result appended, or its exception swallowed) -> skip those to avoid re-handling.
                # Keep any future that finished but was not yet yielded; cancel the rest. NOTE:
                # cancel() only stops NOT-yet-started futures — an already-running worker is awaited
                # by the `with` block's shutdown, so this can overshoot pool_timeout by up to one
                # max single-task runtime (bounded against EVAL_WALLCLOCK_BUDGET_S by the constant).
                for fut in futures:
                    if fut in collected:
                        continue
                    if fut.done() and not fut.cancelled():
                        try:
                            results.append(fut.result())
                        except Exception:
                            pass
                    else:
                        fut.cancel()
    except Exception:
        # Pool failed to start (e.g. spawn bootstrap error) -> sequential fallback, wall-clock
        # guarded: drop the remaining tasks once `deadline` passes rather than running ~70 tasks
        # (each up to ~370 s) serially and getting SIGKILLed by the harness.
        for t in tasks:
            if time.monotonic() > deadline:
                break
            try:
                results.append(distance_worker(t))
            except Exception:
                pass
    return results


# --- Feedback / metrics assembly ------------------------------------------------

def _failure(text: str, public: Optional[dict] = None) -> dict[str, Any]:
    return {
        "combined_score": 0.0,
        "correct": False,
        "public": public or {"error": text},
        "private": {},
        "extra_data": {},
        "text_feedback": text,
    }


def _best_code_str(r: dict) -> str:
    return (
        f"[[{r['n']},{r['k']},<={r['d']}]] FOM={r.get('fom', 0.0):.2f} "
        f"trust={_trust_level_for_result(r)} method={r.get('d_method', '?')} "
        f"at ({r['ell']},{r['m']})\n"
        f"  A={r['A_terms']} B={r['B_terms']}\n"
        f"  C={r['C_terms']} D={r['D_terms']}"
    )


def aggregate_fn(results: list) -> dict[str, Any]:
    t0 = time.monotonic()
    if not results:
        return _failure("run_experiment returned no result")
    generate_fn = results[0]
    if not callable(generate_fn):
        return _failure(
            f"run_experiment must return a callable generate_candidates; "
            f"got {type(generate_fn).__name__}"
        )

    deadline = t0 + EVAL_WALLCLOCK_BUDGET_S
    per_lattice_top, per_lattice_rest, total_candidates, errors = _build_and_select(
        generate_fn, STAGE2_LATTICES, MAX_DISTANCE_PER_LATTICE, deadline
    )

    if total_candidates == 0:
        return _failure(
            "generate_candidates produced no candidates across all lattices. "
            + ("First errors: " + "; ".join(errors[:5]) if errors else ""),
            public={"total_candidates": 0, "n_errors": len(errors),
                    "first_errors": errors[:5]},
        )

    # Distance tasks: (ell, m, A, B, C, D, num_trials) — rebuilt in the worker.
    distance_tasks: list[tuple] = []
    for (_lat, top) in per_lattice_top:
        for info in top:
            distance_tasks.append((
                info["ell"], info["m"],
                info["A_terms"], info["B_terms"],
                info["C_terms"], info["D_terms"],
                NUM_TRIALS,
            ))

    pool_budget = max(1.0, deadline - time.monotonic())
    pool_timeout = min(DISTANCE_POOL_TIMEOUT_S, pool_budget)
    distance_results = _run_distance(distance_tasks, NUM_WORKERS, pool_timeout, deadline)

    n_distance_done = len(distance_results)
    n_distance_tasks = len(distance_tasks)
    if n_distance_done < n_distance_tasks:
        errors.append(
            f"distance pipeline: {n_distance_done}/{n_distance_tasks} tasks completed "
            f"(rest dropped on pool timeout/worker failure)"
        )

    all_results = list(distance_results)
    for (_lat, rest) in per_lattice_rest:
        all_results.extend(rest)

    # --- Scoring: sum over lattices of best trust-adjusted FOM (Campaign-5) -----
    per_lattice_best: dict[tuple[int, int], float] = {}
    for r in all_results:
        d = r.get("d", 0)
        k = r.get("k", 0)
        n = r.get("n", 0)
        if k <= 0 or n <= 0:
            continue
        key = (r["ell"], r["m"])
        if d < MIN_RELEVANT_D:
            fom = k / n * 0.1   # tiny floor so "valid but low-d" > "no valid code"
        else:
            fom = _scored_fom(r)
        per_lattice_best[key] = max(per_lattice_best.get(key, 0.0), fom)
    combined = float(sum(per_lattice_best.values()))

    # --- Aggregate metrics ------------------------------------------------------
    valid = [r for r in all_results if r.get("k", 0) > 0]
    scored = [_scored_fom(r) for r in valid if _scored_fom(r) > 0]
    best_fom = max(scored) if scored else 0.0
    mean_fom = sum(scored) / len(scored) if scored else 0.0
    best_encoding_rate = max((r.get("encoding_rate", 0.0) for r in valid), default=0.0)
    high_k_codes = [r for r in valid if r.get("k", 0) >= 8]
    lattices_with_high_k = len(set((r["ell"], r["m"]) for r in high_k_codes))

    credible_codes = [
        r for r in all_results
        if r.get("d", 0) >= MIN_RELEVANT_D and _trust_multiplier(r) > 0
    ]
    best_credible = max(credible_codes, key=lambda r: r.get("fom", 0.0)) if credible_codes else None

    # --- text_feedback artifacts (convergence signal; faithful to upstream) -----
    fb: list[str] = []
    if best_credible is not None:
        fb.append("BEST CREDIBLE CODE:\n" + _best_code_str(best_credible))
    elif valid:
        bv = max(valid, key=lambda r: r.get("k", 0))
        fb.append(
            f"No d>=5 credible code yet. Best valid (by k): "
            f"[[{bv['n']},{bv['k']},<={bv.get('d', 0)}]] at ({bv['ell']},{bv['m']}) "
            f"(d<=4 scores 0)."
        )

    top5 = sorted(credible_codes, key=_scored_fom, reverse=True)[:5]
    if top5:
        lines = [
            f"  [[{r['n']},{r['k']},<={r['d']}]] FOM={r.get('fom', 0.0):.1f} "
            f"({r['ell']},{r['m']}) trust={_trust_level_for_result(r)} "
            f"|C|={len(r.get('C_terms', []))} |D|={len(r.get('D_terms', []))}"
            for r in top5
        ]
        fb.append("TOP CREDIBLE CODES (d>=5):\n" + "\n".join(lines))
        struct = [
            f"  [[{r['n']},{r['k']},<={r['d']}]] FOM={r.get('fom', 0.0):.1f} "
            f"trust={_trust_level_for_result(r)}:\n"
            f"    A={r['A_terms']} B={r['B_terms']}\n"
            f"    C={r['C_terms']} ({len(r.get('C_terms', []))} terms)  "
            f"D={r['D_terms']} ({len(r.get('D_terms', []))} terms)"
            for r in top5[:3]
        ]
        fb.append("STRUCTURE OF TOP CODES:\n" + "\n".join(struct))

    if per_lattice_best:
        fb.append(
            "PER-LATTICE best credible FOM:\n"
            + "\n".join(f"  ({a},{b}): {v:.2f}" for (a, b), v in sorted(per_lattice_best.items()))
        )

    # Conditional low-d hint (upstream `low_d_warning` pattern).
    low_d = [r for r in valid if 0 < r.get("d", 0) < MIN_RELEVANT_D]
    if low_d and not credible_codes:
        max_k_low = max((r.get("k", 0) for r in low_d), default=0)
        fb.append(
            f"WARNING: {len(low_d)} valid codes but all d<=4 (max k={max_k_low}) — "
            f"these score 0. The A,B base likely has low-weight logicals. Only bases "
            f"with d>=5 count; avoid C=D (always d=2) and empty C/D (that is CSS)."
        )
    if errors:
        fb.append("NOTES: " + "; ".join(errors[:5]))

    fb.append(
        f"SUMMARY: {total_candidates} candidates over {len(STAGE2_LATTICES)} lattices; "
        f"valid(k>0)={len(valid)}; high-k(>=8)={len(high_k_codes)}; "
        f"credible(d>=5)={len(credible_codes)}; best trust-adj FOM={best_fom:.2f}; "
        f"combined_score={combined:.2f} (sum of best trust-adjusted FOM per lattice; higher is better)."
    )
    text_feedback = "\n".join(fb)

    public = {
        "combined_score": combined,
        "best_fom": float(best_fom),
        "mean_fom": float(mean_fom),
        "num_valid": len(valid),
        "num_high_k": len(high_k_codes),
        "lattices_with_high_k": lattices_with_high_k,
        "num_credible": len(credible_codes),
        "best_encoding_rate": float(best_encoding_rate),
        "total_candidates": total_candidates,
        "distance_tasks": n_distance_tasks,
        "distance_done": n_distance_done,
        "per_lattice_fom": {f"{a},{b}": round(v, 4) for (a, b), v in per_lattice_best.items()},
        "best_code": (
            {
                "n": best_credible["n"], "k": best_credible["k"], "d": best_credible["d"],
                "fom": round(best_credible.get("fom", 0.0), 3),
                "trust": _trust_level_for_result(best_credible),
                "method": best_credible.get("d_method"),
                "ell": best_credible["ell"], "m": best_credible["m"],
                "A": best_credible["A_terms"], "B": best_credible["B_terms"],
                "C": best_credible["C_terms"], "D": best_credible["D_terms"],
            }
            if best_credible is not None else None
        ),
        "n_errors": len(errors),
    }

    # Raw per-code distance internals stay private (the inner loop sees only the
    # trust-adjusted view in `public`/`text_feedback`).
    private = {
        "all_results": all_results,
        "errors": errors,
    }

    return {
        "combined_score": combined,
        "correct": True,
        "public": public,
        "private": private,
        "extra_data": {"eval_seconds": round(time.monotonic() - t0, 1)},
        "text_feedback": text_feedback,
    }


# --- Main entry -----------------------------------------------------------------

def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


def main(program_path: str, results_dir: str) -> None:
    _force_utf8_stdio()
    print(f"Evaluating program: {program_path}")
    print(f"Saving results to: {results_dir}")
    os.makedirs(results_dir, exist_ok=True)
    print(f"Lattices: {STAGE2_LATTICES} | max_dist/lattice={MAX_DISTANCE_PER_LATTICE} "
          f"| workers={NUM_WORKERS} | budget={EVAL_WALLCLOCK_BUDGET_S:.0f}s")

    from shinka.core import run_shinka_eval  # deferred so spawn children skip it

    metrics, correct, err = run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="run_experiment",
        num_runs=1,
        get_experiment_kwargs=lambda i: {},
        aggregate_metrics_fn=aggregate_fn,
        validate_fn=None,
    )
    if not correct:
        print(f"Evaluation FAILED: {err}")
    else:
        print("Evaluation completed successfully.")
    print(f"combined_score = {metrics.get('combined_score')!r}")
    if isinstance(metrics.get("public"), dict):
        for k, v in metrics["public"].items():
            if k in ("per_lattice_fom", "best_code"):
                print(f"  public.{k} = {v!r}")
            elif not isinstance(v, dict):
                print(f"  public.{k} = {v!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="pbb_code_discovery evaluator (Campaign 5, non-CSS PBB)")
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)

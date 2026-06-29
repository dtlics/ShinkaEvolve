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
genuinely high-d codes or n=360. Distance runs over a spawn ProcessPoolExecutor
with the MILP tier decomposed PER LOGICAL (one job per (code, logical) so cores
stay saturated), aggregated in the driver (_run_distance); BP-OSD runs in a
separate pool so one ldpc C-extension segfault cannot sink the MILP batch. Env
knobs below tune the budget; each worker is pinned to 1 thread (OMP_NUM_THREADS=1).

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

# Pin BLAS / OpenMP / ldpc to ONE thread PER PROCESS. The distance stage runs many worker
# processes (the per-logical pool); if each worker's numpy/scipy/ldpc also fanned a single op
# across all cores, N workers would oversubscribe (N x ~all-core threads thrash). One thread per
# process => N workers use N cores cleanly. Spawn children inherit these via os.environ. MUST be
# set BEFORE the first numpy/scipy/ldpc import below. Use setdefault so an explicit env wins.
for _thr_var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_thr_var, "1")

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
# Distance-pool worker count (each worker is single-threaded HiGHS / BP-OSD). The
# distance stage now decomposes per-LOGICAL, so the pool stays saturated even when
# few codes survive the k-screen; leave 4 cores for the driver, the harness and the
# OS (cpu-4 ~= 20 on this 24-core host). Override with PBB_NUM_WORKERS.
NUM_WORKERS: int = int(os.environ.get("PBB_NUM_WORKERS", str(max(1, (os.cpu_count() or 5) - 4))))
# Whole-eval wall-clock budget (50%-bumped from 1500). The harness task.eval_time MUST exceed
# this (the shipped config uses 00:48:00 = 2880 s).
EVAL_WALLCLOCK_BUDGET_S: float = float(os.environ.get("PBB_EVAL_WALLCLOCK_BUDGET_S", str(2250)))
# Ceiling on the parallel distance phase (collect-and-drop on timeout), 50%-bumped from 1000. The
# unit of work is ONE per-logical ILP solve (<= per-logical cap, <= 90 s at n=360). A
# ProcessPoolExecutor cannot cancel an already-RUNNING worker, but the driver shuts the pool down
# with cancel_futures=True, so the overshoot past this ceiling is bounded by ONE per-logical cap
# (<= 90 s) plus a final BP-OSD pass (its own pool, also deadline-guarded). Invariant:
#   pool_timeout + max_task  <  EVAL_WALLCLOCK_BUDGET_S  <  eval_time   (1500 + 90 < 2250 < 2880).
DISTANCE_POOL_TIMEOUT_S: float = float(os.environ.get("PBB_DISTANCE_POOL_TIMEOUT_S", str(1500)))
# Per-CODE MILP budget is measured as CPU-TIME (the SUM of that code's logical solve-times), NOT
# wall-clock -- so a code is only charged for compute it actually used, never for time its logicals
# spent QUEUED behind other codes in the shared pool (that wall-clock charge previously starved
# slow codes under contention). Budget = min(2k * per_logical, MILP_PER_CODE_CAP_S): enough to
# examine every one of the 2k logicals, capped so a pathological high-k code can't dominate.
MILP_PER_CODE_CAP_S: float = float(os.environ.get("PBB_MILP_PER_CODE_CAP_S", str(1800)))


def _per_logical_cap(n: int) -> int:
    """Per-logical symplectic-MILP timeout (seconds), adaptive by code size (50%-bumped)."""
    if n <= 108:
        return 22
    if n <= 216:
        return 45
    return 90
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


# --- Parallel 3-tier distance: per-LOGICAL MILP over a shared spawn pool ---------

def _run_distance(tasks: list[tuple], num_workers: int, pool_timeout: float, deadline: float) -> list[dict]:
    """Certify distance for every code, decomposing the MILP tier per-LOGICAL.

    Three phases over a SHARED spawn pool (plus a separate BP-OSD pool):

      A. Hash, per CODE (parallel). ``hash_stage_worker``: EXACT codes (d<=6 at
         n<=216 / d<=4 at n>216) short-circuit; survivors hand back their stabilizer
         + logicals arrays.
      B. MILP, per LOGICAL (parallel, same pool). One job per (code, logical). A
         parent-side accumulator does min-over-logicals, all-optimal => milp_exact,
         a w<=4 early-reject, and a per-CODE CPU-TIME budget = min(2k * per_logical,
         MILP_PER_CODE_CAP_S) -- the SUM of that code's logical solve-times, so a code
         is charged only for compute it used, never for time spent QUEUED (no
         contention starvation). Hitting the budget cancels its still-QUEUED logical
         futures. Round-robin-by-logical submission + a bounded in-flight window keep
         every code covered and the queued-pickle memory small.
      C. BP-OSD, per CODE (parallel, SEPARATE pool). Fallback for non-exact,
         non-rejected codes -- isolated so an ldpc ``BpOsdDecoder`` segfault
         (BrokenProcessPool) cannot poison in-flight MILP work.

    Aggregation reproduces ``distance_worker``'s exactly-4 result-dict shapes
    (hash exact_w{d} / milp_exact / milp+bposd / bposd); each code yields AT MOST
    ONE result row. Collect-and-drop on the global cutoff (= min(deadline,
    start+pool_timeout)). Falls back to the verbatim sequential ``distance_worker``
    only if the shared pool fails catastrophically (e.g. spawn bootstrap error).
    """
    if not tasks:
        return []

    import concurrent.futures as cf
    import multiprocessing as mp
    from collections import deque
    from concurrent.futures.process import BrokenProcessPool

    from qcode_eval._parallel_distance import (
        hash_stage_worker, milp_logical_worker, bposd_stage_worker,
        assemble_milp_exact_result, assemble_reject_result,
    )

    now = time.monotonic
    cutoff = min(deadline, now() + pool_timeout)
    nw = max(1, num_workers)
    reject_w = MIN_RELEVANT_D - 1            # d <= 4 -> fom 0 -> reject

    results_by_cid: dict[int, dict] = {}
    accs: dict[int, dict] = {}               # cid -> accumulator
    needs_bposd: list[int] = []              # cids that fall through to Phase C
    pool_ok = True
    ctx = mp.get_context("spawn")

    pool = cf.ProcessPoolExecutor(max_workers=nw, mp_context=ctx)
    try:
        # ---------------- Phase A: hash, per code ----------------
        hash_futs = {
            pool.submit(hash_stage_worker, (cid, t[0], t[1], t[2], t[3], t[4], t[5])): cid
            for cid, t in enumerate(tasks)
        }
        try:
            for fut in cf.as_completed(hash_futs, timeout=max(0.1, cutoff - now())):
                cid = hash_futs[fut]
                try:
                    out = fut.result()
                except BrokenProcessPool:
                    raise
                except Exception:
                    continue
                tag = out[0]
                if tag == "EXACT":
                    results_by_cid[cid] = out[2]
                elif tag == "NEEDS_MILP":
                    _, _, base, stab, logicals = out
                    num_logicals = int(logicals.shape[0])
                    cap = _per_logical_cap(base["n"])
                    if num_logicals == 0:
                        # No logicals -> straight to BP-OSD (milp_worked=False).
                        accs[cid] = {"base": base, "status": "needs_bposd",
                                     "d_milp": base["n"], "milp_worked": False,
                                     "num_trials": tasks[cid][6]}
                        needs_bposd.append(cid)
                    else:
                        accs[cid] = {
                            "base": base, "stab": stab, "logicals": logicals,
                            "num_logicals": num_logicals, "n": base["n"], "k": base["k"],
                            "tpl": cap,
                            # CPU-time budget (sum of this code's solve-times), not wall-clock.
                            "cpu_budget": min(num_logicals * cap, MILP_PER_CODE_CAP_S),
                            "cpu_used": 0.0, "n_returned": 0, "n_optimal": 0,
                            "d_best": base["n"], "any_found": False,
                            "status": "pending", "num_trials": tasks[cid][6],
                        }
                # tag == "ERROR" -> drop the code
        except cf.TimeoutError:
            pass  # hash phase hit the cutoff -> drop unfinished hash codes
        # Free pool capacity held by any still-pending hash jobs before Phase B.
        for f in hash_futs:
            if not f.done():
                f.cancel()

        # ---------------- Phase B: MILP, per logical ----------------
        milp_accs = {cid: a for cid, a in accs.items() if a["status"] == "pending"}
        if milp_accs and now() < cutoff:
            cid_order = list(milp_accs.keys())
            maxL = max(a["num_logicals"] for a in milp_accs.values())
            job_queue: deque = deque()
            for lidx in range(maxL):                       # round-robin by logical index
                for cid in cid_order:
                    if lidx < milp_accs[cid]["num_logicals"]:
                        job_queue.append((cid, lidx))

            window = max(1, 2 * nw)
            inflight: dict = {}                            # future -> (cid, lidx)

            def _cancel_code(cid: int) -> None:
                for f in [f for f, (c, _l) in inflight.items() if c == cid]:
                    if f.cancel():
                        inflight.pop(f, None)

            def _finalize(cid: int, a: dict) -> None:
                if a["status"] != "pending":
                    return
                if a["n_optimal"] == a["num_logicals"]:
                    a["status"] = "exact"
                else:
                    a["status"] = "needs_bposd"
                    a["milp_worked"] = a["any_found"]
                    a["d_milp"] = a["d_best"] if a["any_found"] else a["n"]
                    needs_bposd.append(cid)

            def _submit_next() -> None:
                while job_queue and len(inflight) < window:
                    cid, lidx = job_queue.popleft()
                    a = milp_accs[cid]
                    if a["status"] != "pending":
                        continue
                    fut = pool.submit(
                        milp_logical_worker,
                        (cid, lidx, a["stab"], a["logicals"][lidx], a["tpl"]),
                    )
                    inflight[fut] = (cid, lidx)

            _submit_next()
            while inflight:
                remaining = cutoff - now()
                if remaining <= 0:
                    break                                  # global cutoff -> drop still-pending codes
                done, _ = cf.wait(list(inflight.keys()), timeout=remaining,
                                  return_when=cf.FIRST_COMPLETED)
                if not done:
                    break                                  # hit cutoff
                for fut in done:
                    cid, lidx = inflight.pop(fut)
                    a = milp_accs[cid]
                    if a["status"] != "pending":
                        continue                           # straggler for a finalized code
                    try:
                        _cid, _lidx, w, optimal, solve_s = fut.result()
                    except BrokenProcessPool:
                        raise
                    except Exception:
                        w, optimal, solve_s = None, False, 0.0
                    a["n_returned"] += 1
                    a["cpu_used"] += float(solve_s or 0.0)   # CPU-time, immune to queue-wait
                    if w is not None:
                        a["any_found"] = True
                        if w < a["d_best"]:
                            a["d_best"] = w
                        if optimal:
                            a["n_optimal"] += 1
                    if w is not None and w <= reject_w:           # priority 1: early-reject d<=4
                        a["status"] = "rejected"
                        _cancel_code(cid)
                    elif a["n_returned"] >= a["num_logicals"]:    # 2: every logical back
                        _finalize(cid, a)
                    elif a["cpu_used"] >= a["cpu_budget"]:        # 3: per-code CPU budget spent
                        _finalize(cid, a)
                        _cancel_code(cid)
                _submit_next()

            # Terminal Phase-B outcomes -> one row each.
            for cid, a in milp_accs.items():
                if a["status"] == "exact":
                    results_by_cid[cid] = assemble_milp_exact_result(a["base"], a["d_best"], 0.0)
                elif a["status"] == "rejected":
                    results_by_cid[cid] = assemble_reject_result(a["base"], a["d_best"], 0.0)
                # "pending" (dropped on cutoff) -> no row; "needs_bposd" -> Phase C
    except Exception:
        pool_ok = False                                    # spawn bootstrap / broken pool
    finally:
        # cancel_futures=True so a cutoff break does not await the whole pool queue;
        # overshoot is bounded by the <= nw running solves (each <= per-logical cap).
        pool.shutdown(wait=True, cancel_futures=True)

    # ---------------- Phase C: BP-OSD fallback (SEPARATE pool) ----------------
    if pool_ok and needs_bposd and now() < cutoff:
        bposd_tasks = []
        for cid in needs_bposd:
            a = accs[cid]; t = tasks[cid]
            bposd_tasks.append((cid, t[0], t[1], t[2], t[3], t[4], t[5],
                                a["num_trials"], a["d_milp"], a["milp_worked"]))
        bpool = cf.ProcessPoolExecutor(max_workers=nw, mp_context=ctx)
        try:
            bfuts = {bpool.submit(bposd_stage_worker, bt): bt[0] for bt in bposd_tasks}
            try:
                for fut in cf.as_completed(bfuts, timeout=max(0.1, cutoff - now())):
                    try:
                        cid, res = fut.result()
                    except Exception:
                        continue                           # one segfault/failure -> drop that code
                    if res is not None:
                        results_by_cid[cid] = res
            except cf.TimeoutError:
                for fut in bfuts:
                    if fut.done() and not fut.cancelled():
                        try:
                            cid, res = fut.result()
                            if res is not None:
                                results_by_cid[cid] = res
                        except Exception:
                            pass
        except Exception:
            pass                                           # pool broke -> drop remaining bposd codes
        finally:
            bpool.shutdown(wait=True, cancel_futures=True)

    # ---- Sequential fallback ONLY if the shared pool failed catastrophically ----
    if not pool_ok:
        for cid, t in enumerate(tasks):
            if cid in results_by_cid:
                continue
            if now() > deadline:
                break
            try:
                results_by_cid[cid] = distance_worker(t)
            except Exception:
                pass

    return list(results_by_cid.values())


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

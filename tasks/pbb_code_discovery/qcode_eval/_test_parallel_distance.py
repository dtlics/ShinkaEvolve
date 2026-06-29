"""Standalone equivalence/sanity gate for the per-LOGICAL parallel distance path.

NOT collected by pytest (pyproject testpaths excludes tasks/*); run it directly
with the shinka interpreter. Cap cores for a quick run:

    PBB_NUM_WORKERS=3 python tasks/pbb_code_discovery/qcode_eval/_test_parallel_distance.py
    PBB_TEST_SLOW=1 PBB_NUM_WORKERS=3 python .../qcode_eval/_test_parallel_distance.py  # + n=360 pool test

Gates:
  1. dict-equivalence: hash_stage_worker EXACT dict == distance_worker dict (all keys but time_s)
  2. per-logical MILP == sequential compute_distance_milp_symplectic(early_stop=None)  (d + exact flag)
  3. assemble_bposd_result: keys + values match the vendored Stage-3 formula
  4. _run_distance plumbing: one row per code, every scorer-read key present, hash codes == distance_worker
  5. (slow, opt-in) _run_distance exercises Phase B (per-logical MILP) on an n=360 code through the pool
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TASK_DIR = os.path.dirname(_HERE)
for _p in (_TASK_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

import evaluate  # noqa: E402  (the task evaluator; pulls in qcode_eval via its sys.path insert)
from qcode_eval.pbb_code import build_pbb_code, get_symplectic_logicals  # noqa: E402
from qcode_eval._noncss_distance_worker import distance_worker, _trust_level  # noqa: E402
from qcode_eval.distance_milp import compute_distance_milp_symplectic  # noqa: E402
from qcode_eval._parallel_distance import (  # noqa: E402
    hash_stage_worker, milp_logical_worker, assemble_bposd_result,
)

# A hash-EXACT [[72,12,6]] code (Base7e, MILP-verified d=6) and a small (6,3) code.
SEED_7E = (6, 6, [(2, 1), (3, 1), (4, 4)], [(0, 0), (5, 1), (4, 5)],
           [(2, 2), (2, 5)], [(0, 4), (4, 4), (5, 1)])
SMALL_63 = (6, 3, [(0, 1), (3, 2), (4, 1)], [(0, 1), (4, 0), (4, 1)],
            [(0, 2), (1, 1), (2, 1), (3, 1), (4, 0)], [(0, 2), (2, 1)])

# Scorer-read keys aggregate_fn / the trust filter / text_feedback depend on.
SCORER_KEYS = {"ell", "m", "n", "k", "d", "fom", "d_is_exact", "trust_level",
               "d_method", "d_over_sqrtn", "encoding_rate"}

_failures: list[str] = []


def _check(cond: bool, msg: str) -> None:
    print(("  PASS" if cond else "  FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


def test_hash_dict_equivalence() -> None:
    print("[1] hash_stage_worker EXACT dict == distance_worker dict")
    ell, m, A, B, C, D = SEED_7E
    dw = distance_worker((ell, m, A, B, C, D, 1000))
    tag, _cid, hs = hash_stage_worker((0, ell, m, A, B, C, D))
    _check(tag == "EXACT", f"hash classifies seed as EXACT (got {tag})")
    _check(dw.get("d_method", "").startswith("exact_w"),
           f"distance_worker took the hash path (d_method={dw.get('d_method')})")
    _check(set(hs.keys()) == set(dw.keys()), "key sets identical")
    diffs = {k: (hs.get(k), dw.get(k)) for k in set(hs) | set(dw)
             if k != "time_s" and hs.get(k) != dw.get(k)}
    _check(not diffs, f"all values (except time_s) identical (diffs={diffs})")
    _check(hs.get("d") == 6 and hs.get("trust_level") == "EXACT",
           f"seed certifies [[72,12,6]] EXACT (d={hs.get('d')})")


def test_per_logical_equals_sequential() -> None:
    print("[2] per-logical aggregation == sequential MILP (early_stop=None)")
    ell, m, A, B, C, D = SMALL_63
    code = build_pbb_code(ell, m, A, B, C, D)
    tpl = 60
    d_seq, det = compute_distance_milp_symplectic(
        code, timeout_per_logical=tpl, total_timeout=100000, early_stop=None, verbose=False)
    stab = (np.array(code.matrix, dtype=np.uint8) % 2)
    logicals = (np.asarray(get_symplectic_logicals(code), dtype=np.uint8) % 2)
    num_logicals = logicals.shape[0]
    d_best = code.num_qudits
    n_opt = 0
    n_ret = 0
    for i in range(num_logicals):
        _cid, _li, w, opt, _st = milp_logical_worker((0, i, stab, logicals[i], tpl))
        n_ret += 1
        if w is not None:
            d_best = min(d_best, w)
            if opt:
                n_opt += 1
    par_exact = (n_ret == num_logicals and n_opt == num_logicals)
    _check(d_best == d_seq, f"distance equal (parallel={d_best}, sequential={d_seq})")
    _check(par_exact == bool(det.get("exact")),
           f"exact flag equal (parallel={par_exact}, sequential={det.get('exact')})")


def test_assemble_bposd() -> None:
    print("[3] assemble_bposd_result keys + formula")
    ell, m, A, B, C, D = SMALL_63
    code = build_pbb_code(ell, m, A, B, C, D)
    n, k = code.num_qudits, code.dimension
    base = {"ell": ell, "m": m, "n": n, "k": k, "A_terms": A, "B_terms": B,
            "C_terms": C, "D_terms": D, "encoding_rate": k / n}
    # milp_worked=True: d_best = min(d_milp, d_bp)
    r = assemble_bposd_result(base, d_milp=8, d_bp=10, milp_worked=True, time_s=1.2)
    exp_d = 8
    _check(r["d"] == exp_d and r["d_method"] == "milp+bposd", f"d/method (d={r['d']})")
    _check(r["d_milp"] == 8 and r["d_bposd"] == 10 and r["milp_exact"] is False, "milp/bposd fields")
    _check(r["trust_level"] == _trust_level(exp_d, n, exact=False), "trust label matches vendored")
    _check(abs(r["fom"] - round(k * exp_d * exp_d / n, 2)) < 1e-9, "fom = k*d^2/n rounded")
    # milp_worked=False: pure BP-OSD, d_milp dropped to None
    r2 = assemble_bposd_result(base, d_milp=n, d_bp=7, milp_worked=False, time_s=0.5)
    _check(r2["d"] == 7 and r2["d_method"] == "bposd" and r2["d_milp"] is None, "pure-bposd shape")
    expected_keys = set(base.keys()) | {
        "d", "d_method", "d_milp", "d_bposd", "milp_exact", "d_is_exact",
        "d_is_upper_bound", "trust_level", "d_over_sqrtn", "fom", "time_s"}
    _check(set(r.keys()) == expected_keys, f"key set complete ({expected_keys - set(r.keys())} missing)")


def test_run_distance_plumbing() -> None:
    print("[4] _run_distance: one row/code, scorer keys, hash codes == distance_worker")
    import time
    nw = int(os.environ.get("PBB_NUM_WORKERS", "3"))
    tasks = [
        (*SEED_7E, 1000),    # hash-EXACT [[72,12,6]]
        (*SMALL_63, 1000),   # hash-EXACT small
    ]
    deadline = time.monotonic() + 600
    results = evaluate._run_distance(tasks, nw, 590.0, deadline)
    _check(len(results) == len(tasks), f"one row per code ({len(results)}/{len(tasks)})")
    _check(all(SCORER_KEYS <= set(r.keys()) for r in results), "every row has all scorer keys")
    # the [[72,12,6]] row must match distance_worker exactly (hash path)
    by_n = {r["n"]: r for r in results}
    if 72 in by_n:
        dw = distance_worker((*SEED_7E, 1000))
        r = by_n[72]
        diffs = {k: (r.get(k), dw.get(k)) for k in set(r) | set(dw)
                 if k != "time_s" and r.get(k) != dw.get(k)}
        _check(not diffs, f"[[72,12,6]] row == distance_worker (diffs={diffs})")


def test_run_distance_milp_slow() -> None:
    print("[5][slow] _run_distance exercises Phase B (per-logical MILP) on n=360")
    import time
    import initial  # the seed generator (commutativity-valid candidates)
    nw = int(os.environ.get("PBB_NUM_WORKERS", "3"))
    # An n=360 code: hash (max_weight=4) won't certify -> Phase B MILP runs. Source a
    # real, buildable candidate from the seed so commutativity is guaranteed.
    cand = None
    for A, B, C, D in initial.generate_candidates(30, 6):
        try:
            code = build_pbb_code(30, 6, A, B, C, D)
            if code.num_qudits == 360 and code.dimension > 0:
                cand = (30, 6, A, B, C, D, 200)
                break
        except Exception:
            continue
    _check(cand is not None, "found a buildable n=360 seed candidate")
    if cand is None:
        return
    # Shrink the per-logical cap so the test finishes quickly.
    orig = evaluate._per_logical_cap
    evaluate._per_logical_cap = lambda n: 8
    try:
        deadline = time.monotonic() + 400
        results = evaluate._run_distance([cand], nw, 390.0, deadline)
    finally:
        evaluate._per_logical_cap = orig
    _check(len(results) == 1, f"exactly one row ({len(results)})")
    if results:
        r = results[0]
        _check(SCORER_KEYS <= set(r.keys()), "row has all scorer keys")
        _check(r["n"] == 360 and r["d"] > 0, f"n=360 row, d={r.get('d')} method={r.get('d_method')}")


if __name__ == "__main__":
    test_hash_dict_equivalence()
    test_per_logical_equals_sequential()
    test_assemble_bposd()
    test_run_distance_plumbing()
    if os.environ.get("PBB_TEST_SLOW") == "1":
        test_run_distance_milp_slow()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        sys.exit(1)
    print("ALL CHECKS PASSED")

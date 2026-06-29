# === EVALUATOR ORCHESTRATION (NOT vendored science) =====================
# Per-LOGICAL parallel distance orchestration for non-CSS PBB codes.
#
# This module holds the top-level, spawn-picklable STAGE WORKERS and the pure
# result-assembly helpers used by ../evaluate.py's per-logical distance driver
# (``_run_distance``). It changes ONLY the orchestration -- which logical runs
# in which process, the timeouts, and how partial results are aggregated. Every
# piece of *science* (the symplectic ILP ``ilp_min_weight_symplectic``, the hash
# low-weight check ``has_low_weight_logical``, BP-OSD ``estimate_distance_noncss``,
# and the FOM / trust math) is imported UNCHANGED from the vendored backbone
# (``pbb_code``, ``distance_milp``, ``distance_bposd_noncss``,
# ``_noncss_distance_worker``). The vendored files are NOT touched.
#
# The functions live at module top level (NOT in evaluate.py, which
# ``run_shinka_eval`` imports dynamically -- which breaks pickle for functions
# defined there) so a ``spawn`` ProcessPoolExecutor can pickle them by qualified
# name. This is the same reason ``_noncss_distance_worker.py`` exists.
#
# Outcome dicts produced here are BYTE-COMPATIBLE with ``distance_worker``'s:
# the same keys, values and rounding, so ../evaluate.py's scoring + text_feedback
# are untouched. ``_test_parallel_distance.py`` is the equivalence gate.
# =========================================================================
"""Spawn-picklable per-stage workers + assembly helpers for the per-logical
parallel distance pipeline. See module banner for the design contract."""

from __future__ import annotations

import time

import numpy as np


def _base_result(ell, m, n, k, A_terms, B_terms, C_terms, D_terms) -> dict:
    """The 9 identity keys every distance result dict carries (matches
    ``distance_worker``'s ``base_result``, lines ~62-67)."""
    return {
        "ell": ell, "m": m, "n": n, "k": k,
        "A_terms": A_terms, "B_terms": B_terms,
        "C_terms": C_terms, "D_terms": D_terms,
        "encoding_rate": k / n if n > 0 else 0.0,
    }


# --- Stage workers (run in spawn child processes) -------------------------

def hash_stage_worker(task: tuple):
    """Per-CODE Stage 1 (hash low-weight check). Byte-faithful to
    ``distance_worker`` Stage 1 (lines ~69-93).

    ``task = (code_id, ell, m, A, B, C, D)``. Returns one of:
      ``("EXACT", code_id, result_dict)``      -- hash found d<=6 (n<=216) / d<=4 (n>216)
      ``("NEEDS_MILP", code_id, base, stab_u8, logicals_u8)`` -- ship arrays to the MILP phase
      ``("ERROR", code_id, None)``             -- build/hash failure; the driver drops the code
    """
    code_id, ell, m, A, B, C, D = task
    try:
        from qcode_eval.pbb_code import build_pbb_code as _build, get_pbb_params_fast as _params
        from qcode_eval.pbb_code import get_symplectic_logicals as _logs
        from qcode_eval.distance_bposd_noncss import has_low_weight_logical as _lwl

        t0 = time.time()
        code = _build(ell, m, A, B, C, D)
        n, k = _params(code)
        base = _base_result(ell, m, n, k, A, B, C, D)

        max_weight = 6 if n <= 216 else 4
        found, d_exact = _lwl(code, max_weight=max_weight)
        if found:
            elapsed = time.time() - t0
            fom = 0.0 if d_exact <= 4 else (k * d_exact * d_exact / n if n > 0 else 0.0)
            return ("EXACT", code_id, {
                **base,
                "d": d_exact, "d_method": f"exact_w{d_exact}",
                "d_is_exact": True, "d_is_upper_bound": False,
                "trust_level": "EXACT",
                "d_over_sqrtn": round(d_exact / (n ** 0.5), 3) if n > 0 else 0.0,
                "fom": round(fom, 2), "time_s": round(elapsed, 1),
            })

        # Survivor: ship the (picklable) numpy arrays once. The driver fans these
        # out per logical; QuditCode itself is not picklable. dtype uint8 keeps
        # IPC small; the worker casts back to int (byte-identical to
        # compute_distance_milp_symplectic's np.array(..., dtype=int) % 2).
        stab = np.array(code.matrix, dtype=np.uint8) % 2
        logicals = np.asarray(_logs(code), dtype=np.uint8) % 2
        return ("NEEDS_MILP", code_id, base, stab, logicals)
    except Exception:
        return ("ERROR", code_id, None)


def milp_logical_worker(task: tuple):
    """Per-LOGICAL Stage 2: solve ONE logical's symplectic ILP, unchanged.

    ``task = (code_id, logical_idx, stab_u8, logical_row_u8, timeout_s)``.
    Returns ``(code_id, logical_idx, weight_or_None, optimal_bool, solve_seconds)``.
    ``solve_seconds`` lets the driver charge the owning code a CPU-time budget (sum of
    its logicals' solve-times) instead of wall-clock. The arrays are cast to ``int``
    exactly as ``compute_distance_milp_symplectic`` passes them, so the solve is
    byte-identical to the sequential path.
    """
    code_id, logical_idx, stab_u8, logical_row_u8, timeout_s = task
    try:
        from qcode_eval.distance_milp import ilp_min_weight_symplectic as _ilp

        stab = np.asarray(stab_u8, dtype=int)
        lrow = np.asarray(logical_row_u8, dtype=int)
        t0 = time.monotonic()
        w, optimal = _ilp(stab, lrow, timeout=timeout_s)
        solve_s = time.monotonic() - t0
        return (code_id, logical_idx, (None if w is None else int(w)), bool(optimal),
                round(solve_s, 3))
    except Exception:
        # A single failed logical is a valid "no solution" (w=None); it makes the
        # code non-exact but never sinks the batch (matches the sequential
        # ilp_min_weight_symplectic returning (None, False)).
        return (code_id, logical_idx, None, False, 0.0)


def bposd_stage_worker(task: tuple):
    """Per-CODE Stage 3 (BP-OSD fallback). Byte-faithful to ``distance_worker``
    Stage 3 (lines ~142-168).

    ``task = (code_id, ell, m, A, B, C, D, num_trials, d_milp, milp_worked)``.
    Returns ``(code_id, result_dict_or_None)``. Rebuilds the code (BP-OSD needs
    the QuditCode, which is not picklable). Runs in a SEPARATE pool from the MILP
    stage so an ldpc segfault cannot poison in-flight MILP work.
    """
    code_id, ell, m, A, B, C, D, num_trials, d_milp, milp_worked = task
    try:
        from qcode_eval.pbb_code import build_pbb_code as _build, get_pbb_params_fast as _params
        from qcode_eval.distance_bposd_noncss import estimate_distance_noncss as _est

        t0 = time.time()
        code = _build(ell, m, A, B, C, D)
        n, k = _params(code)
        base = _base_result(ell, m, n, k, A, B, C, D)

        d_bp1 = _est(code, num_trials=num_trials, seed=42)
        d_bp2 = _est(code, num_trials=max(num_trials // 2, 100), seed=137)
        d_bp = min(d_bp1, d_bp2)
        elapsed = time.time() - t0
        return (code_id, assemble_bposd_result(base, d_milp, d_bp, milp_worked, elapsed))
    except Exception:
        return (code_id, None)


# --- Pure result-assembly helpers (driver-side; unit-tested vs distance_worker) ---

def assemble_milp_exact_result(base: dict, d_best: int, time_s: float) -> dict:
    """All ``num_logicals`` logicals proven optimal -> EXACT. Byte-identical to
    ``distance_worker`` lines ~130-140 (there ``d_best == d_milp``)."""
    n = base["n"]; k = base["k"]
    return {
        **base,
        "d": d_best, "d_method": "milp_exact",
        "d_milp": d_best, "d_bposd": None,
        "milp_exact": True,
        "d_is_exact": True, "d_is_upper_bound": False,
        "trust_level": "EXACT",
        "d_over_sqrtn": round(d_best / (n ** 0.5), 3) if n > 0 else 0.0,
        "fom": round(k * d_best * d_best / n if n > 0 else 0.0, 2),
        "time_s": round(time_s, 1),
    }


def assemble_reject_result(base: dict, d_best: int, time_s: float) -> dict:
    """A logical proved symplectic weight <= 4 -> d <= 4 -> reject. Routed through
    the SAME ``fom=0`` EXACT shape a hash ``d<=4`` result uses, so the trust label
    cannot diverge. DORMANT in practice: the hash pre-filter guarantees any
    MILP-stage code already has d>=7 (n<=216) or d>=5 (n>216), so a coset weight
    <=4 cannot appear here -- this is a correct, cheap robustness hedge."""
    n = base["n"]
    return {
        **base,
        "d": d_best, "d_method": f"exact_w{d_best}",
        "d_is_exact": True, "d_is_upper_bound": False,
        "trust_level": "EXACT",
        "d_over_sqrtn": round(d_best / (n ** 0.5), 3) if n > 0 else 0.0,
        "fom": 0.0, "time_s": round(time_s, 1),
    }


def assemble_bposd_result(base: dict, d_milp: int, d_bp: int,
                          milp_worked: bool, time_s: float) -> dict:
    """Non-exact MILP -> BP-OSD fallback. Byte-identical to ``distance_worker``
    lines ~142-168."""
    from qcode_eval._noncss_distance_worker import _trust_level

    n = base["n"]; k = base["k"]
    d_best = min(d_milp, d_bp) if milp_worked else d_bp
    fom = k * d_best * d_best / n if d_best > 0 and n > 0 else 0.0
    method = "milp+bposd" if milp_worked else "bposd"
    return {
        **base,
        "d": d_best, "d_method": method,
        "d_milp": d_milp if milp_worked else None,
        "d_bposd": d_bp,
        "milp_exact": False,
        "d_is_exact": False,
        "d_is_upper_bound": True,
        "trust_level": _trust_level(d_best, n, exact=False),
        "d_over_sqrtn": round(d_best / (n ** 0.5), 3) if n > 0 else 0.0,
        "fom": round(fom, 2), "time_s": round(time_s, 1),
    }

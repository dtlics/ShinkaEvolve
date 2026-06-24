"""Evaluator for the gamma_depth_synth task.

Drives a deterministic benchmark: for each grid size L in L_RANGE, call the
candidate's `build_gamma(L)` to obtain a circuit (a list of gate tuples) that
implements the diagonal phase operator Gamma on the bare L x L qubit grid, then
enforce three correctness gates and measure 2-qubit-gate depth.

The candidate emits a dependency-free gate list. Each gate is one of:
    ("CX", (r0, c0), (r1, c1))   # CNOT, control=(r0,c0) target=(r1,c1)
    ("CZ", (r0, c0), (r1, c1))   # CZ (symmetric)
    ("Z",  (r,  c))              # single-qubit Z (FREE in the depth metric)
Qubits are grid sites (r, c) with 0 <= r, c < L. There are NO ancillas: every
qubit must be one of the L*L grid sites, and every 2-qubit gate must act on a
nearest-neighbour pair (|dr| + |dc| == 1).

WHAT GAMMA MUST BE (the correctness spec — condition (star), the parity-encoding
condition). Gamma is a diagonal operator Gamma|s> = (-1)^{f(s)}|s>. Built from
CX/CZ/Z it is automatically a degree-<=2 GF(2) phase polynomial f. It is a valid
Gamma iff, for every VERTICAL grid-neighbour pair (r,c)<->(r+1,c) with snake
Jordan-Wigner indices j<k, and every basis state s with s_j != s_k:

    f(s) XOR f(s with both flipped) == XOR of s_l over snake-sites l strictly
                                       between j and k.                  (star)

(star) is geometry-only and is EXACTLY the condition that makes
Gamma * FSWAP_bare * Gamma = FSWAP_full for each vertical pair (Jiang et al.
arXiv:1711.04789; ancilla-free construction in the source repo). Horizontal
neighbours are snake-adjacent and need no correction, so Gamma only constrains
vertical pairs.

Because f is degree <= 2, (star) reduces to EXACT algebraic constraints on f's
coefficients (verified here against brute force over all 2^N states for small L):
with f(s) = XOR_i a_i s_i  XOR  XOR_{i<j} b_ij s_i s_j, for every vertical pair
(qubits qa, qb in row-major indexing) with between-set B:
    (C1)  a_qa == a_qb
    (C2)  for every l not in {qa, qb}:  b_{qa,l} XOR b_{qb,l} == 1[l in B]
The valid f form an AFFINE SPACE: any one particular solution (e.g. the seed's
phase polynomial) plus the homogeneous null-space of the (C1)/(C2) system. That
space is large — e.g. a constant can be added to the linear part a uniformly
across any column, and (C2) has a nontrivial null-space — so a DIFFERENT valid f
may be far cheaper to realise. (b_{qa,qb} drops out of pair (qa,qb)'s own (C2),
but it couples through the adjacent pairs sharing qa or qb, so it is not
independently free.) Any f in this space is a correct Gamma, not just the seed's
— this is the freedom HOPPS / the 4L construction exploit.

Score (PREFACTOR metric, mirrors cnot_grid_synth): the depth of Gamma scales as
c*L + O(1); the objective is to lower the prefactor c (baseline c = 8).
    score = max(0, sum_L (D_base(L) - D_cand(L)) / sum_L L)
i.e. the L-weighted average reduction in the per-L prefactor c_hat(L) =
depth(L)/L across grid sizes (larger L weighted more, where the prefactor's
power shows). The per-L baseline depths D_base(L) come from a FROZEN copy of the
8L+O(1) pipelined construction cached in `_baseline_cache.json`; the seed in
initial.py is that same construction -> score == 0 by construction. A 4L
construction scores ~ +4; a 2L (HOPPS-style) construction scores ~ +6. Scoring
ABSOLUTE depth-per-L at every size (not the depth-vs-L slope) cannot be gamed by
inflating small-L depth: padding any size only raises its depth and lowers the
score. The OLS slope + R^2 are kept as diagnostics only.

Three hard correctness gates (any failure -> combined_score = 0, correct = False):
  Gate 1 (format/adjacency): only CX/CZ/Z gates; every qubit a grid site;
                             every 2q gate nearest-neighbour; no ancillas.
  Gate 2 (diagonal): the net CNOT linear map is the identity (Gamma is diagonal).
  Gate 3 (parity-encoding): condition (star) holds via (C1)/(C2), checked
                            EXACTLY (deterministically) for every vertical pair.

Depth measurement (only after all gates pass): ASAP layering of the 2-qubit
gates respecting the emitted order and qubit exclusion; single-qubit Z gates are
free (do not consume a layer). This is the optimal depth for the emitted gate
sequence, so a candidate minimises depth only by emitting a shorter critical
path -- it cannot be gamed by moment bookkeeping.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from typing import Any, Optional

import numpy as np

from shinka.core import run_shinka_eval


# --- Benchmark constants (frozen; do not depend on candidate) ---------------

L_RANGE: list[int] = list(range(3, 11))           # L = 3..10, n = 9..100
# Defensive bounds (env-overridable so the orchestrator can retune the budget).
PER_TRIAL_TIMEOUT_S: float = float(os.environ.get("GAMMA_PER_TRIAL_TIMEOUT_S", "120"))
EVAL_WALLCLOCK_BUDGET_S: float = float(os.environ.get("GAMMA_EVAL_WALLCLOCK_BUDGET_S", str(20 * 60)))
# A valid Gamma needs O(N) gates; cap the emitted gate count at a generous
# polynomial bound so a pathological candidate cannot blow up the oracle.
MAX_GATES_PER_N: int = int(os.environ.get("GAMMA_MAX_GATES_PER_N", "200"))
BASELINE_CACHE_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_baseline_cache.json"
)


# ---------------------------------------------------------------------------
# Frozen geometry: snake (Jordan-Wigner) ordering on the L x L grid.
# (Defense-in-depth: never imported from initial.py.)
# ---------------------------------------------------------------------------

def _rc_to_raster(r: int, c: int, L: int) -> int:
    return r * L + c


def _rc_to_snake(r: int, c: int, L: int) -> int:
    return r * L + c if r % 2 == 0 else r * L + (L - 1 - c)


def _snake_to_rc(idx: int, L: int) -> tuple[int, int]:
    r = idx // L
    p = idx % L
    c = p if r % 2 == 0 else L - 1 - p
    return r, c


def _between_raster(r: int, c: int, L: int) -> list[int]:
    """Row-major indices strictly between vertical neighbours (r,c),(r+1,c) in
    snake order — the parity string of condition (star)."""
    j = _rc_to_snake(r, c, L)
    k = _rc_to_snake(r + 1, c, L)
    lo, hi = (j, k) if j < k else (k, j)
    out = []
    for idx in range(lo + 1, hi):
        sr, sc = _snake_to_rc(idx, L)
        out.append(_rc_to_raster(sr, sc, L))
    return out


def _vertical_pairs(L: int):
    for r in range(L - 1):
        for c in range(L):
            yield (r, c, r + 1, c)


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

def _validate_gates(ops: Any, L: int) -> tuple[bool, Optional[str]]:
    """Gate 1: only CX/CZ/Z, every qubit a grid site, every 2q gate NN, no
    ancillas, no self-loops, gate count bounded."""
    if not isinstance(ops, (list, tuple)):
        return False, f"build_gamma must return a list of gates, got {type(ops).__name__}"
    n = L * L
    if len(ops) > MAX_GATES_PER_N * n:
        return False, f"gate count {len(ops)} exceeds bound {MAX_GATES_PER_N}*n={MAX_GATES_PER_N * n}"

    def _site_ok(q) -> bool:
        return (
            isinstance(q, (tuple, list)) and len(q) == 2
            and isinstance(q[0], (int, np.integer)) and isinstance(q[1], (int, np.integer))
            and 0 <= q[0] < L and 0 <= q[1] < L
        )

    for g in ops:
        if not isinstance(g, (tuple, list)) or len(g) < 2:
            return False, f"malformed gate {g!r}"
        kind = g[0]
        if kind == "Z":
            if len(g) != 2 or not _site_ok(g[1]):
                return False, f"bad Z gate {g!r}"
        elif kind in ("CX", "CZ"):
            if len(g) != 3:
                return False, f"bad {kind} gate {g!r}"
            qa, qb = g[1], g[2]
            if not _site_ok(qa) or not _site_ok(qb):
                return False, f"{kind} qubit off-grid {g!r}"
            if tuple(qa) == tuple(qb):
                return False, f"{kind} self-loop {g!r}"
            if abs(qa[0] - qb[0]) + abs(qa[1] - qb[1]) != 1:
                return False, f"{kind} not nearest-neighbour {g!r}"
        else:
            return False, f"unknown gate type {kind!r} in {g!r}"
    return True, None


def _extract(ops: Any, L: int):
    """Single symbolic pass over the gate list. Returns (is_diagonal, a, Bsym):
      - is_diagonal: net CNOT linear map == identity (Gamma is diagonal).
      - a: length-N GF(2) linear coefficients of f.
      - Bsym: N x N symmetric GF(2) quadratic coefficients (Bsym[i,j]=b_ij).
    f(s) = XOR_i a_i s_i  XOR  XOR_{i<j} Bsym_ij s_i s_j  (f is degree <= 2 since
    every gate is CX/CZ/Z). Validated against direct per-state simulation and
    against brute-force (star) for small L in the task's scratch checks."""
    n = L * L
    V = np.eye(n, dtype=np.int8)        # V[q] = linear form of qubit q over inputs
    a = np.zeros(n, dtype=np.int8)
    B = np.zeros((n, n), dtype=np.int8)  # upper triangle accumulator
    for g in ops:
        kind = g[0]
        if kind == "CX":
            ci = _rc_to_raster(int(g[1][0]), int(g[1][1]), L)
            ti = _rc_to_raster(int(g[2][0]), int(g[2][1]), L)
            V[ti] ^= V[ci]
        elif kind == "CZ":
            ai = _rc_to_raster(int(g[1][0]), int(g[1][1]), L)
            bi = _rc_to_raster(int(g[2][0]), int(g[2][1]), L)
            va = V[ai]; vb = V[bi]
            outer = np.outer(va, vb) & 1
            a ^= (va & vb)                      # diagonal x_i^2 = x_i -> linear
            M = (outer ^ outer.T) & 1           # symmetric off-diagonal
            B ^= np.triu(M, k=1)
        elif kind == "Z":
            ai = _rc_to_raster(int(g[1][0]), int(g[1][1]), L)
            a ^= V[ai]
    is_diagonal = bool(np.array_equal(V, np.eye(n, dtype=np.int8)))
    Bsym = (B ^ B.T) & 1
    return is_diagonal, a, Bsym


def _check_star(a: np.ndarray, Bsym: np.ndarray, L: int) -> tuple[bool, Optional[str]]:
    """Gate 3: condition (star) via (C1) a_qa==a_qb and
    (C2) b_{qa,l} XOR b_{qb,l} == 1[l in between] for every vertical pair."""
    n = L * L
    for (r1, c1, r2, c2) in _vertical_pairs(L):
        qa = _rc_to_raster(r1, c1, L)
        qb = _rc_to_raster(r2, c2, L)
        if a[qa] != a[qb]:
            return False, f"(C1) a mismatch at vertical pair ({r1},{c1})-({r2},{c2})"
        between = np.zeros(n, dtype=np.int8)
        for l in _between_raster(r1, c1, L):
            between[l] = 1
        got = (Bsym[qa] ^ Bsym[qb]) & 1
        for l in range(n):
            if l == qa or l == qb:
                continue
            if int(got[l]) != int(between[l]):
                return False, (
                    f"(C2) parity-string mismatch at vertical pair "
                    f"({r1},{c1})-({r2},{c2}), qubit l={l}"
                )
    return True, None


def _asap_2q_depth(ops: Any, L: int) -> int:
    """2q-gate depth: ASAP layering in emitted order with qubit exclusion;
    single-qubit Z gates are free. Optimal depth for the emitted sequence."""
    last: dict[tuple[int, int], int] = {}
    depth = 0
    for g in ops:
        if g[0] in ("CX", "CZ"):
            qa = (int(g[1][0]), int(g[1][1]))
            qb = (int(g[2][0]), int(g[2][1]))
            layer = max(last.get(qa, 0), last.get(qb, 0)) + 1
            last[qa] = layer
            last[qb] = layer
            if layer > depth:
                depth = layer
    return depth


def _evaluate_circuit(ops: Any, L: int) -> tuple[Optional[int], Optional[str]]:
    """Full per-L oracle. Returns (depth, None) on success or (None, reason)."""
    ok, why = _validate_gates(ops, L)
    if not ok:
        return None, f"gate/adjacency: {why}"
    is_diag, a, Bsym = _extract(ops, L)
    if not is_diag:
        return None, "not diagonal: net CNOT linear map is not the identity"
    ok, why = _check_star(a, Bsym, L)
    if not ok:
        return None, f"parity-encoding (star): {why}"
    return _asap_2q_depth(ops, L), None


# ---------------------------------------------------------------------------
# Frozen baseline: the 8L+O(1) pipelined ancilla-free Gamma. IMMUTABLE — the LLM
# cannot mutate this copy; the seed in initial.py is identical, so it ties the
# baseline (score 0). (Ported from common/gamma_pipeline.py of the source repo.)
# ---------------------------------------------------------------------------

def _col_parity_cascade(L: int, inverse: bool = False):
    ops = []
    if not inverse:
        for r in range(L - 2, -1, -1):
            for c in range(L):
                ops.append(("CX", (r + 1, c), (r, c)))
    else:
        for r in range(L - 1):
            for c in range(L):
                ops.append(("CX", (r + 1, c), (r, c)))
    return ops


def _prefix_cascade(r: int, L: int):
    return [("CX", (r, c - 1), (r, c)) for c in range(1, L)]


def _undo_prefix_cascade(r: int, L: int):
    return [("CX", (r, c - 1), (r, c)) for c in range(L - 1, 0, -1)]


def _same_row_T_prefix(r: int, L: int):
    ops = _prefix_cascade(r, L)
    ops += [("CZ", (r, c), (r, c + 1)) for c in range(L - 1)]
    ops += _undo_prefix_cascade(r, L)
    ops += [("Z", (r, p)) for p in range(L - 1) if (L - 1 - p) % 2 == 1]
    return ops


def _pipeline_same_cross(r: int, L: int):
    ops = []
    r2 = r + 1
    for tau in range(L - 2 + 4 + 1):
        if 0 <= tau <= L - 2:
            ops.append(("CX", (r, tau), (r, tau + 1)))
        c = tau - 2
        if 0 <= c < L:
            ops.append(("CZ", (r, c), (r2, c)))
        c_lo, c_hi = tau - 4, tau - 3
        if 0 <= c_lo and c_hi < L:
            ops.append(("CZ", (r, c_lo), (r, c_hi)))
    for tau in range(L - 2, -1 - 3 - 1, -1):
        if 0 <= tau <= L - 2:
            ops.append(("CX", (r, tau), (r, tau + 1)))
        c = tau + 2
        if 0 <= c < L:
            ops.append(("CZ", (r, c), (r2, c)))
        c = tau + 3
        if 0 <= c < L and (L - 1 - c) % 2 == 1:
            ops.append(("Z", (r, c)))
    return ops


def _pipeline_same_skip(r: int, L: int):
    ops = []
    r_mid, r2 = r + 1, r + 2
    for tau in range(L - 2 + 6 + 1):
        if 0 <= tau <= L - 2:
            ops.append(("CX", (r, tau), (r, tau + 1)))
        c = tau - 1
        if 0 <= c < L:
            ops.append(("CZ", (r_mid, c), (r2, c)))
        c = tau - 2
        if 0 <= c < L:
            ops.append(("CX", (r, c), (r_mid, c)))
        c = tau - 3
        if 0 <= c < L:
            ops.append(("CZ", (r_mid, c), (r2, c)))
        c = tau - 4
        if 0 <= c < L:
            ops.append(("CX", (r, c), (r_mid, c)))
        c_lo, c_hi = tau - 6, tau - 5
        if 0 <= c_lo and c_hi < L:
            ops.append(("CZ", (r, c_lo), (r, c_hi)))
    for tau in range(L - 2, -1 - 5 - 1, -1):
        if 0 <= tau <= L - 2:
            ops.append(("CX", (r, tau), (r, tau + 1)))
        c = tau + 1
        if 0 <= c < L:
            ops.append(("CZ", (r_mid, c), (r2, c)))
        c = tau + 2
        if 0 <= c < L:
            ops.append(("CX", (r, c), (r_mid, c)))
        c = tau + 3
        if 0 <= c < L:
            ops.append(("CZ", (r_mid, c), (r2, c)))
        c = tau + 4
        if 0 <= c < L:
            ops.append(("CX", (r, c), (r_mid, c)))
        c = tau + 5
        if 0 <= c < L and (L - 1 - c) % 2 == 1:
            ops.append(("Z", (r, c)))
    return ops


def _pipeline_skip_only(r: int, L: int):
    ops = []
    r_mid, r2 = r + 1, r + 2
    for tau in range(L - 1 + 4 + 1):
        if 0 <= tau <= L - 2:
            ops.append(("CX", (r, tau), (r, tau + 1)))
        c = tau - 1
        if 0 <= c < L:
            ops.append(("CZ", (r_mid, c), (r2, c)))
        c = tau - 2
        if 0 <= c < L:
            ops.append(("CX", (r, c), (r_mid, c)))
        c = tau - 3
        if 0 <= c < L:
            ops.append(("CZ", (r_mid, c), (r2, c)))
        c = tau - 4
        if 0 <= c < L:
            ops.append(("CX", (r, c), (r_mid, c)))
    for tau in range(L - 2, -1 - 4 - 1, -1):
        if 0 <= tau <= L - 2:
            ops.append(("CX", (r, tau), (r, tau + 1)))
        c = tau + 1
        if 0 <= c < L:
            ops.append(("CZ", (r_mid, c), (r2, c)))
        c = tau + 2
        if 0 <= c < L:
            ops.append(("CX", (r, c), (r_mid, c)))
        c = tau + 3
        if 0 <= c < L:
            ops.append(("CZ", (r_mid, c), (r2, c)))
        c = tau + 4
        if 0 <= c < L:
            ops.append(("CX", (r, c), (r_mid, c)))
    return ops


def _baseline_pipelined_gamma(L: int):
    """Frozen 8L+O(1) ancilla-free pipelined Gamma (the reference)."""
    ops = list(_col_parity_cascade(L, inverse=False))
    fused_rows = [r for r in range(2, L, 2) if r + 2 <= L - 1]
    skip_only = [0] if 2 <= L - 1 else []
    same_only_parity = [r for r in range(2, L, 2) if r + 2 > L - 1]

    batch1_fused = [r for r in fused_rows if r % 4 == 0]
    batch1_skip = [r for r in skip_only if r % 4 == 0]
    for r in batch1_fused:
        ops += _pipeline_same_skip(r, L)
    for r in batch1_skip:
        ops += _pipeline_skip_only(r, L)
    b1_rows = set()
    for r in batch1_fused:
        b1_rows.update([r, r + 1, r + 2])
    for r in batch1_skip:
        b1_rows.update([r, r + 1, r + 2])
    b1_same_done = []
    for r in same_only_parity:
        if r not in b1_rows:
            ops += _same_row_T_prefix(r, L)
            b1_same_done.append(r)

    batch2_fused = [r for r in fused_rows if r % 4 == 2]
    batch2_skip = [r for r in skip_only if r % 4 == 2]
    for r in batch2_fused:
        ops += _pipeline_same_skip(r, L)
    for r in batch2_skip:
        ops += _pipeline_skip_only(r, L)
    b2_rows = set()
    for r in batch2_fused:
        b2_rows.update([r, r + 1, r + 2])
    for r in batch2_skip:
        b2_rows.update([r, r + 1, r + 2])
    for r in same_only_parity:
        if r not in b1_same_done and r not in b2_rows:
            ops += _same_row_T_prefix(r, L)

    ops += _col_parity_cascade(L, inverse=True)
    for r in range(0, L - 1, 2):
        ops += _pipeline_same_cross(r, L)
    if L % 2 == 1:
        ops += _same_row_T_prefix(L - 1, L)
    return ops


# --- Per-trial timeout (POSIX SIGALRM; Windows threading fallback) -----------

class _TrialTimeout(Exception):
    pass


_HAVE_SIGALRM = hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")


def _alarm_handler(signum, frame):  # pragma: no cover (signal callback)
    raise _TrialTimeout("trial timed out")


def _call_builder_with_timeout(builder, L, timeout_s):
    if _HAVE_SIGALRM:
        old = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, float(timeout_s))
        try:
            return builder(L)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, old)
    box: dict = {}

    def _worker():
        try:
            box["value"] = builder(L)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(float(timeout_s))
    if t.is_alive():
        raise _TrialTimeout(f"trial timed out > {timeout_s}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _run_trial(builder, L: int, timeout_s: float) -> tuple[Optional[int], Optional[str]]:
    try:
        ops = _call_builder_with_timeout(builder, L, timeout_s)
    except _TrialTimeout:
        return None, f"timeout > {timeout_s}s"
    except Exception as e:  # noqa: BLE001
        return None, f"exception: {e!r}"
    return _evaluate_circuit(ops, L)


# --- Baseline auto-cache ----------------------------------------------------

def _load_or_compute_baseline() -> dict:
    if os.path.exists(BASELINE_CACHE_PATH):
        with open(BASELINE_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)

    print("[evaluate] Computing frozen pipelined-Gamma baseline (one-time)…")
    t0 = time.perf_counter()
    per_L: dict[str, int] = {}
    for L in L_RANGE:
        ops = _baseline_pipelined_gamma(L)
        depth, err = _evaluate_circuit(ops, L)
        assert depth is not None, (
            f"FROZEN baseline failed its own oracle at L={L}: {err}. "
            "Bug in the reference construction — aborting cache write."
        )
        per_L[str(L)] = int(depth)
        print(f"[evaluate]   baseline L={L}: depth={depth} c_hat={depth / L:.3f}")

    cache = {
        "L_range": L_RANGE,
        "per_L_depth": per_L,
        "note": "Frozen 8L+O(1) ancilla-free pipelined Gamma; seed ties this -> score 0.",
    }
    with open(BASELINE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    print(f"[evaluate] Baseline cached → {BASELINE_CACHE_PATH} (took {time.perf_counter() - t0:.1f}s)")
    return cache


# --- Aggregator -------------------------------------------------------------

def aggregate_fn(results: list) -> dict[str, Any]:
    if not results:
        return {
            "combined_score": 0.0, "correct": False,
            "public": {"error": "run_experiment returned no result"},
            "private": {}, "extra_data": {},
            "text_feedback": "run_experiment returned no result",
        }
    builder = results[0]
    if not callable(builder):
        return {
            "combined_score": 0.0, "correct": False,
            "public": {"error": f"run_experiment must return a callable; got {type(builder).__name__}"},
            "private": {}, "extra_data": {},
            "text_feedback": f"run_experiment returned {type(builder).__name__}, expected a callable build_gamma(L)",
        }

    baseline = _load_or_compute_baseline()
    failures: list[tuple[int, str]] = []
    per_L: dict[int, dict[str, Any]] = {}
    eval_started = time.monotonic()
    early_abort: str | None = None

    for L in L_RANGE:
        if early_abort is not None:
            break
        elapsed = time.monotonic() - eval_started
        if elapsed > EVAL_WALLCLOCK_BUDGET_S:
            early_abort = f"eval wallclock {elapsed:.0f}s exceeded budget {EVAL_WALLCLOCK_BUDGET_S:.0f}s (stopped at L={L})"
            break
        depth, err = _run_trial(builder, L, PER_TRIAL_TIMEOUT_S)
        if depth is None:
            failures.append((L, err))
            continue
        per_L[L] = {"depth": int(depth), "c_hat": float(depth) / L}

    if failures or early_abort is not None:
        public = {
            "n_failures": len(failures),
            "first_failures": [{"L": L, "reason": why} for L, why in failures[:5]],
            "per_L_ok": {L: per_L[L] for L in per_L},
        }
        feedback = (
            f"{len(failures)} grid size(s) invalid. First reasons: "
            + "; ".join(f"L={L}: {why}" for L, why in failures[:5])
        )
        if early_abort is not None:
            public["early_abort"] = early_abort
            feedback = f"EARLY ABORT: {early_abort}. " + feedback
        return {
            "combined_score": 0.0, "correct": False,
            "public": public,
            "private": {"all_failures": [{"L": L, "reason": why} for L, why in failures]},
            "extra_data": {},
            "text_feedback": feedback,
        }

    Ls = np.array(L_RANGE, dtype=float)
    depths = np.array([per_L[L]["depth"] for L in L_RANGE], dtype=float)
    base_depths = np.array([baseline["per_L_depth"][str(L)] for L in L_RANGE], dtype=float)

    # PREFACTOR metric: L-weighted total depth saved per unit L. score == 0 ties
    # the baseline; ~+4 for a 4L construction, ~+6 for a 2L one. Floored at 0.
    score = max(0.0, float((base_depths - depths).sum() / Ls.sum()))

    # Diagnostics only (NOT the objective): the depth-vs-L line + fit quality.
    A_lin = np.vstack([Ls, np.ones_like(Ls)]).T
    slope, intercept = np.linalg.lstsq(A_lin, depths, rcond=None)[0]
    resid = depths - (slope * Ls + intercept)
    sse = float(np.sum(resid ** 2))
    sst = float(np.sum((depths - depths.mean()) ** 2)) if len(depths) > 1 else 1.0
    r_squared = 1.0 - sse / sst if sst > 0 else 1.0

    cand_chat = {L: float(per_L[L]["c_hat"]) for L in L_RANGE}
    base_chat = {L: float(base_depths[i] / L) for i, L in enumerate(L_RANGE)}

    return {
        "combined_score": float(score),
        "correct": True,
        "public": {
            "prefactor_score": float(score),
            "implied_prefactor_slope": float(slope),
            "baseline_prefactor_slope": float(
                (base_depths[-1] - base_depths[0]) / (Ls[-1] - Ls[0])
            ),
            "intercept": float(intercept),
            "r_squared": float(r_squared),
            "per_L_depth": {L: per_L[L]["depth"] for L in L_RANGE},
            "per_L_c_hat": {L: round(cand_chat[L], 3) for L in L_RANGE},
        },
        "private": {
            "baseline_per_L_depth": {L: int(base_depths[i]) for i, L in enumerate(L_RANGE)},
        },
        "extra_data": {},
        "text_feedback": (
            f"OBJECTIVE = lower Gamma's 2q-depth prefactor c (depth ~ c*L+O(1); baseline c=8) at "
            f"EVERY grid size, larger L weighted more. score={score:+.4f} (L-weighted depth-per-L "
            f"saved vs baseline; seed=0, higher is better; ~+4 means ~4L, ~+6 means ~2L). "
            f"implied slope={slope:.2f}, R2={r_squared:.3f} (diagnostic; a clean construction fits "
            f"~1.0). Per-L depth/L (yours/baseline; lower is better, especially at large L): "
            + "; ".join(f"L={L}:{cand_chat[L]:.2f}/{base_chat[L]:.2f}" for L in L_RANGE)
        ),
    }


# --- Main entry -------------------------------------------------------------

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
    if "public" in metrics and isinstance(metrics["public"], dict):
        for k, v in metrics["public"].items():
            if k in ("per_L_depth", "per_L_c_hat"):
                continue
            print(f"  public.{k} = {v!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gamma_depth_synth evaluator")
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)

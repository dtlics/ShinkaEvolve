"""Low-depth ancilla-free Gamma synthesis on a 2D L x L qubit grid.

The function `build_gamma(L)` (inside the EVOLVE-BLOCK below) is what evolution
mutates. Everything outside the EVOLVE-BLOCK is fixed scaffolding shared by every
candidate (grid geometry + a self-test helper).

PROBLEM
  Gamma is the diagonal "parity-correction" operator that turns a bare lattice
  vertical SWAP into a true fermionic SWAP on a snake (Jordan-Wigner) ordered
  L x L grid (Jiang et al. arXiv:1711.04789). It is a diagonal Clifford,
  Gamma|s> = (-1)^{f(s)}|s>, and must be built with ONLY nearest-neighbour
  CX/CZ/Z gates on the bare grid -- ZERO ancilla qubits. The goal is to MINIMISE
  its 2-qubit-gate depth. Known constructions:
      8L + O(1)   ancilla-free pipelined (the seed below; the source paper)
      4L + O(1)   a sharper construction
      2L + O(1)   a SAT-solver search (HOPPS), demonstrated for L <= 5
  Evolution's target: drive the depth prefactor c (depth ~ c*L) below 8 toward 4,
  then 2, with a construction that GENERALISES across L.

OUTPUT CONTRACT
  `build_gamma(L)` returns a flat Python list of gate tuples:
      ("CX", (r0, c0), (r1, c1))   # CNOT, control=(r0,c0) target=(r1,c1)
      ("CZ", (r0, c0), (r1, c1))   # CZ (symmetric)
      ("Z",  (r,  c))              # single-qubit Z -- FREE in the depth metric
  Qubits are grid sites (r, c), 0 <= r, c < L. No ancillas. Every 2-qubit gate
  must be nearest-neighbour: |r0-r1| + |c0-c1| == 1.

CORRECTNESS (condition (star), the parity-encoding condition)
  Since every gate is CX/CZ/Z, f is automatically a degree-<=2 GF(2) polynomial
      f(s) = XOR_i a_i s_i  XOR  XOR_{i<j} b_ij s_i s_j.
  Gamma is a VALID parity correction iff, for every VERTICAL neighbour pair
  (r,c)<->(r+1,c) with snake indices j<k, and every basis state with s_j != s_k:
      f(s) XOR f(s with both flipped) == XOR of s_l over snake-sites l strictly
                                         between j and k.
  Equivalently (the form the evaluator checks, exactly, for every vertical pair
  with row-major qubit indices qa, qb and between-set B):
      (C1) a_qa == a_qb
      (C2) for every l not in {qa, qb}:  b_{qa,l} XOR b_{qb,l} == 1[l in B]
  Horizontal neighbours are snake-adjacent and need no correction. ANY f
  satisfying (C1)/(C2) for every vertical pair is a correct Gamma -- the valid f
  form an affine space (one particular solution, e.g. the seed's, plus the
  homogeneous null-space of the (C1)/(C2) system). That freedom is large (e.g. a
  constant added to a uniformly across a whole column is free, and (C2) has a
  nontrivial null-space), and a DIFFERENT valid f may be far cheaper to realise.
  (The cross term b_{qa,qb} drops out of pair (qa,qb)'s own (C2) but couples
  through the adjacent pairs sharing qa or qb, so it is not independently free.)
  You are NOT required to reproduce the seed's phase polynomial; this freedom is
  exactly what the 4L / 2L constructions exploit.

SCORE
  depth(L) ~ c*L + O(1); score rewards lowering the prefactor c at every L,
  L-weighted (larger L matters more):
      score = max(0, sum_L (baseline_depth(L) - your_depth(L)) / sum_L L)
  The seed ties the 8L+O(1) baseline -> score 0. ~+4 means you reached ~4L; ~+6
  means ~2L. Padding small L does NOT help (it only raises your depth).

LEVERS (roughly increasing difficulty)
  1. Pipeline harder. The seed fuses same-row T with cross/skip-row T into shared
     cascade sweeps. More terms can trail one cascade wavefront at distinct
     column offsets; tighten the drain tails; overlap the four phases.
  2. Pick a CHEAPER valid f. (C1)/(C2) leave large gauge freedom: a per-column
     constant added to the linear part a is free, and the homogeneous solutions
     of the (C1)/(C2) system form a linear space. A well-chosen f in that space
     may need shorter cascades / fewer CZ layers.
  3. Drop the column-parity basis change. The seed spends two L-1 cascades
     (Phases 1 & 3) entering/leaving the column-parity basis to realise the
     skip-row terms. A construction that avoids the basis change, or amortises it
     into the interaction sweeps, could save ~2L of depth outright.
  4. Use vertical bandwidth. The grid has both horizontal and vertical NN edges;
     the seed cascades horizontally and interacts vertically. A 2D-native sweep
     (e.g. diagonal wavefronts) may shorten the critical path.
  5. Per-L search (HOPPS-style). For each L, search directly over short gate
     layers that satisfy (C1)/(C2) -- e.g. a greedy/annealed/ILP layer builder --
     and emit the shortest found. Generalise the pattern it finds across L.
"""

from __future__ import annotations

import numpy as np


# === Fixed scaffolding (NOT evolved) =========================================

def rc_to_raster(r: int, c: int, L: int) -> int:
    """Row-major index of grid site (r, c)."""
    return r * L + c


def rc_to_snake(r: int, c: int, L: int) -> int:
    """Snake (Jordan-Wigner) index: left->right on even rows, right->left on odd."""
    return r * L + c if r % 2 == 0 else r * L + (L - 1 - c)


def snake_to_rc(idx: int, L: int) -> tuple[int, int]:
    """Inverse of rc_to_snake."""
    r = idx // L
    p = idx % L
    return (r, p) if r % 2 == 0 else (r, L - 1 - p)


def snake_sites_between(r: int, c: int, L: int) -> list[int]:
    """Row-major indices strictly between vertical neighbours (r,c),(r+1,c) in
    snake order -- the parity string those two qubits must pick up."""
    j = rc_to_snake(r, c, L)
    k = rc_to_snake(r + 1, c, L)
    lo, hi = (j, k) if j < k else (k, j)
    return [rc_to_raster(*snake_to_rc(idx, L), L) for idx in range(lo + 1, hi)]


def check_valid_gamma(ops: list, L: int) -> tuple[bool, str]:
    """Self-test mirroring the evaluator's oracle (the spec is public; use it to
    verify a candidate before returning). Returns (ok, reason). Checks the gate
    set / NN adjacency, that Gamma is diagonal, and condition (star) via
    (C1)/(C2) on the extracted degree-2 phase coefficients."""
    n = L * L
    V = np.eye(n, dtype=np.int8)
    a = np.zeros(n, dtype=np.int8)
    B = np.zeros((n, n), dtype=np.int8)
    for g in ops:
        kind = g[0]
        if kind in ("CX", "CZ"):
            (r0, c0), (r1, c1) = g[1], g[2]
            if not (0 <= r0 < L and 0 <= c0 < L and 0 <= r1 < L and 0 <= c1 < L):
                return False, f"{kind} off-grid {g}"
            if abs(r0 - r1) + abs(c0 - c1) != 1:
                return False, f"{kind} not nearest-neighbour {g}"
            i0, i1 = rc_to_raster(r0, c0, L), rc_to_raster(r1, c1, L)
            if kind == "CX":
                V[i1] ^= V[i0]
            else:
                outer = np.outer(V[i0], V[i1]) & 1
                a ^= (V[i0] & V[i1])
                B ^= np.triu((outer ^ outer.T) & 1, k=1)
        elif kind == "Z":
            (r, c) = g[1]
            if not (0 <= r < L and 0 <= c < L):
                return False, f"Z off-grid {g}"
            a ^= V[rc_to_raster(r, c, L)]
        else:
            return False, f"unknown gate {g}"
    if not np.array_equal(V, np.eye(n, dtype=np.int8)):
        return False, "not diagonal (net CNOT map != identity)"
    Bsym = (B ^ B.T) & 1
    for r in range(L - 1):
        for c in range(L):
            qa, qb = rc_to_raster(r, c, L), rc_to_raster(r + 1, c, L)
            if a[qa] != a[qb]:
                return False, f"(C1) fail at vertical pair ({r},{c})-({r+1},{c})"
            want = np.zeros(n, dtype=np.int8)
            for l in snake_sites_between(r, c, L):
                want[l] = 1
            got = (Bsym[qa] ^ Bsym[qb]) & 1
            for l in range(n):
                if l in (qa, qb):
                    continue
                if int(got[l]) != int(want[l]):
                    return False, f"(C2) fail vertical pair ({r},{c})-({r+1},{c}) l={l}"
    return True, "valid"


# === EVOLVE-BLOCK-START ======================================================
# Seed: the ancilla-free PIPELINED Gamma (depth 8L + O(1)). Ported from the
# source paper's best ancilla-free construction. It fuses same-row T(x,x) with
# cross-row / skip-row T(x,y) terms into shared prefix-cascade sweeps; the
# interaction gates trail the cascade wavefront at fixed column offsets.
#
# Structure (4 phases):
#   Phase 1: column-parity cascade forward            depth  L-1
#   Phase 2: parity-basis interactions f_B (2 batches) depth ~4L + O(1)
#   Phase 3: column-parity cascade inverse            depth  L-1
#   Phase 4: original-basis interactions f_D          depth ~2L + O(1)
# Total ~ 8L + O(1). See the module docstring LEVERS for how to go below 8.

def _col_parity_cascade(L, inverse=False):
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


def _prefix_cascade(r, L):
    return [("CX", (r, c - 1), (r, c)) for c in range(1, L)]


def _undo_prefix_cascade(r, L):
    return [("CX", (r, c - 1), (r, c)) for c in range(L - 1, 0, -1)]


def _same_row_T(r, L):
    """Same-row T(x,x): prefix cascade, CZ ladder, undo, Z corrections. Depth ~2L."""
    ops = _prefix_cascade(r, L)
    ops += [("CZ", (r, c), (r, c + 1)) for c in range(L - 1)]
    ops += _undo_prefix_cascade(r, L)
    ops += [("Z", (r, p)) for p in range(L - 1) if (L - 1 - p) % 2 == 1]
    return ops


def _pipeline_same_cross(r, L):
    """Fuse same-row T(x,x) + cross-row T(x,y) for rows r, r+1. Depth ~2L."""
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


def _pipeline_same_skip(r, L):
    """Fuse same-row T + skip-row T for rows r, r+1, r+2 (r+1 is routing). Depth ~2L."""
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


def _pipeline_skip_only(r, L):
    """Skip-row T only (row 0 in parity basis: f_B has no same-row T for r=0)."""
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


def build_gamma(L: int) -> list:
    """Build the ancilla-free pipelined Gamma circuit (depth 8L + O(1)).

    Returns a flat list of ("CX"/"CZ", (r,c), (r,c)) and ("Z", (r,c)) gates on
    the bare L x L grid. This seed ties the baseline (score 0); see the module
    docstring LEVERS to push the depth prefactor below 8."""
    ops = list(_col_parity_cascade(L, inverse=False))                 # Phase 1

    # Phase 2: parity-basis interactions f_B, two batches on disjoint row triples.
    fused_rows = [r for r in range(2, L, 2) if r + 2 <= L - 1]
    skip_only = [0] if 2 <= L - 1 else []
    same_only = [r for r in range(2, L, 2) if r + 2 > L - 1]

    b1_fused = [r for r in fused_rows if r % 4 == 0]
    b1_skip = [r for r in skip_only if r % 4 == 0]
    for r in b1_fused:
        ops += _pipeline_same_skip(r, L)
    for r in b1_skip:
        ops += _pipeline_skip_only(r, L)
    b1_rows = set()
    for r in b1_fused + b1_skip:
        b1_rows.update([r, r + 1, r + 2])
    b1_same_done = []
    for r in same_only:
        if r not in b1_rows:
            ops += _same_row_T(r, L)
            b1_same_done.append(r)

    b2_fused = [r for r in fused_rows if r % 4 == 2]
    b2_skip = [r for r in skip_only if r % 4 == 2]
    for r in b2_fused:
        ops += _pipeline_same_skip(r, L)
    for r in b2_skip:
        ops += _pipeline_skip_only(r, L)
    b2_rows = set()
    for r in b2_fused + b2_skip:
        b2_rows.update([r, r + 1, r + 2])
    for r in same_only:
        if r not in b1_same_done and r not in b2_rows:
            ops += _same_row_T(r, L)

    ops += _col_parity_cascade(L, inverse=True)                       # Phase 3

    for r in range(0, L - 1, 2):                                      # Phase 4
        ops += _pipeline_same_cross(r, L)
    if L % 2 == 1:
        ops += _same_row_T(L - 1, L)
    return ops

# === EVOLVE-BLOCK-END ========================================================


# === Fixed entry point called by evaluate.py (NOT evolved) ===================

def run_experiment():
    """Return the Gamma builder so evaluate.py can drive the L benchmark loop.
    shinka's run_shinka_eval calls this once (num_runs=1); aggregate_metrics_fn
    receives [build_gamma] and runs the full grid sweep itself."""
    return build_gamma

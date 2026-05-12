"""CNOT-equivalent linear-function synthesis on a 2D L×L square-grid topology.

The function `synthesize_cnot_grid(matrix, L)` (inside the EVOLVE-BLOCK below)
is what evolution mutates. Everything outside the EVOLVE-BLOCK is fixed
scaffolding shared by every candidate.

Problem statement:
  Given an invertible binary matrix M ∈ GL(n, F_2) where n = L², emit a
  Clifford circuit on n qubits whose action on every computational basis
  state |x⟩ ∈ {0,1}ⁿ is exactly |Mx⟩.

Allowed gates (the harness rejects any candidate that violates these):
  - any single-qubit Clifford gate (h, s, sdg, x, y, z, id, sx, sxdg) —
    these are FREE in the depth metric, applied to any qubit at any time.
  - any 2-qubit Clifford gate (cx, cz, swap, iswap, dcx, ecr, …) — allowed
    ONLY between grid-adjacent qubit pairs (4-neighbours in row-major
    indexing: qubit i = row*L + col).
  - non-Clifford gates (T, RX(θ), RZZ(θ), …) and 3+-qubit gates (Toffoli,
    Fredkin, …) are FORBIDDEN — auto-fail.

Score: CX-only depth, computed AFTER transpile to {cx, u3} at
optimization_level=0. So SWAP transparently costs depth 3, CZ costs depth 1
(decomposes to H·CX·H), iSWAP ~2, etc. The score formula rewards
reductions in CX-depth slope (mean depth vs n=L²) across L = 2..10, 30
random matrices each.

Mental model: row operations over F_2.
  Applying CX(c, t) to a running n×n matrix A (initially the identity)
  performs A[t] ^= A[c]. After applying all gates in order, A must equal M.
  Equivalently: any sequence of CX gates on adjacent qubits implements the
  same linear function as the corresponding sequence of row XORs in F_2.
"""

import numpy as np
from qiskit import QuantumCircuit


# === Fixed scaffolding (NOT evolved) =========================================

def grid_neighbours(L: int) -> set[tuple[int, int]]:
    """Set of directed grid edges (i, j): qubit pairs adjacent in the L×L grid
    under row-major indexing. Both (i, j) and (j, i) are included — edges are
    treated as undirected by the adjacency check."""
    edges: set[tuple[int, int]] = set()
    for r in range(L):
        for c in range(L):
            i = r * L + c
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < L and 0 <= cc < L:
                    edges.add((i, rr * L + cc))
    return edges


def snake_permutation(L: int) -> list[int]:
    """A Hamiltonian path through the L×L grid: snake left→right on even
    rows, right→left on odd rows. Returns a list `perm` of length n=L²
    where consecutive entries `perm[k]` and `perm[k+1]` are always
    4-adjacent in the grid.

    This is one specific embedding of a 1D line into the 2D grid. It uses
    only L*(L-1) of the grid's 2*L*(L-1) edges — all horizontal edges and
    one vertical "elbow" between each pair of rows. The other L*(L-1) - L
    vertical edges are unused by this embedding but are still grid-adjacent
    and can be used directly without going through the snake. Whether to
    keep this snake or replace it with a different embedding (a Hilbert
    curve, a row-block scan, or no 1D embedding at all) is one of the most
    consequential algorithmic choices a candidate can make."""
    perm: list[int] = []
    for r in range(L):
        cols = range(L) if r % 2 == 0 else range(L - 1, -1, -1)
        perm.extend(r * L + c for c in cols)
    return perm


def apply_cx_to_identity(cx_list: list[tuple[int, int]], n: int) -> np.ndarray:
    """Self-test helper: apply a sequence of CX(c, t) gates (each doing
    A[t] ^= A[c]) to the n×n identity matrix and return the result. Useful
    inside the evolve block for verifying correctness of pure-CX
    constructions before returning the QuantumCircuit. The harness does its
    own (stronger) verification via Clifford tableau equality."""
    A = np.eye(n, dtype=bool)
    for c, t in cx_list:
        A[t] ^= A[c]
    return A


# === EVOLVE-BLOCK-START ======================================================
# Hand-coded Kutin-Moulton-Smithline (KMS) LNN CNOT synthesis, ported from
# Qiskit's Rust accelerator (crates/synthesis/src/linear/lnn.rs) so that
# every step is visible and mutable.
#
# KMS guarantees CX-only depth ≤ 5n on a 1D line. Empirically on a 2D grid
# via the snake-path lift, mean depth/n is ≈ 4.5. This is the baseline the
# harness scores against; the seed below ties the baseline by construction.
#
# Roadmap to improvement (ordered by likely difficulty):
#   1. Use the L*(L-1) - L vertical "rung" edges of the grid that the snake
#      embedding skips. They are grid-adjacent (free under the adjacency
#      check) but never used by snake-LNN-KMS — every row operation that
#      crosses a snake fold currently routes the long way around. A direct
#      rung CX would be a constant saving per such operation.
#   2. Schedule independent operations into the same depth layer. KMS's
#      even/odd parity sweep already parallelizes some swaps; a custom
#      scheduler aware of *all* commuting pairs (not just within a parity
#      class) can do better.
#   3. Exploit free single-qubit Cliffords: H-conjugation flips CX
#      direction, so right-vs-left direction is free; H·CX·H = CZ permits
#      tableau-rebasing tricks; phase gates change stabilizer roles.
#   4. Recognize special-structure matrices (sparse / banded /
#      low-permutation-distance) and short-circuit the full KMS pipeline.
#   5. Block-style elimination: process windows of columns together when
#      the matrix has redundant column subsets within the window.
#   6. Replace KMS entirely. The grid has 2L(L-1) edges vs the line's
#      L²-1 — substantially higher edge bandwidth — so a fundamentally
#      2D-native algorithm is plausible.

def _row_op(mat: np.ndarray, ctrl: int, trgt: int) -> None:
    """In-place row XOR: row[trgt] ^= row[ctrl]. Mirrors the linear-algebra
    effect of applying CX(ctrl, trgt) to a running matrix."""
    mat[trgt] ^= mat[ctrl]


def _col_op(mat: np.ndarray, ctrl: int, trgt: int) -> None:
    """In-place column XOR: col[trgt] ^= col[ctrl]."""
    mat[:, trgt] ^= mat[:, ctrl]


def _calc_inverse_matrix_f2(mat: np.ndarray) -> np.ndarray:
    """Inverse of an invertible boolean matrix over F_2 via Gauss-Jordan."""
    n = mat.shape[0]
    A = np.hstack([mat.astype(bool, copy=True), np.eye(n, dtype=bool)])
    for col in range(n):
        # find pivot row
        pivot = -1
        for r in range(col, n):
            if A[r, col]:
                pivot = r
                break
        if pivot < 0:
            raise ValueError("matrix not invertible over F_2")
        if pivot != col:
            A[[col, pivot]] = A[[pivot, col]]
        # eliminate the column on every other row
        for r in range(n):
            if r != col and A[r, col]:
                A[r] ^= A[col]
    return A[:, n:].copy()


def _get_lower_triangular(n: int, mat: np.ndarray, mat_inv: np.ndarray):
    """KMS preprocessing (Prop. 7.3 of arXiv:quant-ph/0701194).

    Apply a sequence of column ops to `mat` (a private copy) so it becomes
    a permuted-lower-triangular matrix `mat_t`. Each column op is the
    inverse of a row op on `mat_inv`, so we update `mat_inv` accordingly,
    yielding `mat_inv_t`. The CX instructions discovered here are NOT
    emitted as gates — they are scaffolding for the bubble-sort phase
    that follows. Returns (mat_t, mat_inv_t)."""
    mat = mat.copy()
    mat_t = mat.copy()
    cx_instructions_rows: list[tuple[int, int]] = []

    # For each row from last to first: find the rightmost 1 in that row,
    # then use column ops (rightward) to zero out every other 1 in that row,
    # leaving a single rightmost 1. Then use row ops upward to zero out the
    # 1s above that pivot in the same column.
    for i in range(n - 1, -1, -1):
        cols_to_update = [j for j in range(n - 1, -1, -1) if mat[i, j]]
        first_j = cols_to_update[0]
        for j in cols_to_update[1:]:
            _col_op(mat, first_j, j)

        rows_to_update = [k for k in range(i - 1, -1, -1) if mat[k, first_j]]
        for k in rows_to_update:
            cx_instructions_rows.append((i, k))
            _row_op(mat, i, k)

    # Replay U-instructions on mat_t (forward row ops) and on mat_inv
    # (as inverted column ops, i.e. col[ctrl] ^= col[trgt] — note swapped args).
    for ctrl, trgt in cx_instructions_rows:
        _row_op(mat_t, ctrl, trgt)
        _col_op(mat_inv, trgt, ctrl)
    return mat_t, mat_inv


def _get_label_arr(n: int, mat_t: np.ndarray) -> list[int]:
    """For each row i of mat_t, return the column index of the *last*
    (rightmost) 1 in that row, counted from the right — i.e. the number
    of zeros after the last 1. If the row is all zeros, return n."""
    label_arr = []
    for i in range(n):
        idx = n  # all-zeros sentinel
        for j in range(n):
            if mat_t[i, n - 1 - j]:
                idx = j
                break
        label_arr.append(idx)
    return label_arr


def _get_label_arr_t(n: int, label_arr: list[int]) -> list[int]:
    """Inverse permutation: label_arr_t[label_arr[i]] = i."""
    label_arr_t = [0] * n
    for i in range(n):
        label_arr_t[label_arr[i]] = i
    return label_arr_t


def _in_linear_combination(label_arr_t, mat_inv_t, row, k):
    """Test: is `row` a linear combination of all rows of mat_inv_t
    EXCEPT the row labelled by k? Returns True/False.
    Implementation: build w = XOR of mat_inv_t[l] for l ∈ {1s of row}, and
    check whether w[label_arr_t[k]] == 0 (which means the k-th label was
    NOT consumed)."""
    n = len(row)
    w_needed = np.zeros(n, dtype=bool)
    for row_l in range(n):
        if row[row_l]:
            w_needed ^= mat_inv_t[row_l]
    return not bool(w_needed[label_arr_t[k]])


def _matrix_to_north_west(n, mat, mat_inv):
    """KMS phase A — Prop. 7.3.

    Transform an arbitrary invertible matrix into "north-west" triangular
    form (1s only on or above the anti-diagonal) using alternating-parity
    layers of adjacent CX gates that bubble-sort labels.

    Each iteration alternates between operating on (0,1), (2,3), (4,5)…
    pairs and (1,2), (3,4), (5,6)… pairs. Within a layer, every pair
    (i, i+1) with label_arr[i] > label_arr[i+1] gets swapped — but the CX
    direction depends on a linear-combination test against the inverse,
    chosen to keep `mat` consistent with the swap. Ends after two empty
    layers in a row (nothing left to swap)."""
    mat_t, mat_inv_t = _get_lower_triangular(n, mat, mat_inv)
    label_arr = _get_label_arr(n, mat_t)
    label_arr_t = _get_label_arr_t(n, label_arr)

    cx_instructions_rows: list[tuple[int, int]] = []
    first_qubit = 0
    empty_layers = 0
    while True:
        at_least_one_needed = False
        for i in range(first_qubit, n - 1, 2):
            if label_arr[i] > label_arr[i + 1]:
                at_least_one_needed = True
                row_sum = mat[i] ^ mat[i + 1]
                if _in_linear_combination(label_arr_t, mat_inv_t, mat[i + 1], label_arr[i + 1]):
                    pass  # no CX needed
                elif _in_linear_combination(label_arr_t, mat_inv_t, row_sum, label_arr[i + 1]):
                    cx_instructions_rows.append((i, i + 1))
                    _row_op(mat, i, i + 1)
                elif _in_linear_combination(label_arr_t, mat_inv_t, mat[i], label_arr[i + 1]):
                    cx_instructions_rows.append((i + 1, i))
                    _row_op(mat, i + 1, i)
                    cx_instructions_rows.append((i, i + 1))
                    _row_op(mat, i, i + 1)
                label_arr[i], label_arr[i + 1] = label_arr[i + 1], label_arr[i]

        if not at_least_one_needed:
            empty_layers += 1
            if empty_layers > 1:
                break
        else:
            empty_layers = 0
        first_qubit = 1 - first_qubit

    return cx_instructions_rows


def _north_west_to_identity(n, mat):
    """KMS phase B — Prop. 7.4.

    A north-west matrix has labels in reverse order. Bubble them back to
    sorted with adjacent SWAP-like blocks: each "swap" is either 2 CXes
    (when row i already has the right bit at position label[i+1]) or 3
    CXes (full SWAP). Total depth ≤ 3n."""
    label_arr = list(range(n - 1, -1, -1))
    cx_instructions_rows: list[tuple[int, int]] = []
    first_qubit = 0
    empty_layers = 0
    while True:
        at_least_one_needed = False
        for i in range(first_qubit, n - 1, 2):
            if label_arr[i] > label_arr[i + 1]:
                at_least_one_needed = True
                if not mat[i, label_arr[i + 1]]:
                    # 3-CX full SWAP: the leading row op turns the next two
                    # CXes (CX(i,i+1), CX(i+1,i)) into a swap between rows.
                    cx_instructions_rows.append((i + 1, i))
                    _row_op(mat, i + 1, i)
                cx_instructions_rows.append((i, i + 1))
                _row_op(mat, i, i + 1)
                cx_instructions_rows.append((i + 1, i))
                _row_op(mat, i + 1, i)
                label_arr[i], label_arr[i + 1] = label_arr[i + 1], label_arr[i]

        if not at_least_one_needed:
            empty_layers += 1
            if empty_layers > 1:
                break
        else:
            empty_layers = 0
        first_qubit = 1 - first_qubit

    return cx_instructions_rows


def synthesize_cnot_grid(matrix: np.ndarray, L: int) -> QuantumCircuit:
    """Synthesize a Clifford circuit C on n=L² qubits with C|x⟩ = |Mx⟩.

    Strategy (the seed):
      1. Embed the 2D grid into a 1D line via the snake path. Adjacent
         positions in the snake are 4-adjacent in the grid, so any LNN
         circuit in snake-coords lifts directly to a grid-valid circuit.
      2. KMS synthesis (arXiv:quant-ph/0701194 §7) is applied to the
         INVERSE of the snake-permuted target matrix — KMS's two-phase
         decomposition (Prop. 7.3 + Prop. 7.4) builds the inverse via
         depth ≤ 5n CX gates between adjacent line positions.
      3. Translate each LNN CX(c, t) (c, t consecutive integers) to
         CX(perm[c], perm[t]) on grid qubits and emit into the circuit.

    The whole pipeline (LNN synthesis + helpers) is in this evolve block,
    so every step is mutable. The roadmap comments at the top of the
    block list specific levers — the most fundamental one being to stop
    routing every operation through the snake and start using the grid's
    "rung" edges directly."""
    n = L * L
    if matrix.shape != (n, n):
        raise ValueError(f"matrix must be {n}x{n} for L={L}, got {matrix.shape}")

    perm = snake_permutation(L)
    # M_snake[i, j] = matrix[perm[i], perm[j]] — relabel to snake basis.
    M_snake = matrix[np.ix_(perm, perm)].astype(bool, copy=True)

    # KMS synthesizes the inverse, so we compute both and operate on a copy.
    mat_inv = M_snake.copy()
    mat_cpy = _calc_inverse_matrix_f2(mat_inv)

    # Phase A: arbitrary invertible → north-west triangular (depth ≤ 2n).
    instr_a = _matrix_to_north_west(n, mat_cpy, mat_inv)
    # Phase B: north-west → identity (depth ≤ 3n).
    instr_b = _north_west_to_identity(n, mat_cpy)

    # Concatenate; gates are in the order they should be applied to identity
    # to construct M_snake⁻¹⁻¹ = M_snake (KMS's convention handles the
    # double-inverse internally — the recorded sequence implements M_snake).
    cx_list_lnn = instr_a + instr_b

    # Lift LNN gates into grid coordinates via the snake permutation.
    qc = QuantumCircuit(n)
    for ctrl_lnn, trgt_lnn in cx_list_lnn:
        qc.cx(perm[ctrl_lnn], perm[trgt_lnn])
    return qc

# === EVOLVE-BLOCK-END ========================================================


# === Fixed entry point called by evaluate.py (NOT evolved) ===================

def run_experiment():
    """Returns the synthesis function so evaluate.py can drive the (L, M)
    benchmark loop deterministically. shinka's run_shinka_eval calls this
    once with no kwargs (num_runs=1); aggregate_metrics_fn receives
    [synthesize_cnot_grid] and runs the full grid sweep itself."""
    return synthesize_cnot_grid

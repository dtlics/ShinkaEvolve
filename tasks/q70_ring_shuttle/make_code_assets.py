"""One-time generator + verifier for the Q70 code assets (qecc/q70.json).

Q70 = [[70,6,9]] BB7 code from the Walking Cat paper (arXiv:2604.19481, App. C):
    l = 7, m = 5,  x = S_7 (x) I_5,  y = I_7 (x) S_5
    A = y^2 + x^2 + x^3 + x^4      (A1..A4 in printed order)
    B = y   + x   + x^3            (B1..B3 in printed order)
    Hx = [A | B],  Hz = [B^T | A^T]
Published schedule permutation (Table X):
    ((B1,B3^T),(A1,A3^T),(A4,A2^T),(B2,B2^T),(A3,A1^T),(A2,A4^T),(B3,B1^T))

Run:  python make_code_assets.py        (writes qecc/q70.json, prints checks)
"""

import json
import os
import numpy as np

L, M = 7, 5
N_HALF = L * M          # 35
N = 2 * N_HALF          # 70 data qubits

# Monomial exponents (i, j) meaning x^i y^j, i in Z_7, j in Z_5, printed order.
A_EXPS = [(0, 2), (2, 0), (3, 0), (4, 0)]   # A1=y^2, A2=x^2, A3=x^3, A4=x^4
B_EXPS = [(0, 1), (1, 0), (3, 0)]           # B1=y,   B2=x,   B3=x^3

# Published Table X schedule: (X-family, X-index, Z-family, Z-index), 0-based.
SCHEDULE = [
    ("B", 0, "B", 2),
    ("A", 0, "A", 2),
    ("A", 3, "A", 1),
    ("B", 1, "B", 1),
    ("A", 2, "A", 0),
    ("A", 1, "A", 3),
    ("B", 2, "B", 0),
]


def gidx(i, j):
    """Group element (i, j) in Z_7 x Z_5 -> flat index 5*i + j."""
    return (i % L) * M + (j % M)


def monomial_matrix(exp):
    """35x35 permutation matrix of multiplication by x^i y^j on the group algebra.

    Row g has a 1 in column g+exp (i.e. maps indicator of g to g+exp read as
    'check g touches data g+exp', matching Hx rows below).
    """
    a, b = exp
    P = np.zeros((N_HALF, N_HALF), dtype=np.uint8)
    for i in range(L):
        for j in range(M):
            P[gidx(i, j), gidx(i + a, j + b)] = 1
    return P


def poly_matrix(exps):
    Mt = np.zeros((N_HALF, N_HALF), dtype=np.uint8)
    for e in exps:
        Mt ^= monomial_matrix(e)
    return Mt


def rank_gf2(mat):
    mat = mat.copy() % 2
    r = 0
    rows, cols = mat.shape
    for c in range(cols):
        piv = None
        for rr in range(r, rows):
            if mat[rr, c]:
                piv = rr
                break
        if piv is None:
            continue
        mat[[r, piv]] = mat[[piv, r]]
        for rr in range(rows):
            if rr != r and mat[rr, c]:
                mat[rr] ^= mat[r]
        r += 1
    return r


def nullspace_gf2(mat):
    """Basis of right null space over GF(2), as rows of a matrix."""
    mat = mat.copy() % 2
    rows, cols = mat.shape
    aug = mat.copy()
    pivots = []
    r = 0
    for c in range(cols):
        piv = None
        for rr in range(r, rows):
            if aug[rr, c]:
                piv = rr
                break
        if piv is None:
            continue
        aug[[r, piv]] = aug[[piv, r]]
        for rr in range(rows):
            if rr != r and aug[rr, c]:
                aug[rr] ^= aug[r]
        pivots.append(c)
        r += 1
    free = [c for c in range(cols) if c not in pivots]
    basis = []
    for f in free:
        v = np.zeros(cols, dtype=np.uint8)
        v[f] = 1
        for ri, pc in enumerate(pivots):
            if aug[ri, f]:
                v[pc] = 1
        basis.append(v)
    return np.array(basis, dtype=np.uint8) if basis else np.zeros((0, cols), np.uint8)


def in_rowspace(v, mat):
    stacked = np.vstack([mat, v])
    return rank_gf2(stacked) == rank_gf2(mat)


def quotient_reps(null_basis, stab):
    """Rows of null_basis that project to a basis of null/rowspace(stab)."""
    reps = []
    base = stab.copy()
    r0 = rank_gf2(base)
    for v in null_basis:
        stacked = np.vstack([base, v])
        r1 = rank_gf2(stacked)
        if r1 > r0:
            reps.append(v.copy())
            base = stacked
            r0 = r1
    return np.array(reps, dtype=np.uint8)


def inv_gf2(mat):
    n = mat.shape[0]
    aug = np.concatenate([mat.copy() % 2, np.eye(n, dtype=np.uint8)], axis=1)
    r = 0
    for c in range(n):
        piv = None
        for rr in range(r, n):
            if aug[rr, c]:
                piv = rr
                break
        if piv is None:
            raise ValueError("singular")
        aug[[r, piv]] = aug[[piv, r]]
        for rr in range(n):
            if rr != r and aug[rr, c]:
                aug[rr] ^= aug[r]
        r += 1
    return aug[:, n:]


def greedy_min_weight(v, stab_rows, rng, restarts=200, passes=60):
    """Randomized greedy weight reduction of v modulo the row space of stab_rows."""
    best = int(v.sum())
    nrows = stab_rows.shape[0]
    for _ in range(restarts):
        cur = v.copy()
        # random pre-mix
        for ri in rng.choice(nrows, size=rng.integers(0, 6), replace=True):
            cur ^= stab_rows[ri]
        improved = True
        p = 0
        while improved and p < passes:
            improved = False
            p += 1
            order = rng.permutation(nrows)
            w = int(cur.sum())
            for ri in order:
                cand = cur ^ stab_rows[ri]
                cw = int(cand.sum())
                if cw < w:
                    cur = cand
                    w = cw
                    improved = True
        best = min(best, int(cur.sum()))
    return best


def main():
    A = poly_matrix(A_EXPS)
    B = poly_matrix(B_EXPS)
    Hx = np.concatenate([A, B], axis=1)              # 35 x 70
    Hz = np.concatenate([B.T, A.T], axis=1)          # 35 x 70

    # --- checks -----------------------------------------------------------
    comm = (Hx @ Hz.T) % 2
    assert not comm.any(), "Hx Hz^T != 0 (CSS commutation broken)"
    print("[ok] CSS commutation Hx Hz^T = 0")

    rx, rz = rank_gf2(Hx), rank_gf2(Hz)
    k = N - rx - rz
    print(f"[ok] rank(Hx)={rx} rank(Hz)={rz} -> k={k}")
    assert k == 6, f"expected k=6, got {k}"

    # check weights / Tanner degrees
    assert all(int(Hx[r].sum()) == 7 for r in range(N_HALF))
    assert all(int(Hz[r].sum()) == 7 for r in range(N_HALF))
    colx = Hx.sum(axis=0)
    colz = Hz.sum(axis=0)
    print(f"[ok] check weight 7; data-qubit degrees: X {sorted(set(int(c) for c in colx))}, "
          f"Z {sorted(set(int(c) for c in colz))}")

    # --- logical operators ------------------------------------------------
    # Z-type logicals: ker(Hx) / rowspace(Hz); X-type: ker(Hz) / rowspace(Hx)
    kerx = nullspace_gf2(Hx)
    kerz = nullspace_gf2(Hz)
    Lz = quotient_reps(kerx, Hz)
    Lx = quotient_reps(kerz, Hx)
    assert Lz.shape[0] == k and Lx.shape[0] == k, (Lz.shape, Lx.shape)
    Mm = (Lx @ Lz.T) % 2
    Minv = inv_gf2(Mm)
    Lz = (Minv.T @ Lz) % 2  # Lx.(W Lz)^T = M W^T = M M^-1 = I with W = (M^-1)^T
    Mm2 = (Lx @ Lz.T) % 2
    assert (Mm2 == np.eye(k, dtype=np.uint8)).all()
    assert not ((Hx @ Lz.T) % 2).any() and not ((Hz @ Lx.T) % 2).any()
    print(f"[ok] {k} symplectic logical pairs; Lx.Lz^T = I; logicals commute with stabilizers")

    # --- distance probe (d = 9 exact per paper; we verify d <= 9 and probe below) --
    rng = np.random.default_rng(20260726)
    dz_ub = min(greedy_min_weight(v, Hz, rng) for v in Lz)
    dx_ub = min(greedy_min_weight(v, Hx, rng) for v in Lx)
    # also probe random logical combos
    for _ in range(40):
        cz = np.zeros(N, dtype=np.uint8)
        while not cz.any():
            sel = rng.integers(0, 2, size=k)
            cz = (sel @ Lz) % 2
        dz_ub = min(dz_ub, greedy_min_weight(cz, Hz, rng, restarts=60))
    for _ in range(40):
        cx = np.zeros(N, dtype=np.uint8)
        while not cx.any():
            sel = rng.integers(0, 2, size=k)
            cx = (sel @ Lx) % 2
        dx_ub = min(dx_ub, greedy_min_weight(cx, Hx, rng, restarts=60))
    print(f"[ok] randomized distance probe: d_Z <= {dz_ub}, d_X <= {dx_ub} "
          f"(paper: d = 9 exact; probe must not find < 9)")
    assert dz_ub >= 9 and dx_ub >= 9, "probe found logical below weight 9 — construction wrong!"
    assert dz_ub == 9 or dx_ub == 9, "probe did not reach weight 9 — weak probe or wrong code"

    # --- schedule validity + alignment closure ----------------------------
    xa = [t[:2] for t in SCHEDULE]
    za = [t[2:] for t in SCHEDULE]
    assert sorted(xa) == sorted([("A", i) for i in range(4)] + [("B", i) for i in range(3)])
    assert sorted(za) == sorted([("A", i) for i in range(4)] + [("B", i) for i in range(3)])
    for (fx, ix, fz, iz) in SCHEDULE:
        assert fx == fz, "pair mixes A and B families"
    print("[ok] schedule: 7 pairs, same-family pairs, X and Z sides each visit every term once")

    # alignment closure: over the 7 rounds, ancilla g must touch exactly its check support
    exps = {"A": A_EXPS, "B": B_EXPS}
    for g in range(N_HALF):
        i, j = divmod(g, M)
        xtouch, ztouch = set(), set()
        for (fx, ix, fz, iz) in SCHEDULE:
            a1, b1 = exps[fx][ix]
            a2, b2 = exps[fz][iz]
            if fx == "A":
                xtouch.add(gidx(i + a1, j + b1))            # left block
                ztouch.add(N_HALF + gidx(i - a2, j - b2))   # right block
            else:
                xtouch.add(N_HALF + gidx(i + a1, j + b1))   # right block
                ztouch.add(gidx(i - a2, j - b2))            # left block
        assert xtouch == set(np.nonzero(Hx[g])[0]), f"X-check {g} alignment mismatch"
        assert ztouch == set(np.nonzero(Hz[g])[0]), f"Z-check {g} alignment mismatch"
    print("[ok] alignment closure: schedule visits exactly the Tanner supports of every check")

    # --- write assets ------------------------------------------------------
    out = {
        "name": "Q70",
        "family": "BB",
        "n": N, "k": k, "d": 9, "d_circ_published": 9,
        "l": L, "m": M,
        "A_exps": A_EXPS, "B_exps": B_EXPS,
        "schedule": SCHEDULE,
        "hx": [sorted(int(c) for c in np.nonzero(Hx[r])[0]) for r in range(N_HALF)],
        "hz": [sorted(int(c) for c in np.nonzero(Hz[r])[0]) for r in range(N_HALF)],
        "logical_xs": [sorted(int(c) for c in np.nonzero(v)[0]) for v in Lx],
        "logical_zs": [sorted(int(c) for c in np.nonzero(v)[0]) for v in Lz],
        "provenance": "arXiv:2604.19481 App. C (Table XXX) + Table X schedule; generated by make_code_assets.py",
    }
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "qecc"), exist_ok=True)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qecc", "q70.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"[ok] wrote {path}")
    print(f"     logical X weights: {[int(v.sum()) for v in Lx]}")
    print(f"     logical Z weights: {[int(v.sum()) for v in Lz]}")


if __name__ == "__main__":
    main()

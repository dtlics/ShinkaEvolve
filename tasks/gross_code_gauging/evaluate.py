"""ShinkaEvolve EVALUATOR — gauging measurement of the gross-code logical Xalpha.

Faithful encoding of the gross-code worked example in Williamson & Yoder,
"Low-overhead fault-tolerant quantum computation by gauging logical operators"
(arXiv:2410.02213, Appendix B; Nature Physics 22:598, 2026). This file is
SELF-CONTAINED and FIXED (never evolved). It

  (1) builds the [[144,12,12]] gross code and the weight-12 logical Xalpha = X(f,0),
  (2) takes the candidate's extra edges, builds the deformed (gauged) code,
  (3) estimates the deformed-code distance with BP+OSD (an UPPER bound),
  (4) scores the candidate and returns diagnostic feedback.

The candidate (initial.py) evolves ONE thing: `propose_extra_edges()` -> a list
of extra graph edges (pairs of the 12 fixed vertices). Everything that decides
correctness — the code, the 18 forced matching edges, the Av/Bp deformation, the
distance, the score — lives HERE and is computed by this file. The candidate
never supplies a distance or a score.

================  CONSTRUCTION (paper Appendix B; cross-checked verbatim)  =====
  * Gross code: l=12, m=6, A = x^3+y^2+y, B = y^3+x^2+x; HX=[A|B], HZ=[B^T|A^T];
    n=144, k=12, d=12 (the standard IBM/Bravyi gross code).
  * Logical Xalpha (alpha=1) = X(f,0); f = 1+x+x^2+x^3+x^6+x^7+x^8+x^9
    +(x+x^5+x^7+x^11)y^3  (paper Eq. 4) -> the 12 monomials = the graph VERTICES,
    weight 12. (Verified: f commutes with all Z-checks and is not a stabilizer.)
  * Gauging: graph G on the 12 support qubits, one ancilla per EDGE. New X-checks
    A_v = X_v * prod_{e ∋ v} X_e (Gauss's law, one per vertex). New Z-checks =
    each original Z-check routed through the matching ancillas (solve Inc·g = s_V
    over GF(2)) + flux checks B_p = prod_{e ∈ cycle} Z_e (a cycle basis of G).
  * BASE_EDGES = 18 forced matching edges: connect f-monomials g,d that share a
    Z-check (g = B^T_i B_j d). Always added by the evaluator.

================  SCORING (Shinka MAXIMISES combined_score)  ===================
  malformed graph / crash (correct=False)         -> -1000
  valid build, distance d < 12 (TASK FAILED)      -> -100 + d   (in [-92,-89])
  valid build, distance d == 12 (a real solution) -> BASELINE_QUBITS - qubits
A d==12 graph at 24 qubits scores 0; the paper's 22-qubit solution scores +2; each
further qubit saved is +1. The seed (18 base + 6 sparsest-cut-greedy extra = 24
qubits) is a feasibility-VERIFIED d==12 baseline scoring 0; the SAME greedy holds
d==12 at 4 edges (22q, +2), so it is a graph to PRUNE toward the paper and below.
(The originally drafted seed used BLIND lowest-degree greedy, whose 6 edges have
true distance <=10 — a false positive the weak default oracle reported as 12; the
hardened oracle rejects it, which is why the seed now uses SPARSEST-CUT greedy that
reinforces the actual expansion bottleneck.) Any d==12 result outranks any distance
failure, which outranks a malformed graph. The qubit objective is the paper's
headline overhead metric and is basis-independent.

================  THE DISTANCE ORACLE IS AN UPPER BOUND — IT CAN BE GAMED  =====
BP+OSD coset minimisation returns an UPPER bound (it exhibits real logical
operators), so true_d <= reported_d. The only error mode is a FALSE POSITIVE:
reporting 12 when true < 12. A search that maximises (24 - qubits) subject to
reported-d==12 is under direct pressure to find sparse graphs where BP+OSD
OVER-reports — i.e. to reward-hack the oracle. We harden against this in three
layers (none is free; the third is the only true proof):

  (1) ASYMMETRIC BUDGET. The X-distance (dx) is the sole over-report source
      (dz is reliably 12); it gets GAUGE_BUDGET_X_S (default 10s) vs the Z-side's
      GAUGE_BUDGET_Z_S (6s), at osd_order 25 / max_iter 120 — a raised everyday floor.
  (2) VERIFY-BEFORE-ACCEPT. When the default pass returns d>=12 AND
      qubits<=GAUGE_VERIFY_MAX_QUBITS (default 24 = BASELINE_QUBITS, i.e. the whole
      non-negative-score region — every tie/beat of the seed's zero-point, the paper's
      22 included), the X-side is re-decoded much harder (GAUGE_VERIFY_BUDGET_X_S=15s,
      osd_order 30, max_iter 200, min over GAUGE_VERIFY_SEEDS=0,1,2) before the d==12
      verdict is credited. Feasible candidates above 24q score negative, use the
      everyday floor, and self-correct as evolution prunes them below 24q. Multi-seed at
      the *default* budget is useless (the over-report is deterministic bias, not
      noise); only the larger budget here makes the extra seeds pay off. `verified`
      records whether this fired.
  (3) ILP CERTIFICATION (OFF-LINE, the run owner's job). Even a passing verify is
      NOT a proof — some graphs over-report 12 at every BP+OSD budget tested. EXACTLY
      AS IN THE PAPER, certify the distance of any sub-22-qubit winner with integer
      programming before believing or reporting it. The paper's own reference answer
      (4 extra edges -> 22 qubits, score +2) is the gold check; see README.

================  PROCESS ISOLATION IS LOAD-BEARING  ==========================
Any evolve framework executes candidate code, and run_shinka_eval loads the
candidate module IN THIS PROCESS. A candidate's module-level code therefore runs
before scoring and could monkey-patch a module global. We bind the decoder class
to a private name (`_DECODER_CLS`) at import — before any candidate loads — so a
patch of the public `BpOsdDecoder` name does not redirect the oracle; and Shinka's
fresh-process-per-candidate isolation must stay on (a forged global dies with the
process). Do NOT reuse an eval process across candidates, and never import the
candidate into a trusted namespace.

================  MEASURED RUNTIME (Windows shinka env, warm)  =================
  import (numpy+ldpc) + gross-code build : ~0.5-1 s
  build_deformed                         : ~0.003 s
  default distance pass (10s X + 6s Z)   : ~16 s
  verify gate (<=24q d>=12 claims only)  : +~45 s
  => ~16 s per ordinary eval; ~61 s when a candidate reaches the score-0 region.
  Lower GAUGE_BUDGET_X_S/_Z_S to go faster; raise GAUGE_VERIFY_* to shrink
  false-positive risk further. All knobs are env-overridable.
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
import time
import traceback
from typing import Any, Optional

import numpy as np
from ldpc import BpOsdDecoder

from shinka.core import run_shinka_eval

# Private decoder reference, bound at import BEFORE any candidate module is loaded
# in this process (see "PROCESS ISOLATION" above). _coset_min uses this name.
_DECODER_CLS = BpOsdDecoder

# ----------------------------------------------------------------------
# Gross code [[144,12,12]]: l=12, m=6, A=x^3+y^2+y, B=y^3+x^2+x
# ----------------------------------------------------------------------
L, M = 12, 6
N = L * M                       # 72 = #L-qubits = #R-qubits
def _idx(a, b): return (a % L) * M + (b % M)
_A  = [(3, 0), (0, 2), (0, 1)]
_B  = [(0, 3), (2, 0), (1, 0)]
_BT = [((-c) % L, (-d) % M) for c, d in _B]
_AT = [((-c) % L, (-d) % M) for c, d in _A]

def _build_code():
    HX = np.zeros((N, 2 * N), np.int8); HZ = np.zeros((N, 2 * N), np.int8)
    for a in range(L):
        for b in range(M):
            r = _idx(a, b)
            for c, d in _A:  HX[r, _idx(a + c, b + d)] ^= 1
            for c, d in _B:  HX[r, N + _idx(a + c, b + d)] ^= 1
            for c, d in _BT: HZ[r, _idx(a + c, b + d)] ^= 1
            for c, d in _AT: HZ[r, N + _idx(a + c, b + d)] ^= 1
    return HX, HZ
HX0, HZ0 = _build_code()
assert (HX0 @ HZ0.T % 2 == 0).all()

# Logical Xalpha (alpha=1): X(f,0). The 12 monomials of f are the graph VERTICES.
F_TERMS  = [(0,0),(1,0),(2,0),(3,0),(6,0),(7,0),(8,0),(9,0),(1,3),(5,3),(7,3),(11,3)]
VERTICES = [_idx(a, b) for a, b in F_TERMS]
def _base_edges():
    conn = {((ci+cj) % L, (di+dj) % M) for ci, di in _BT for cj, dj in _B} - {(0, 0)}
    fset = set(F_TERMS); E = set()
    for a, b in F_TERMS:
        for cc, dd in conn:
            nb = ((a+cc) % L, (b+dd) % M)
            if nb in fset and nb != (a, b):
                E.add(frozenset((_idx(a, b), _idx(*nb))))
    return sorted(E, key=lambda e: sorted(e))
BASE_EDGES = _base_edges()
assert len(BASE_EDGES) == 18

TARGET_DISTANCE = 12
PAPER_QUBITS    = 22    # paper's reported solution (18 base + 4 extra); NOT proven minimal
BASELINE_QUBITS = 24    # score zero-point: a d==12 graph at 24 qubits scores 0
                        # (the sparsest-cut-greedy seed is 24q -> 0; see initial.py)

# --- Distance-oracle config (env-overridable; see module docstring) ----------
# dx (X-distance) is the sole over-report source; dz is reliably 12. So budget_x > budget_z.
DISTANCE_BUDGET_X_S = float(os.environ.get("GAUGE_BUDGET_X_S", "10.0"))
DISTANCE_BUDGET_Z_S = float(os.environ.get("GAUGE_BUDGET_Z_S", "6.0"))
DISTANCE_OSD_ORDER  = int(os.environ.get("GAUGE_OSD_ORDER", "25"))
DISTANCE_MAX_ITER   = int(os.environ.get("GAUGE_MAX_ITER", "120"))
# Verify-before-accept gate: fires when d>=12 AND qubits<=VERIFY_MAX_QUBITS. The
# default ceiling is BASELINE_QUBITS (24) -> the entire non-negative-score region
# (every candidate that ties/beats the seed's zero-point, including the paper's 22)
# is re-verified, so no forged success can top the archive. Feasible candidates
# ABOVE 24q score negative, use the everyday floor, and self-correct as evolution
# prunes them into the gated region.
VERIFY_BUDGET_X_S   = float(os.environ.get("GAUGE_VERIFY_BUDGET_X_S", "15.0"))
VERIFY_OSD_ORDER    = int(os.environ.get("GAUGE_VERIFY_OSD_ORDER", "30"))
VERIFY_MAX_ITER     = int(os.environ.get("GAUGE_VERIFY_MAX_ITER", "200"))
VERIFY_MAX_QUBITS   = int(os.environ.get("GAUGE_VERIFY_MAX_QUBITS", str(BASELINE_QUBITS)))
VERIFY_SEEDS        = tuple(
    int(s) for s in os.environ.get("GAUGE_VERIFY_SEEDS", "0,1,2").split(",") if s.strip() != ""
) or (0,)

# ----------------------------------------------------------------------
# GF(2) helpers
# ----------------------------------------------------------------------
def _rank(Mx):
    Mx = Mx.copy() % 2; r = 0; rows, cols = Mx.shape
    for c in range(cols):
        piv = next((i for i in range(r, rows) if Mx[i, c]), None)
        if piv is None: continue
        Mx[[r, piv]] = Mx[[piv, r]]
        for i in range(rows):
            if i != r and Mx[i, c]: Mx[i] ^= Mx[r]
        r += 1
        if r == rows: break
    return r

def _solve(A, b):
    A = A.copy() % 2; b = b.copy() % 2; rows, cols = A.shape
    Mx = np.concatenate([A, b.reshape(-1, 1)], 1); where = []; r = 0
    for c in range(cols):
        piv = next((i for i in range(r, rows) if Mx[i, c]), None)
        if piv is None: continue
        Mx[[r, piv]] = Mx[[piv, r]]
        for i in range(rows):
            if i != r and Mx[i, c]: Mx[i] ^= Mx[r]
        where.append(c); r += 1
        if r == rows: break
    if any(Mx[i, -1] for i in range(r, rows)): return None
    x = np.zeros(cols, np.int8)
    for i, c in enumerate(where): x[c] = Mx[i, -1]
    return x

def _nullspace(A):
    A = A.copy() % 2; rows, cols = A.shape; Mx = A.copy(); pc = {}; r = 0
    for c in range(cols):
        piv = next((i for i in range(r, rows) if Mx[i, c]), None)
        if piv is None: continue
        Mx[[r, piv]] = Mx[[piv, r]]
        for i in range(rows):
            if i != r and Mx[i, c]: Mx[i] ^= Mx[r]
        pc[c] = r; r += 1
        if r == rows: break
    free = [c for c in range(cols) if c not in pc]; B = []
    for f in free:
        v = np.zeros(cols, np.int8); v[f] = 1
        for c, rr in pc.items(): v[c] = Mx[rr, f]
        B.append(v)
    return np.array(B, np.int8) if B else np.zeros((0, cols), np.int8)

# ----------------------------------------------------------------------
# Deformed code from an edge set  (distance + qubit count are basis-independent)
# ----------------------------------------------------------------------
def build_deformed(extra_edges):
    extra = []
    for e in extra_edges:
        try: e = frozenset(e)
        except TypeError:
            return None, None, {"valid": False, "reason": f"edge not a pair: {e!r}"}
        if len(e) != 2 or not e <= set(VERTICES):
            return None, None, {"valid": False, "reason": f"bad edge {tuple(e)} "
                                f"(must be 2 distinct vertices from {VERTICES})"}
        extra.append(e)
    edges = list(BASE_EDGES) + extra
    E = len(edges); vpos = {v: i for i, v in enumerate(VERTICES)}
    Inc = np.zeros((len(VERTICES), E), np.int8)
    for j, e in enumerate(edges):
        for v in e: Inc[vpos[v], j] ^= 1
    pad = lambda H: np.concatenate([H, np.zeros((H.shape[0], E), np.int8)], 1)
    Av = np.zeros((len(VERTICES), 2 * N + E), np.int8)
    for i, v in enumerate(VERTICES):
        Av[i, v] ^= 1
        for j, e in enumerate(edges):
            if v in e: Av[i, 2 * N + j] ^= 1
    HX_def = np.concatenate([pad(HX0), Av], 0)
    HZp = pad(HZ0).copy()
    for r in range(HZ0.shape[0]):
        sV = HZ0[r, VERTICES] % 2
        if sV.any():
            g = _solve(Inc, sV)
            if g is None:
                return None, None, {"valid": False, "reason": "G disconnected on a Z-check"}
            HZp[r, 2 * N:2 * N + E] ^= g
    cyc = _nullspace(Inc)
    Bp = np.zeros((cyc.shape[0], 2 * N + E), np.int8); Bp[:, 2 * N:2 * N + E] = cyc
    HZ_def = np.concatenate([HZp, Bp], 0)
    assert (HX_def @ HZ_def.T % 2 == 0).all(), "deformed code not CSS"
    info = {"valid": True, "new_qubits": E, "extra_edges": len(extra),
            "added_X_checks": len(VERTICES),
            "cycle_space_dim": E - len(VERTICES) + 1,   # raw #Bp (paper reduces 11->7)
            "n": HX_def.shape[1], "edges": edges}       # full graph (base + extra)
    return HX_def, HZ_def, info

# ----------------------------------------------------------------------
# Quantum CSS distance via BP+OSD coset minimisation (UPPER BOUND).
#   true_d <= reported_d ; only error mode is over-reporting (false positive).
#   dx (X-distance) is the bottleneck the oracle over-reports; dz (Z-distance) is
#   reliably 12. See the module docstring for the three-layer hardening.
# ----------------------------------------------------------------------
def _logical_reps(stab, kspace):
    cur = stab.copy() % 2; base = _rank(cur); reps = []
    for v in kspace:
        test = np.vstack([cur, v.reshape(1, -1)])
        if _rank(test) > base:
            cur = test; base += 1; reps.append(v)
    return np.array(reps, np.int8) if reps else np.zeros((0, stab.shape[1]), np.int8)

def _coset_min(checks, logicals, budget, er=0.05, osd_order=DISTANCE_OSD_ORDER,
               seed=0, max_iter=DISTANCE_MAX_ITER):
    """Returns (best_weight, best_operator). best_operator is the actual low-weight
    logical the decoder exhibited (a column vector over the deformed code's qubits),
    so callers can read off WHERE the distance is limited; None if k==0."""
    n = checks.shape[1]; k = logicals.shape[0]
    if k == 0: return n + 1, None
    H = np.vstack([checks % 2, logicals % 2]).astype(np.uint8); mc = checks.shape[0]
    dec = _DECODER_CLS(H, error_rate=er, max_iter=max_iter, bp_method="minimum_sum",
                       osd_method="osd_cs", osd_order=osd_order)
    best = n + 1; best_op = None; t0 = time.time(); rng = np.random.default_rng(seed)
    todo = [np.eye(k, dtype=np.uint8)[i] for i in range(k)]   # singletons first
    while time.time() - t0 < budget:
        w = todo.pop() if todo else rng.integers(0, 2, k).astype(np.uint8)
        if w.sum() == 0: continue
        s = np.concatenate([np.zeros(mc, np.uint8), w]).astype(np.uint8)
        op = dec.decode(s); wt = int(op.sum())
        if 0 < wt < best: best = wt; best_op = op.copy()
    return best, best_op

def measure_distance(HX, HZ, qubits):
    """Policy oracle: a default pass, then — only when the result reaches the
    non-negative-score region (d>=12 and qubits<=VERIFY_MAX_QUBITS, default 24) —
    a hardened re-decode of the X-side (the sole over-report source). Returns
    (d, dx, dz, verified, lim_op), where lim_op is the limiting (smaller-distance)
    logical operator for the structural feedback. A passing verify is NOT a proof;
    ILP-certify any sub-22-qubit winner (module docstring layer 3)."""
    Lx = _logical_reps(HX, _nullspace(HZ))   # X-logicals -> Z-distance (reliable)
    Lz = _logical_reps(HZ, _nullspace(HX))   # Z-logicals -> X-distance (bottleneck)
    dz, opz = _coset_min(HX, Lx, DISTANCE_BUDGET_Z_S, osd_order=DISTANCE_OSD_ORDER,
                         max_iter=DISTANCE_MAX_ITER, seed=0)
    dx, opx = _coset_min(HZ, Lz, DISTANCE_BUDGET_X_S, osd_order=DISTANCE_OSD_ORDER,
                         max_iter=DISTANCE_MAX_ITER, seed=0)
    verified = False
    if min(dx, dz) >= TARGET_DISTANCE and qubits <= VERIFY_MAX_QUBITS:
        for s in VERIFY_SEEDS:
            w, op = _coset_min(HZ, Lz, VERIFY_BUDGET_X_S, osd_order=VERIFY_OSD_ORDER,
                               max_iter=VERIFY_MAX_ITER, seed=s)
            if w < dx: dx, opx = w, op
        verified = True
    lim_op = opx if dx <= dz else opz        # the limiting (smaller) side's operator
    return min(dx, dz, TARGET_DISTANCE), dx, dz, verified, lim_op

# ----------------------------------------------------------------------
# Graph-structure diagnostics (surfaced to the inner loop so evolution can
# REASON about expansion, not blindly enumerate edges). The deformed-code
# distance is governed by the expansion of G: a low-weight logical lives on a
# sparse vertex cut, and reinforcing that cut is what raises the distance.
# ----------------------------------------------------------------------
def _graph_diag(edges):
    """Sparsest cut (min conductance over all 2^11 vertex bipartitions; 12 vertices
    is tiny), algebraic connectivity (Fiedler value = expansion), and degrees, of
    the graph on VERTICES with these edges. Pure structure — no BP+OSD."""
    nV = len(VERTICES); vpos = {v: i for i, v in enumerate(VERTICES)}
    adj = {i: set() for i in range(nV)}
    Lap = np.zeros((nV, nV))
    for e in edges:
        u, w = tuple(e); i, j = vpos[u], vpos[w]
        adj[i].add(j); adj[j].add(i)
        Lap[i, i] += 1; Lap[j, j] += 1; Lap[i, j] -= 1; Lap[j, i] -= 1
    deg = [len(adj[i]) for i in range(nV)]; vol = sum(deg)
    fiedler = float(sorted(np.linalg.eigvalsh(Lap))[1]) if nV > 1 else 0.0
    best = None
    for r in range(1, nV // 2 + 1):
        for S in itertools.combinations(range(nV), r):
            Sset = set(S)
            cut = sum(1 for i in S for j in adj[i] if j not in Sset)
            vS = sum(deg[i] for i in S); other = vol - vS
            cond = cut / min(vS, other) if min(vS, other) > 0 else 9.0
            if best is None or cond < best[0]: best = (cond, cut, S)
    cond, cut, S = best
    return {"fiedler": round(fiedler, 3), "cut_conductance": round(cond, 3),
            "cut_edges": cut, "cut_side": sorted(VERTICES[i] for i in S),
            "min_degree": min(deg), "max_degree": max(deg),
            "low_degree_vertices": sorted(VERTICES[i] for i in range(nV) if deg[i] == min(deg))}

def _op_graph_support(op, edges):
    """Map a low-weight logical operator to the graph: which VERTICES (data qubits)
    and how many EDGE ancillas it is supported on. Reveals WHERE the distance is
    pinched. Returns None if no operator was found."""
    if op is None: return None
    verts = [v for v in VERTICES if op[v]]
    n_edges = sum(1 for j in range(len(edges)) if op[2 * N + j])
    return {"on_vertices": verts, "n_vertices": len(verts), "n_edge_ancillas": n_edges}

# ----------------------------------------------------------------------
# ShinkaEvolve entry point
# ----------------------------------------------------------------------
def _fail(text: str) -> dict:
    return {"combined_score": -1000.0, "correct": False,
            "public": {"valid": 0}, "private": {}, "extra_data": {},
            "text_feedback": text}

def aggregate_fn(results: list) -> dict:
    if not results:
        return _fail("run_experiment returned no result.")
    propose = results[0]
    if not callable(propose):
        return _fail(f"run_experiment must return a callable propose_extra_edges; "
                     f"got {type(propose).__name__}.")
    try:
        edges = propose()
    except Exception:
        return _fail("Candidate propose_extra_edges() crashed:\n" + traceback.format_exc())
    try:
        HX, HZ, info = build_deformed(edges)
    except Exception:
        return _fail("build_deformed crashed:\n" + traceback.format_exc())
    if not info.get("valid", False):
        return _fail(f"Invalid graph: {info.get('reason')}. Return a list of unordered "
                     f"pairs (u,v) of the 12 vertices {VERTICES}.")

    qubits, extra = info["new_qubits"], info["extra_edges"]
    d, dx, dz, verified, lim_op = measure_distance(HX, HZ, qubits)

    # Structural diagnostics (the distance ↔ graph-expansion handle for evolution).
    diag = _graph_diag(info["edges"])
    lim = _op_graph_support(lim_op, info["edges"])
    cut_str = (f"weakest cut {diag['cut_side']} | rest "
               f"({diag['cut_edges']} crossing edges, conductance {diag['cut_conductance']}); "
               f"expansion(Fiedler) {diag['fiedler']}")

    if d >= TARGET_DISTANCE:
        score = float(BASELINE_QUBITS - qubits)
        beats = qubits < PAPER_QUBITS
        verdict = (
            f"SUCCESS: distance 12 with {qubits} qubits ({extra} extra edges); "
            f"score = {BASELINE_QUBITS}-{qubits} = {score:+.0f}. The paper uses 22 "
            f"qubits (4 extra edges). "
            + ("X-distance was re-verified at a hardened BP+OSD budget. " if verified else "")
            + ("BEATS the paper — but BP+OSD is only an UPPER bound; this record must be "
               "CERTIFIED with integer programming before it is believed, then pushed lower. "
               if beats else
               "Remove an extra edge and keep distance 12 to improve on this. ")
            + f"GRAPH NOW: {cut_str}. To shed a qubit, drop an edge that is NOT critical to "
            f"the weakest cut (or swap two edges for one) and keep distance 12. "
            f"(Degree<=7 is handled downstream by a min-weight cycle basis; it changes "
            f"neither distance nor qubit count.)"
        )
    else:
        score = -100.0 + d
        bott = "X-distance" if dx <= dz else "Z-distance"
        pinch = (f" The limiting weight-{d} {bott} logical is supported on vertices "
                 f"{lim['on_vertices']} (+{lim['n_edge_ancillas']} edge ancillas) — add edges "
                 f"that separate that set." if lim and lim['on_vertices'] else "")
        verdict = (
            f"FAIL: distance dropped to {d} (dx={dx}, dz={dz}); the task requires 12. "
            f"The graph lacks EXPANSION on the {bott} bottleneck: {cut_str}. Reinforce the "
            f"weakest cut (add edges crossing {diag['cut_side']} <-> rest), especially at the "
            f"low-degree vertices {diag['low_degree_vertices']}.{pinch} "
            f"Currently {extra} extra edges / {qubits} qubits."
        )

    public = {
        "combined_score": score, "valid": 1, "distance": d, "dx": dx, "dz": dz,
        "qubits": qubits, "extra_edges": extra, "added_X_checks": info["added_X_checks"],
        "cycle_space_dim": info["cycle_space_dim"], "n": info["n"], "verified": verified,
        "fiedler": diag["fiedler"], "cut_conductance": diag["cut_conductance"],
        "cut_edges": diag["cut_edges"], "cut_side": diag["cut_side"],
        "min_degree": diag["min_degree"],
        "limiting_logical_vertices": (lim["on_vertices"] if lim else None),
    }
    # Held-out benchmark constants stay private (the inner loop sees only the
    # candidate's own measured result via `public` + `text_feedback`).
    private = {
        "paper_qubits": PAPER_QUBITS, "target_distance": TARGET_DISTANCE,
        "baseline_qubits": BASELINE_QUBITS,
    }
    return {"combined_score": score, "correct": True, "public": public,
            "private": private, "extra_data": {}, "text_feedback": verdict}

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
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
    print(f"Distance oracle: X={DISTANCE_BUDGET_X_S}s Z={DISTANCE_BUDGET_Z_S}s "
          f"osd={DISTANCE_OSD_ORDER} | verify(<= {VERIFY_MAX_QUBITS}q): X={VERIFY_BUDGET_X_S}s "
          f"osd={VERIFY_OSD_ORDER} seeds={VERIFY_SEEDS}")
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
        print(f"Evaluation reported correct=False: {err}")
    else:
        print("Evaluation completed successfully.")
    print(f"combined_score = {metrics.get('combined_score')!r}")
    if isinstance(metrics.get("public"), dict):
        for k, v in metrics["public"].items():
            print(f"  public.{k} = {v!r}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gross_code_gauging evaluator")
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)

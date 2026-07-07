"""ShinkaEvolve EVALUATOR — END-TO-END gauging measurement of the gross-code
logical X_alpha (v2, full-design-space, circuit-level).

The candidate designs the complete GAUGING GADGET of Williamson & Yoder,
"Low-overhead fault-tolerant quantum computation by gauging logical operators"
(arXiv:2410.02213; Nature Physics 22:598, 2026) for the weight-12 logical
X_alpha of the [[144,12,12]] gross code:

    spec = {"edges": [(u, v), ...], "rounds": R}

  * labels 0..11  = the 12 data qubits in supp(X_alpha)  (graph vertices)
  * labels 12..35 = OPTIONAL dummy vertices (paper Remark 2: they carry NO
    physical qubit; their Gauss-law check is A_v = prod_{e:v} X_e). Dummy
    vertices are how the paper expresses Shor-style stars, surgery grids and
    the thickened/cellulated (layered) constructions — all inside this one
    (vertices, edges) design space.
  * each EDGE carries one new data qubit (init |0>); multigraph edges allowed
    (the paper's double-gross example uses a doubled edge); self-loops not.
  * R = number of deformed-code syndrome-extraction rounds (the time axis;
    paper Thm 2 uses d=12; Cross et al. arXiv:2407.18393 found R=7 < d optimal
    at p=1e-3 — the timelike/spacelike tradeoff is REAL and is measured here).

Everything else is derived DETERMINISTICALLY here (never evolved):
  * A_v Gauss-law checks; original Z-checks routed by exact minimum-weight
    T-joins (paper Def. 2 "minimum weight path" convention); flux checks B_p
    on a minimum-weight cycle basis (Horton), reduced by the BB-code Z-check
    redundancy exactly as in paper App. B (the paper graph yields 11 -> 7);
  * a generic syndrome-extraction schedule via greedy Tanner-graph edge
    coloring (the coloration-circuit construction, Tremblay-Delfosse-Beverland
    arXiv:2109.14609 — the standard generic scheduler for irregular deformed
    codes). The schedule is the SAME ALGORITHM for every candidate, so its
    depth is an endogenous consequence of the gadget's degrees/check weights;
  * the full end-to-end protocol circuit (stim), following Cross-He-Rall-Yoder
    arXiv:2407.18393 Sec. 3.2 adapted to gauging:
       ideal init MPPs -> N_BASE noisy base rounds -> gauge-in (edge qubits
       |0>) -> R noisy deformed rounds -> gauge-out (edge MZ) -> N_BASE noisy
       base rounds -> ideal final MPPs.
    First-round A_v outcomes are individually random (no detector; paper
    App. F Lemma 3) and their product x the initial ideal X_alpha MPP is the
    MEASUREMENT observable; B_p and deformed checks get boundary detectors at
    gauge-in/out; the ungauging byproduct enters the Z-logical observables as
    edge-readout parities.
  * circuit-level depolarizing noise at PHYS_P (DEPOLARIZE2 after each CNOT,
    measure/reset flips, aggregated idle noise per phase so SCHEDULE DEPTH is
    priced), BP+OSD-0 decoding (stimbposd/ldpc; see BP_ITERS/OSD_ORDER note),
    parallel sampling via sinter.

WHY END-TO-END (v1 postmortem). v1 gated on a BP+OSD *estimate* of the
deformed-code distance — an upper bound the paper itself used only as a fast
filter before proving distance with integer programming — and scored pure
qubit count. That is doubly misleading: the estimate can over-report (v1's
docs admit the reward-hack pressure), and even EXACT distance is an
insufficient proxy for a measurement gadget's real quality — e.g. the gross
code's own depth-7 schedule has circuit distance <= 10 < d=12 (Bravyi et al.
Nature 2024, Table 1), and more merge rounds (higher timelike distance) can
WORSEN total error (Cross et al. Sec. 4.2). Worse, a distance+qubits score
makes the paper's dummy-vertex/thickening design axes strictly losing moves
(they cost qubits and buy only check-weight/depth, which v1 declared "not
your job") — evolution could never rediscover them. Here the score is the
measured end-to-end error of the actual measurement protocol, which is what
those design axes exist to improve, plus the ancilla overhead.

================  SCORING (Shinka MAXIMISES combined_score)  ===================
  Q  = total added elements = edge qubits + A_v checks + B_p checks
       (the paper gadget: 22 + 12 + 7 = 41 — its "additional checks and
        qubits total 41", App. B; each check costs an ancilla qubit).
  overall = 1 - (1-p_X)(1-p_Z) from the two protocol circuits (X basis:
       measurement-outcome observable + preservation of the 12 X logicals;
       Z basis: preservation of the 11 Z logicals that commute with X_alpha,
       byproduct-corrected).
  GATE = GATE_FACTOR x LER_REF, where LER_REF is this harness's measured
       overall error of the PAPER gadget (22 edges, R=12) — calibrated once,
       see private metrics.

  candidate crashes / returns garbage          -> -1000   (correct=False)
  parseable but invalid gadget (SpecError)     ->  -100   (+ named reason)
  valid, overall > GATE (unreliable)           ->  -8 + log10(GATE/overall)
                                                   (clamped to >= -30; smooth
                                                    gradient toward the gate)
  valid, overall <= GATE (reliable)            ->  (Q_REF - Q)
                                                   + min(2.0, log10(GATE/overall))
       Q_REF = 41. So: reproduce the paper gadget ->  ~0..+2; every element
       saved below the paper is +1; the capped log-margin bonus rewards
       reliability headroom but can never buy more than 2 elements. A gadget
       that is smaller AND still reliable outranks everything else.

================  ANTI-GAMING  ================================================
The candidate returns only the gadget spec. The code, the deformation, the
schedule, the circuit, the observables, the decoder and the sampling all live
here. The score's only measured quantity is the stim-sampled logical error of
an evaluator-built circuit — there is no oracle to over-report to (v1's
failure mode). Sampling noise is bounded by error-budget collection (sinter
max_errors) with a fresh seed per eval, so a candidate cannot lock onto a
lucky noise realization; the GATE margin (GATE_FACTOR=2 -> 0.30 decades)
is ~10x the score's sampling std at the default budgets. Fresh-process-per-
candidate isolation (the Shinka harness default) must stay ON; sinter workers
are fresh spawned processes that re-import this module and stim from disk, so
a candidate monkey-patching module globals in the eval process does not reach
the samplers/decoders. Do not reuse an eval process across candidates.

================  RUNTIME (24-core Windows, shinka env, measured)  =============
  build + structural checks + circuits        ~10 s
  sinter sampling, 2 circuits, 20 workers     ~7 min for the seed (6.7k shots,
       ~1.3 core-s/shot effective; error-budget: unreliable candidates hit
       MAX_ERRORS fast and finish sooner; excellent candidates run toward the
       shot cap)
  worst case bounded by the mechanism-scaled shot cap: ~11 min. Set the
  harness eval_time generously (>= 00:15:00). Lower GAUGE_MAX_ERRORS (e.g. 50)
  to trade score noise for throughput.
Env overrides: GAUGE_PHYS_P, GAUGE_MAX_ERRORS, GAUGE_MAX_SHOTS, GAUGE_WORKERS.
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
import time
import traceback
from typing import Optional

import numpy as np
import stim

from shinka.core import run_shinka_eval

# Bind third-party entry points privately at import, BEFORE any candidate
# module is loaded in this process (see ANTI-GAMING above).
import sinter as _sinter_mod
import stimbposd as _stimbposd_mod
_SINTER_COLLECT = _sinter_mod.collect
_SINTER_TASK = _sinter_mod.Task
_SINTER_DEC_CLS = _stimbposd_mod.SinterDecoder_BPOSD

# ----------------------------------------------------------------------
# Benchmark / scoring constants
# ----------------------------------------------------------------------
PHYS_P      = float(os.environ.get("GAUGE_PHYS_P", "0.002"))
IDLE_FRAC   = 0.1          # idle depolarize prob = IDLE_FRAC * PHYS_P per tick
N_BASE      = 1            # base-code rounds before and after (Remark 9: constant ok;
                           # the ideal MPP brackets anchor the reference frames)
BP_ITERS    = 12           # decoder: BP+OSD-0 (osd_method="osd0"). Deliberately
OSD_ORDER   = 0            # fast-but-weak (~5x faster than BP+LSD here at equal
                           # observed accuracy); absolute LERs are NOT paper-
                           # comparable, but every candidate AND the reference
                           # gadget are decoded identically, so the relative
                           # gate is self-consistent (same philosophy as
                           # bb_syndrome_sched's osd_order=3 choice).
MAX_ERRORS  = int(os.environ.get("GAUGE_MAX_ERRORS", "80"))    # per circuit
MAX_SHOTS   = int(os.environ.get("GAUGE_MAX_SHOTS", "5000"))   # per circuit
N_WORKERS   = int(os.environ.get("GAUGE_WORKERS", str(max(2, min(20, (os.cpu_count() or 8) - 4)))))

Q_REF       = 41           # paper gadget: 22 edge qubits + 12 A_v + 7 B_p (App. B)
GATE_FACTOR = 2.0
# Overall end-to-end error of the PAPER gadget (18 matching + 4 expansion
# edges, R=12) under THIS harness (PHYS_P=0.002, BP+OSD-0, error-budget
# sampling) — calibrated by scripts in this repo; recalibrate if PHYS_P,
# the noise model, the scheduler or the decoder change.
LER_REF     = float(os.environ.get("GAUGE_LER_REF", "6.5563e-2"))
                            # calibrate.py 2026-07-07: X 426/17453=2.44e-2,
                            # Z 414/9814=4.22e-2, overall 6.556e-2 (+-0.015
                            # decades) at p=0.002, BP+OSD-0(12 iters), n_base=1
GATE        = GATE_FACTOR * LER_REF

INVALID_SCORE = -100.0   # below any buildable gadget's score (min feasible ~ -76)
CRASH_SCORE   = -1000.0

MAX_EDGES   = 60
MAX_DUMMIES = 24
MAX_ROUNDS  = 24

# ----------------------------------------------------------------------
# Gross code [[144,12,12]] and the logical X_alpha (paper App. B)
# ----------------------------------------------------------------------
L, M = 12, 6
N = L * M                       # 72
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

F_TERMS  = [(0,0),(1,0),(2,0),(3,0),(6,0),(7,0),(8,0),(9,0),(1,3),(5,3),(7,3),(11,3)]
SUPPORT  = [_idx(a, b) for a, b in F_TERMS]     # data-qubit index of label 0..11

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

def _logical_reps(stab, kspace, seed_ops=()):
    cur = stab.copy() % 2; base = _rank(cur); reps = []
    for v in list(seed_ops) + list(kspace):
        test = np.vstack([cur, np.asarray(v, np.int8).reshape(1, -1)])
        if _rank(test) > base:
            cur = test; base += 1; reps.append(np.asarray(v, np.int8))
    return np.array(reps, np.int8)

XALPHA = np.zeros(2 * N, np.int8)
for _q in SUPPORT: XALPHA[_q] = 1
assert (HZ0 @ XALPHA % 2 == 0).all()

LOGX = _logical_reps(HX0, _nullspace(HZ0), seed_ops=[XALPHA])      # 12, X_alpha first
_LOGZ_raw = _logical_reps(HZ0, _nullspace(HX0))                    # 12
assert LOGX.shape[0] == 12 and _LOGZ_raw.shape[0] == 12

def _fix_z_basis():
    Lz = _LOGZ_raw.copy()
    ov = (Lz @ XALPHA) % 2
    piv = int(np.flatnonzero(ov)[0])
    for i in np.flatnonzero(ov)[1:]:
        Lz[i] ^= Lz[piv]
    keep = [i for i in range(12) if i != piv]
    return Lz[keep]
LOGZ_COMM = _fix_z_basis()      # the 11 Z logicals preserved by the measurement

# ----------------------------------------------------------------------
# Gadget spec -> deformed (gauged) code
# ----------------------------------------------------------------------
class SpecError(ValueError):
    pass

def parse_spec(spec):
    if not isinstance(spec, dict):
        raise SpecError(f"spec must be a dict {{'edges': [...], 'rounds': int}}, "
                        f"got {type(spec).__name__}")
    edges_in = spec.get("edges")
    rounds = spec.get("rounds", 12)
    try:
        rounds = int(rounds)
    except Exception:
        raise SpecError(f"rounds must be an int, got {rounds!r}")
    if not (1 <= rounds <= MAX_ROUNDS):
        raise SpecError(f"rounds must be in [1,{MAX_ROUNDS}], got {rounds}")
    if not isinstance(edges_in, (list, tuple)):
        raise SpecError("spec['edges'] must be a list of (u,v) label pairs")
    edges = []
    for e in edges_in:
        try:
            u, v = int(e[0]), int(e[1])
        except Exception:
            raise SpecError(f"edge {e!r} is not a pair of integer labels")
        if u == v:
            raise SpecError(f"self-loop edge {e!r} not allowed")
        if not (0 <= u < 12 + MAX_DUMMIES and 0 <= v < 12 + MAX_DUMMIES):
            raise SpecError(f"edge {e!r} uses a label outside 0..{12 + MAX_DUMMIES - 1} "
                            f"(0..11 = support vertices, 12..{12 + MAX_DUMMIES - 1} = dummies)")
        edges.append((min(u, v), max(u, v)))
    if not edges:
        raise SpecError("no edges — the graph must connect all 12 support vertices")
    if len(edges) > MAX_EDGES:
        raise SpecError(f"{len(edges)} edges exceeds the cap of {MAX_EDGES}")
    dummies = sorted({x for e in edges for x in e if x >= 12})
    verts = list(range(12)) + dummies
    adj = {v: set() for v in verts}
    for (u, v) in edges:
        adj[u].add(v); adj[v].add(u)
    seen, stack = set(), [0]
    while stack:
        x = stack.pop()
        if x in seen: continue
        seen.add(x); stack.extend(adj[x] - seen)
    missing = set(verts) - seen
    if missing:
        raise SpecError(f"graph disconnected: vertices {sorted(missing)} unreachable from "
                        f"vertex 0 — all 12 support vertices and every used dummy must lie "
                        f"in ONE connected component (Theorem 1 hypothesis)")
    return edges, dummies, rounds

def _shortest_paths(verts, edges):
    adj = {v: [] for v in verts}
    for j, (u, v) in enumerate(edges):
        adj[u].append((v, j)); adj[v].append((u, j))
    paths = {}
    for s in verts:
        prev = {s: None}; order = [s]; qi = 0
        while qi < len(order):
            x = order[qi]; qi += 1
            for (y, j) in adj[x]:
                if y not in prev:
                    prev[y] = (x, j); order.append(y)
        for t in verts:
            if t in prev:
                es = set(); x = t
                while prev[x] is not None:
                    px, j = prev[x]; es.add(j); x = px
                paths[(s, t)] = frozenset(es)
    return paths

def _min_tjoin(T, paths):
    """Exact min-weight T-join (unit weights): min over perfect matchings of T
    of the XOR of shortest paths; |T| <= 6 here so <= 15 matchings."""
    T = list(T)
    if not T:
        return frozenset()
    def matchings(rem):
        if not rem:
            yield []; return
        a = rem[0]
        for i in range(1, len(rem)):
            rest = rem[1:i] + rem[i + 1:]
            for m in matchings(rest):
                yield [(a, rem[i])] + m
    best = None
    for m in matchings(T):
        es = set(); ok = True
        for (a, b) in m:
            if (a, b) not in paths:
                ok = False; break
            es ^= set(paths[(a, b)])
        if ok and (best is None or len(es) < len(best)):
            best = es
    if best is None:
        raise SpecError("Z-check routing failed (graph not connected on required vertices)")
    return frozenset(best)

def _min_cycle_basis(verts, edges, paths):
    """Horton candidate set (SP(v,x) xor SP(v,y) xor {e}) + parallel-edge
    2-cycles; greedy independent by ascending weight -> minimum cycle basis."""
    E = len(edges)
    dim = E - len(verts) + 1
    if dim <= 0:
        return []
    cands = set()
    for j, (x, y) in enumerate(edges):
        for v in verts:
            if (v, x) in paths and (v, y) in paths:
                c = set(paths[(v, x)]) ^ set(paths[(v, y)])
                c.add(j)
                deg = {}
                for k in c:
                    for w in edges[k]:
                        deg[w] = deg.get(w, 0) + 1
                if all(d % 2 == 0 for d in deg.values()):
                    cands.add(frozenset(c))
    by_pair = {}
    for j, (x, y) in enumerate(edges):
        by_pair.setdefault((x, y), []).append(j)
    for js in by_pair.values():
        for a, b in itertools.combinations(js, 2):
            cands.add(frozenset({a, b}))
    cands = sorted(cands, key=lambda c: (len(c), sorted(c)))
    basis, cur = [], np.zeros((0, E), np.int8)
    for c in cands:
        v = np.zeros(E, np.int8)
        for j in c: v[j] = 1
        test = np.vstack([cur, v.reshape(1, -1)])
        if _rank(test) > cur.shape[0]:
            cur = test; basis.append(sorted(c))
            if len(basis) == dim:
                break
    if len(basis) != dim:
        raise SpecError("failed to construct a full cycle basis (internal error)")
    return basis

def build_gauged(edges, dummies):
    """Deformed code from the gadget graph. Data qubits: 0..2N-1 base,
    2N..2N+E-1 edges. Dummy vertices carry NO qubit (paper Remark 2)."""
    E = len(edges)
    verts = list(range(12)) + list(dummies)
    nq = 2 * N + E
    paths = _shortest_paths(verts, edges)
    pad = lambda H: np.concatenate([H, np.zeros((H.shape[0], E), np.int8)], 1)

    Av = np.zeros((len(verts), nq), np.int8)
    for i, v in enumerate(verts):
        if v < 12:
            Av[i, SUPPORT[v]] ^= 1
        for j, (a, b) in enumerate(edges):
            if v == a or v == b:
                Av[i, 2 * N + j] ^= 1
    prod = Av.sum(0) % 2
    assert (prod[:2 * N] == XALPHA).all() and (prod[2 * N:] == 0).all()

    HZroute = pad(HZ0).copy()
    route_sets = []
    for r in range(N):
        T = [i for i in range(12) if HZ0[r, SUPPORT[i]]]
        gamma = _min_tjoin(T, paths) if T else frozenset()
        for j in gamma:
            HZroute[r, 2 * N + j] ^= 1
        route_sets.append(sorted(gamma))

    basis = _min_cycle_basis(verts, edges, paths)
    stack = HZroute.copy(); base_rank = _rank(stack)
    Bp_rows, Bp_cycles = [], []
    for c in sorted(basis, key=len):
        v = np.zeros(nq, np.int8)
        for j in c: v[2 * N + j] = 1
        test = np.vstack([stack, v.reshape(1, -1)])
        rk = _rank(test)
        if rk > base_rank:
            stack = test; base_rank = rk
            Bp_rows.append(v); Bp_cycles.append(c)
    Bp = np.array(Bp_rows, np.int8) if Bp_rows else np.zeros((0, nq), np.int8)

    HXdef = np.concatenate([pad(HX0), Av], 0)
    HZdef = np.concatenate([HZroute, Bp], 0)
    assert (HXdef @ HZdef.T % 2 == 0).all(), "deformed code not CSS (internal error)"

    k_def = nq - _rank(HXdef) - _rank(HZdef)
    if k_def != 11:
        raise SpecError(f"deformed code has k={k_def}, expected 11 — this graph does not "
                        f"gauge exactly X_alpha")

    z_joins = []
    for ell in LOGZ_COMM:
        T = [i for i in range(12) if ell[SUPPORT[i]]]
        z_joins.append(sorted(_min_tjoin(T, paths) if T else frozenset()))

    return {
        "edges": edges, "dummies": dummies, "verts": verts, "nq": nq, "E": E,
        "Av": Av, "HZroute": HZroute, "Bp": Bp, "Bp_cycles": Bp_cycles,
        "route_sets": route_sets, "z_joins": z_joins,
        "HXdef": HXdef, "HZdef": HZdef,
        "n_av": Av.shape[0], "n_bp": Bp.shape[0],
        "overhead": E + Av.shape[0] + Bp.shape[0],
        "wz_max": int(HZdef.sum(1).max()), "wx_max": int(HXdef.sum(1).max()),
        "qdeg_max": int((HXdef.sum(0) + HZdef.sum(0)).max()),
        "route_w_max": max((len(s) for s in route_sets), default=0),
        "cycle_w_max": max((len(c) for c in Bp_cycles), default=0),
    }

# ----------------------------------------------------------------------
# Generic syndrome-extraction schedule: greedy Tanner-graph edge coloring
# ----------------------------------------------------------------------
def color_schedule(H_rows, anc_ids):
    ticks, busy_q, busy_a = [], [], []
    for r in range(H_rows.shape[0]):
        anc = anc_ids[r]
        for q in np.flatnonzero(H_rows[r]):
            t = 0
            while t < len(ticks) and (q in busy_q[t] or anc in busy_a[t]):
                t += 1
            if t == len(ticks):
                ticks.append([]); busy_q.append(set()); busy_a.append(set())
            ticks[t].append((int(q), int(anc)))
            busy_q[t].add(q); busy_a[t].add(anc)
    return ticks

# ----------------------------------------------------------------------
# End-to-end protocol circuit (see module docstring)
# ----------------------------------------------------------------------
def build_protocol_circuit(g, rounds, basis, p=PHYS_P, n_base=N_BASE,
                           p_idle_frac=IDLE_FRAC):
    E, nq = g["E"], g["nq"]
    anc0 = nq
    ancX_orig = list(range(anc0, anc0 + N))
    ancAv     = list(range(anc0 + N, anc0 + N + g["n_av"]))
    ancZ_orig = list(range(anc0 + N + g["n_av"], anc0 + 2 * N + g["n_av"]))
    ancBp     = list(range(anc0 + 2 * N + g["n_av"],
                           anc0 + 2 * N + g["n_av"] + g["n_bp"]))
    n_all = anc0 + 2 * N + g["n_av"] + g["n_bp"]

    padE = lambda H: np.concatenate([H, np.zeros((H.shape[0], E), np.int8)], 1)
    HX0p, HZ0p = padE(HX0), padE(HZ0)

    base_x = color_schedule(HX0p, ancX_orig)
    base_z = color_schedule(HZ0p, ancZ_orig)
    def_x  = color_schedule(np.concatenate([HX0p, g["Av"]], 0), ancX_orig + ancAv)
    def_z  = color_schedule(np.concatenate([g["HZroute"], g["Bp"]], 0), ancZ_orig + ancBp)

    c = stim.Circuit()
    mctr = [0]
    recs = {}

    def note(tag, rnd, count):
        for i in range(count):
            recs[(tag, rnd, i)] = mctr[0] + i
        mctr[0] += count

    def mpp(vec, xtype):
        targ = []
        t = stim.target_x if xtype else stim.target_z
        for q in np.flatnonzero(vec[:nq]):
            if targ: targ.append(stim.target_combiner())
            targ.append(t(int(q)))
        c.append("MPP", targ)
        idx = mctr[0]; mctr[0] += 1
        return idx

    def noisy_phase(ticks, anc_list, xtype, tag, rnd):
        if xtype:
            c.append("RX", anc_list); c.append("Z_ERROR", anc_list, p)
        else:
            c.append("R", anc_list); c.append("X_ERROR", anc_list, p)
        active = {}
        for tick in ticks:
            for (q, a) in tick:
                c.append("CNOT", [a, q] if xtype else [q, a])
                c.append("DEPOLARIZE2", [a, q] if xtype else [q, a], p)
                active[q] = active.get(q, 0) + 1
                active[a] = active.get(a, 0) + 1
            c.append("TICK")
        k = len(ticks); p_id = p * p_idle_frac
        for q in range(nq):     # aggregated idle noise on all data+edge qubits
            idle = k - active.get(q, 0)
            if idle > 0:
                c.append("DEPOLARIZE1", q, 1 - (1 - p_id) ** idle)
        if xtype:
            c.append("Z_ERROR", anc_list, p); c.append("MX", anc_list)
        else:
            c.append("X_ERROR", anc_list, p); c.append("M", anc_list)
        note(tag, rnd, len(anc_list))

    def det(*ms):
        c.append("DETECTOR", [stim.target_rec(m - mctr[0]) for m in ms])

    c.append("R", list(range(n_all)))
    init_stabX = [mpp(HX0p[i], True) for i in range(N)]
    init_stabZ = [mpp(HZ0p[i], False) for i in range(N)]
    if basis == "X":
        init_logs = [mpp(padE(LOGX)[i], True) for i in range(12)]
    else:
        init_logs = [mpp(padE(LOGZ_COMM)[i], False) for i in range(11)]

    for r in range(n_base):
        noisy_phase(base_x, ancX_orig, True,  "bx", r)
        noisy_phase(base_z, ancZ_orig, False, "bz", r)
        if r == 0:
            for i in range(N): det(init_stabX[i], recs[("bx", 0, i)])
            for i in range(N): det(init_stabZ[i], recs[("bz", 0, i)])
        else:
            for i in range(N): det(recs[("bx", r - 1, i)], recs[("bx", r, i)])
            for i in range(N): det(recs[("bz", r - 1, i)], recs[("bz", r, i)])

    edge_q = list(range(2 * N, 2 * N + E))
    c.append("R", edge_q)
    c.append("X_ERROR", edge_q, p)

    for r in range(rounds):
        noisy_phase(def_x, ancX_orig + ancAv, True,  "dx", r)
        noisy_phase(def_z, ancZ_orig + ancBp, False, "dz", r)
        if r == 0:
            for i in range(N): det(recs[("bx", n_base - 1, i)], recs[("dx", 0, i)])
            # A_v round-1: individually random -> NO detector (their product is
            # the measurement observable; paper App. F Lemma 3)
            for i in range(N): det(recs[("bz", n_base - 1, i)], recs[("dz", 0, i)])
            for i in range(g["n_bp"]): det(recs[("dz", 0, N + i)])
        else:
            for i in range(N + g["n_av"]): det(recs[("dx", r - 1, i)], recs[("dx", r, i)])
            for i in range(N + g["n_bp"]): det(recs[("dz", r - 1, i)], recs[("dz", r, i)])

    c.append("X_ERROR", edge_q, p)
    c.append("M", edge_q)
    note("edge", 0, E)
    for i, cyc in enumerate(g["Bp_cycles"]):
        det(recs[("dz", rounds - 1, N + i)], *[recs[("edge", 0, j)] for j in cyc])

    for r in range(n_base):
        noisy_phase(base_x, ancX_orig, True,  "ax", r)
        noisy_phase(base_z, ancZ_orig, False, "az", r)
        if r == 0:
            for i in range(N): det(recs[("dx", rounds - 1, i)], recs[("ax", 0, i)])
            for i in range(N):
                det(recs[("dz", rounds - 1, i)], recs[("az", 0, i)],
                    *[recs[("edge", 0, j)] for j in g["route_sets"][i]])
        else:
            for i in range(N): det(recs[("ax", r - 1, i)], recs[("ax", r, i)])
            for i in range(N): det(recs[("az", r - 1, i)], recs[("az", r, i)])

    fin_stabX = [mpp(HX0p[i], True) for i in range(N)]
    fin_stabZ = [mpp(HZ0p[i], False) for i in range(N)]
    for i in range(N): det(recs[("ax", n_base - 1, i)], fin_stabX[i])
    for i in range(N): det(recs[("az", n_base - 1, i)], fin_stabZ[i])

    if basis == "X":
        fin_logs = [mpp(padE(LOGX)[i], True) for i in range(12)]
        obs = [init_logs[0]] + [recs[("dx", 0, N + i)] for i in range(g["n_av"])]
        c.append("OBSERVABLE_INCLUDE", [stim.target_rec(m - mctr[0]) for m in obs], 0)
        for i in range(12):
            c.append("OBSERVABLE_INCLUDE",
                     [stim.target_rec(init_logs[i] - mctr[0]),
                      stim.target_rec(fin_logs[i] - mctr[0])], 1 + i)
    else:
        fin_logs = [mpp(padE(LOGZ_COMM)[i], False) for i in range(11)]
        for i in range(11):
            targ = [init_logs[i], fin_logs[i]] + \
                   [recs[("edge", 0, j)] for j in g["z_joins"][i]]
            c.append("OBSERVABLE_INCLUDE",
                     [stim.target_rec(m - mctr[0]) for m in targ], i)

    meta = {"depth_base": (len(base_x), len(base_z)),
            "depth_def": (len(def_x), len(def_z))}
    return c, meta

def _noiseless_ok(circuit, shots=16):
    nl = circuit.without_noise()
    det, obs = nl.compile_detector_sampler().sample(shots, separate_observables=True)
    return (not det.any()) and (not obs.any())

# ----------------------------------------------------------------------
# Structural diagnostics for feedback
# ----------------------------------------------------------------------
def _graph_diag(edges, dummies):
    verts = list(range(12)) + list(dummies)
    nV = len(verts); vpos = {v: i for i, v in enumerate(verts)}
    adj = {i: set() for i in range(nV)}
    Lap = np.zeros((nV, nV))
    for (u, w) in edges:
        i, j = vpos[u], vpos[w]
        adj[i].add(j); adj[j].add(i)
        Lap[i, i] += 1; Lap[j, j] += 1; Lap[i, j] -= 1; Lap[j, i] -= 1
    deg = [len(adj[i]) for i in range(nV)]
    fiedler = float(sorted(np.linalg.eigvalsh(Lap))[1]) if nV > 1 else 0.0
    # sparsest cut: exact for <= 14 vertices, Fiedler sweep otherwise
    best = None
    if nV <= 14:
        vol = sum(deg)
        for r in range(1, nV // 2 + 1):
            for S in itertools.combinations(range(nV), r):
                Ss = set(S)
                cut = sum(1 for i in S for j in adj[i] if j not in Ss)
                vS = sum(deg[i] for i in S); other = vol - vS
                cond = cut / min(vS, other) if min(vS, other) > 0 else 9.0
                if best is None or cond < best[0]:
                    best = (cond, cut, [verts[i] for i in S])
    else:
        vec = np.linalg.eigh(Lap)[1][:, 1]
        order = np.argsort(vec); vol = sum(deg)
        for cutpos in range(1, nV):
            S = order[:cutpos]; Ss = set(S.tolist())
            cut = sum(1 for i in Ss for j in adj[i] if j not in Ss)
            vS = sum(deg[i] for i in Ss); other = vol - vS
            if min(vS, other) == 0: continue
            cond = cut / min(vS, other)
            if best is None or cond < best[0]:
                best = (cond, cut, sorted(verts[i] for i in Ss))
    cond, cut, side = best if best else (9.0, 0, [])
    return {"fiedler": round(fiedler, 3), "cut_conductance": round(cond, 3),
            "cut_edges": cut, "cut_side": side,
            "min_degree": int(min(deg)), "max_degree": int(max(deg))}

# ----------------------------------------------------------------------
# Sampling
# ----------------------------------------------------------------------
REF_MECHS = 90_000    # ~error mechanisms of the reference circuit; the shot cap
                      # scales inversely with a candidate's mechanism count so
                      # eval wall-clock stays ~flat as gadgets grow (statistics
                      # matter most near the frontier, where gadgets are small)

def sample_ler(circ_x, circ_z):
    mechs = max(circ_x.detector_error_model().num_errors,
                circ_z.detector_error_model().num_errors)
    max_shots_eff = int(min(MAX_SHOTS, max(2500, MAX_SHOTS * REF_MECHS / max(1, mechs))))
    tasks = [_SINTER_TASK(circuit=circ_x, json_metadata={"basis": "X"}),
             _SINTER_TASK(circuit=circ_z, json_metadata={"basis": "Z"})]
    results = _SINTER_COLLECT(
        num_workers=N_WORKERS,
        tasks=tasks,
        decoders=["bposd0"],
        custom_decoders={"bposd0": _SINTER_DEC_CLS(
            max_bp_iters=BP_ITERS, osd_order=OSD_ORDER, osd_method="osd0")},
        max_errors=MAX_ERRORS,
        max_shots=max_shots_eff,
        print_progress=False,
    )
    out = {}
    for r in results:
        out[r.json_metadata["basis"]] = (int(r.errors), int(r.shots))
    ex, sx = out.get("X", (0, 0))
    ez, sz = out.get("Z", (0, 0))
    if sx == 0 or sz == 0:
        raise RuntimeError("sinter returned no shots")
    px = ex / sx; pz = ez / sz
    overall = 1.0 - (1.0 - px) * (1.0 - pz)
    return px, pz, overall, (ex, sx, ez, sz)

# ----------------------------------------------------------------------
# ShinkaEvolve entry point
# ----------------------------------------------------------------------
def _crash(text):
    return {"combined_score": CRASH_SCORE, "correct": False,
            "public": {"valid": 0}, "private": {}, "extra_data": {},
            "text_feedback": text}

def _invalid(reason):
    return {"combined_score": INVALID_SCORE, "correct": True,
            "public": {"valid": 0, "reason": reason}, "private": {},
            "extra_data": {},
            "text_feedback": f"INVALID GADGET (score {INVALID_SCORE:.0f}): {reason}"}

def aggregate_fn(results: list) -> dict:
    if not np.isfinite(LER_REF) or LER_REF <= 0:
        return _crash("LER_REF is not calibrated — run calibrate.py and set the "
                      "constant in evaluate.py (or GAUGE_LER_REF) first")
    if not results:
        return _crash("run_experiment returned no result.")
    propose = results[0]
    if not callable(propose):
        return _crash(f"run_experiment must return the propose_gadget callable, "
                      f"got {type(propose).__name__}.")
    try:
        spec = propose()
    except Exception:
        return _crash("Candidate propose_gadget() crashed:\n" + traceback.format_exc())

    try:
        edges, dummies, rounds = parse_spec(spec)
        g = build_gauged(edges, dummies)
    except SpecError as e:
        return _invalid(str(e))
    except Exception:
        return _crash("Gadget construction crashed:\n" + traceback.format_exc())

    t0 = time.time()
    circ_x, meta = build_protocol_circuit(g, rounds, "X")
    circ_z, _ = build_protocol_circuit(g, rounds, "Z")
    if not (_noiseless_ok(circ_x) and _noiseless_ok(circ_z)):
        # Should be impossible for a spec that passed build_gauged; treat as
        # an evaluator-side assertion, not a candidate mistake.
        return _crash("protocol circuit failed the noiseless determinism self-check "
                      "(evaluator invariant violated — report this)")
    build_s = time.time() - t0

    t0 = time.time()
    try:
        px, pz, overall, (ex, sx, ez, sz) = sample_ler(circ_x, circ_z)
    except Exception:
        return _crash("sinter sampling crashed:\n" + traceback.format_exc())
    sim_s = time.time() - t0

    n_err = ex + ez
    est_std = 0.434 / np.sqrt(n_err) if n_err > 0 else None
    if overall <= 0.0:
        overall_eff = 1.0 / max(sx, sz)     # resolution floor: 0 observed errors
    else:
        overall_eff = overall
    margin = float(np.log10(GATE / overall_eff))
    Q = g["overhead"]
    diag = _graph_diag(edges, dummies)

    depths = f"SE depth/round: X={meta['depth_def'][0]} Z={meta['depth_def'][1]} ticks (base code: 8/8)"
    wstr = (f"max deformed check weight Z={g['wz_max']} X={g['wx_max']}, max qubit degree "
            f"{g['qdeg_max']}, worst Z-check routing +{g['route_w_max']} edges, longest "
            f"flux cycle {g['cycle_w_max']}")
    cstr = (f"graph: {len(edges)} edges, {len(dummies)} dummies; weakest cut "
            f"{diag['cut_side']} (conductance {diag['cut_conductance']}, "
            f"{diag['cut_edges']} crossing), Fiedler {diag['fiedler']}")
    lstr = (f"measured end-to-end: overall={overall_eff:.3e} (X-circuit {ex}/{sx}, "
            f"Z-circuit {ez}/{sz}; +-{est_std:.3f} decades)" if est_std else
            f"measured end-to-end: overall<{overall_eff:.1e} (0 errors in {sx}+{sz} shots)")

    if margin >= 0.0:
        score = float(Q_REF - Q) + float(min(2.0, margin))
        verdict = (
            f"RELIABLE at {Q} added elements ({g['E']} edge qubits + {g['n_av']} A_v + "
            f"{g['n_bp']} B_p checks), R={rounds} deformed rounds; score={score:+.2f} "
            f"(elements saved vs 41-element reference {Q_REF - Q:+d}, reliability margin "
            f"bonus {min(2.0, margin):.2f}, margin {margin:.2f} decades). {lstr}. {depths}; "
            f"{wstr}. {cstr}. To improve: remove elements (edges/dummies/implied checks) "
            f"or reduce rounds while staying reliable; watch the margin — it shrinks as "
            f"you cut. Structure moves that keep reliability cheap: keep check weights "
            f"and degrees low (schedule depth follows them), keep routing short, keep "
            f"flux cycles short, keep the graph expanding where the code's logicals "
            f"pinch it."
        )
    else:
        score = float(max(-30.0, -8.0 + margin))
        verdict = (
            f"UNRELIABLE: measured end-to-end error is {-margin:.2f} decades ABOVE the "
            f"reliability gate; score={score:.2f}. {lstr}. Config: {Q} elements "
            f"({g['E']} edges, {g['n_av']} A_v, {g['n_bp']} B_p), R={rounds}. {depths}; "
            f"{wstr}. {cstr}. Likely causes: too few deformed rounds (measurement "
            f"observable is protected only by A_v repetitions: R={rounds}), a graph cut "
            f"too sparse where a logical pinches (add edges/dummy structure across the "
            f"weakest cut), or heavy checks/degrees inflating schedule depth and idle "
            f"noise (split long routings/cycles with dummy vertices)."
        )

    public = {
        "combined_score": round(score, 3), "valid": 1,
        "overall_ler": overall_eff, "x_ler": px, "z_ler": pz,
        "gate_margin_decades": round(margin, 3),
        "elements": Q, "edge_qubits": g["E"], "av_checks": g["n_av"],
        "bp_checks": g["n_bp"], "dummies": len(dummies), "rounds": rounds,
        "depth_x": meta["depth_def"][0], "depth_z": meta["depth_def"][1],
        "wz_max": g["wz_max"], "wx_max": g["wx_max"], "qdeg_max": g["qdeg_max"],
        "route_w_max": g["route_w_max"], "cycle_w_max": g["cycle_w_max"],
        "fiedler": diag["fiedler"], "cut_conductance": diag["cut_conductance"],
        "cut_side": diag["cut_side"],
    }
    private = {
        "ler_ref": LER_REF, "gate": GATE, "gate_factor": GATE_FACTOR,
        "q_ref": Q_REF, "phys_p": PHYS_P,
        "shots": [sx, sz], "errors": [ex, ez], "score_std_decades": est_std,
        "build_s": round(build_s, 1), "sim_s": round(sim_s, 1),
    }
    return {"combined_score": score, "correct": True, "public": public,
            "private": private, "extra_data": {}, "text_feedback": verdict}

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def _force_utf8_stdio():
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
    print(f"Benchmark: p={PHYS_P}, gate={GATE:.3e} ({GATE_FACTOR}x reference), "
          f"budgets: {MAX_ERRORS} errors / {MAX_SHOTS} shots per circuit, "
          f"{N_WORKERS} workers")
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
    parser = argparse.ArgumentParser(description="gross_code_gauging end-to-end evaluator")
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)

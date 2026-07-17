"""
ShinkaEvolve INITIAL PROGRAM -- end-to-end gauging measurement of the logical
X_alpha on the gross code (Williamson & Yoder, arXiv:2410.02213).

GOAL. Design the complete GAUGING GADGET that measures the weight-12 logical
X_alpha of the [[144,12,12]] gross code, evaluated END-TO-END: the evaluator
builds the deformed code from your graph, schedules its syndrome extraction
with the CANONICAL deterministic scheduler (exact minimum edge coloring of
the Tanner graph, Konig -- so each phase's depth EQUALS the deformed Tanner
graph's max degree, a pure invariant of YOUR design), runs the full
measurement protocol (gauge-in -> R deformed rounds -> gauge-out) as a stim
circuit under circuit-level depolarizing noise at a THREE-POINT NOISE CURVE
(p = 0.7x, 1x, 1.4x the benchmark rate), decodes with BP+OSD, and MEASURES
the protocol's error at each point: the probability that the reported
measurement outcome is wrong or that any of the other 11 logical qubits is
corrupted. IN ADDITION it estimates the PROTOCOL FAULT DISTANCE d_hat =
min(R, lightest dressed logical of the deformed code found by a BP+OSD
attack on either CSS side, lightest circuit-level fault set found by a stim
search on the actual protocol circuits). d_hat exists because Monte Carlo at
the benchmark noise rates CANNOT see a low-weight fault tail: a small design
can beat the whole measured curve on level while failing catastrophically at
the operating point below it (an 11-edge spanning tree once 'won' this task
exactly that way -- its weight-7..8 dressed logicals and R=5 timelike cap
were invisible to sampling). Feasibility demands BOTH measured reliability
AND d_hat >= 10, the gross code's own circuit-level ceiling (Bravyi et al.
Nature 2024: every depth-7 schedule caps at d_circ <= 10, so 10 is full
protection here; 12 is unreachable through any gadget).

WHAT YOU RETURN (the whole design space of the paper, not just edges):
  propose_gadget() -> {"edges": [(u, v), ...], "rounds": R}
  * labels 0..11 = the 12 data qubits in supp(X_alpha) -- the graph VERTICES.
    Monomial map (paper App. B): 0:1, 1:x, 2:x^2, 3:x^3, 4:x^6, 5:x^7, 6:x^8,
    7:x^9, 8:xy^3, 9:x^5y^3, 10:x^7y^3, 11:x^11y^3.
  * labels 12..35 = OPTIONAL DUMMY VERTICES (paper Remark 2). A dummy carries
    NO physical qubit; its Gauss-law check is A_v = prod of X on its incident
    edge qubits. Dummies are how the paper builds Shor-style stars (Remark 12),
    surgery grids (Remark 11), and the THICKENED / cellulated layer stacks
    (Definition 3) that keep check weights low -- all expressible right here
    as extra labels + edges. A dummy costs 1 check (its A_v ancilla), and each
    edge costs 1 data qubit, so structure is never free -- it must pay for
    itself through the measured error.
  * every EDGE = one new data qubit (init |0>). Parallel edges allowed
    (the paper's double-gross example is a multigraph); no self-loops.
  * "rounds" R in [1, 24] = deformed-code syndrome rounds. The measurement
    outcome is protected in TIME only by the R repeats of the A_v checks --
    a chain of R measurement flips on one A_v is undetectable and flips the
    outcome (Cross et al. arXiv:2407.18393 Lemma 9: measurement fault
    distance = min(R, ...)), so d_hat <= R ALWAYS: R < 10 can never be
    feasible. Every round above ~10 only adds noise exposure on all 12
    logical qubits, so the useful range is narrow and R=10..12 is the
    natural region.

VALIDITY (evaluator-enforced; violations score -100 with a named reason):
  * all 12 support vertices + every used dummy in ONE connected component
    (Theorem 1: connectivity is what makes the measurement measure X_alpha);
  * labels in range, no self-loops, <= 60 edges, <= 24 dummies, R in [1,24];
  * the deformed code must have k=11 (checked; automatic when connected);
  * LDPC hardware caps: max deformed check weight <= 9 and max qubit degree
    <= 9 (the paper reference's profile is 7/7). These block the star-graph
    family: a heavy check is a hook-error channel (one ancilla fault spreads
    to ~weight/2 qubits) that no probe or affordable simulation can see.
    preview_gadget() predicts both numbers locally before you spend an eval.

WHAT THE EVALUATOR DERIVES FOR YOU (deterministic, same for every candidate):
  A_v Gauss-law checks; original Z-checks routed through your graph by exact
  minimum-weight T-joins; flux checks B_p on a minimum-weight cycle basis,
  reduced by the BB Z-check redundancy (the paper's 22-edge graph yields
  exactly its published 7 B_p: five triangles + two squares); the coloration
  schedule; the protocol circuit, detectors and byproduct corrections.

SCORE (higher is better; Q = edge_qubits + A_v checks + B_p checks):
  margin(p) = log10(reference_error(p) / your_error(p)) -- TRUE headroom vs
      the calibrated paper-reference gadget at the same noise rate.
  FEASIBLE := margin(p_gate) >= -0.30 AND margin(p_lo) >= -0.45   (measured
      curve non-inferior to the reference, worst case over the scored points)
      AND d_hat >= 10                                             (protected)
  feasible:    score = (41 - Q) + 3 * min(2, max(0, min(margin_lo, margin_gate)))
  infeasible:  score = -8 + min(0, margin_gate+0.30) + min(0, margin_lo+0.45)
                          - 1.5 * (10 - d_hat)    [clamped to >= -30]
  invalid spec: -100;  crash: -1000.
  The reference design (the paper's 22-edge graph, Q=41, R=12) scores ~0.
  Saving an element while staying FEASIBLE is +1 per element. The LER bonus
  is WORST-CASE over the low+gate points and only pays for TRUE dominance
  (matching the reference earns 0; 0.33 decades better everywhere = +1
  element; capped at +6), so end-to-end error is what tie-breaks designs at
  the same size -- but protection (d_hat) can never be traded away for it.
  The frontier is: SMALLEST gadget with FULL (ceiling-level) protection and
  a non-inferior measured curve. Everything is measured or attacked on the
  real protocol -- nothing is a static graph proxy.

LEVERS THAT ACTUALLY MOVE THE SCORE (all reported in feedback):
  * expansion where it matters: d_hat is attacked, not proxied -- when the
    attack finds a light dressed logical, the feedback names its support
    (which base qubits + which gadget edges). Reinforce THAT cut; Fiedler /
    sparsest_cut tools below are only local heuristics for screening;
  * check weight & degree -> schedule depth -> idle noise: depth per phase
    IS the deformed Tanner graph's max degree (exact minimum edge coloring),
    so heavy routed Z-checks, long flux cycles and high-degree vertices
    deepen every round for everyone. Dummy vertices can shorten routings and
    chop long cycles (the paper's whole reason for thickening) -- and the
    LDPC caps (weight/degree <= 9) are hard validity;
  * rounds R: d_hat <= R (R < 10 is auto-infeasible); above ~10 each round
    only adds exposure -- so tune within the narrow useful band;
  * raw size: every edge/check is an element of Q AND another noise location.

THE SEED below is the known-good flat design: the 18 matching edges (the
paper's weight-1-deformation motif -- NOT forced, but a strong starting
skeleton) + 6 sparsest-cut-greedy expansion edges, R=12, no dummies
(Q=45, feasible with d_hat=12, ~4 elements above the reference; the paper's
own 4 expansion edges reach Q=41, and a GeneCS-style spectral synthesis
reaches Q=37 at d_hat=10 -- so at least 8 elements of the seed are provably
removable). Directions the seed does NOT explore: pruning/replacing matching
edges, dummy-vertex structure (stars/layers/cellulation), R tuning within
the >= 10 band, parallel edges.

TOOLS PROVIDED (fixed, callable from the EVOLVE-BLOCK):
  graph_adjacency(edges)      {vertex: set(neighbors)}
  vertex_degrees(edges)       {vertex: degree}
  fiedler_value(edges)        algebraic connectivity (expansion proxy)
  sparsest_cut(edges)         (side, conductance, n_crossing) weakest cut
  preview_gadget(edges, R)    LOCAL structural preview mirroring the
                              evaluator: element count Q, #B_p after
                              redundancy reduction, max check weights/degree,
                              EXACT per-phase schedule depths (= Tanner max
                              degree), worst routing, longest cycle. Costs
                              milliseconds -- use it to screen designs BEFORE
                              spending an evaluation. (Structure only; the
                              measured noise curve needs the real evaluation.)
"""

import itertools
import numpy as np

# ---- fixed problem data (must match the evaluator) ----
L, M = 12, 6
N = L * M
def _idx(a, b): return (a % L) * M + (b % M)
F_TERMS = [(0,0),(1,0),(2,0),(3,0),(6,0),(7,0),(8,0),(9,0),(1,3),(5,3),(7,3),(11,3)]
SUPPORT = [_idx(a, b) for a, b in F_TERMS]
MAX_DUMMIES, MAX_EDGES, MAX_ROUNDS = 24, 60, 24

_Bpoly  = [(0, 3), (2, 0), (1, 0)]
_BTpoly = [((-c) % L, (-d) % M) for c, d in _Bpoly]
_conn = {((ci + cj) % L, (di + dj) % M) for ci, di in _BTpoly for cj, dj in _Bpoly} - {(0, 0)}
_fpos = {t: i for i, t in enumerate(F_TERMS)}
MATCHING_EDGES = sorted({
    (min(_fpos[(a, b)], _fpos[nb]), max(_fpos[(a, b)], _fpos[nb]))
    for (a, b) in F_TERMS for (cc, dd) in _conn
    for nb in [((a + cc) % L, (b + dd) % M)]
    if nb in _fpos and nb != (a, b)
})          # the 18 pairs of support vertices that share a Z-check

def _build_hz():
    _ATpoly = [((-c) % L, (-d) % M) for c, d in [(3, 0), (0, 2), (0, 1)]]
    HZ = np.zeros((N, 2 * N), np.int8)
    for a in range(L):
        for b in range(M):
            r = _idx(a, b)
            for c, d in _BTpoly: HZ[r, _idx(a + c, b + d)] ^= 1
            for c, d in _ATpoly: HZ[r, N + _idx(a + c, b + d)] ^= 1
    return HZ
_HZ0 = _build_hz()

# ---- graph primitives over the gadget graph (support labels 0..11 + dummies) ----
def _verts_of(edges):
    dummies = sorted({x for e in edges for x in e if x >= 12})
    return list(range(12)) + dummies

def graph_adjacency(edges):
    adj = {v: set() for v in _verts_of(edges)}
    for (u, w) in edges:
        adj[u].add(w); adj[w].add(u)
    return adj

def vertex_degrees(edges):
    deg = {v: 0 for v in _verts_of(edges)}
    for (u, w) in edges:
        deg[u] += 1; deg[w] += 1
    return deg

def fiedler_value(edges):
    verts = _verts_of(edges); pos = {v: i for i, v in enumerate(verts)}
    Lap = np.zeros((len(verts), len(verts)))
    for (u, w) in edges:
        i, j = pos[u], pos[w]
        Lap[i, i] += 1; Lap[j, j] += 1; Lap[i, j] -= 1; Lap[j, i] -= 1
    return float(sorted(np.linalg.eigvalsh(Lap))[1]) if len(verts) > 1 else 0.0

def sparsest_cut(edges):
    """Weakest cut (exact for <= 14 vertices, Fiedler sweep beyond)."""
    verts = _verts_of(edges); nV = len(verts); pos = {v: i for i, v in enumerate(verts)}
    adj = {i: set() for i in range(nV)}
    for (u, w) in edges:
        adj[pos[u]].add(pos[w]); adj[pos[w]].add(pos[u])
    deg = [len(adj[i]) for i in range(nV)]; vol = sum(deg); best = None
    if nV <= 14:
        for r in range(1, nV // 2 + 1):
            for S in itertools.combinations(range(nV), r):
                Ss = set(S)
                cut = sum(1 for i in S for j in adj[i] if j not in Ss)
                vS = sum(deg[i] for i in S); other = vol - vS
                cond = cut / min(vS, other) if min(vS, other) > 0 else 9.0
                if best is None or cond < best[0]:
                    best = (cond, cut, sorted(verts[i] for i in S))
    else:
        Lap = np.zeros((nV, nV))
        for (u, w) in edges:
            i, j = pos[u], pos[w]
            Lap[i, i] += 1; Lap[j, j] += 1; Lap[i, j] -= 1; Lap[j, i] -= 1
        vec = np.linalg.eigh(Lap)[1][:, 1]; order = np.argsort(vec)
        for cutpos in range(1, nV):
            Ss = set(order[:cutpos].tolist())
            cut = sum(1 for i in Ss for j in adj[i] if j not in Ss)
            vS = sum(deg[i] for i in Ss); other = vol - vS
            if min(vS, other) == 0: continue
            cond = cut / min(vS, other)
            if best is None or cond < best[0]:
                best = (cond, cut, sorted(verts[i] for i in Ss))
    cond, cut, side = best
    return side, cond, cut

# ---- local structural preview (mirrors the evaluator's derivations) ----
def _paths(verts, edges):
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

def _tjoin(T, paths):
    T = list(T)
    if not T: return frozenset()
    def matchings(rem):
        if not rem:
            yield []; return
        a = rem[0]
        for i in range(1, len(rem)):
            for m in matchings(rem[1:i] + rem[i + 1:]):
                yield [(a, rem[i])] + m
    best = None
    for m in matchings(T):
        es = set(); ok = True
        for (a, b) in m:
            if (a, b) not in paths: ok = False; break
            es ^= set(paths[(a, b)])
        if ok and (best is None or len(es) < len(best)):
            best = es
    return frozenset(best) if best is not None else None

def _rank2(Mx):
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

def preview_gadget(edges, rounds=12):
    """Structural preview of what the evaluator will build. Returns a dict
    (or {'error': reason}). Milliseconds; no simulation."""
    edges = [(min(int(u), int(v)), max(int(u), int(v))) for (u, v) in edges]
    if any(u == v for (u, v) in edges): return {"error": "self-loop"}
    if len(edges) > MAX_EDGES: return {"error": f">{MAX_EDGES} edges"}
    verts = _verts_of(edges)
    adj = graph_adjacency(edges)
    seen, stack = set(), [0]
    while stack:
        x = stack.pop()
        if x in seen: continue
        seen.add(x); stack.extend(adj[x] - seen)
    if set(verts) - seen:
        return {"error": f"disconnected: {sorted(set(verts) - seen)}"}
    E = len(edges); paths = _paths(verts, edges)
    nq = 2 * N + E
    # routing of the overlapping Z checks (min-weight T-joins), captured as FULL
    # (2N+E)-column rows [HZ0 | edge-indicator] so the cycle basis is redundancy-
    # reduced against them over the SAME columns the evaluator uses (build_gauged
    # reduces cycles against HZroute in the full base+edge space; a cycle is
    # redundant only when a combination of routed rows whose BASE part cancels
    # equals it — i.e. a BB Z-check dependency). Reducing over edge columns alone
    # over-reduces.
    route_w, edge_use = [], {}
    routed_rows = []
    for r in range(N):
        T = [i for i in range(12) if _HZ0[r, SUPPORT[i]]]
        row = np.zeros(nq, np.int8)
        row[:2 * N] = _HZ0[r]
        if T:
            g = _tjoin(T, paths)
            if g is None: return {"error": "routing failed"}
            route_w.append(len(g))
            for j in g:
                edge_use[j] = edge_use.get(j, 0) + 1
                row[2 * N + j] = 1
        routed_rows.append(row)
    # min cycle basis (Horton-lite):
    dim = E - len(verts) + 1
    cands = set()
    for j, (x, y) in enumerate(edges):
        for v in verts:
            if (v, x) in paths and (v, y) in paths:
                cset = set(paths[(v, x)]) ^ set(paths[(v, y)]); cset.add(j)
                dd = {}
                for k in cset:
                    for w in edges[k]: dd[w] = dd.get(w, 0) + 1
                if all(d % 2 == 0 for d in dd.values()):
                    cands.add(frozenset(cset))
    bp = {}
    for j, e in enumerate(edges): bp.setdefault(e, []).append(j)
    for js in bp.values():
        for a, b in itertools.combinations(js, 2): cands.add(frozenset({a, b}))
    # Redundancy reduction: keep only cycles INDEPENDENT of the routed Z rows —
    # exactly build_gauged's rank test over [HZroute ; kept_cycles] (full cols).
    stack = np.array(routed_rows, np.int8)
    base_rank = _rank2(stack)
    basis = []
    for cset in sorted(cands, key=lambda c: (len(c), sorted(c))):
        v = np.zeros(nq, np.int8)
        for j in cset: v[2 * N + j] = 1
        t = np.vstack([stack, v.reshape(1, -1)])
        rk = _rank2(t)
        if rk > base_rank:
            stack, base_rank = t, rk
            basis.append(cset)
            if len(basis) == dim: break
    n_bp = len(basis)                          # exact kept flux-check count
    n_av = len(verts)
    deg = vertex_degrees(edges)
    # EXACT schedule depths (the evaluator's minimum edge coloring achieves the
    # Tanner graph's max degree exactly): X phase = max(original check weight 6,
    # largest A_v weight, support-qubit column degree 4); Z phase = max(heaviest
    # routed check, longest KEPT flux cycle, busiest edge qubit's Z-degree, 6).
    avw_max = max((deg[v] + (1 if v < 12 else 0)) for v in verts)
    depth_x = max(6, avw_max, 4)
    cyc_use = {}
    for cset in basis:
        for j in cset:
            cyc_use[j] = cyc_use.get(j, 0) + 1
    edge_zdeg = max((edge_use.get(j, 0) + cyc_use.get(j, 0) for j in range(E)),
                    default=0)
    depth_z = max([6] + [6 + w for w in route_w] + [len(c) for c in basis]
                  + [edge_zdeg, 3])
    Q = E + n_av + n_bp
    return {
        "elements": Q, "edge_qubits": E, "av_checks": n_av, "bp_checks": n_bp,
        "dummies": len(verts) - 12, "rounds": int(rounds),
        "score_if_feasible": 41 - Q,
        "wz_max_est": max([6 + w for w in route_w] + [max((len(c) for c in basis), default=0)]),
        "route_w_max": max(route_w, default=0),
        "cycle_w_max": max((len(c) for c in basis), default=0),
        "max_degree": max(deg.values()),
        "depth_x": depth_x, "depth_z": depth_z,
        "fiedler": round(fiedler_value(edges), 3),
        "sparsest_cut": sparsest_cut(edges),
    }

# EVOLVE-BLOCK-START
def propose_gadget():
    """Return the gauging gadget spec: {"edges": [(u,v), ...], "rounds": R}.

    SEED: the 18 matching edges (weight-1 deformation motif) + sparsest-cut
    greedy expansion edges, no dummies, R=12. This is a feasible flat graph
    at Q=45 (score ~ -4 + bonus; d_hat=12); the reference design reaches
    Q=41 with only 4 expansion edges, a GeneCS-style synthesis reaches Q=37
    at d_hat=10, and NOTHING says a flat, dummy-free, R=12 graph is optimal.
    Use preview_gadget() to screen structure cheaply; the evaluator's
    feedback tells you where the dressed-logical attack bites when you cut
    too deep (it names the operator's support). Feasibility is BOTH measured
    reliability AND d_hat >= 10 -- never trade protection for size.
    """
    NUM_EXTRA = 6
    edges = list(MATCHING_EDGES)
    for _ in range(NUM_EXTRA):
        side, _cond, _ncross = sparsest_cut(edges)
        adj = graph_adjacency(edges)
        deg = vertex_degrees(edges)
        side_set = set(side)
        outside = [w for w in _verts_of(edges) if w not in side_set]
        cands = sorted((deg[u] + deg[w], u, w)
                       for u in side for w in outside if w not in adj[u])
        if not cands:
            break
        _, u, w = cands[0]
        edges.append((min(u, w), max(u, w)))
    return {"edges": edges, "rounds": 12}
# EVOLVE-BLOCK-END


def run_experiment():
    """Fixed entry point called by evaluate.py (NOT evolved).

    Returns the candidate's gadget-proposing FUNCTION so the evaluator can
    call it inside its own try/except (a crash -> score -1000) and then
    build, schedule, simulate and score the protocol with its own trusted
    code. shinka's run_shinka_eval calls this once with no kwargs.
    """
    return propose_gadget


if __name__ == "__main__":
    spec = run_experiment()()
    print(f"edges={len(spec['edges'])}, rounds={spec['rounds']}")
    print(preview_gadget(spec["edges"], spec["rounds"]))

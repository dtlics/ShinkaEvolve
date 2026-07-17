"""
ShinkaEvolve INITIAL PROGRAM -- minimum-edge spectral certification on the
gross-code gauging graph (v2, post-star-loophole rework).

GOAL. Find the SMALLEST simple graph on the 12 labelled ports (labels 0..11
-- the qubits of the weight-12 logical X_alpha of the [[144,12,12]] gross
code; optional dummy labels 12..35) whose Laplacian algebraic connectivity
satisfies lambda_2 >= 2.0 -- certified Cheeger constant >= 1, the
Williamson-Yoder Theorem 2 expansion bar for gauging graphs. This is a
clean combinatorial-spectral problem; no quantum knowledge is needed to
optimize it. (Provenance, honestly: the criterion is STRICTER than the
GeneCS compiler's real acceptance -- lambda_2 >= 2*beta, beta ~ 0.34 -- and
GeneCS makes no minimality claim; its constructor-style output E=24 and the
hand-crafted WY 22-edge graph, lambda_2 = 0.925 uncertified, are context
baselines, not a head-to-head.)

STATE OF KNOWLEDGE (verified; do not re-discover -- go BEYOND):
  * THE SEED below is the known record: a certified E=20 graph, degrees
    3^8 4^4, INTEGRAL Laplacian spectrum {0, 2^5, 4^3, 6^3}, lambda_2 = 2
    exactly -- found by a previous evolution run, independently
    re-verified, and NOT reproducible by 300-restart simulated annealing.
  * Certified E=21 is reachable by plain SA; circulants and dummy/bipartite
    constructions need E=24; dummies dilute lambda_2 (useless here).
  * E=19's lambda_2 ceiling found so far is ~1.59 (E=18: ~1.47), and no
    12-vertex 3-regular graph can certify (adjacency-trace argument). The
    Fiedler bound (lambda_2 <= vertex connectivity <= min degree) only
    forces E >= 12.
  TARGETS, in order of value:
  1. A certified E<=19 graph (+11 and up): the OPEN jackpot. Strong
     evidence says it does not exist -- so treat it as a long-shot
     discovery hunt: exotic structures (integral graphs, strongly-regular
     fragments, algebraic constructions over the port labels), not blind
     local search, are the plausible route.
  2. Structurally DISTINCT certified E=20 graphs (same score +10; the
     archive's novelty machinery keeps them): is the known record unique,
     or a family?
  3. Better secondary profiles at E=20..22: lower congestion, lower max
     degree (tiny tiebreaks reward them at equal E).

WHAT YOU RETURN:
  propose_graph() -> {"edges": [(u, v), ...]}
  * SIMPLE graph only: no self-loops, NO parallel edges (invalid, -100);
  * all 12 ports present and connected (with any used dummies) in one
    component; <= 60 edges; <= 24 dummies; max degree <= 12.

SCORE (higher is better; deterministic, < 1 s):
  CERTIFIED (lambda_2 >= 2.0):  6.0 + (24 - E)
                                 - 0.02*max(0, congestion-2)
                                 - 0.01*max(0, maxdeg-4)
      E=24 -> +6, E=21 -> +9, E=20 (this seed) -> +10, E<=19 -> +11+.
  UNCERTIFIED:  2.5*lambda_2 - 1.0*(#degree-1 vertices) - tiebreaks
      Monotone in lambda_2, capped below every competitive certified
      score. EDGE COUNT EARNS NOTHING BELOW CERTIFICATION -- v1's champion
      was an E=11 port-star (lambda_2 = 1.0, uncertified) that exploited a
      frontier-interpolation credit; that mechanism is deleted. Leaves are
      penalized because a degree-1 vertex caps lambda_2 at 1 (Fiedler),
      making certification impossible while it exists.
  invalid spec: -100;  crash: -1000.

TOOLS PROVIDED (fixed, callable from the EVOLVE-BLOCK):
  graph_lambda2(edges)         lambda_2 of the graph Laplacian
  fiedler_cut(edges)           (weak side, crossing count) -- the spectral
                               bottleneck; add edges across it to raise
                               lambda_2 fastest
  vertex_degrees(edges)        {vertex: degree}
  preview(edges)               {'edges', 'lam2', 'certified', 'leaves',
                                'score_if_certified', 'weak_cut', ...} --
                               microseconds; screen every idea before
                               returning it

HOW TO WRITE A STRONG CANDIDATE. propose_graph() may run arbitrary bounded
computation: implement a real search (constructions + local moves screened
with preview()), seed any RNG deterministically, and return the best
still-valid graph found. For target 1, bias toward structure: the known
record is an integral graph -- enumerate/perturb algebraic constructions
(vertex-transitive-ish patterns, graph joins, subdivided expanders) rather
than pure random swaps, which are known to stall at E=21.
"""

import numpy as np

# ---- fixed problem data ----
N_PORTS, MAX_DUMMIES, MAX_EDGES, MAX_DEGREE = 12, 24, 60, 12
L, M = 12, 6
F_TERMS = [(0,0),(1,0),(2,0),(3,0),(6,0),(7,0),(8,0),(9,0),(1,3),(5,3),(7,3),(11,3)]

_Bpoly  = [(0, 3), (2, 0), (1, 0)]
_BTpoly = [((-c) % L, (-d) % M) for c, d in _Bpoly]
_conn = {((ci + cj) % L, (di + dj) % M) for ci, di in _BTpoly for cj, dj in _Bpoly} - {(0, 0)}
_fpos = {t: i for i, t in enumerate(F_TERMS)}
MATCHING_EDGES = sorted({
    (min(_fpos[(a, b)], _fpos[nb]), max(_fpos[(a, b)], _fpos[nb]))
    for (a, b) in F_TERMS for (cc, dd) in _conn
    for nb in [((a + cc) % L, (b + dd) % M)]
    if nb in _fpos and nb != (a, b)
})          # the 18-edge path-matching motif (context)
PAPER_EXPANSION = [(2, 9), (2, 4), (9, 11), (10, 11)]   # WY App. B Eq. 5

# The verified record: certified E=20, degrees 3^8 4^4, integral spectrum
# {0, 2^5, 4^3, 6^3} (lambda_2 = 2 exactly). Found by run spectral_v1,
# independently re-verified from this raw edge list.
RECORD_E20 = [
    (0, 1), (0, 8), (0, 11), (1, 2), (1, 5), (2, 3), (2, 6), (2, 10),
    (3, 4), (3, 11), (4, 5), (4, 8), (5, 6), (5, 9), (6, 7), (7, 8),
    (7, 11), (8, 9), (9, 10), (10, 11),
]


def _verts_of(edges):
    dummies = sorted({x for e in edges for x in e if x >= N_PORTS})
    return list(range(N_PORTS)) + dummies


def vertex_degrees(edges):
    deg = {v: 0 for v in _verts_of(edges)}
    for (u, w) in edges:
        deg[u] += 1; deg[w] += 1
    return deg


def _laplacian(edges):
    verts = _verts_of(edges)
    pos = {v: i for i, v in enumerate(verts)}
    n = len(verts)
    Lap = np.zeros((n, n))
    for (u, w) in edges:
        i, j = pos[u], pos[w]
        Lap[i, i] += 1; Lap[j, j] += 1
        Lap[i, j] -= 1; Lap[j, i] -= 1
    return Lap, verts


def graph_lambda2(edges):
    Lap, _ = _laplacian(edges)
    return float(np.linalg.eigvalsh(Lap)[1]) if Lap.shape[0] > 1 else 0.0


def fiedler_cut(edges):
    """(weak side, #crossing edges): the Fiedler-vector sign split — the
    spectral bottleneck. Edges across it raise lambda_2 fastest."""
    Lap, verts = _laplacian(edges)
    w, V = np.linalg.eigh(Lap)
    fied = V[:, 1]
    side = sorted(verts[i] for i in range(len(verts)) if fied[i] < 0)
    if len(side) > len(verts) // 2:
        side = sorted(set(verts) - set(side))
    sset = set(side)
    crossing = sum(1 for (u, v) in edges if (u in sset) != (v in sset))
    return side, crossing


def preview(edges):
    """Microsecond screening mirror of the evaluator's core quantities."""
    edges = [(min(int(u), int(v)), max(int(u), int(v))) for (u, v) in edges]
    if any(u == v for (u, v) in edges):
        return {"error": "self-loop"}
    if len(edges) != len(set(edges)):
        return {"error": "parallel edge (simple graphs only)"}
    if len(edges) > MAX_EDGES:
        return {"error": f">{MAX_EDGES} edges"}
    deg = vertex_degrees(edges)
    if max(deg.values()) > MAX_DEGREE:
        return {"error": f"degree {max(deg.values())} > {MAX_DEGREE}"}
    verts = _verts_of(edges)
    adj = {v: set() for v in verts}
    for (u, w) in edges:
        adj[u].add(w); adj[w].add(u)
    seen, stack = set(), [0]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x); stack.extend(adj[x] - seen)
    if set(verts) - seen:
        return {"error": f"disconnected: {sorted(set(verts) - seen)}"}
    lam2 = graph_lambda2(edges)
    side, crossing = fiedler_cut(edges)
    E = len(edges)
    leaves = sum(1 for v in verts if deg[v] == 1)
    return {"edges": E, "dummies": len(verts) - N_PORTS, "leaves": leaves,
            "lam2": round(lam2, 4), "certified": lam2 >= 2.0 - 1e-9,
            "score_if_certified": 6.0 + (24 - E),
            "weak_cut": side, "weak_cut_crossing": crossing,
            "max_degree": max(deg.values())}


# EVOLVE-BLOCK-START
def propose_graph():
    """Return {"edges": [(u, v), ...]} (simple graph).

    SEED: the verified record itself — certified E=20, integral spectrum
    {0, 2^5, 4^3, 6^3}, score +10. It is the strongest known solution, so
    the job is to go BEYOND it, in order of value:
      1. certified E<=19 (+11; open jackpot, likely nonexistent — hunt with
         STRUCTURED constructions: integral graphs, algebraic patterns over
         the ports, joins/subdivisions of small expanders; blind swap-SA is
         known to stall at E=21);
      2. structurally DISTINCT certified E=20 graphs (novelty keeps them);
      3. equal-E refinements (congestion, max degree) via the tiny
         tiebreaks.
    Use preview() to screen every idea in microseconds. Removing any single
    edge from THIS seed drops lambda_2 below 2 (it is edge-minimal in that
    sense) — a certified E=19 needs a genuinely different structure, not a
    pruned copy.
    """
    return {"edges": list(RECORD_E20)}
# EVOLVE-BLOCK-END


def run_experiment():
    """Fixed entry point called by evaluate.py (NOT evolved). Returns the
    candidate's graph-proposing FUNCTION so the evaluator can call it inside
    its own try/except and score with its own trusted code."""
    return propose_graph


if __name__ == "__main__":
    spec = run_experiment()()
    print(f"edges={len(spec['edges'])}")
    print(preview(spec["edges"]))

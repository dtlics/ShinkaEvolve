"""
ShinkaEvolve INITIAL PROGRAM -- the GeneCS-criteria spectral synthesis RACE
on the gross-code gauging graph.

GOAL. Find the SMALLEST graph that GeneCS's own acceptance criterion
certifies, on GeneCS's own benchmark instance -- the gauging graph for the
weight-12 logical X_alpha of the [[144,12,12]] gross code. The criterion
(reverse-engineered from their published outcome; see the task README):

    lambda_2(graph Laplacian) >= 2.0     (certified Cheeger constant >= 1,
                                          the full Williamson-Yoder
                                          expansion bar)

In GeneCS's generic accounting, checks = #vertices A_v checks + cycle checks
= E + 1 regardless of dummies, so their qubits+checks objective equals
2E + 1: THE OBJECTIVE IS PURELY THE EDGE COUNT E. Their compiler heuristic
(add-only deficit-weighted random edge addition with first-passage
acceptance, 100 restarts) reaches E = 24 on every measured seed, and their
published gross-code result (24 qubits / 25 checks) matches it exactly.
Score = (24 - E) once certified: E = 24 matches the published compiler,
E <= 23 BEATS it at its own game.

WHAT YOU RETURN:
  propose_graph() -> {"edges": [(u, v), ...]}
  * labels 0..11 = the 12 qubits of supp(X_alpha) -- the ports. Monomial map
    (WY App. B): 0:1, 1:x, 2:x^2, 3:x^3, 4:x^6, 5:x^7, 6:x^8, 7:x^9,
    8:xy^3, 9:x^5y^3, 10:x^7y^3, 11:x^11y^3.
  * labels 12..35 = OPTIONAL dummy vertices -- a move the GeneCS pipeline
    does NOT have, and FREE in its accounting (+1 A_v check, -1 cycle
    check): a well-placed hub or subdivision changes which topologies exist.
  * parallel edges allowed (another move their simple-graph family lacks);
    no self-loops; <= 60 edges; <= 24 dummies; max degree 12 (their bound).

WHERE THE ROOM IS (measured -- their E=24 is NOT optimal):
  * their search only ADDS edges to the fixed 18-edge path-matching graph
    and stops the instant lambda_2 crosses -- it never removes a redundant
    earlier addition, never swaps, and never considers graphs that DROP
    matching edges (any connected graph works: the deformation routes
    through paths);
  * MEASURED: plain simulated annealing over edge swaps already finds a
    CERTIFIED E=21 graph (lambda_2 = 2.000; score +3) and lambda_2 = 2.28
    at E=23 -- their frontier is beatable by +0.34..+0.80 at every size.
    So the real discovery target is the TRUE MINIMUM certified E: the
    Fiedler bound (lambda_2 <= vertex connectivity <= min degree) only
    forces E >= 12, and where in [12, 21] the boundary lies is open;
  * structured graphs (circulant-like patterns over the monomial labels,
    near-regular expanders) beat random matchings on lambda_2-per-edge; a
    dummy hub is free in their accounting; a doubled edge is a legal move;
  * the evaluator's feedback reports the WEAKEST SPECTRAL CUT (Fiedler
    split) and its crossing count every eval -- edges across that cut raise
    lambda_2 fastest; edges inside it are candidates for removal.

SCORE (higher is better; deterministic, < 1 s per eval): the FRONTIER
  score -- E_theirs(lam2) = edges THEIR compiler needs to reach your
  expansion level (measured anchors (0.438,18) (0.70,19) (1.105,20)
  (1.202,21) (1.438,22) (1.722,23) (2.0,24), piecewise linear, capped at
  24 above the acceptance threshold):
  lam2 >= 0.438 (the G0 level):  3.0 + E_theirs(lam2) - E
                                   - 0.02*max(0, congestion-2)
                                   - 0.01*max(0, maxdeg-4)
      The scale: THIS SEED boots at ~+0.5 (valid, modest); +3.0 is
      GeneCS-compiler PARITY (their measured outputs land there);
      anything above +3 beats the published pipeline at its own beta
      knob; CERTIFIED (lam2 >= 2.0) graphs earn +1 more per edge below
      24 (the known annealing result, certified E=21, scores +6; the
      open-question region below that is worth more).
  lam2 < 0.438:  -4 - 6*(0.438 - lam2) - 0.05*E   (no frontier credit
      below their own start graph; gradient points back up)
  invalid spec: -100;  crash: -1000.

THE SEED is the hand-crafted WY/IBM 22-edge graph (18 matching + 4
expansion edges -- the graph of the 41-element paper gadget). NOTE: the
certificate REJECTS it (lambda_2 = 0.925, certified Cheeger 0.46), even
though its actual deformed distance 12 is proven by integer programming --
the spectral criterion cannot see real distance, and racing it anyway is
the point of this task (optimizer-vs-optimizer, same rules). From this seed
you must first CROSS the certificate boundary (add/re-route edges across
weak cuts; their compiler needs 24 edges to get there), then SHRINK below
E=24 using the moves their pipeline lacks (removal, swaps, dummies,
parallel edges, dropping matching edges). Any certified E <= 23 should also
be cross-scored on the real protocol via ../gross_code_gauging/.

TOOLS PROVIDED (fixed, callable from the EVOLVE-BLOCK):
  graph_lambda2(edges)         lambda_2 of the multigraph Laplacian
  fiedler_cut(edges)           (weak side, crossing count) -- the spectral
                               bottleneck; raise lambda_2 by crossing it
  vertex_degrees(edges)        {vertex: degree}
  preview(edges)               {'edges', 'lam2', 'certified',
                                'score_if_certified', 'weak_cut', ...} --
                               microseconds; screen every idea before
                               returning it
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
})          # the 18-edge path-matching motif (GeneCS's G0)
PAPER_EXPANSION = [(2, 9), (2, 4), (9, 11), (10, 11)]   # WY App. B Eq. 5


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
    return {"edges": E, "dummies": len(verts) - N_PORTS,
            "lam2": round(lam2, 4), "certified": lam2 >= 2.0 - 1e-9,
            "score_if_certified": 24 - E,
            "qubits_plus_checks": 2 * E + 1,
            "weak_cut": side, "weak_cut_crossing": crossing,
            "max_degree": max(deg.values())}


# EVOLVE-BLOCK-START
def propose_graph():
    """Return {"edges": [(u, v), ...]}.

    SEED: the hand-crafted WY/IBM 22-edge graph (18 matching + 4 expansion
    edges) of the 41-element paper gadget. The certificate REJECTS it
    (lambda_2 = 0.925 < 2.0) despite its IP-proven distance 12 -- so the
    first job is to cross the certificate boundary (the feedback and
    fiedler_cut() point at the weakest cut every eval; edges across it buy
    the most lambda_2), and the second job is to get back UNDER 24 edges
    using moves the GeneCS compiler does not have: remove redundant edges
    (their first-passage search never re-checks), swap across cuts, drop
    matching edges, place dummy hubs (free in this accounting), double
    strategic edges. preview() screens every idea in microseconds -- use it
    liberally inside this function and return the best graph you find.
    """
    edges = list(MATCHING_EDGES) + list(PAPER_EXPANSION)
    return {"edges": edges}
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

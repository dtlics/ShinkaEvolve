"""
ShinkaEvolve INITIAL PROGRAM  --  gauging measurement of the logical Xalpha on
the gross code (Williamson & Yoder, arXiv:2410.02213, Appendix B).

GOAL. Measure the logical operator Xalpha on the [[144,12,12]] gross code by
"gauging" it: put a graph G on the 12 qubits in Xalpha's support, add one
ancilla qubit per edge, and deform the code. The deformed code must keep the
full distance 12. You choose the EDGES of G.

THE KEY STRUCTURE (this is the paper's beautiful result -- exploit it).
The deformed-code distance is governed by the EXPANSION of the graph G. A
low-weight logical operator lives on a SPARSE VERTEX CUT of G, so the distance
is limited by G's worst (sparsest) cut, and the only way to raise the distance
is to reinforce that cut with edges. Concretely: the 18 mandatory base edges
leave G with a sharp bottleneck -- its sparsest cut is crossed by only 2 edges --
and that is exactly why the base-only distance is 8, not 12. Every one of the
paper's 4 extra edges crosses that bottleneck. (The y^0 / y^3 monomial blocks are
NOT the bottleneck -- the base graph already connects them with 12 of its 18
edges.) So this is a graph-expansion problem, not blind edge enumeration.

WHAT IS FIXED (done for you by the evaluator -- you cannot change it):
  * the gross code and the logical Xalpha;
  * the 12 VERTICES of G = the 12 qubits of Xalpha (the monomials of f);
  * the 18 mandatory MATCHING edges (BASE_EDGES): connect g,d in f whenever they
    share a Z-check (g = B^T_i B_j d). The evaluator always adds these.
  * the deformed-code construction (Av/Bp checks) and the distance computation.

WHAT YOU EVOLVE: propose_extra_edges() -> a list of EXTRA edges added on top of
the 18 matching edges. Each edge is an unordered pair (u, v) of vertices from
VERTICES. Each extra edge costs exactly ONE ancilla qubit.

TOOLS PROVIDED (fixed, call them from propose_extra_edges):
  * graph_adjacency(extra)  -> {vertex: set(neighbours)} of G = base + extra
  * vertex_degrees(extra)   -> {vertex: degree}
  * fiedler_value(extra)    -> algebraic connectivity (2nd Laplacian eigenvalue);
                               higher = more expansion = (heuristically) higher distance
  * sparsest_cut(extra)     -> (cut_side, conductance, n_crossing_edges): the WEAKEST
                               cut of G (the bottleneck). Bridge it to raise the distance.
The evaluator's text feedback ALSO reports, every step, your graph's weakest cut,
its Fiedler value, and -- on a failure -- which vertices the limiting low-weight
logical sits on (where the distance is pinched). Use these signals.

VALIDITY (enforced by the evaluator; a violation scores -1000):
  * every edge is a pair of two DISTINCT vertices, both in VERTICES; no self-loops.
  (Duplicate / parallel edges are allowed but only waste a qubit.)

HARD CONSTRAINT: deformed-code distance must equal 12. Distance is estimated with
a hardened BP+OSD UPPER bound (any near-optimal claim is re-verified, and the final
winner is certified exactly with integer programming, as in the paper). IMPORTANT:
graph conductance is only a PROXY for the true quantum distance -- the distance
depends on the SPECIFIC logical operators, so the minimal edge set is subtler than
max-conductance greedy. Combine the cut/expansion structure with the dx/dz +
limiting-logical feedback to decide which edges actually matter.

OBJECTIVE: minimise the ancilla qubits (extra edges) while KEEPING distance 12.
SCORE (higher is better): a distance-12 graph scores 24 - total_qubits (total =
18 + extra_edges). So 24 qubits -> 0, the paper's 22 qubits -> +2, every qubit
saved past that is +1. Distance < 12 scores about -90; an invalid edge -1000.

THE SEED returns the sparsest-cut-greedy graph at 6 extra edges (24 qubits, score
0): a feasibility-verified distance-12 baseline that reinforces the bottleneck. It
overspends -- the SAME greedy reaches distance 12 at just 4 edges (22 qubits, the
paper's count), so dropping the 2 non-critical edges already scores +2. Pushing
BELOW 22 (beating the paper, which did NOT prove 22 minimal) is the open frontier
and needs a smarter edge choice than greedy.

NOT YOUR JOB (handled downstream, as in the paper): once edges are fixed, a
min-weight cycle basis keeps the code LDPC (degree <= 7); it changes neither the
distance nor the qubit count, so only the edge SET matters here.
"""

import itertools
import numpy as np

# ---- fixed problem data (must match the evaluator) ----
L, M = 12, 6
def _idx(a, b): return (a % L) * M + (b % M)
F_TERMS  = [(0,0),(1,0),(2,0),(3,0),(6,0),(7,0),(8,0),(9,0),(1,3),(5,3),(7,3),(11,3)]
VERTICES = [_idx(a, b) for a, b in F_TERMS]                 # 12 L-qubit indices
VERT_AB  = {_idx(a, b): (a, b) for a, b in F_TERMS}         # index -> (a,b) monomial
_B   = [(0, 3), (2, 0), (1, 0)]                             # B = y^3 + x^2 + x
_BT  = [((-c) % L, (-d) % M) for c, d in _B]
_CONN = {((ci+cj) % L, (di+dj) % M) for ci, di in _BT for cj, dj in _B} - {(0, 0)}
_FS  = set(F_TERMS)
BASE_EDGES = sorted(                                        # the 18 forced edges
    {frozenset((_idx(a, b), _idx((a+cc) % L, (b+dd) % M)))
     for a, b in F_TERMS for cc, dd in _CONN
     if ((a+cc) % L, (b+dd) % M) in _FS and ((a+cc) % L, (b+dd) % M) != (a, b)},
    key=lambda e: sorted(e))

# ---- fixed GRAPH PRIMITIVES on G = base + extra (call these from the EVOLVE-BLOCK) ----
def graph_adjacency(extra):
    """{vertex: set(neighbours)} for G = BASE_EDGES + extra."""
    adj = {v: set() for v in VERTICES}
    for e in list(BASE_EDGES) + [frozenset(p) for p in extra]:
        u, w = tuple(e); adj[u].add(w); adj[w].add(u)
    return adj

def vertex_degrees(extra):
    """{vertex: degree} for G = BASE_EDGES + extra."""
    adj = graph_adjacency(extra)
    return {v: len(adj[v]) for v in VERTICES}

def fiedler_value(extra):
    """Algebraic connectivity (2nd-smallest Laplacian eigenvalue) of G; a proxy
    for expansion -- higher tends to mean higher code distance."""
    idx = {v: i for i, v in enumerate(VERTICES)}; nV = len(VERTICES)
    Lap = np.zeros((nV, nV))
    for e in list(BASE_EDGES) + [frozenset(p) for p in extra]:
        u, w = tuple(e); i, j = idx[u], idx[w]
        Lap[i, i] += 1; Lap[j, j] += 1; Lap[i, j] -= 1; Lap[j, i] -= 1
    return float(sorted(np.linalg.eigvalsh(Lap))[1])

def sparsest_cut(extra):
    """Weakest cut of G = base + extra: returns (cut_side, conductance, n_crossing).
    cut_side is the smaller side (sorted vertex ids); 12 vertices -> exact brute
    force. This bottleneck is what limits the code distance -- bridge it."""
    adj = graph_adjacency(extra); nV = len(VERTICES)
    deg = {v: len(adj[v]) for v in VERTICES}; vol = sum(deg.values()); best = None
    for r in range(1, nV // 2 + 1):
        for S in itertools.combinations(VERTICES, r):
            Sset = set(S)
            cut = sum(1 for u in S for w in adj[u] if w not in Sset)
            vS = sum(deg[u] for u in S); other = vol - vS
            cond = cut / min(vS, other) if min(vS, other) > 0 else 9.0
            if best is None or cond < best[0]: best = (cond, cut, sorted(S))
    cond, cut, S = best
    return S, cond, cut

# EVOLVE-BLOCK-START
def propose_extra_edges():
    """Return a list of extra edges (u, v), with u, v in VERTICES.

    Seed = SPARSEST-CUT GREEDY: repeatedly find G's weakest cut and add the cheapest
    edge crossing it (lowest combined degree), NUM_EXTRA times. This reinforces the
    expansion bottleneck that limits the distance -- the way the paper's edges do.
    At NUM_EXTRA=6 it is a feasibility-verified distance-12 graph (24 qubits, score 0)
    that OVERSPENDS: the same greedy holds distance 12 at 4 edges (22 qubits, +2), so
    dropping the 2 non-critical edges already matches the paper. Beating 22 needs a
    smarter choice than greedy (conductance is only a proxy for the true distance) --
    use sparsest_cut()/fiedler_value()/vertex_degrees() with the evaluator's dx/dz +
    cut + limiting-logical feedback to pick edges that the true distance actually needs.
    """
    NUM_EXTRA = 6  # 24 qubits, score 0; 4 already suffice (22q, +2) -- prune, then go lower

    extra = []
    for _ in range(NUM_EXTRA):
        side, _cond, _ncross = sparsest_cut(extra)
        adj = graph_adjacency(extra); deg = vertex_degrees(extra)
        side_set = set(side)
        outside = [w for w in VERTICES if w not in side_set]
        cands = sorted((deg[u] + deg[w], u, w)
                       for u in side for w in outside if w not in adj[u])
        if not cands:
            break
        _, u, w = cands[0]
        extra.append((u, w))
    return extra
# EVOLVE-BLOCK-END


def run_experiment():
    """Fixed entry point called by evaluate.py (NOT evolved).

    Returns the candidate's edge-proposing FUNCTION so the evaluator can call it
    inside its own try/except (a crash -> score -1000) and then build + score the
    deformed code with its own trusted functions. shinka's run_shinka_eval calls
    this once with no kwargs (num_runs=1); aggregate_metrics_fn receives
    [propose_extra_edges] and drives the build/distance/scoring itself.
    """
    return propose_extra_edges


if __name__ == "__main__":
    print(run_experiment()())

"""GeneCS-style baseline synthesizer for the gross-code gauging gadget.

A faithful-at-this-scale reimplementation of the ancilla-graph synthesis of
Zhou, Javadi-Abhari & Li, "GeneCS: Synthesizing Resource-Efficient Code
Surgery for Arbitrary Quantum Stabilizer Codes" (arXiv:2605.21746), Sec. 4
(Algorithm 1, "Conditioned Expander Construction"), specialized to the
weight-12 logical X_alpha of the [[144,12,12]] gross code so its output can
be scored apple-to-apple by THIS task's evaluator (same deformation, same
Konig schedule, same protocol, same decoder, same noise curve).

The GeneCS design space is the SAME (vertices, edges) gauging space as this
task (their vertices = X-checks A_v, edges = ancilla qubits, independent
cycles = Z-checks B_p — exactly Williamson-Yoder's construction, which the
paper generalizes):

  * The PATH-MATCHING graph G0 (their Sec. 2.3): for every stabilizer S of
    the base code that anticommutes with the logical on a set K(S,L), add a
    path matching of K(S,L). For X_alpha of the gross code every overlapping
    Z-check anticommutes on exactly 2 support qubits, so G0 IS the 18-edge
    matching motif (each pair of support qubits sharing a Z-check), 3-regular
    on the 12 ports. (lambda_2(G0) = 0.438.)
  * Algorithm 1 Phase 1 ("Regularization to Maximum Degree"): Delta = max
    degree of G0; repeatedly add an edge {u,v} between deficit vertices
    (deg < Delta), chosen with probability proportional to
    (Delta - deg(u)) * (Delta - deg(v)), recomputing lambda_2 of the graph
    Laplacian after EVERY addition; accept as soon as lambda_2 >= 2*beta
    (Cheeger inequality: Cheeger constant >= lambda_2 / 2 >= beta). G0 here
    is already 3-regular, so Phase 1 is a no-op unless Delta is overridden.
  * Algorithm 1 Phase 2 ("Degree Augmentation"): add random 1-regular layers
    (random perfect matchings, drawn pair-by-pair uniformly), checking
    lambda_2 after each single edge and stopping the instant the threshold
    clears; capped at tau layers.
  * Restarts: the whole construction is randomized; run `restarts` times and
    keep the smallest accepted graph (fewest edges; tie-break lower Delta,
    then lower congestion).
  * Congestion / degree control (their Sec. 5): candidate edges that would
    push a vertex past `max_degree` are rejected. (The full paper also
    maintains a dynamic cycle basis with load-aware forest updates; at 12
    vertices this evaluator derives the exact minimum cycle basis itself, so
    basis congestion is measured post hoc and used only as a tie-break.)

GeneCS has NO protocol/rounds treatment (its LER check is a static deformed-
code simulation; see README), so the returned spec uses R = 12 = d, the same
convention as the paper reference gadget, unless overridden.

SCOPE / FIDELITY. This implements the mono-layer Algorithm-1 path (plus an
exact minimum-cycle-basis congestion tie-break — at 12 vertices we can afford
exactness where the paper needs Algorithms 2-4's dynamic maintenance). It
does NOT implement thickening (Cartesian product with a path graph, their
Sec. 5.1) or cellulation: those exist to certify relative Cheeger >= 1, and
this task's evaluator VERIFIES protection directly (dressed-logical attack +
fault-distance estimate) instead of requiring the certificate — mining the
gap below the certificate is this task's core question. For the record, the
GeneCS paper's own certified gross-code result (Full-Opt pipeline) is
24 ancilla qubits + 25 checks = 49 elements at degrees 7/8 (their Sec. 7;
ablations: Exp-Opt 122/123, Cong-Opt 89/90; baselines: WY hand-crafted
22 qubits + 19 checks = 41 at degrees 7/7, CKBB 348/342, Gauge 239/240 with
beta = 0.34) — i.e. certified-GeneCS is LARGER than the WY reference on this
code; the >85% headline reductions are vs the generic pipelines. The
mono-layer synthesis here, scored by OUR evaluator, is the apple-to-apple
frontier this task actually competes with.

Usage:
    python tasks/gross_code_gauging/genecs.py                # one gadget, beta=0.46
    python tasks/gross_code_gauging/genecs.py --beta 0.5 --restarts 400
    (import) genecs_spec(beta=..., seed=...) -> {"edges": [...], "rounds": 12}

The default beta = 0.46 matches the spectral level the PAPER's own 22-edge
gadget certifies (lambda_2 = 0.925 -> Cheeger >= 0.46), i.e. "what would
GeneCS build if asked for the same certified expansion the hand-crafted
reference has?" — the honest apple-to-apple anchor. beta is deliberately a
knob: the GeneCS paper only states beta is "a constant smaller than 1" (it
never publishes its own Alg-1 beta; tau is likewise never given a number).
Measured beta -> size frontier here (restarts=150, seed 1): beta 0.35 ->
19 edges (Q=35), 0.46 -> 20 (Q=37), 0.55 -> 21 (Q=39), 0.65 -> 22 (Q=41),
0.8 -> 23 (Q=43). The evaluator's dressed-logical attack measures the
beta=0.46 graph at X-distance 10 (feasible, score +4 — the current benchmark)
and the beta=0.35 graph at X-distance 9 (UNPROTECTED — rejected, despite its
spectral certificate; certified floors are 5.6 and 4.2 respectively, so the
certificate is loose in BOTH directions: 0.46 under-promises a real 10, 0.35
would let a broken graph through — which is exactly why this task verifies
by attack instead of gating on the certificate).
"""

from __future__ import annotations

import argparse
import itertools
import random

import numpy as np

# ---- fixed problem data (identical to initial.py / evaluate.py) ----
L, M = 12, 6
N = L * M
def _idx(a, b): return (a % L) * M + (b % M)
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
})          # the 18-edge path-matching graph G0 (3-regular on the 12 ports)

N_V = 12


def _lambda2(edges):
    """Second-smallest eigenvalue of the (multi)graph Laplacian on the 12 ports."""
    Lap = np.zeros((N_V, N_V))
    for (u, v) in edges:
        Lap[u, u] += 1; Lap[v, v] += 1
        Lap[u, v] -= 1; Lap[v, u] -= 1
    return float(np.linalg.eigvalsh(Lap)[1])


def _degrees(edges):
    deg = [0] * N_V
    for (u, v) in edges:
        deg[u] += 1; deg[v] += 1
    return deg


def _congestion(edges):
    """Max number of minimum-cycle-basis cycles through any edge (rho, their
    Sec. 5 congestion), computed exactly on the small graph via the same
    Horton-style construction the evaluator uses. Tie-break only."""
    E = len(edges)
    verts = list(range(N_V))
    dim = E - N_V + 1
    if dim <= 0:
        return 0
    # shortest paths as edge sets
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
            es = set(); x = t
            while prev[x] is not None:
                px, j = prev[x]; es.add(j); x = px
            paths[(s, t)] = frozenset(es)
    cands = set()
    for j, (x, y) in enumerate(edges):
        for v in verts:
            c = set(paths[(v, x)]) ^ set(paths[(v, y)]); c.add(j)
            dd = {}
            for k in c:
                for w in edges[k]:
                    dd[w] = dd.get(w, 0) + 1
            if all(d % 2 == 0 for d in dd.values()):
                cands.add(frozenset(c))
    by_pair = {}
    for j, e in enumerate(edges):
        by_pair.setdefault(tuple(e), []).append(j)
    for js in by_pair.values():
        for a, b in itertools.combinations(js, 2):
            cands.add(frozenset({a, b}))
    # greedy independent by weight
    def _rank(Mx):
        Mx = Mx.copy() % 2; r = 0
        for c in range(Mx.shape[1]):
            piv = next((i for i in range(r, Mx.shape[0]) if Mx[i, c]), None)
            if piv is None: continue
            Mx[[r, piv]] = Mx[[piv, r]]
            for i in range(Mx.shape[0]):
                if i != r and Mx[i, c]: Mx[i] ^= Mx[r]
            r += 1
        return r
    basis, cur = [], np.zeros((0, E), np.int8)
    for c in sorted(cands, key=lambda c: (len(c), sorted(c))):
        v = np.zeros(E, np.int8)
        for j in c: v[j] = 1
        t = np.vstack([cur, v.reshape(1, -1)])
        if _rank(t) > cur.shape[0]:
            cur = t; basis.append(c)
            if len(basis) == dim: break
    use = [0] * E
    for c in basis:
        for j in c:
            use[j] += 1
    return max(use) if use else 0


def genecs_graph(beta=0.46, seed=0, restarts=200, tau=8, max_degree=8,
                 delta_override=None, allow_parallel=False, verbose=False):
    """Run Algorithm 1 (conditioned expander construction) `restarts` times;
    return (edges, info) for the smallest accepted graph."""
    threshold = 2.0 * beta
    best = None
    base = [tuple(e) for e in MATCHING_EDGES]
    for r in range(restarts):
        rng = random.Random((seed << 20) ^ r)
        edges = list(base)
        deg = _degrees(edges)
        lam = _lambda2(edges)
        accepted = lam >= threshold

        # ---- Phase 1: regularization to maximum degree ----
        delta = delta_override if delta_override is not None else max(deg)
        while not accepted:
            deficit = [(u, v) for u in range(N_V) for v in range(u + 1, N_V)
                       if deg[u] < delta and deg[v] < delta
                       and (allow_parallel or (u, v) not in set(edges))]
            if not deficit:
                break
            weights = [(delta - deg[u]) * (delta - deg[v]) for (u, v) in deficit]
            (u, v) = rng.choices(deficit, weights=weights, k=1)[0]
            edges.append((u, v)); deg[u] += 1; deg[v] += 1
            lam = _lambda2(edges)
            accepted = lam >= threshold

        # ---- Phase 2: degree augmentation by random 1-regular layers ----
        layers = 0
        while not accepted and layers < tau:
            pool = [v for v in range(N_V) if deg[v] < max_degree]
            rng.shuffle(pool)
            while len(pool) >= 2 and not accepted:
                u = pool.pop()
                # uniform partner among remaining pool (avoid parallel edges
                # unless allowed; fall back to any partner if all parallel)
                cands = [w for w in pool if allow_parallel or
                         (min(u, w), max(u, w)) not in set(edges)] or pool
                w = rng.choice(cands)
                pool.remove(w)
                e = (min(u, w), max(u, w))
                edges.append(e); deg[u] += 1; deg[w] += 1
                lam = _lambda2(edges)
                accepted = lam >= threshold
            layers += 1

        if not accepted:
            continue
        key = (len(edges), max(deg), _congestion(sorted(edges)))
        if best is None or key < best[0]:
            best = (key, sorted(edges), lam)
            if verbose:
                print(f"  restart {r}: accepted E={len(edges)} "
                      f"lambda2={lam:.3f} maxdeg={max(deg)} cong={key[2]}")
    if best is None:
        raise RuntimeError(
            f"GeneCS synthesis failed: no restart reached lambda_2 >= {threshold} "
            f"within tau={tau} layers at max_degree={max_degree}")
    key, edges, lam = best
    info = {"edges": len(edges), "extra_edges": len(edges) - len(base),
            "lambda2": round(lam, 4), "cheeger_certified": round(lam / 2, 4),
            "max_degree": key[1], "congestion": key[2], "beta": beta,
            "restarts": restarts}
    return edges, info


def genecs_spec(beta=0.46, seed=0, restarts=200, rounds=12, **kw):
    """The gadget spec this task's evaluator consumes."""
    edges, info = genecs_graph(beta=beta, seed=seed, restarts=restarts, **kw)
    return {"edges": [list(e) for e in edges], "rounds": rounds}, info


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="GeneCS-style gadget synthesis")
    ap.add_argument("--beta", type=float, default=0.46,
                    help="target certified Cheeger constant (accept lambda_2 >= 2*beta)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--restarts", type=int, default=200)
    ap.add_argument("--tau", type=int, default=8)
    ap.add_argument("--max-degree", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    spec, info = genecs_spec(beta=args.beta, seed=args.seed,
                             restarts=args.restarts, rounds=args.rounds,
                             tau=args.tau, max_degree=args.max_degree,
                             verbose=args.verbose)
    print(f"GeneCS gadget (beta={args.beta}): {info}")
    print(f"edges = {spec['edges']}")
    print(f"rounds = {spec['rounds']}")
    print(f"Q = {info['edges']} edge qubits + 12 A_v + (cycle checks; evaluator derives)")

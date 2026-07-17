"""ShinkaEvolve EVALUATOR — the GeneCS-criteria spectral synthesis RACE.

Head-to-head question: is Shinka-style evolution a better OPTIMIZER than the
GeneCS compiler heuristic (Zhou, Javadi-Abhari, Li, arXiv:2605.21746,
Algorithm 1: deficit-weighted random edge addition with first-passage
acceptance, 100 restarts) at GeneCS's OWN acceptance criteria, on GeneCS's
own benchmark instance — the gauging-measurement graph for the weight-12
logical X_alpha of the [[144,12,12]] gross code?

THE CRITERIA ARE THEIRS, REVERSE-ENGINEERED FROM THEIR PUBLISHED OUTCOME
(../gross_code_gauging/genecs.py --fit-published): their gross-code Full-Opt
result (24 ancilla qubits, 25 checks, degrees 7/8) pins E = 24 mono-layer,
reproduced exactly by acceptance lambda_2(G) >= 2.0 — i.e. certified Cheeger
constant >= 1 via the spectral proxy (Cheeger >= lambda_2/2), which is the
full Williamson-Yoder Theorem 2 expansion bar. In their generic accounting
checks = (#vertices) A_v + (E - #vertices + 1) cycle checks = E + 1
regardless of dummies, so qubits + checks = 2E + 1 and THE OBJECTIVE IS
PURELY: MINIMIZE THE EDGE COUNT E SUBJECT TO lambda_2 >= 2.

    spec = {"edges": [(u, v), ...]}      (no rounds — no protocol here)

  * labels 0..11  = the 12 qubits of supp(X_alpha) (the ports);
  * labels 12..35 = OPTIONAL dummy vertices (a structural move the GeneCS
    pipeline does not have: in their accounting a dummy is FREE — +1 A_v
    check, -1 cycle check — but changes which topologies exist);
  * parallel edges allowed (another move their simple-graph family lacks;
    a doubled edge is the gauging double-gross motif), no self-loops.

WHY EVOLUTION CAN WIN (the structural gaps in their search): Algorithm 1 is
add-only over the fixed path-matching graph with first-passage acceptance —
it cannot remove a redundant earlier addition, cannot swap, cannot drop
path-matching edges (any connected graph deforms via paths — T-joins — so
G0-containment is their restriction, not the theory's), and has no dummy
vertices. Measured wall: within their family E jumps 23 (lambda_2 ~ 1.72)
-> 24 (lambda_2 = 2.000 exactly) on every seed; the spectral floor is far
below (degree >= 2 only forces E >= 12). Whether ANY 12-port multigraph
(possibly with dummies) reaches lambda_2 >= 2 at E <= 23 is the race.

THE SEED is the hand-crafted WY/IBM 22-edge graph of the 41-element paper
gadget — which their certificate REJECTS (lambda_2 = 0.925, certified
Cheeger 0.46 < 1) even though its deformed distance 12 is proven by integer
programming. The certificate cannot see actual distance; this task
deliberately optimizes THEIR criterion anyway (the race is about search,
not physics), and any certified winner should be cross-scored on the real
protocol by ../gross_code_gauging/ (calibrate.py --compare machinery).

================  SCORING (Shinka MAXIMISES combined_score)  ===================
  lam2       = second-smallest eigenvalue of the (multi)graph Laplacian
  CERTIFIED := lam2 >= LAM2_MIN (2.0; env SPECTRAL_LAM2_MIN)
  rho        = congestion: max #(minimum-cycle-basis cycles) through an edge
               (their secondary criterion; gentle tiebreak only)
  E_theirs(lam2) = the GeneCS compiler's measured lambda_2-vs-E frontier
               (GENECS_FRONTIER anchors, piecewise linear, capped at 24
               above the acceptance threshold — overshoot earns nothing,
               matching their first-passage semantics)

  crash / garbage return               -> -1000   (correct=False)
  invalid spec                         ->  -100   (+ named reason)
  valid, lam2 below the G0 level       ->  -4 - 6*(0.438 - lam2) - 0.05*E
       (their add-only pipeline has no output below its own start graph,
        so there is no frontier credit down there — only a gradient up)
  valid, lam2 >= G0 level              ->  3.0 + E_theirs(lam2) - E
                                           - tiebreaks
                                           (- 0.02*max(0, rho-2)
                                            - 0.01*max(0, maxdeg-4))
       FRONTIER SCORE, offset so the scale reads naturally for evolution:
       the WY seed boots at ~+0.5 (valid, modest), +3.0 is GeneCS-compiler
       PARITY (their measured outputs land there), anything above +3 beats
       the published pipeline at its own beta knob, and a CERTIFIED graph
       earns +1 more per edge below 24 (certified E=21 -> +6). MEASURED
       HEADROOM: plain local annealing already finds certified E=21 and
       lambda_2=2.28 at E=23 — their frontier is beatable by +0.34..+0.80
       everywhere — so the real discovery target is the TRUE minimum
       certified E (the Fiedler bound lambda_2 <= vertex-connectivity <=
       min-degree only forces E >= 12; where in [12, 21] the boundary
       lies is open).

Anti-gaming: the candidate returns only the edge list; the Laplacian, the
eigensolve, the congestion and the scoring all live here, and the eval is
DETERMINISTIC (no sampling, no seeds) — nothing to get lucky against.

RUNTIME: < 1 s per candidate (12-36-vertex eigensolve + Horton cycle basis).
Set eval_time ~ 00:02:00; this task is built for very high candidate
throughput — the race is about search moves, not evaluation cost.
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
import traceback

import numpy as np

from shinka.core import run_shinka_eval

LAM2_MIN    = float(os.environ.get("SPECTRAL_LAM2_MIN", "2.0"))
LAM2_EPS    = 1e-9      # certification tolerance: graphs whose true lambda_2
                        # IS the threshold (integer spectra are common here —
                        # the E=24 GeneCS graphs land at exactly 2) must not
                        # flip on eigensolver rounding
E_BASE      = 24        # GeneCS Algorithm 1 at these criteria (measured; also
                        # matches their published 24-qubit gross Full-Opt)
# THEIR compiler's measured lambda_2-vs-E frontier (best over seeds, Alg-1 /
# 100 restarts; from ../gross_code_gauging/genecs.py --fit-published + scans):
# the anchor of the race score — a candidate is scored by how many edges
# THEIR compiler needs to reach the candidate's certified expansion level.
GENECS_FRONTIER = ((0.4384, 18),   # G0, the 18-edge path-matching motif
                   (0.7007, 19), (1.105, 20), (1.202, 21),
                   (1.438, 22), (1.722, 23), (LAM2_MIN, 24))
TIE_CONG    = 0.02      # congestion tiebreak
TIE_DEG     = 0.01      # degree tiebreak
SCORE_OFFSET = 3.0      # constant shift of the frontier score so the scale
                        # reads naturally for evolution: the WY seed boots at
                        # a modest POSITIVE (~+0.5), GeneCS-compiler parity is
                        # the +3.0 milestone (not the origin), and anything
                        # above +3 beats the published pipeline. Pure offset —
                        # every gradient and ordering is unchanged; negative
                        # scores are reserved for below-seed-quality graphs.


def frontier_edges(lam2):
    """E_theirs(lambda_2): edges the GeneCS compiler needs (piecewise-linear
    in lambda_2 through the measured anchors; capped at E_BASE above the
    acceptance threshold — overshooting expansion earns nothing, exactly
    like their first-passage acceptance). Defined only for lambda_2 >= the
    G0 level 0.4384 — below that THEIR pipeline has no output at all (it is
    add-only from G0), so there is no frontier to beat."""
    pts = GENECS_FRONTIER
    if lam2 >= pts[-1][0]:
        return float(pts[-1][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if lam2 <= x1:
            t = (lam2 - x0) / (x1 - x0)
            return float(y0 + t * (y1 - y0))
    return float(pts[0][1])
MAX_EDGES   = 60
MAX_DUMMIES = 24
MAX_DEGREE  = 12        # GeneCS's stated degree bound
INVALID_SCORE = -100.0
CRASH_SCORE   = -1000.0

N_PORTS = 12


class SpecError(ValueError):
    pass


def parse_spec(spec):
    if not isinstance(spec, dict):
        raise SpecError(f"spec must be a dict {{'edges': [...]}}, got "
                        f"{type(spec).__name__}")
    edges_in = spec.get("edges")
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
        if not (0 <= u < N_PORTS + MAX_DUMMIES and 0 <= v < N_PORTS + MAX_DUMMIES):
            raise SpecError(f"edge {e!r} uses a label outside 0.."
                            f"{N_PORTS + MAX_DUMMIES - 1}")
        edges.append((min(u, v), max(u, v)))
    if not edges:
        raise SpecError("no edges — the graph must connect all 12 ports")
    if len(edges) > MAX_EDGES:
        raise SpecError(f"{len(edges)} edges exceeds the cap of {MAX_EDGES}")
    dummies = sorted({x for e in edges for x in e if x >= N_PORTS})
    verts = list(range(N_PORTS)) + dummies
    adj = {v: set() for v in verts}
    deg = {v: 0 for v in verts}
    for (u, v) in edges:
        adj[u].add(v); adj[v].add(u)
        deg[u] += 1; deg[v] += 1
    if max(deg.values()) > MAX_DEGREE:
        raise SpecError(f"max degree {max(deg.values())} exceeds the GeneCS "
                        f"degree bound {MAX_DEGREE}")
    seen, stack = set(), [0]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack.extend(adj[x] - seen)
    missing = set(verts) - seen
    if missing:
        raise SpecError(f"graph disconnected: vertices {sorted(missing)} "
                        f"unreachable from port 0 — all 12 ports and every "
                        f"used dummy must lie in one component")
    return edges, dummies, verts


def lambda2_and_fiedler(edges, verts):
    """(lambda_2, Fiedler-vector sign split, #crossing edges) of the
    multigraph Laplacian — the split is the weakest spectral cut, i.e.
    exactly where an edge buys the most lambda_2."""
    pos = {v: i for i, v in enumerate(verts)}
    n = len(verts)
    Lap = np.zeros((n, n))
    for (u, v) in edges:
        i, j = pos[u], pos[v]
        Lap[i, i] += 1; Lap[j, j] += 1
        Lap[i, j] -= 1; Lap[j, i] -= 1
    w, V = np.linalg.eigh(Lap)
    lam2 = float(w[1])
    fied = V[:, 1]
    side = sorted(verts[i] for i in range(n) if fied[i] < 0)
    if len(side) > n // 2:
        side = sorted(set(verts) - set(side))
    sset = set(side)
    crossing = sum(1 for (u, v) in edges if (u in sset) != (v in sset))
    return lam2, side, crossing


def congestion(edges, verts):
    """Max number of minimum-cycle-basis cycles through any edge (GeneCS's
    congestion rho), exact via Horton candidates + greedy independence."""
    E = len(edges)
    dim = E - len(verts) + 1
    if dim <= 0:
        return 0
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

    def _rank(Mx):
        Mx = Mx.copy() % 2; r = 0
        for c in range(Mx.shape[1]):
            piv = next((i for i in range(r, Mx.shape[0]) if Mx[i, c]), None)
            if piv is None:
                continue
            Mx[[r, piv]] = Mx[[piv, r]]
            for i in range(Mx.shape[0]):
                if i != r and Mx[i, c]:
                    Mx[i] ^= Mx[r]
            r += 1
        return r

    basis, cur = [], np.zeros((0, E), np.int8)
    for c in sorted(cands, key=lambda c: (len(c), sorted(c))):
        v = np.zeros(E, np.int8)
        for j in c:
            v[j] = 1
        t = np.vstack([cur, v.reshape(1, -1)])
        if _rank(t) > cur.shape[0]:
            cur = t; basis.append(c)
            if len(basis) == dim:
                break
    use = [0] * E
    for c in basis:
        for j in c:
            use[j] += 1
    return max(use) if use else 0


def _crash(text):
    return {"combined_score": CRASH_SCORE, "correct": False,
            "public": {"valid": 0}, "private": {}, "extra_data": {},
            "text_feedback": text}


def _invalid(reason):
    return {"combined_score": INVALID_SCORE, "correct": True,
            "public": {"valid": 0, "reason": reason}, "private": {},
            "extra_data": {},
            "text_feedback": f"INVALID ({INVALID_SCORE:.0f}): {reason}"}


def aggregate_fn(results: list) -> dict:
    if not results:
        return _crash("run_experiment returned no result.")
    propose = results[0]
    if not callable(propose):
        return _crash(f"run_experiment must return the propose_graph callable, "
                      f"got {type(propose).__name__}.")
    try:
        spec = propose()
    except Exception:
        return _crash("Candidate propose_graph() crashed:\n" + traceback.format_exc())
    try:
        edges, dummies, verts = parse_spec(spec)
    except SpecError as e:
        return _invalid(str(e))
    except Exception:
        return _crash("Graph parsing crashed:\n" + traceback.format_exc())

    E = len(edges)
    lam2, weak_side, crossing = lambda2_and_fiedler(edges, verts)
    rho = congestion(edges, verts)
    deg = {v: 0 for v in verts}
    for (u, v) in edges:
        deg[u] += 1; deg[v] += 1
    maxdeg = max(deg.values())
    certified = lam2 >= LAM2_MIN - LAM2_EPS
    checks_raw = E + 1                       # (12+d) A_v + (E-(12+d)+1) cycles
    qpc = 2 * E + 1                          # their qubits+checks objective

    tiebreak = TIE_CONG * max(0, rho - 2) + TIE_DEG * max(0, maxdeg - 4)
    if lam2 >= GENECS_FRONTIER[0][0]:
        # FRONTIER SCORE: how many edges THEIR compiler needs to reach this
        # candidate's expansion level, minus what the candidate spent.
        # Positive = beats the GeneCS compiler at its own beta knob; 0 = ties
        # its measured outputs; certified E <= 23 is automatically >= +1.
        f_e = frontier_edges(lam2)
        score = float(SCORE_OFFSET + f_e - E - tiebreak)
        status = "CERTIFIED" if certified else "uncertified"
        verdict = (
            f"{status} at E={E} edges, lambda_2={lam2:.3f} (certified Cheeger "
            f">= {lam2 / 2:.2f}; acceptance is lambda_2 >= {LAM2_MIN}); "
            f"score={score:+.2f} = {SCORE_OFFSET} + (their compiler needs "
            f"{f_e:.1f} edges for this expansion level) - (your {E}) - "
            f"tiebreaks; {SCORE_OFFSET:+.1f} is GeneCS-compiler PARITY — "
            f"anything above it beats the published pipeline. In GeneCS "
            f"accounting: {E} qubits + {checks_raw} checks = {qpc} (their "
            f"published gross result: 24 + 25 = 49). {len(dummies)} dummies, "
            f"congestion rho={rho}, max degree {maxdeg}. "
            + (f"BEATS the certified GeneCS compiler output by {E_BASE - E} "
               f"edge(s) — cross-score this graph end-to-end in "
               f"../gross_code_gauging/. "
               if certified and E < E_BASE else
               (f"Certified and matching their compiler; push E below "
                f"{E_BASE} (local annealing is KNOWN to reach certified "
                f"E=21 — beat that, then find the true minimum; the Fiedler "
                f"bound only forces E >= 12). "
                if certified else
                f"Not yet certified — the score still pays for beating their "
                f"frontier at THIS expansion level; certification (lambda_2 "
                f">= {LAM2_MIN}) unlocks the E<=23 jackpot ladder. "))
            + f"Moves their add-only first-passage search cannot make: remove "
            f"an edge whose loss keeps lambda_2 high (they never re-check), "
            f"swap edges across the weakest spectral cut {weak_side} "
            f"({crossing} crossing now), drop matching edges, place a dummy "
            f"hub (free in this accounting), double a strategic edge."
        )
    else:
        # Below the G0 level their pipeline has no output at all — no
        # frontier to beat down here, only a gradient back up.
        score = float(-4.0 - 6.0 * (GENECS_FRONTIER[0][0] - lam2)
                      - 0.05 * E - tiebreak)
        verdict = (
            f"BELOW THE G0 EXPANSION LEVEL: lambda_2={lam2:.3f} < "
            f"{GENECS_FRONTIER[0][0]:.3f} (the 18-edge path-matching motif "
            f"itself) — the GeneCS pipeline has no output this weak, so there "
            f"is no frontier credit here; score={score:.2f}. E={E} edges, "
            f"congestion rho={rho}, max degree {maxdeg}. The weakest spectral "
            f"cut is {weak_side} with only {crossing} crossing edge(s) — add "
            f"or re-route edges across THAT cut to raise lambda_2 fastest."
        )

    public = {
        "combined_score": round(score, 3), "valid": 1,
        "certified": int(certified), "lam2": round(lam2, 4),
        "cheeger_cert": round(lam2 / 2, 4),
        "frontier_edges_theirs": (round(frontier_edges(lam2), 2)
                                  if lam2 >= GENECS_FRONTIER[0][0] else None),
        "edges": E, "dummies": len(dummies),
        "checks_raw": checks_raw, "qubits_plus_checks": qpc,
        "congestion": rho, "max_degree": maxdeg,
        "weak_cut_side": weak_side, "weak_cut_crossing": crossing,
    }
    private = {
        "lam2_min": LAM2_MIN, "e_base": E_BASE,
        "score_offset": SCORE_OFFSET,
        "genecs_frontier": [list(p) for p in GENECS_FRONTIER],
        "tie_cong": TIE_CONG, "tie_deg": TIE_DEG,
    }
    return {"combined_score": score, "correct": True, "public": public,
            "private": private, "extra_data": {}, "text_feedback": verdict}


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
    print(f"Race criteria: lambda_2 >= {LAM2_MIN} (GeneCS fitted gross-code "
          f"acceptance), objective = minimize E (their qubits+checks = 2E+1); "
          f"baseline to beat: their Algorithm 1 at E={E_BASE}.")
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
    parser = argparse.ArgumentParser(description="gross_code_spectral_synth evaluator")
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)

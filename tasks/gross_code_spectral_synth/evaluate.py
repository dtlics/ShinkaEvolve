"""ShinkaEvolve EVALUATOR — minimum-edge spectral certification on the
gross-code gauging graph (v2, post-star-loophole rework).

THE PROBLEM (self-contained, combinatorial): find the SMALLEST simple graph
on the 12 labelled "ports" (plus optional dummy vertices) whose Laplacian
algebraic connectivity satisfies

    lambda_2(G) >= 2.0

i.e. certified Cheeger constant >= 1 via Cheeger's inequality h >= lambda_2/2
— the expansion bar of Williamson-Yoder Theorem 2 (arXiv:2410.02213) for the
gauging graph of the weight-12 logical X_alpha of the [[144,12,12]] gross
code. Ports 0..11 are that logical's qubits.

PROVENANCE, STATED HONESTLY (post-run verification, do not oversell): this
criterion is STRICTER than the GeneCS compiler's real acceptance — the paper
(arXiv:2605.21746) gates at lambda_2 >= 2*beta with beta < 1 (~0.34-0.46),
and its gross-code result (24 qubits / 25 checks, degrees 7/8, Table 2) is
degree-augmentation-driven with NO minimality claim. So this task is NOT a
head-to-head against GeneCS's published numbers; it is a clean spectral
minimization problem grounded in the WY expansion desideratum, with the
GeneCS-style constructor's measured output (E=24 at lambda_2 >= 2) and the
hand-crafted WY 22-edge graph (lambda_2 = 0.925, UNcertified — its distance
12 is IP-proven, which the spectral certificate cannot see) as context.

STATE OF KNOWLEDGE (independently verified — the baselines to beat):
  * certified E=20 EXISTS (run spectral_v1's record, re-verified from the
    raw edge list): degrees 3^8 4^4, integral Laplacian spectrum
    {0, 2^5, 4^3, 6^3}, lambda_2 = 2 exactly. It is the SEED of this task.
  * 300-restart simulated annealing reaches only certified E=21 and cannot
    reproduce E=20 from scratch; all circulants C_12(S) need E=24; dummy /
    bipartite constructions (K_{2,12}, cones) need E=24 — dummies DILUTE
    lambda_2 and are useless for certification (verified).
  * E=19 lambda_2 ceiling found so far ~1.59, E=18 ~1.47; no 12-vertex
    3-regular graph can certify (provable by an adjacency-trace argument).
    The Fiedler bound (lambda_2 <= vertex connectivity <= min degree) only
    forces E >= 12. So certified E <= 19 is the OPEN jackpot: strong
    evidence says it does not exist, and any find would be a genuine
    combinatorial discovery. Secondary targets: structurally distinct
    certified E=20 graphs (the known one is a rare integral graph; the
    novelty machinery rewards distinct structures) and better secondary
    profiles (congestion, max degree) at E=20-22.

    spec = {"edges": [(u, v), ...]}      (SIMPLE graph: no parallel edges)

  * labels 0..11  = the 12 ports (must all be present and connected);
  * labels 12..35 = optional dummy vertices (allowed but verified
    self-defeating for certification — they dilute lambda_2);
  * no self-loops, no parallel edges (v2: multigraphs removed so the
    minimum-edge claim stays a clean simple-graph statement), <= 60 edges,
    <= 24 dummies, max degree <= 12.

================  WHY v2 (the star-loophole postmortem)  =======================
Run spectral_v1's champion (+11.67) was an E=11 PORT-STAR with lambda_2 =
1.0 — uncertified, physically the worst possible expander. v1's score paid
"frontier credit" for reaching intermediate lambda_2 levels with few edges,
interpolated from the constructor's measured curve; but that curve lives on
G0-containing graphs (E >= 18) while sparse hubs reach mid-band lambda_2
trivially (a star has lambda_2 = 1 exactly), so the credit at low lambda_2
was wildly over-generous and the degenerate corner became the global
maximum — the same failure class as the gcg1 spanning tree: any region
where score can improve while the certifying quantity degrades will become
the champion. v2 removes the entire mechanism:

  1. EDGES ARE ONLY REWARDED AFTER CERTIFICATION. Below lambda_2 = 2 the
     edge count earns nothing (and costs nothing): the only way up is
     raising lambda_2.
  2. CERTIFIED ALWAYS DOMINATES in the competitive region: every certified
     graph with E <= 24 outscores every possible uncertified graph
     (uncertified scores are capped below 5.0; certified starts at +6.0
     for E=24).
  3. LEAVES ARE PRICED AS CERTIFICATION-BLOCKERS: by Fiedler,
     lambda_2 <= vertex connectivity <= min degree, so ANY degree-1 vertex
     caps lambda_2 at 1 and makes certification impossible while it
     exists. Each leaf costs -1.0 in the uncertified branch — a port-star
     (11 leaves) scores ~-8.6 instead of +11.67.

================  SCORING (Shinka MAXIMISES combined_score)  ===================
  lam2       = algebraic connectivity of the (simple) graph Laplacian
  CERTIFIED := lam2 >= 2.0 - 1e-9   (integer spectra are common here; the
                                     epsilon absorbs eigensolver rounding)
  leaves     = number of degree-1 vertices (ports or dummies)
  rho        = congestion: max #(minimum-cycle-basis cycles) through an edge
  tiebreaks  = 0.02*max(0, rho - 2) + 0.01*max(0, maxdeg - 4)   (tiny)

  crash / garbage return       -> -1000   (correct=False)
  invalid spec                 ->  -100   (+ named reason; includes parallel
                                           edges, self-loops, degree > 12,
                                           disconnection)
  valid, UNCERTIFIED           ->  2.5 * lam2 - 1.0 * leaves
                                   - 1.0 * max(0, E - 24) - tiebreaks
       monotone in lambda_2, hard-capped below 5.0 (< any certified score
       at E <= 24, whose floor is 6.0 minus a bounded tiebreak >= ~5.7);
       NO edge-count REWARD — sparsity buys nothing until the certificate
       holds (v1's star lesson: only penalties, never rewards, for edges
       below certification; the oversize term just pushes E > 24 bloat
       back toward the competitive region and removes the lambda_2->2-
       from-below plateau that would otherwise out-order oversized
       certified graphs). The WY 22-edge context graph sits at ~+2.3; a
       port-star at ~-8.6; a bare 12-cycle at ~+0.7.
  valid, CERTIFIED             ->  6.0 + (24 - E) - tiebreaks
       E=24 (constructor level) -> +6, E=21 (SA record) -> +9,
       E=20 (the seed / known record) -> +10, E=19 (open jackpot) -> +11,
       and +1 more per further edge. At equal E the tiebreaks prefer lower
       congestion and lower max degree — measured to matter: the DEGENERATE
       second known certified-E=20 graph, the two-hub K_{2,10} (lambda_2 =
       2 exactly, vertex connectivity 2, congestion 9, max degree 10),
       scores 9.80 and correctly ranks BELOW the genuine record's 9.96.
       It is disclosed here so no search budget is wasted rediscovering
       it; structurally NEW certified E=20 graphs are rewarded through the
       archive's novelty machinery, not the scalar.

Anti-gaming: the candidate returns only the edge list; the Laplacian, the
eigensolve, the leaf count, the congestion and the scoring all live here,
and the eval is DETERMINISTIC at the GRAPH level (edges are canonically
sorted before scoring, so permuting the submitted list cannot move the
congestion tiebreak). The v2 invariant — no uncertified graph outscores
ANY certified graph with E <= 24 — is checked by test_spectral.py
(landmark regressions + a fuzz property test), and an independent
adversarial exploit-hunt (3 attack agents + numeric verification) found no
breaks-intent exploit: the uncertified cap and the certified floor hold
analytically and empirically, the 1e-9 epsilon is load-bearing (the
record's lambda_2 computes 1.3e-15 below 2) but uninhabited by genuinely
sub-2 graphs (closest found: 1.7e-4 below), and dummies/hubs/parse tricks
all fail.

RUNTIME: < 1 s per candidate. Set eval_time ~ 00:02:00; throughput is the
whole point. SPECTRAL_LAM2_MIN moves the certification bar (default 2.0).
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
LAM2_EPS    = 1e-9      # certification tolerance (integer spectra are common)
E_REF       = 24        # the GeneCS-style constructor's measured output at
                        # lambda_2 >= 2 (context baseline; certified E=24
                        # scores +6, every edge below is +1)
CERT_BASE   = 6.0       # certified-branch floor: strictly above the
                        # uncertified cap (2.5 * lam2 < 5.0), so certified
                        # dominates everywhere in the competitive region
W_LAM       = 2.5       # uncertified: score per unit of lambda_2
LEAF_PEN    = 1.0       # uncertified: per degree-1 vertex (a leaf caps
                        # lambda_2 at 1 — Fiedler — so it BLOCKS certification)
TIE_CONG    = 0.02      # congestion tiebreak
TIE_DEG     = 0.01      # degree tiebreak
MAX_EDGES   = 60
MAX_DUMMIES = 24
MAX_DEGREE  = 12
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
    seen_pairs = set()
    for e in edges_in:
        if isinstance(e, str):
            raise SpecError(f"edge {e!r} is a string, not a pair of integer labels")
        try:
            eu, ev = tuple(e)          # exactly two entries — no silent truncation
            u, v = int(eu), int(ev)
            if u != eu or v != ev:     # integral values only — no float rounding
                raise ValueError
        except Exception:
            raise SpecError(f"edge {e!r} is not a pair of integer labels")
        if u == v:
            raise SpecError(f"self-loop edge {e!r} not allowed")
        if not (0 <= u < N_PORTS + MAX_DUMMIES and 0 <= v < N_PORTS + MAX_DUMMIES):
            raise SpecError(f"edge {e!r} uses a label outside 0.."
                            f"{N_PORTS + MAX_DUMMIES - 1}")
        pair = (min(u, v), max(u, v))
        if pair in seen_pairs:
            raise SpecError(f"parallel edge {pair} — v2 requires a SIMPLE "
                            f"graph (the minimum-edge claim is a simple-graph "
                            f"statement)")
        seen_pairs.add(pair)
        edges.append(pair)
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
        raise SpecError(f"max degree {max(deg.values())} exceeds the bound "
                        f"{MAX_DEGREE}")
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
    # Canonical order: the congestion tiebreak's Horton tie-breaking depends
    # on edge indices, so sort the normalized edge list — the score is then a
    # function of the GRAPH, not of the submitted edge ordering (exploit-hunt
    # finding: permuted edge lists shifted rho by 1, +-0.02 score).
    return sorted(edges), dummies, verts


def lambda2_and_fiedler(edges, verts):
    """(lambda_2, Fiedler-vector sign split, #crossing edges): the second
    Laplacian eigenvalue and the weakest spectral cut — where an edge buys
    the most lambda_2."""
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
    """Max number of minimum-cycle-basis cycles through any edge, exact via
    Horton candidates + greedy independence (tiebreak only)."""
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
    leaves = sum(1 for v in verts if deg[v] == 1)
    certified = lam2 >= LAM2_MIN - LAM2_EPS
    tiebreak = TIE_CONG * max(0, rho - 2) + TIE_DEG * max(0, maxdeg - 4)

    if certified:
        score = float(CERT_BASE + (E_REF - E) - tiebreak)
        verdict = (
            f"CERTIFIED at E={E} edges (lambda_2={lam2:.4f} >= {LAM2_MIN}, "
            f"certified Cheeger >= {lam2 / 2:.2f}); score={score:+.2f} = "
            f"{CERT_BASE} + ({E_REF} - {E}) - tiebreaks. Landmarks: "
            f"constructor E=24 -> +6, SA record E=21 -> +9, the known-record "
            f"integral graph E=20 -> +10 (the seed), OPEN jackpot E<=19 -> "
            f"+11 and up (strong evidence it does not exist — E=19 lambda_2 "
            f"ceiling found so far ~1.59 — so any find is a genuine "
            f"discovery). {len(dummies)} dummies, congestion rho={rho}, max "
            f"degree {maxdeg}. "
            + (f"NEW RECORD TERRITORY — independently re-verify lambda_2 "
               f"from the raw edge list, then cross-score the graph on the "
               f"real protocol in ../gross_code_gauging/. "
               if E < 20 else "")
            + f"At equal E, lower congestion / lower max degree win the "
            f"tiebreaks, and structurally DISTINCT certified graphs are "
            f"valuable (the known E=20 record has degrees 3^8 4^4 and "
            f"integral spectrum {{0, 2^5, 4^3, 6^3}} — different structures "
            f"at E=20 are worth keeping via novelty even at equal score)."
        )
    else:
        oversize = max(0, E - E_REF)
        score = float(W_LAM * lam2 - LEAF_PEN * leaves
                      - 1.0 * oversize - tiebreak)
        leaf_note = (f" {leaves} degree-1 vertex(es) cost -{LEAF_PEN * leaves:.0f}: "
                     f"a leaf caps lambda_2 at 1 (Fiedler: lambda_2 <= vertex "
                     f"connectivity <= min degree), so leaves BLOCK "
                     f"certification — attach every vertex at least twice."
                     if leaves else "")
        size_note = (f" OVERSIZED: {oversize} edge(s) beyond E={E_REF} cost "
                     f"-1.0 each — shed edges toward the competitive region "
                     f"(certification only pays at E <= {E_REF})."
                     if oversize else "")
        verdict = (
            f"UNCERTIFIED: lambda_2={lam2:.4f} < {LAM2_MIN}; score={score:+.2f} "
            f"= {W_LAM}*lambda_2 - {LEAF_PEN}*leaves - oversize - tiebreaks. "
            f"Edge count earns NOTHING until the certificate holds (v1's star "
            f"loophole is closed): the only way up is raising lambda_2 toward "
            f"2.0, then shaving edges.{leaf_note}{size_note} E={E} edges, "
            f"{len(dummies)} dummies, congestion rho={rho}, max degree "
            f"{maxdeg}. The weakest spectral cut is {weak_side} with "
            f"{crossing} crossing edge(s) — add or re-route edges across THAT "
            f"cut to raise lambda_2 fastest. (Dummies are verified "
            f"self-defeating here: they dilute lambda_2.)"
        )

    public = {
        "combined_score": round(score, 3), "valid": 1,
        "certified": int(certified), "lam2": round(lam2, 4),
        "cheeger_cert": round(lam2 / 2, 4),
        "edges": E, "dummies": len(dummies), "leaves": leaves,
        "congestion": rho, "max_degree": maxdeg,
        "weak_cut_side": weak_side, "weak_cut_crossing": crossing,
    }
    private = {
        "lam2_min": LAM2_MIN, "e_ref": E_REF, "cert_base": CERT_BASE,
        "w_lam": W_LAM, "leaf_pen": LEAF_PEN,
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
    print(f"v2 criteria: certified = lambda_2 >= {LAM2_MIN} (WY Cheeger >= 1 "
          f"bar); certified score = {CERT_BASE} + ({E_REF} - E); uncertified = "
          f"{W_LAM}*lambda_2 - {LEAF_PEN}*leaves (edges pay only after "
          f"certification; certified always dominates).")
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
    parser = argparse.ArgumentParser(description="gross_code_spectral_synth evaluator (v2)")
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)

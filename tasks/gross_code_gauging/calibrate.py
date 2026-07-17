"""Recalibrate / cross-compare the gross_code_gauging evaluator (v4).

Default mode measures the LER_REFS noise curve of the PAPER REFERENCE gadget
(18 matching + 4 expansion edges, R=12) at every P_GRID point under the
current harness configuration (noise model, scheduler, protocol shape,
decoder), plus its protocol fault-distance estimate. Run this whenever any of
those change, then update the three constants in evaluate.py (or set
GAUGE_LER_REF_LO/GATE/HI).

--compare mode runs the full v4 scoring pipeline (fault-distance probes +
curve sampling + score arithmetic) over the standard benchmark set — the
paper reference, the gcg1 champion spanning tree (the design that beat the
v2/v3 LER-only evaluator), the GeneCS-style spectral baseline (genecs.py),
the matching-only graph, and the shipped seed — and prints a comparison
table. Use it after ANY scoring/threshold retune to confirm the story:
tree killed, reference ~0, GeneCS placed by measurement.

Usage:
    python tasks/gross_code_gauging/calibrate.py [--errors 300]
        [--shots-lo 10000] [--shots-gate 8000] [--shots-hi 4000]
    python tasks/gross_code_gauging/calibrate.py --compare [--budget-scale 1.0]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

_TASK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_TASK_DIR)))   # repo root (shinka)
sys.path.insert(0, _TASK_DIR)                                     # evaluate.py


PAPER_EXTRA = [(2, 9), (2, 4), (9, 11), (10, 11)]   # App. B Eq. 5 in label space
GCG1_TREE = [(0, 1), (1, 2), (2, 3), (3, 8), (8, 11), (11, 4),
             (4, 5), (5, 9), (9, 10), (10, 7), (7, 6)]   # gcg1 champion anchor


def _matching(ev):
    fpos = {t: i for i, t in enumerate(ev.F_TERMS)}
    conn = {((ci + cj) % ev.L, (di + dj) % ev.M)
            for ci, di in ev._BT for cj, dj in ev._B} - {(0, 0)}
    matching = sorted({
        (min(fpos[(a, b)], fpos[nb]), max(fpos[(a, b)], fpos[nb]))
        for (a, b) in ev.F_TERMS for (cc, dd) in conn
        for nb in [((a + cc) % ev.L, (b + dd) % ev.M)]
        if nb in fpos and nb != (a, b)
    })
    assert len(matching) == 18
    return matching


def _reference_spec(ev):
    return {"edges": _matching(ev) + PAPER_EXTRA, "rounds": 12}


def calibrate(args):
    import numpy as np
    import evaluate as ev  # the task evaluator (same directory)

    edges, dummies, rounds = ev.parse_spec(_reference_spec(ev))
    g = ev.build_gauged(edges, dummies)
    assert (g["E"], g["n_av"], g["n_bp"], g["overhead"]) == (22, 12, 7, 41), \
        f"reference gadget drifted: {g['E']}/{g['n_av']}/{g['n_bp']}/{g['overhead']}"

    circs = []
    for p in ev.P_GRID:
        cx, meta = ev.build_protocol_circuit(g, rounds, "X", p)
        cz, _ = ev.build_protocol_circuit(g, rounds, "Z", p)
        circs.append((p, cx, cz))
    assert ev._noiseless_ok(circs[1][1]) and ev._noiseless_ok(circs[1][2])

    rng = np.random.default_rng(0)
    d_hat, parts, counts, weakest, _ = ev.estimate_fault_distance(
        g, rounds, circs[1][1], circs[1][2], rng)
    print(f"reference gadget OK (Q=41, deformed depths {meta['depth_def']}); "
          f"lightest probed fault set weight {d_hat} (weakest={weakest}, "
          f"parts={parts}); tail price at gate p: "
          f"{ev.tail_bound(parts, counts, ev.P_GATE):.2e} (should be ~0 for "
          f"the reference).")

    ev.P_BUDGET = ((args.errors, args.shots_lo),
                   (args.errors, args.shots_gate),
                   (args.errors, args.shots_hi))
    print(f"sampling P_GRID={tuple(round(p, 5) for p in ev.P_GRID)} with "
          f"{ev.N_WORKERS} workers, budgets {ev.P_BUDGET} ...", flush=True)
    t0 = time.time()
    curve = ev.sample_curve(circs)
    dt = time.time() - t0
    names = ("LO  ", "GATE", "HI  ")
    for name, c in zip(names, curve):
        std = 0.434 / max(1, c["ex"] + c["ez"]) ** 0.5
        print(f"p={c['p']:.5f} [{name}]: X {c['ex']}/{c['sx']}={c['px']:.4e}  "
              f"Z {c['ez']}/{c['sz']}={c['pz']:.4e}  overall={c['overall']:.4e} "
              f"(+-{std:.3f} decades)")
    print(f"({dt:.0f} s)")
    print("-> in evaluate.py, replace the three __CAL_*__ placeholder DEFAULTS "
          "inside the _ref() calls (this preserves the GAUGE_LER_REF_* env-override path):")
    print(f'     LER_REFS = (_ref("GAUGE_LER_REF_LO",   "{curve[0]["overall"]:.4e}"),')
    print(f'                 _ref("GAUGE_LER_REF_GATE", "{curve[1]["overall"]:.4e}"),')
    print(f'                 _ref("GAUGE_LER_REF_HI",   "{curve[2]["overall"]:.4e}"))')
    print(f"   or export GAUGE_LER_REF_LO={curve[0]['overall']:.4e} "
          f"GAUGE_LER_REF_GATE={curve[1]['overall']:.4e} "
          f"GAUGE_LER_REF_HI={curve[2]['overall']:.4e}")


def compare(args):
    import numpy as np
    import evaluate as ev

    if args.budget_scale != 1.0:
        ev.P_BUDGET = tuple((max(10, int(e * args.budget_scale)),
                             max(400, int(s * args.budget_scale)))
                            for (e, s) in ev.P_BUDGET)
        print(f"(budgets scaled x{args.budget_scale}: {ev.P_BUDGET})")

    cases = [("paper-41 (R=12)", _reference_spec(ev))]
    cases.append(("gcg1-tree (R=5)", {"edges": GCG1_TREE, "rounds": 5}))
    cases.append(("gcg1-tree (R=12)", {"edges": GCG1_TREE, "rounds": 12}))
    cases.append(("matching-18 (R=12)", {"edges": _matching(ev), "rounds": 12}))
    try:
        import genecs
        spec_g, info = genecs.genecs_spec(beta=0.46, seed=1, restarts=150)
        cases.append((f"genecs b=.46 E={info['edges']}", spec_g))
        spec_g2, info2 = genecs.genecs_spec(beta=0.35, seed=1, restarts=150)
        cases.append((f"genecs b=.35 E={info2['edges']}", spec_g2))
    except Exception as e:
        print(f"(genecs baseline skipped: {e})")
    try:
        import importlib.util as iu
        s = iu.spec_from_file_location("gauge_init", os.path.join(_TASK_DIR, "initial.py"))
        init = iu.module_from_spec(s); s.loader.exec_module(init)
        cases.append(("seed (initial.py)", init.run_experiment()()))
    except Exception as e:
        print(f"(seed skipped: {e})")

    if args.only:
        keys = [k.strip().lower() for k in args.only.split(",") if k.strip()]
        cases = [(n, s) for (n, s) in cases
                 if any(k in n.lower() for k in keys)]
        print(f"(--only filter -> {[n for n, _ in cases]})")

    rows = []
    for name, spec in cases:
        t0 = time.time()
        try:
            res = ev.aggregate_fn([lambda spec=spec: spec])
        except Exception as e:
            print(f"{name}: CRASH {e}")
            continue
        pub = res.get("public", {})
        rows.append((name, res["combined_score"], pub, time.time() - t0))
        print(f"[{time.strftime('%H:%M:%S')}] {name}: score={res['combined_score']:.2f} "
              f"({rows[-1][3]:.0f} s)", flush=True)

    print("\n=== v4.1 comparison table ===")
    hdr = (f"{'gadget':<22} {'score':>7} {'Q':>3} {'R':>3} {'w_min':>5} "
           f"{'tail@gate':>9} {'cross_p':>8} {'m_lo':>6} {'m_gate':>6} "
           f"{'LER_gate':>9} {'feasible':>8}")
    print(hdr); print("-" * len(hdr))
    for name, score, pub, dt in rows:
        if pub.get("valid"):
            feas = "YES" if score > -8 else "no"
            cp = pub.get("tail_crossover_p")
            cps = f"{cp:.0e}" if cp is not None else "none"
            print(f"{name:<22} {score:>7.2f} {pub['elements']:>3} {pub['rounds']:>3} "
                  f"{pub['fault_dist_est']:>5} {pub['tail_gate']:>9.1e} {cps:>8} "
                  f"{pub['margin_lo']:>6.2f} {pub['margin_gate']:>6.2f} "
                  f"{pub['overall_ler']:>9.2e} {feas:>8}")
        else:
            print(f"{name:<22} {score:>7.2f}  INVALID: {pub.get('reason', '?')[:60]}")


def ablate(args):
    """Scheduler-sensitivity ablation: score the paper reference and the
    GeneCS beta=0.46 gadget under the evaluator's Konig exact minimum edge
    coloring AND a greedy first-fit coloring (the two common generic
    choices; greedy can exceed the Tanner max degree Delta, deepening every
    round). Purpose: neither GeneCS nor Cross et al. publish their merged-
    circuit scheduler in reusable form, so this brackets how much the
    unpublished choice could move the comparison — if the ranking and
    margins are stable across schedulers, the story does not depend on
    guessing theirs."""
    import numpy as np
    import evaluate as ev

    def greedy_color_schedule(H_rows, anc_ids):
        H = np.asarray(H_rows) % 2
        check_used = [set() for _ in range(H.shape[0])]
        qubit_used = [set() for _ in range(H.shape[1])]
        color = {}
        for r in range(H.shape[0]):
            for q in np.flatnonzero(H[r]):
                q = int(q)
                c = 0
                while c in check_used[r] or c in qubit_used[q]:
                    c += 1
                color[(r, q)] = c
                check_used[r].add(c); qubit_used[q].add(c)
        delta = max(color.values()) + 1 if color else 0
        ticks = [[] for _ in range(delta)]
        for (r, q), c in sorted(color.items()):
            ticks[c].append((int(q), int(anc_ids[r])))
        return ticks

    if args.budget_scale != 1.0:
        ev.P_BUDGET = tuple((max(10, int(e * args.budget_scale)),
                             max(400, int(s * args.budget_scale)))
                            for (e, s) in ev.P_BUDGET)
        print(f"(budgets scaled x{args.budget_scale}: {ev.P_BUDGET})")

    import genecs
    spec_g, _ = genecs.genecs_spec(beta=0.46, seed=1, restarts=150)
    cases = [("paper-41", _reference_spec(ev)), ("genecs b=.46", spec_g)]
    konig = ev.color_schedule
    rows = []
    for sched_name, sched in (("konig", konig),
                              ("greedy", greedy_color_schedule)):
        ev.color_schedule = sched
        for name, spec in cases:
            t0 = time.time()
            res = ev.aggregate_fn([lambda spec=spec: spec])
            pub = res.get("public", {})
            rows.append((sched_name, name, res["combined_score"], pub))
            print(f"[{time.strftime('%H:%M:%S')}] {sched_name}/{name}: "
                  f"score={res['combined_score']:.2f} depths="
                  f"{pub.get('depth_x')}/{pub.get('depth_z')} "
                  f"({time.time() - t0:.0f} s)", flush=True)
    ev.color_schedule = konig
    print("\n=== scheduler ablation ===")
    hdr = (f"{'sched':<7} {'gadget':<13} {'score':>7} {'dX/dZ':>6} "
           f"{'m_lo':>6} {'m_gate':>6} {'LER_gate':>9}")
    print(hdr); print("-" * len(hdr))
    for sched_name, name, score, pub in rows:
        if pub.get("valid"):
            print(f"{sched_name:<7} {name:<13} {score:>7.2f} "
                  f"{pub['depth_x']}/{pub['depth_z']:>4} {pub['margin_lo']:>6.2f} "
                  f"{pub['margin_gate']:>6.2f} {pub['overall_ler']:>9.2e}")
    print("NOTE: greedy margins are computed against the KONIG-calibrated "
          "LER_REFS, so within one scheduler compare candidates to the "
          "paper row of the SAME scheduler, not to the absolute margins.")


def main():
    ap = argparse.ArgumentParser(description="calibrate LER_REFS / compare benchmark gadgets")
    ap.add_argument("--errors", type=int, default=300, help="max errors per circuit per point")
    ap.add_argument("--shots-lo", type=int, default=10000)
    ap.add_argument("--shots-gate", type=int, default=8000)
    ap.add_argument("--shots-hi", type=int, default=4000)
    ap.add_argument("--compare", action="store_true",
                    help="score the benchmark set (paper/tree/genecs/matching/seed) "
                         "through the full v4 pipeline and print a table")
    ap.add_argument("--ablate", action="store_true",
                    help="scheduler-sensitivity ablation (Konig vs greedy "
                         "first-fit) on the paper + GeneCS gadgets")
    ap.add_argument("--only", type=str, default="",
                    help="comma-separated substrings selecting compare cases "
                         "(e.g. --only paper,tree)")
    ap.add_argument("--budget-scale", type=float, default=1.0,
                    help="scale the sampling budgets in --compare/--ablate "
                         "(0.5 halves shots/errors — coarser margins, faster)")
    args = ap.parse_args()
    if args.compare:
        compare(args)
    elif args.ablate:
        ablate(args)
    else:
        calibrate(args)


if __name__ == "__main__":
    main()

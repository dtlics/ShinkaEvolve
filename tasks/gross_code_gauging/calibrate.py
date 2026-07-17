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
    d_hat, parts, weakest, _ = ev.estimate_fault_distance(
        g, rounds, circs[1][1], circs[1][2], rng)
    print(f"reference gadget OK (Q=41, deformed depths {meta['depth_def']}); "
          f"fault-distance estimate d_hat={d_hat} (weakest={weakest}, parts={parts}) "
          f"— the evaluator's D_TARGET={ev.D_TARGET} must be <= the spatial parts here.")

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

    print("\n=== v4 comparison table ===")
    hdr = (f"{'gadget':<22} {'score':>7} {'Q':>3} {'R':>3} {'d_hat':>5} "
           f"{'m_lo':>6} {'m_gate':>6} {'LER_gate':>9} {'feasible':>8}")
    print(hdr); print("-" * len(hdr))
    for name, score, pub, dt in rows:
        if pub.get("valid"):
            feas = "YES" if (pub.get("protected") and score > -8) else "no"
            print(f"{name:<22} {score:>7.2f} {pub['elements']:>3} {pub['rounds']:>3} "
                  f"{pub['fault_dist_est']:>5} {pub['margin_lo']:>6.2f} "
                  f"{pub['margin_gate']:>6.2f} {pub['overall_ler']:>9.2e} {feas:>8}")
        else:
            print(f"{name:<22} {score:>7.2f}  INVALID: {pub.get('reason', '?')[:60]}")


def main():
    ap = argparse.ArgumentParser(description="calibrate LER_REFS / compare benchmark gadgets")
    ap.add_argument("--errors", type=int, default=300, help="max errors per circuit per point")
    ap.add_argument("--shots-lo", type=int, default=10000)
    ap.add_argument("--shots-gate", type=int, default=8000)
    ap.add_argument("--shots-hi", type=int, default=4000)
    ap.add_argument("--compare", action="store_true",
                    help="score the benchmark set (paper/tree/genecs/matching/seed) "
                         "through the full v4 pipeline and print a table")
    ap.add_argument("--only", type=str, default="",
                    help="comma-separated substrings selecting compare cases "
                         "(e.g. --only paper,tree)")
    ap.add_argument("--budget-scale", type=float, default=1.0,
                    help="scale the sampling budgets in --compare (0.5 halves "
                         "shots/errors — coarser margins, faster table)")
    args = ap.parse_args()
    if args.compare:
        compare(args)
    else:
        calibrate(args)


if __name__ == "__main__":
    main()

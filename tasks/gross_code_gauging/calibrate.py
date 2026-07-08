"""Recalibrate the LER_REFS noise curve for the gross_code_gauging evaluator.

Measures the end-to-end error of the PAPER REFERENCE gadget (18 matching +
4 expansion edges, R=12) at every P_GRID point under the current harness
configuration (noise model, scheduler, protocol shape, decoder). Run this
whenever any of those change, then update the three constants in evaluate.py
(or set GAUGE_LER_REF_LO/GATE/HI).

Usage:
    python tasks/gross_code_gauging/calibrate.py [--errors 300]
        [--shots-lo 10000] [--shots-gate 8000] [--shots-hi 4000]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

_TASK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_TASK_DIR)))   # repo root (shinka)
sys.path.insert(0, _TASK_DIR)                                     # evaluate.py


def main():
    ap = argparse.ArgumentParser(description="calibrate LER_REFS")
    ap.add_argument("--errors", type=int, default=300, help="max errors per circuit per point")
    ap.add_argument("--shots-lo", type=int, default=10000)
    ap.add_argument("--shots-gate", type=int, default=8000)
    ap.add_argument("--shots-hi", type=int, default=4000)
    args = ap.parse_args()

    import evaluate as ev  # the task evaluator (same directory)

    paper_extra = [(2, 9), (2, 4), (9, 11), (10, 11)]   # App. B Eq. 5 in label space
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

    edges, dummies, rounds = ev.parse_spec({"edges": matching + paper_extra, "rounds": 12})
    g = ev.build_gauged(edges, dummies)
    assert (g["E"], g["n_av"], g["n_bp"], g["overhead"]) == (22, 12, 7, 41), \
        f"reference gadget drifted: {g['E']}/{g['n_av']}/{g['n_bp']}/{g['overhead']}"

    circs = []
    for p in ev.P_GRID:
        cx, meta = ev.build_protocol_circuit(g, rounds, "X", p)
        cz, _ = ev.build_protocol_circuit(g, rounds, "Z", p)
        circs.append((p, cx, cz))
    assert ev._noiseless_ok(circs[1][1]) and ev._noiseless_ok(circs[1][2])

    ev.P_BUDGET = ((args.errors, args.shots_lo),
                   (args.errors, args.shots_gate),
                   (args.errors, args.shots_hi))
    print(f"reference gadget OK (Q=41, deformed depths {meta['depth_def']}); sampling "
          f"P_GRID={tuple(round(p, 5) for p in ev.P_GRID)} with {ev.N_WORKERS} workers, "
          f"budgets {ev.P_BUDGET} ...", flush=True)
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


if __name__ == "__main__":
    main()

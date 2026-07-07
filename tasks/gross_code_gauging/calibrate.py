"""Recalibrate LER_REF for the gross_code_gauging evaluator.

Measures the end-to-end error of the PAPER REFERENCE gadget (18 matching +
4 expansion edges, R=12) under the current harness configuration (PHYS_P,
noise model, scheduler, decoder). Run this whenever any of those change, then
update LER_REF in evaluate.py (or set GAUGE_LER_REF).

Usage:
    python tasks/gross_code_gauging/calibrate.py [--errors 300] [--shots 30000]
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
    ap = argparse.ArgumentParser(description="calibrate LER_REF")
    ap.add_argument("--errors", type=int, default=300, help="max errors per circuit")
    ap.add_argument("--shots", type=int, default=30000, help="max shots per circuit")
    args = ap.parse_args()

    import evaluate as ev  # the task evaluator (same directory)

    paper_extra = [(2, 9), (2, 4), (9, 11), (10, 11)]   # App. B Eq. 5 in label space
    # the 18 matching edges, in label space, from the evaluator's own data
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

    circ_x, meta = ev.build_protocol_circuit(g, rounds, "X")
    circ_z, _ = ev.build_protocol_circuit(g, rounds, "Z")
    assert ev._noiseless_ok(circ_x) and ev._noiseless_ok(circ_z)

    print(f"reference gadget OK (Q=41, depths def={meta['depth_def']}); "
          f"sampling at p={ev.PHYS_P} with {ev.N_WORKERS} workers, "
          f"max {args.errors} errors / {args.shots} shots per circuit ...")
    ev.MAX_ERRORS, ev.MAX_SHOTS = args.errors, args.shots
    t0 = time.time()
    px, pz, overall, (ex, sx, ez, sz) = ev.sample_ler(circ_x, circ_z)
    dt = time.time() - t0
    std = 0.434 / max(1, (ex + ez)) ** 0.5
    print(f"X-circuit: {ex}/{sx} = {px:.4e}")
    print(f"Z-circuit: {ez}/{sz} = {pz:.4e}")
    print(f"overall   = {overall:.4e}  (+-{std:.3f} decades, {dt:.0f} s)")
    print(f"-> set LER_REF = {overall:.4e} in evaluate.py (current: {ev.LER_REF:.4e})")


if __name__ == "__main__":
    main()

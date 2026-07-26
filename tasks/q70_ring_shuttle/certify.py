"""Out-of-loop certification: real Monte-Carlo LER for a candidate plan.

Re-runs a candidate program in a FRESH process (so in-loop scores must
reproduce — any mismatch is a red flag), compiles its plan, and measures the
logical error rate of the assembled circuit with BP-OSD at one or more
physical error rates. This is where the head-to-head against the paper's
published Q70 numbers happens; it is far too slow for the inner loop
(BP-OSD costs ~0.1-1 s/shot on this circuit).

Usage:
  python tasks/q70_ring_shuttle/certify.py --program_path tasks/q70_ring_shuttle/initial.py
  python tasks/q70_ring_shuttle/certify.py --program_path <elite.py> \
      --p 1e-3 2e-3 3e-3 --target_errors 100 --max_shots 200000 --osd_order 3

Paper Q70 anchors (arXiv:2604.19481, Pauli-only reference curve, Table XII):
  LER/SEC 9.72e-7 @ p=1e-3  ->  7.27e-11 @ p=1e-4 via the ansatz
  p^ceil(d_circ/2) * exp(alpha p^2 + beta p + zeta), (alpha,beta,zeta) =
  (1.07e6, -3410, 23.0); SEC = 27.70 POC (abstract) / ~34.2 (micro-counted);
  424 transport rounds; 220 physical qubits. Our noise accounting follows the
  same moving-qubit model but derives transport noise from the candidate's own
  timeline, so compare LER curves qualitatively and POC/rounds directly.
"""

import argparse
import importlib.util
import json
import os
import sys
import time

import numpy as np

_TASK = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TASK))
for pth in (_ROOT, _TASK):
    if pth not in sys.path:
        sys.path.insert(0, pth)

import evaluate as ev  # noqa: E402


def load_candidate(path):
    spec = importlib.util.spec_from_file_location("_q70_candidate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program_path", required=True)
    ap.add_argument("--p", nargs="+", type=float, default=[2e-3])
    ap.add_argument("--target_errors", type=int, default=ev.TARGET_ERRORS)
    ap.add_argument("--max_shots", type=int, default=20_000)
    ap.add_argument("--osd_order", type=int, default=0)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    cand = load_candidate(args.program_path)
    plan = cand.run_experiment(ev.get_kwargs(0)["spec"])
    compiled = ev.compile_plan(plan)
    ok, which = ev.noiseless_ok(compiled)
    if not ok:
        raise SystemExit(f"FAIL: circuit ({which}) not noiseless-deterministic")

    print(f"plan: transport_rounds={compiled['transport_rounds']} "
          f"(paper 424), T_SEC={compiled['t_sec_poc']:.2f} POC "
          f"(paper 27.70/34.2), zones={compiled['zones']}, "
          f"exposure={compiled['exposure']:.2f}")
    out = ev._aggregate_impl([plan])
    print(f"in-loop combined_score (must reproduce the archive value): "
          f"{out['combined_score']:+.4f}")

    rng = np.random.default_rng(args.seed if args.seed is not None
                                else np.random.SeedSequence().entropy % 2**31)
    rows = []
    for p in args.p:
        lers = {}
        for obs in ("X", "Z"):
            circ = ev.build_circuit(compiled, obs, p)
            t0 = time.time()
            ler, shots, errs = ev.ler_sample(
                circ, args.target_errors, rng, max_shots=args.max_shots,
                osd_order=args.osd_order,
                progress=lambda s, e: print(
                    f"    p={p:g} {obs}: {s} shots, {e} errors", flush=True))
            lers[obs] = (ler, shots, errs, time.time() - t0)
        px, pz = lers["X"][0], lers["Z"][0]
        overall = 1 - (1 - px) * (1 - pz)
        per_sec = 1 - (1 - overall) ** (1 / ev.NC_SECS) if overall < 1 else 1.0
        rows.append((p, px, pz, overall, per_sec,
                     lers["X"][1] + lers["Z"][1],
                     lers["X"][2] + lers["Z"][2],
                     lers["X"][3] + lers["Z"][3]))
        print(f"  p={p:g}: X {px:.3e}  Z {pz:.3e}  overall {overall:.3e} "
              f"per shot ({ev.NC_SECS} SECs)  ->  LER/SEC {per_sec:.3e}")

    print("\np        LER/shot   LER/SEC    shots    errors  time_s")
    for p, px, pz, ov, ps, sh, er, tt in rows:
        print(f"{p:<8g} {ov:<10.3e} {ps:<10.3e} {sh:<8d} {er:<7d} {tt:.0f}")
    print("\npaper Q70 reference: LER/SEC 9.72e-7 @ p=1e-3 "
          "(ansatz -> 7.27e-11 @ p=1e-4)")
    print(json.dumps({"rows": [list(r) for r in rows]}))


if __name__ == "__main__":
    main()

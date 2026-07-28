"""Does the deterministic exposure metric actually predict real LER?

The in-loop score replaced Monte-Carlo LER with `exposure` (expected fault
events per SEC), justified by the paper's ansatz LER ~ p_eff^ceil(d_circ/2),
i.e. LER ratio = (exposure ratio)^5 with the circuit frozen. That substitution
has never been validated against measured LER ACROSS PLANS — run v2 certified
only one plan.

This measures real BP-OSD LER for the three shipped seeds, which span a 6%
spread in total exposure, and checks (a) ORDERING (does lower exposure really
mean lower LER — all that selection needs) and (b) MAGNITUDE (does the ^5
prediction hold, or is the metric mis-calibrated).

Decoder is held fixed across plans (osd_order configurable) so the comparison
is apples-to-apples even if the absolute numbers are decoder-limited.
"""
import json
import os
import sys
import time

import numpy as np

TASK = r"C:\Users\dtlic\Documents\GitHub\ShinkaEvolve\.claude\worktrees\infallible-gagarin-a88215\tasks\q70_ring_shuttle"
sys.path.insert(0, TASK)
import evaluate as ev  # noqa: E402

P = float(os.environ.get("MV_P", "2e-3"))
TARGET = int(os.environ.get("MV_TARGET", "400"))
MAXSHOTS = int(os.environ.get("MV_MAXSHOTS", "60000"))
OSD = int(os.environ.get("MV_OSD", "0"))
OBS = os.environ.get("MV_OBS", "X")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metric_validation.json")

SEEDS = [("unfolded", "initial"), ("folded", "initial_folded"),
         ("evolved", "initial_evolved")]


def main():
    import importlib
    spec = ev.get_kwargs(0)["spec"]
    rows = []
    for label, mod_name in SEEDS:
        mod = importlib.import_module(mod_name)
        plan = mod.build_embedding_and_shuttle(spec)
        c = ev.compile_plan(plan)
        circ = ev.build_circuit(c, OBS, P)
        rng = np.random.default_rng(0xC0FFEE + len(label))
        t0 = time.time()
        ler, shots, errs = ev.ler_sample(
            circ, TARGET, rng, max_shots=MAXSHOTS, osd_order=OSD,
            progress=lambda s, e: print(f"    {label}: {s} shots, {e} errors",
                                        flush=True))
        dt = time.time() - t0
        # per-SEC logical error rate, paper convention
        ler_sec = 1.0 - (1.0 - ler) ** (1.0 / ev.NC_SECS)
        rel = ler / np.sqrt(max(errs, 1))
        rows.append({
            "plan": label, "exposure": c["exposure"],
            "var_exposure": c["exposure"] - ev.FROZEN_EXPOSURE,
            "rounds": c["transport_rounds"], "zones": c["zones"],
            "ler_shot": ler, "ler_sec": ler_sec, "shots": shots,
            "errors": errs, "stderr": rel, "seconds": dt,
        })
        print(f"[{label}] exposure={c['exposure']:.2f} rounds={c['transport_rounds']} "
              f"LER/shot={ler:.4e} +-{rel:.1e} ({errs}/{shots}, {dt:.0f}s)",
              flush=True)

    print("\n=== ORDERING (all selection needs) ===")
    order_exp = [r["plan"] for r in sorted(rows, key=lambda r: r["exposure"])]
    order_ler = [r["plan"] for r in sorted(rows, key=lambda r: r["ler_shot"])]
    print(f"  by exposure (best first): {order_exp}")
    print(f"  by measured LER         : {order_ler}")
    print(f"  ORDERING AGREES: {order_exp == order_ler}")

    print("\n=== MAGNITUDE (is the ^5 ansatz calibrated?) ===")
    base = [r for r in rows if r["plan"] == "evolved"][0]
    for r in rows:
        if r["plan"] == "evolved":
            continue
        pred = (r["exposure"] / base["exposure"]) ** 5
        meas = r["ler_shot"] / base["ler_shot"]
        sig = abs(meas - 1.0) / np.sqrt((r["stderr"] / r["ler_shot"]) ** 2
                                        + (base["stderr"] / base["ler_shot"]) ** 2)
        print(f"  {r['plan']:9s} vs evolved: exposure ratio "
              f"{r['exposure']/base['exposure']:.4f} -> predicted LER ratio "
              f"{pred:.3f} | measured {meas:.3f} | difference from 1.0 is "
              f"{sig:.1f} sigma")

    json.dump({"p": P, "osd_order": OSD, "observable": OBS, "rows": rows},
              open(OUT, "w", encoding="utf-8"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()

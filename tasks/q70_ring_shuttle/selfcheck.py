"""Self-check driver: seed build/compile/determinism, scoring, invalid probes.

Usage:  python tasks/q70_ring_shuttle/selfcheck.py [--ler]
"""
import copy
import os
import sys
import time

_TASK = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TASK))
for pth in (_ROOT, _TASK):
    if pth not in sys.path:
        sys.path.insert(0, pth)

import evaluate as ev          # noqa: E402
import initial as seed         # noqa: E402
import initial_folded as seed_folded  # noqa: E402


def check_probes(plan):
    """Malformed/illegal plans must land on the INVALID sentinel, not crash."""
    probes = []

    p = copy.deepcopy(plan)
    probes.append(("empty plan", {}))

    p = copy.deepcopy(plan)
    p["timeline"].append({"t": "gate", "round": 7})
    probes.append(("8th gate round", p))

    p = copy.deepcopy(plan)
    p["timeline"].insert(1, {"t": "move", "moves": [[0, 5, 6]]})
    probes.append(("int-valued sites in move", p))

    p = copy.deepcopy(plan)
    p["timeline"].insert(1, {"t": "split", "pairs": [[0, 7]]})
    probes.append(("int-valued split target", p))

    p = copy.deepcopy(plan)
    b_site = p["layout"]["beacon"][0]
    tgt = ["J", b_site[1], b_site[2]]
    p["timeline"].insert(1, {"t": "move", "moves": [[140, b_site, tgt]]})
    probes.append(("beacon move (static rule)", p))

    p = copy.deepcopy(plan)
    p["timeline"] = [ph for ph in p["timeline"] if ph["t"] != "measure"]
    probes.append(("no measurement", p))

    p = copy.deepcopy(plan)
    p["grid"]["cols"] = 200
    probes.append(("grid too wide", p))

    p = copy.deepcopy(plan)
    p["layout"]["data"] = p["layout"]["data"][:-1]
    probes.append(("69 data qubits", p))

    p = copy.deepcopy(plan)
    p["timeline"] = [{"t": "bogus"}] + p["timeline"]
    probes.append(("unknown phase type", p))

    p = copy.deepcopy(plan)
    p["layout"]["reservoir"] = [lambda x: x] + p["layout"]["reservoir"][1:]
    probes.append(("non-JSON payload", p))

    for name, pp in probes:
        try:
            out = ev._aggregate_impl([pp])
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"probe '{name}' CRASHED: {type(e).__name__}: {e}")
        assert out["combined_score"] == ev.INVALID_SCORE, \
            f"probe '{name}' scored {out['combined_score']}, expected sentinel"
        assert "INVALID" in out["text_feedback"], name
        print(f"[ok] probe '{name}' -> sentinel ({out['text_feedback'][:72]}...)")


def check_seed(name, builder, spec, expect_zero):
    t0 = time.time()
    plan = builder(spec)
    t1 = time.time()
    print(f"\n=== {name} seed ===")
    print(f"[ok] plan built in {t1-t0:.2f}s ({len(plan['timeline'])} phases)")
    compiled = ev.compile_plan(plan)
    t2 = time.time()
    print(f"[ok] compiled+validated in {t2-t1:.2f}s")
    print(f"     transport rounds : {compiled['transport_rounds']}")
    print(f"     merge/split rnds : {compiled['merge_rounds']}")
    print(f"     per-gap used     : {compiled['per_gap_rounds']}")
    print(f"     per-gap floors   : {compiled['per_gap_floors']} (wrap floor "
          f"{compiled['wrap_floor']})")
    print(f"     zones            : {compiled['zones']}")
    print(f"     exposure         : {compiled['exposure']:.2f} "
          f"(plan-dependent {compiled['exposure'] - ev.FROZEN_EXPOSURE:.2f})")
    print(f"     T_core / T_SEC   : {compiled['t_core_poc']:.2f} / "
          f"{compiled['t_sec_poc']:.2f} POC "
          f"(paper Q70: 27.70 abstract, ~34.2 micro-counted)")
    ok, which = ev.noiseless_ok(compiled)
    t3 = time.time()
    assert ok, f"noiseless determinism FAILED on {which}-observable circuit"
    print(f"[ok] noiseless determinism (both observables) in {t3-t2:.2f}s")
    out = ev._aggregate_impl([plan])
    print(f"[ok] scoring path in {time.time()-t3:.2f}s: combined_score = "
          f"{out['combined_score']:+.4f}"
          + ("  (target 0.0000)" if expect_zero else ""))
    if expect_zero:
        assert abs(out["combined_score"]) < 5e-3, "seed anchors need recalibration!"
    return plan


def main():
    spec = ev.get_kwargs(0)["spec"]
    plan = check_seed("unfolded (anchor)", seed.build_embedding_and_shuttle,
                      spec, expect_zero=True)
    check_seed("folded", seed_folded.build_embedding_and_shuttle,
               spec, expect_zero=False)
    print()
    check_probes(plan)

    if "--ler" in sys.argv:
        import numpy as np
        rng = np.random.default_rng(12345)
        for obs in ("X", "Z"):
            circ = ev.build_circuit(compiled, obs, ev.P_CERT_DEFAULT)
            t5 = time.time()
            ler, shots, errs = ev.ler_sample(
                circ, ev.TARGET_ERRORS, rng, max_shots=4096,
                progress=lambda s, e: print(f"      ... {s} shots, {e} errors",
                                            flush=True))
            print(f"[ok] {obs}-LER @ p={ev.P_CERT_DEFAULT}: {ler:.3e} "
                  f"({errs} errors / {shots} shots, {time.time()-t5:.1f}s)")


if __name__ == "__main__":
    main()

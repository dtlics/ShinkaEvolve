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
import initial_folded as seed_folded    # noqa: E402
import initial_evolved as seed_evolved  # noqa: E402
import initial_annealed as seed_annealed  # noqa: E402


def replay(plan):
    """Every ion's position after the timeline, WITHOUT validating anything.

    Used only to build probes; the evaluator does its own replay.
    """
    pos = {}
    for gname, b in (("data", ev.DATA0), ("x_anc", ev.XANC0),
                     ("z_anc", ev.ZANC0), ("beacon", ev.BEAC0),
                     ("reservoir", ev.RES0)):
        for i, s in enumerate(plan["layout"][gname]):
            pos[b + i] = tuple(s)
    for ph in plan["timeline"]:
        if ph["t"] == "move":
            for q, _fr, to in ph["moves"]:
                pos[q] = tuple(to)
        elif ph["t"] == "merge":
            for mob, host in ph["pairs"]:
                pos[mob] = pos[host]
        elif ph["t"] == "split":
            for mob, to in ph["pairs"]:
                pos[mob] = tuple(to)
    return pos


def add_rounds(plan, rounds):
    """Append parallel transport rounds [(qid, from, to), ...] to a plan."""
    for rnd in rounds:
        plan["timeline"].append(
            {"t": "move",
             "moves": [[int(q), list(f), list(t)] for q, f, t in rnd]})
    return plan


def free_step(pos, site):
    """Any site one primitive step from ``site`` that no ion occupies."""
    occ = set(pos.values())
    _k, r, c = site
    cands = [("J", r, c), ("J", r, c - 1), ("U", r, c - 1), ("D", r, c - 1)]
    for s in cands:
        if s[2] >= 0 and s not in occ and ev._is_edge(site, s):
            return s
    return None


def species_swap_rounds(plan, pos):
    """Rounds that swap ONE X-ancilla's site with ONE Z-ancilla's site.

    Needs an X and a Z ancilla resting in the same row two columns apart with
    the section between them, both flanking junctions and one leg well free.
    The high ion ducks into the leg, the low one walks across, then the high
    one walks back — a legal 9-round manoeuvre that leaves the UNION of the
    ancilla sites untouched and only the per-species sets wrong.
    Returns (rounds, ax, az) or None.
    """
    occ = set(pos.values())
    xa = [(q, pos[q]) for q in range(ev.XANC0, ev.XANC0 + ev.N_HALF)]
    za = [(q, pos[q]) for q in range(ev.ZANC0, ev.ZANC0 + ev.N_HALF)]
    for ax, sx in xa:
        if sx[0] != "S":
            continue
        for az, sz in za:
            if sz[0] != "S" or sz[1] != sx[1] or abs(sz[2] - sx[2]) != 2:
                continue
            r = sx[1]
            a = min(sx[2], sz[2])
            lo_q = ax if sx[2] == a else az
            hi_q = az if sx[2] == a else ax
            mid, j0, j1 = ("S", r, a + 1), ("J", r, a), ("J", r, a + 1)
            if any(s in occ for s in (mid, j0, j1)):
                continue
            leg = next((s for s in (("D", r, a + 1), ("U", r, a + 1))
                        if s not in occ), None)
            if leg is None:
                continue
            hi, lo = ("S", r, a + 2), ("S", r, a)
            rounds = [
                [(hi_q, hi, leg)],
                [(lo_q, lo, j0)], [(lo_q, j0, mid)],
                [(lo_q, mid, j1)], [(lo_q, j1, hi)],
                [(hi_q, leg, j1)], [(hi_q, j1, mid)],
                [(hi_q, mid, j0)], [(hi_q, j0, lo)],
            ]
            return rounds, ax, az
    return None


def check_cycle_probes(name, plan):
    """The cycle-boundary rule must not be gameable.

    Four probes, each of which MUST reach the sentinel with a named rule:
      (i)   a DATA ion left displaced        -> non-ancilla exact-site rule
      (ii)  an ancilla off the layout set    -> per-species set rule
      (iii) an X/Z end-position SWAP         -> per-species set rule (the
            union is preserved, so only a SEPARATE per-species comparison
            catches it)
      (iv)  two ancillas ending on one site  -> collision rule
    """
    print(f"\n--- cycle-boundary probes on the {name} plan ---")
    pos = replay(plan)

    p = copy.deepcopy(plan)
    dst = free_step(pos, pos[ev.DATA0])
    assert dst is not None, "no free step next to data 0"
    add_rounds(p, [[(ev.DATA0, pos[ev.DATA0], dst)]])
    run_probe("data ion left displaced", p,
              "non-ancilla ions end away from their layout position")

    p = copy.deepcopy(plan)
    dst = free_step(pos, pos[ev.XANC0])
    assert dst is not None, "no free step next to X-ancilla 0"
    add_rounds(p, [[(ev.XANC0, pos[ev.XANC0], dst)]])
    run_probe("ancilla parked off the layout set", p,
              "the set of X-ancilla end positions is not the set of "
              "X-ancilla layout sites")

    got = species_swap_rounds(plan, pos)
    assert got is not None, f"{name}: no X/Z pair admits the swap manoeuvre"
    rounds, ax, az = got
    p = copy.deepcopy(plan)
    add_rounds(p, rounds)
    # prove the probe is exactly the case a COMBINED check would wave through
    end = replay(p)
    xs = range(ev.XANC0, ev.XANC0 + ev.N_HALF)
    zs = range(ev.ZANC0, ev.ZANC0 + ev.N_HALF)
    home = {q: s for q, s in replay({"layout": plan["layout"],
                                     "timeline": []}).items()}
    assert sorted(end[q] for q in list(xs) + list(zs)) == \
        sorted(home[q] for q in list(xs) + list(zs)), \
        "swap probe should preserve the COMBINED ancilla site set"
    assert sorted(end[q] for q in xs) != sorted(home[q] for q in xs), \
        "swap probe should break the X-species set"
    print("     [proof] the swapped plan preserves the COMBINED ancilla site "
          "set exactly, so only a per-species comparison can reject it")
    run_probe(f"X-anc {ax} <-> Z-anc {az} end-position swap "
              f"(union preserved!)", p,
              "end positions is not the set of")

    p = copy.deepcopy(plan)
    add_rounds(p, rounds[:5] + [[(rounds[0][0][0], rounds[0][0][2],
                                  rounds[0][0][1])]])
    run_probe("two ancillas ending on one site", p,
              "occupied by stationary ion")


def run_probe(name, plan, expect):
    try:
        out = ev._aggregate_impl([plan])
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"probe '{name}' CRASHED: {type(e).__name__}: {e}")
    assert out["combined_score"] == ev.INVALID_SCORE, \
        f"probe '{name}' scored {out['combined_score']}, expected sentinel"
    fb = out["text_feedback"]
    assert "INVALID" in fb, name
    assert expect in fb, \
        f"probe '{name}' hit the sentinel but on the wrong rule:\n  {fb}"
    print(f"[ok] probe '{name}' -> sentinel\n     {fb[:150]}")


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
    print(f"     rounds/floor     : {compiled['transport_rounds']}/"
          f"{compiled['floor_total']} = "
          f"{compiled['transport_rounds']/max(compiled['floor_total'],1):.2f}x")
    print(f"     parallelism      : {compiled['ions_per_round']:.1f} ions/round, "
          f"{compiled['low_occ_rounds']} rounds under 20 ions")
    print(f"     wrap-back rounds : {compiled['wrap_rounds']} "
          f"(post-measure rounds; ancillas need only restore their species' "
          f"site SET, so this should be the DATA walk home)")
    print(f"     rail sections    : {compiled['zones']}  [paper Q70 block ~288]"
          f"   (transit vertices {compiled['vertices']})")
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
    plan_folded = check_seed("folded", seed_folded.build_embedding_and_shuttle,
                             spec, expect_zero=False)
    check_seed("evolved (pitch-4 repair of run v2 best)",
               seed_evolved.build_embedding_and_shuttle, spec, expect_zero=False)
    check_seed("annealed (run v3 geometry + shipped router) BEST",
               seed_annealed.build_embedding_and_shuttle, spec, expect_zero=False)
    print()
    check_probes(plan)
    check_cycle_probes("folded", plan_folded)

    if "--ler" in sys.argv:
        import numpy as np
        rng = np.random.default_rng(12345)
        compiled = ev.compile_plan(plan)     # the anchor seed's compiled plan
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

"""Paper-equivalent Q70 baseline plan: IonQ's published NOISE PROFILE, legal here.

WHAT THIS IS
------------
A LEGAL plan for tasks/q70_ring_shuttle whose noise profile matches the Q70
column of the paper's Table XXVI:

    424 transport rounds, 14 (opt. 16) merge/split rounds,
    1 prep phase (70 ancillas), 1 measure phase (70 ancillas)

  ->  exposure = 504 (frozen) + idle_slots/100 + 140*(424+14)/2000
             = 504 + 1.40 + 30.66 = 536.06          (the README's paper row)

WHY A NOISE-PROFILE MATCH IS THE RIGHT BASELINE, AND WHY IT IS FAIR
-------------------------------------------------------------------
`evaluate.build_circuit(compiled, obs, p)` reads exactly ONE key of the
compiled plan: `compiled["segments"]` (evaluate.py line 786; verified by
grepping every `compiled[...]` access -- all the others are in the scoring
path, never in circuit construction).  A segment is one of
    ("transport", k) | ("ms", k) | ("gate", t) | ("prep", ancs) | ("measure", ancs)
and the noise it emits is
    transport/ms : DEPOLARIZE1 on all 140 sim qubits, q = 1-(1-p/2000)^k
    gate         : the frozen CX/CZ layer + DEPOLARIZE2 at p
    prep/measure : RX/MX with p/10 flip + DEPOLARIZE1 p/100 on the idle rest
So the stim circuit -- hence the DEM, hence the LER -- is a function of the
plan ONLY through (the ordered run-lengths of transport/ms rounds, the gate
order, the prep/measure batch sizes).  Layout, geometry, which ion moved
where, footprint: all invisible to the sampler.  Two legal plans with the same
segment profile produce byte-identical circuits.

Therefore "reproduce the paper's transport/merge/prep/measure counts" is not
an approximation of a fair baseline -- it is exactly the baseline, for LER
purposes.  Any 424-round legal plan is as good as any other.

WHAT THIS IS *NOT*
------------------
It is NOT a reconstruction of IonQ's embedding.  It does not reproduce their
layout, their per-gap routing, their trap footprint (~288 rail sections),
their ion->site assignment, or their Algorithm-1 shift legs.  Its `zones`,
`floor_total`, `rounds_over_floor` and `combined_score` are meaningless as
statements about the paper's design -- only `exposure`, `transport_rounds`,
`merge_rounds`, `t_sec_poc` and the resulting LER curve carry meaning.
It is also NOT a claim that IonQ's decoder would give these numbers: it is an
arm of a two-arm experiment run under ONE fixed decoder, where only the RATIO
between arms is interpretable (their published curve is beam-search decoded).

HOW IT IS BUILT
---------------
Take a shipped seed (default `initial_annealed.py`, our best plan: 244
transport rounds, exposure 523.46) and PAD its transport rounds to 424 by
inserting no-op "out and back" move phases -- one ion steps to a free
neighbouring well and steps back on the next round.  Padding is distributed
across the 7 inter-gate gaps and the wrap region in proportion to the seed's
OWN per-gap round counts (a uniform dilation), because the paper publishes
only the 424 total, not its per-gap split.  Every padded round is charged
exactly like a real transport round (the noise model charges 140*p/2000 per
round no matter how many ions move), so the padding is noise-exact.

The seed's layout, gate alignment, merges, prep/measure batching and cycle
boundary are untouched -- the out-and-back restores the ion's position before
any other phase runs -- so the ONLY difference from our arm is the quantity
under test: 424 vs 244 transport rounds.

Padding targets are chosen to be non-"S" wells (J/U/D) wherever possible so
even the reported footprint stays the seed's.

USAGE
-----
    python paper_equiv_plan.py                 # build, validate, report
    python paper_equiv_plan.py --ms 16         # Table XXVI's 16 merge/split
    python paper_equiv_plan.py --base initial_folded.py --rounds 424

    import paper_equiv_plan as pe
    plan = pe.run_experiment(spec)             # certify.py-compatible
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys

TASK = r"C:\Users\dtlic\Documents\GitHub\ShinkaEvolve\.claude\worktrees\infallible-gagarin-a88215\tasks\q70_ring_shuttle"
ROOT = os.path.dirname(os.path.dirname(TASK))
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TASK, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evaluate as ev  # noqa: E402

PAPER_TRANSPORT_ROUNDS = 424      # Table XXVI, Q70 column
PAPER_MS_ROUNDS = 16              # Table XXVI, Q70 column (2 x 8 2q layers)
DEFAULT_BASE = "initial_annealed.py"
CACHE_DIR = HERE


# ---------------------------------------------------------------------------
# base-plan loading (seeds are slow to build -- initial_annealed.py anneals for
# ~5.5 min -- so cache the produced plan JSON)
# ---------------------------------------------------------------------------
def load_base_plan(base=DEFAULT_BASE, use_cache=True, spec=None):
    cache = os.path.join(CACHE_DIR, f"_base_plan_{base[:-3]}.json")
    if use_cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    import importlib.util
    path = os.path.join(TASK, base)
    mspec = importlib.util.spec_from_file_location("_q70_base_seed", path)
    mod = importlib.util.module_from_spec(mspec)
    mspec.loader.exec_module(mod)
    plan = mod.run_experiment(spec if spec is not None
                              else ev.get_kwargs(0)["spec"])
    plan = json.loads(json.dumps(plan))
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(plan, f)
    return plan


# ---------------------------------------------------------------------------
# chip-graph helpers (mirror evaluate._is_edge; never widen it)
# ---------------------------------------------------------------------------
def neighbors(site, rows, cols):
    k, r, c = site
    out = []
    for kk in ("S", "J", "U", "D"):
        for rr in range(r - 1, r + 2):
            for cc in range(c - 2, c + 3):
                if not (0 <= rr < rows and 0 <= cc < cols):
                    continue
                cand = (kk, rr, cc)
                if cand == (k, r, c):
                    continue
                if ev._is_edge((k, r, c), cand):
                    out.append(cand)
    # prefer non-S wells so the plan's rail-section footprint is unchanged
    out.sort(key=lambda s: (s[0] == "S", s))
    return out


# ---------------------------------------------------------------------------
# timeline replay -- positions/occupancy at every phase boundary
# ---------------------------------------------------------------------------
def replay(plan):
    """Return per-boundary state: [(idx, pos, occupied, gates_done, merged_n,
    measures_done)] for idx in 0..len(timeline)."""
    rows = plan["grid"]["rows"]
    cols = plan["grid"]["cols"]
    lay = plan["layout"]
    pos = {}
    for q, s in enumerate(lay["data"]):
        pos[ev.DATA0 + q] = tuple(s)
    for q, s in enumerate(lay["x_anc"]):
        pos[ev.XANC0 + q] = tuple(s)
    for q, s in enumerate(lay["z_anc"]):
        pos[ev.ZANC0 + q] = tuple(s)
    for q, s in enumerate(lay["beacon"]):
        pos[ev.BEAC0 + q] = tuple(s)
    for q, s in enumerate(lay["reservoir"]):
        pos[ev.RES0 + q] = tuple(s)
    occupied = {s: q for q, s in pos.items()}
    merged = {}
    gates_done = 0
    measures = 0
    states = []

    def snap(i):
        states.append((i, dict(pos), dict(occupied), gates_done, len(merged),
                       measures))

    snap(0)
    for i, ph in enumerate(plan["timeline"]):
        t = ph["t"]
        if t == "move":
            movers = [(q, tuple(fr), tuple(to)) for q, fr, to in ph["moves"]]
            for q, _fr, _to in movers:
                del occupied[pos[q]]
            for q, _fr, to in movers:
                pos[q] = to
                occupied[to] = q
        elif t == "merge":
            for mob, host in ph["pairs"]:
                del occupied[pos[mob]]
                pos[mob] = pos[host]
                merged[mob] = host
        elif t == "split":
            for mob, to in ph["pairs"]:
                merged.pop(mob)
                pos[mob] = tuple(to)
                occupied[tuple(to)] = mob
        elif t == "gate":
            gates_done += 1
        elif t == "measure":
            measures += 1
        snap(i + 1)
    return states, rows, cols


def _shuttle_phases(pos, occupied, rows, cols, n_pairs):
    """n_pairs x (step-out, step-back) legal no-op move phases at this state."""
    for q in range(ev.N_SIM):
        site = pos[q]
        for cand in neighbors(site, rows, cols):
            if cand in occupied:
                continue
            out = {"t": "move", "moves": [[q, list(site), list(cand)]]}
            back = {"t": "move", "moves": [[q, list(cand), list(site)]]}
            return [copy.deepcopy(x) for _ in range(n_pairs)
                    for x in (out, back)]
    raise RuntimeError("no free neighbouring well for any mobile ion")


# ---------------------------------------------------------------------------
def pad_plan(plan, target_rounds=PAPER_TRANSPORT_ROUNDS, ms_rounds=14,
             verbose=False):
    plan = json.loads(json.dumps(plan))
    states, rows, cols = replay(plan)
    tl = plan["timeline"]

    # --- bucket the seed's transport rounds by gap (gaps 0..6, then wrap) ---
    n_gaps = ev.N_ROUNDS                      # 7
    weights = [0] * (n_gaps + 1)
    anchor = [None] * (n_gaps + 1)            # boundary index to insert at
    for i, ph in enumerate(tl):
        if ph["t"] != "move":
            continue
        _idx, _pos, _occ, gdone, nmerged, meas = states[i]
        b = gdone if meas == 0 else n_gaps
        weights[b] += 1
        if anchor[b] is None and nmerged == 0:
            anchor[b] = i
    for b in range(n_gaps + 1):
        if anchor[b] is None:                 # bucket with no transport rounds
            for i in range(len(tl) + 1):
                _idx, _pos, _occ, gdone, nmerged, meas = states[i]
                if nmerged == 0 and (gdone if meas == 0 else n_gaps) == b:
                    anchor[b] = i
                    break

    have = sum(weights)
    need = target_rounds - have
    if need < 0:
        raise ValueError(f"base plan already has {have} > {target_rounds}")
    if need % 2:
        raise ValueError(f"padding {need} is odd; out-and-back pads by 2s")

    # proportional (uniform-dilation) split, rounded to even numbers
    pairs_total = need // 2
    raw = [pairs_total * w / have for w in weights]
    add = [int(x) for x in raw]
    rem = sorted(range(n_gaps + 1), key=lambda b: -(raw[b] - add[b]))
    k = pairs_total - sum(add)
    for b in rem[:k]:
        add[b] += 1

    # --- insert, from the LAST anchor backwards so earlier indices hold ---
    order = sorted(range(n_gaps + 1), key=lambda b: -anchor[b])
    for b in order:
        if add[b] == 0:
            continue
        i = anchor[b]
        _idx, pos, occ, _g, _m, _meas = states[i]
        tl[i:i] = _shuttle_phases(pos, occ, rows, cols, add[b])

    # --- optional merge/split padding, 14 -> 16 (Table XXVI's count) ---
    if ms_rounds not in (14, 16):
        raise ValueError("ms_rounds must be 14 or 16")
    if ms_rounds == 16:
        for want in ("merge", "split"):
            for i, ph in enumerate(tl):
                if ph["t"] == want and len(ph["pairs"]) >= 2:
                    h = len(ph["pairs"]) // 2
                    a = {"t": want, "pairs": ph["pairs"][:h]}
                    z = {"t": want, "pairs": ph["pairs"][h:]}
                    tl[i:i + 1] = [a, z]
                    break

    if verbose:
        print(f"  buckets (gap0..gap6, wrap): base {weights} "
              f"+ padding {[2 * a for a in add]}")
    return plan


# ---------------------------------------------------------------------------
# certify.py-compatible entry point
# ---------------------------------------------------------------------------
def run_experiment(spec, base=DEFAULT_BASE,
                   target_rounds=PAPER_TRANSPORT_ROUNDS, ms_rounds=14):
    return pad_plan(load_base_plan(base, spec=spec),
                    target_rounds=target_rounds, ms_rounds=ms_rounds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--rounds", type=int, default=PAPER_TRANSPORT_ROUNDS)
    ap.add_argument("--ms", type=int, default=14, choices=(14, 16))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--out", default=None, help="write the plan JSON here")
    args = ap.parse_args()

    base = load_base_plan(args.base, use_cache=not args.no_cache)
    cb = ev.compile_plan(base)
    print(f"base {args.base}: transport={cb['transport_rounds']} "
          f"ms={cb['merge_rounds']} exposure={cb['exposure']:.2f} "
          f"zones={cb['zones']} t_sec={cb['t_sec_poc']:.2f}")

    plan = pad_plan(base, target_rounds=args.rounds, ms_rounds=args.ms,
                    verbose=True)
    c = ev.compile_plan(plan)                      # raises PlanError if illegal
    ok, which = ev.noiseless_ok(c)
    print(f"paper-equivalent: transport={c['transport_rounds']} "
          f"(target {args.rounds}) ms={c['merge_rounds']} "
          f"prep={c['prep_phases']} measure={c['measure_phases']}")
    print(f"  exposure = {c['exposure']:.4f}   "
          f"(paper row in README: 536.06)")
    print(f"  t_sec_poc = {c['t_sec_poc']:.2f}   zones = {c['zones']} "
          f"(NOT the paper's ~288 -- see module docstring)")
    print(f"  LEGAL: yes (compile_plan accepted)   "
          f"noiseless-deterministic: {ok}"
          + ("" if ok else f" (failed on {which})"))
    print(f"  segment profile: "
          f"{[(s[0], s[1] if s[0] != 'prep' and s[0] != 'measure' else len(s[1])) for s in c['segments']]}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(plan, f)
        print(f"  wrote {args.out}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

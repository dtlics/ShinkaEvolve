"""EVOLVED seed, routed through the shared round packer (BEST SHIPPED PLAN).

419 transport rounds / 1.27x its own distance floor / score +2.2115. This is
the first plan in this task to beat IonQ's published hand design: 419 vs their
424 rounds as-published, and 352 vs 424 on a like-for-like basis (subtracting
the 67 wrap-back rounds our stricter cyclicity rule charges and theirs does
not). Footprint 301 rail sections vs their ~288.

Lineage: run q70ring_v2's best evolved program -> cell pitch repaired 3->4
(evolution had compressed it to win the then-broken footprint term, aliasing
the X/Z wrap lanes and forcing an X-then-Z sequential fallback) -> every
per-gap movement emission replaced by ONE routing.plan_moves call. The LAYOUT
and the per-gate ion->site assignment are untouched from the evolved original;
only the plumbing changed. 676 -> 566 -> 419 rounds.

WHERE THE REMAINING HEADROOM IS: this layout's floor is 329 rounds, so ~90
rounds of routing slack remain, and the floor ITSELF is a property of the
layout -- a CRT/sheared-torus embedding (l=7, m=5 coprime, so the ring torus is
Z_35 and every realignment becomes ONE 1-D rotation) has an analytic rotation
cost near 194 rounds. Changing the GEOMETRY is now the high-value move; the
router handles the rest.
"""

import os
import sys


def _load_routing():
    """Import the shared router regardless of how the candidate was loaded."""
    try:
        import routing as _r
        return _r
    except ImportError:
        pass
    import importlib.util
    cands = []
    for mod in [sys.modules.get("__main__")] + list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if f and os.path.basename(f) in ("evaluate.py", "routing.py"):
            cands.append(os.path.dirname(os.path.abspath(f)))
    try:
        cands.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    for d in cands:
        p = os.path.join(d, "routing.py")
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("q70_routing", p)
            m = importlib.util.module_from_spec(spec)
            sys.modules["q70_routing"] = m
            spec.loader.exec_module(m)
            return m
    raise ImportError("routing.py (shared parallel-transport router) not found")


rt = _load_routing()


# EVOLVE-BLOCK-START
def build_embedding_and_shuttle(spec):
    l_ring = spec["l"]
    m_ring = spec["m"]
    n_half = l_ring * m_ring
    exps = {"A": [tuple(e) for e in spec["A_exps"]],
            "B": [tuple(e) for e in spec["B_exps"]]}
    schedule = [tuple(t) for t in spec["schedule"]]
    n_rounds = len(schedule)
    qb = spec["qid_bases"]
    DATA0, XANC0, ZANC0 = qb["data"], qb["x_anc"], qb["z_anc"]
    BEAC0, RES0 = qb["beacon"], qb["reservoir"]
    N_SIM = 2 * (2 * n_half)

    CB = 0
    PITCH = 4
    ROWS = max(2 * l_ring + 1, 10)
    COLS = 4 * m_ring + 8
    assert ROWS <= spec["grid_max_rows"] and COLS <= spec["grid_max_cols"]

    def pos(i, j):
        return (i % l_ring) * m_ring + (j % m_ring)

    def cell(p):
        return divmod(p, m_ring)

    def anc_row(i):
        return 2 * (i % l_ring) + 1

    def side_col(j, side):
        base = CB + PITCH * (j % m_ring)
        return base + (1 if side == "L" else 2)

    def spare_col(j):
        return CB + PITCH * (j % m_ring)

    def right_beacon_col(j):
        return CB + PITCH * m_ring + (j % m_ring)

    def other_side(s):
        return "R" if s == "L" else "L"

    # ---- schedule-derived tables ---------------------------------------
    fam = [t[0] for t in schedule]
    ex = [exps[t[0]][t[1]] for t in schedule]
    ez = [exps[t[2]][t[3]] for t in schedule]

    def x_side_at(t):
        return "R" if fam[t] == "B" else "L"

    # ---- static layout (byte-identical to initial_evolved.py) ----------
    x_side, z_side = x_side_at(0), other_side(x_side_at(0))
    e0x, e0z = ex[0], ez[0]
    x_pos = [pos(g // m_ring + e0x[0], g % m_ring + e0x[1])
             for g in range(n_half)]
    z_pos = [pos(g // m_ring - e0z[0], g % m_ring - e0z[1])
             for g in range(n_half)]

    posn = {}
    for p in range(n_half):
        i, j = cell(p)
        posn[DATA0 + p] = ("S", 2 * i, side_col(j, "L"))
        posn[BEAC0 + p] = ("S", 2 * i, spare_col(j))
        posn[DATA0 + n_half + p] = ("S", 2 * i + 2, side_col(j, "R"))
        posn[BEAC0 + n_half + p] = ("S", 2 * i + 2, right_beacon_col(j))
    for g in range(n_half):
        i, j = cell(x_pos[g])
        posn[XANC0 + g] = ("S", anc_row(i), side_col(j, x_side))
        i, j = cell(z_pos[g])
        posn[ZANC0 + g] = ("S", anc_row(i), side_col(j, z_side))
    res_cols = list(range(4 * m_ring, 4 * m_ring + 4))
    res_sites = [("S", r, c) for r in range(1, ROWS, 2) for c in res_cols]
    for i in range(spec["n_reservoir"]):
        posn[RES0 + i] = res_sites[i]

    layout = {
        "data": [list(posn[DATA0 + i]) for i in range(2 * n_half)],
        "x_anc": [list(posn[XANC0 + g]) for g in range(n_half)],
        "z_anc": [list(posn[ZANC0 + g]) for g in range(n_half)],
        "beacon": [list(posn[BEAC0 + i]) for i in range(2 * n_half)],
        "reservoir": [list(posn[RES0 + i]) for i in range(spec["n_reservoir"])],
    }

    # ---- per-gate ion -> site tables (SAME assignment as the seed) ------
    def anc_sites(t):
        xs = x_side_at(t)
        zs = other_side(xs)
        out = {}
        for g in range(n_half):
            i, j = divmod(g, m_ring)
            i2, j2 = cell(pos(i + ex[t][0], j + ex[t][1]))
            out[XANC0 + g] = ("S", anc_row(i2), side_col(j2, xs))
            i2, j2 = cell(pos(i - ez[t][0], j - ez[t][1]))
            out[ZANC0 + g] = ("S", anc_row(i2), side_col(j2, zs))
        return out

    def required_pairs(t):
        f = fam[t]
        pairs = []
        for g in range(n_half):
            i, j = divmod(g, m_ring)
            if f == "A":
                xd = DATA0 + pos(i + ex[t][0], j + ex[t][1])
                zd = DATA0 + n_half + pos(i - ez[t][0], j - ez[t][1])
            else:
                xd = DATA0 + n_half + pos(i + ex[t][0], j + ex[t][1])
                zd = DATA0 + pos(i - ez[t][0], j - ez[t][1])
            pairs.append((xd, XANC0 + g))
            pairs.append((zd, ZANC0 + g))
        return pairs

    # ---- router state ---------------------------------------------------
    static = set(posn[q] for q in range(BEAC0, RES0 + spec["n_reservoir"]))
    live = {q: posn[q] for q in range(N_SIM)}
    home = dict(live)
    # data rest sites: SOFT obstacles for the ancilla routes.  With the
    # beacons they fill the even rows, so honouring them keeps every ancilla
    # route on the odd (ancilla) rails and the vertical ladders instead of
    # wandering into beacon-bounded pockets.
    parked = set(posn[DATA0 + i] for i in range(2 * n_half))
    # FOOTPRINT: every fresh S site an ion ever stands on is a new trap zone.
    # The ancilla rails (odd rows) are already in the footprint; the even
    # (data/beacon) rows are not, so charge a small toll for using them as a
    # horizontal short cut.
    ZONE_TOLL = 28.0        # unused S site on a data row
    RAIL_TOLL = 8.0         # unused S site on an ancilla row
    rest = set(posn.values())
    site_cost = dict((s, 8.0) for s in parked)
    for r in range(ROWS):
        for c in range(COLS):
            s0 = ("S", r, c)
            if s0 in rest:
                continue
            site_cost.setdefault(s0, ZONE_TOLL if r % 2 == 0 else RAIL_TOLL)
    cache = {}
    timeline = []

    dbg = False

    def transport(goals, paths=None, tag=""):
        rounds = rt.plan_moves(live, goals, static, rows=ROWS, cols=COLS,
                               paths=paths, site_cost=site_cost,
                               field_cache=cache)
        if dbg:
            fl = max([rt.site_dist(live[q], goals[q], ROWS, COLS)
                      for q in goals] or [0])
            print(f"  [{tag}] rounds={len(rounds)} longest-single-ion={fl}")
        rt.emit_moves(timeline, rounds)
        rt.apply_rounds(rounds, live)
        return len(rounds)

    all_anc = ([XANC0 + g for g in range(n_half)]
               + [ZANC0 + g for g in range(n_half)])
    timeline.append({"t": "prep", "ancillas": all_anc})

    for t in range(n_rounds):
        sites = anc_sites(t)
        pairs = required_pairs(t)
        dgoal = {}
        for dq, aq in pairs:
            _k, r, c = sites[aq]
            dgoal[dq] = ("J", r, c)
        assert len(set(sites.values()) | set(dgoal.values())) == 140

        if t == 0:
            # from the layout the ancillas already stand on their round-0
            # sites, so this gap is only the data ions' approach hop
            transport(dgoal, tag=f"gap{t}")
        else:
            # The 70 data ions sit on ancilla-row junctions and chop every
            # rail, so they must clear out for the ancilla journey.  Give them
            # an explicit route "home, idle, back" whose idle window is sized
            # by a dry run, so their 8 rounds hide inside the ancillas' journey
            # instead of costing a separate phase.  If that does not actually
            # come out shorter, fall back to the two-phase emission.
            outp, backp, n_out, n_back = {}, {}, 0, 0
            for dq, gsite in dgoal.items():
                outp[dq] = rt.shortest_path(live[dq], home[dq], ROWS, COLS,
                                            static)
                backp[dq] = rt.shortest_path(home[dq], gsite, ROWS, COLS,
                                             static)
                n_out = max(n_out, len(outp[dq]))
                n_back = max(n_back, len(backp[dq]))
            park = dict(sites)
            dry = rt.plan_moves(live, park, static, rows=ROWS, cols=COLS,
                                paths=outp, site_cost=site_cost,
                                field_cache=cache)
            merged = None
            wait = len(dry) - n_out - n_back
            if wait >= 0:
                mp = {dq: outp[dq] + [None] * (len(dry) - len(outp[dq])
                                               - len(backp[dq])) + backp[dq]
                      for dq in dgoal}
                try:
                    merged = rt.plan_moves(
                        live, park, static, rows=ROWS, cols=COLS, paths=mp,
                        site_cost=site_cost, field_cache=cache,
                        max_rounds=len(dry) + n_back - 1)
                except rt.RouteError:
                    merged = None
            if merged is not None and len(merged) < len(dry) + n_back:
                rt.emit_moves(timeline, merged)
                rt.apply_rounds(merged, live)
                if dbg:
                    print(f"  [gap{t}] rounds={len(merged)} "
                          f"(two-phase would be {len(dry) + n_back})")
            else:
                rt.emit_moves(timeline, dry)
                rt.apply_rounds(dry, live)
                transport(dgoal, tag=f"gap{t}b(fallback {len(dry)}+)")

        timeline.append({"t": "merge", "pairs": [[dq, aq] for dq, aq in pairs]})
        timeline.append({"t": "gate", "round": t})
        timeline.append({"t": "split",
                         "pairs": [[dq, ["J", sites[aq][1], sites[aq][2]]]
                                   for dq, aq in pairs]})
        # merge + split are a no-op on router state: every data ion leaves the
        # junction it merged from and returns to exactly that junction.
        if t == n_rounds - 1:
            timeline.append({"t": "measure", "ancillas": all_anc})

    transport(home, tag="wrap")   # cyclicity: everyone back on its layout site

    return {
        "grid": {"rows": ROWS, "cols": COLS},
        "layout": layout,
        "timeline": timeline,
    }
# EVOLVE-BLOCK-END


def run_experiment(spec, **kwargs):
    """Entrypoint called by evaluate.py; returns the plan dict."""
    return build_embedding_and_shuttle(spec)

"""EVOLVED seed plan generator for q70_ring_shuttle (third basin seed).

Provenance: this is run q70ring_v2's best evolved program
(`tail_coupled_folded_router`, 676 transport rounds, archive best) with ONE
constant repaired after the post-run analysis: the cell pitch, which evolution
had compressed from 4 to 3 columns to win the (then mis-defined) footprint
term. Pitch 3 aliased the X and Z vertical wrap lanes onto the same four
columns, forcing a counter-flow deadlock that the router escaped only by
routing all 35 X ancillas to completion and THEN all 35 Z ancillas — 348 of its
676 rounds ran at a mean of 19.8 of 140 ions moving. Restoring pitch 4
(disjoint lanes {0,4,8,12,16} vs {3,7,11,15,19}) with the identical router:
676 -> 566 transport rounds.

Strategy: folded 2D cells (data-L / ancillas / data-R per 3 rows), A/B family
change as a local side flip, per-column vertical conveyors with lane-column
wraps, Fig.-61 embedded shifts along the rows, farthest-first ordering in the
single-phase router.

KNOWN REMAINING SLACK (the point of the next run): still ~2x its own distance
floor. The router is barrier-architected — the i-axis pass runs to completion
before the j-axis pass, and the j-axis pass is itself 5 internal barriers
(hide / main-slide / main-hide / wrap-slide / emerge). Fusing a gap's
(delta_i, delta_j) into ONE stall-tolerant routing pass, and exploiting that a
closed-loop rotation may advance every ion in the same round, is where the
remaining ~300 rounds live.

INVARIANT — keep the X and Z wrap lanes column-disjoint (asserted below). Any
future compaction that violates it destroys vertical concurrency, and the
resulting loss is diffuse enough that it can look like a win locally.
"""


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

    def v_wrap_lane(c, side):
        return c - 1 if side == "L" else c + 1

    # INVARIANT: the two species' vertical wrap lanes must not share a column,
    # or their counter-flowing traffic deadlocks and the router degrades to
    # routing X to completion and then Z (measured: 676 rounds instead of 566).
    _lanes_L = {v_wrap_lane(side_col(j, "L"), "L") for j in range(m_ring)}
    _lanes_R = {v_wrap_lane(side_col(j, "R"), "R") for j in range(m_ring)}
    assert not (_lanes_L & _lanes_R), (
        f"wrap lanes collide: L={sorted(_lanes_L)} R={sorted(_lanes_R)} — "
        f"raise PITCH or re-place the lanes")

    def other_side(s):
        return "R" if s == "L" else "L"

    def legal_edge(a, b):
        ta, ra, ca = a
        tb, rb, cb = b
        if ta == "S" and tb == "J":
            return ra == rb and (cb == ca or cb == ca - 1)
        if ta == "J" and tb == "S":
            return ra == rb and (cb == ca or cb == ca + 1)
        if ta == "J" and tb in ("U", "D"):
            return ra == rb and ca == cb
        if ta in ("U", "D") and tb == "J":
            return ra == rb and ca == cb
        if ta == "D" and tb == "U":
            return rb == ra + 1 and ca == cb
        if ta == "U" and tb == "D":
            return rb == ra - 1 and ca == cb
        return False

    # ---- schedule-derived tables ---------------------------------------
    fam = [t[0] for t in schedule]
    ex = [exps[t[0]][t[1]] for t in schedule]
    ez = [exps[t[2]][t[3]] for t in schedule]

    def delta(e_new, e_old, sign=1):
        return (sign * (e_new[0] - e_old[0]) % l_ring,
                sign * (e_new[1] - e_old[1]) % m_ring)

    gaps = []
    for t in range(n_rounds):
        t2 = (t + 1) % n_rounds
        gaps.append({"flip": fam[t2] != fam[t],
                     "dX": delta(ex[t2], ex[t]),
                     "dZ": delta(ez[t2], ez[t], sign=-1)})

    # ---- initial state --------------------------------------------------
    x_side = "R" if fam[0] == "B" else "L"
    z_side = other_side(x_side)
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

    timeline = []
    occupied = {s: q for q, s in posn.items()}

    # ---- primitive checked emitter -------------------------------------
    def do_round(moves):
        if not moves:
            return

        tgt = {}
        edge_seen = set()
        for q, fr, to in moves:
            assert posn[q] == fr, (q, posn[q], fr)
            assert legal_edge(fr, to), ("illegal edge", q, fr, to)
            assert to not in tgt, ("double target", to)
            assert (to, fr) not in edge_seen, ("head-on", q, fr, to)
            edge_seen.add((fr, to))
            tgt[to] = q

        movers = {q for q, _, _ in moves}
        for to, q in tgt.items():
            holder = occupied.get(to)
            assert holder is None or holder in movers, ("collision", to, holder, q)

        for q, fr, _to in moves:
            del occupied[fr]
        for q, _fr, to in moves:
            assert to not in occupied, ("post-collision", to)
            occupied[to] = q
            posn[q] = to

        timeline.append({"t": "move",
                         "moves": [[q, list(f), list(t)] for q, f, t in moves]})

    def run_concurrent(tracks):
        tracks = [(q, list(p)) for q, p in tracks if p]
        maxlen = max((len(p) for _, p in tracks), default=0)
        for step in range(maxlen):
            moves = []
            for q, p in tracks:
                if step < len(p):
                    moves.append((q, posn[q], p[step]))
            do_round(moves)

    def run_dynamic_tracks(tracks, guard_extra=7000):
        """Greedy wait-inserting runner for fixed primitive tracks."""
        tracks = [(q, list(p)) for q, p in tracks if p]
        if not tracks:
            return

        idx = {q: 0 for q, _ in tracks}
        path = {q: p for q, p in tracks}
        order = [q for q, _ in tracks]
        total_steps = sum(len(p) for _, p in tracks)
        guard = 0

        while any(idx[q] < len(path[q]) for q in order):
            guard += 1
            if guard > total_steps + guard_extra:
                raise AssertionError(("dynamic deadlock", guard))

            cand = {q: path[q][idx[q]] for q in order if idx[q] < len(path[q])}
            selected = set(cand)

            changed = True
            while changed:
                changed = False

                by_t = {}
                for q in selected:
                    by_t.setdefault(cand[q], []).append(q)
                for _to, qs in by_t.items():
                    if len(qs) > 1:
                        qs.sort(key=lambda q: (len(path[q]) - idx[q], -q),
                                reverse=True)
                        for q in qs[1:]:
                            if q in selected:
                                selected.remove(q)
                                changed = True

                edge = {}
                for q in list(selected):
                    fr, to = posn[q], cand[q]
                    other = edge.get((to, fr))
                    if other is not None:
                        rem_q = len(path[q]) - idx[q]
                        rem_o = len(path[other]) - idx[other]
                        drop = q if rem_q < rem_o else other
                        if drop in selected:
                            selected.remove(drop)
                            changed = True
                    else:
                        edge[(fr, to)] = q

                for q in list(selected):
                    holder = occupied.get(cand[q])
                    if holder is not None and holder not in selected:
                        selected.remove(q)
                        changed = True

            if not selected:
                raise AssertionError(("dynamic no progress",
                                      [(q, posn[q], cand[q]) for q in cand]))

            batch = [(q, posn[q], cand[q]) for q in order if q in selected]
            do_round(batch)
            for q in selected:
                idx[q] += 1

    def snapshot():
        return (dict(posn), dict(occupied), len(timeline), x_side, z_side,
                list(x_pos), list(z_pos))

    def restore(snap):
        nonlocal x_side, z_side
        p, occ, tl, xs, zs, xp, zp = snap
        posn.clear()
        posn.update(p)
        occupied.clear()
        occupied.update(occ)
        del timeline[tl:]
        x_side = xs
        z_side = zs
        x_pos[:] = xp
        z_pos[:] = zp

    # ---- path helpers ---------------------------------------------------
    def vpath(c, r_from, r_to):
        path = [("J", r_from, c)]
        if r_to > r_from:
            for r in range(r_from, r_to):
                path += [("D", r, c), ("U", r + 1, c), ("J", r + 1, c)]
        else:
            for r in range(r_from, r_to, -1):
                path += [("U", r, c), ("D", r - 1, c), ("J", r - 1, c)]
        path.append(("S", r_to, c))
        return path

    def lane_steps(row, c_from, c_to):
        path = []
        c = c_from
        while c != c_to:
            if c_to > c:
                path += [("J", row, c), ("S", row, c + 1)]
                c += 1
            else:
                path += [("J", row, c - 1), ("S", row, c - 1)]
                c -= 1
        return path

    def hide_up_path(r, c):
        return [("J", r, c), ("U", r, c)]

    def emerge_up_path(r, c):
        return [("J", r, c), ("S", r, c)]

    def hide_down_path(r, c):
        return [("J", r, c), ("D", r, c)]

    def emerge_down_path(r, c):
        return [("J", r, c), ("S", r, c)]

    # ---- required pairs and data approach -------------------------------
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

    def approach_tracks_for_gate(t):
        tracks = []
        for dq, aq in required_pairs(t):
            _, dr, c = posn[dq]
            _, ar, _ = posn[aq]
            tracks.append((dq, vpath(c, dr, ar)[:-1]))
        return tracks

    # ---- local side flip fallback --------------------------------------
    def flip_sides():
        nonlocal x_side, z_side

        if x_side == "R":
            right_mover = XANC0
            left_mover = ZANC0
        else:
            right_mover = ZANC0
            left_mover = XANC0

        tracks = []
        for g in range(n_half):
            q = right_mover + g
            _, r, c = posn[q]
            tracks.append((q, [("J", r, c - 1), ("U", r, c - 1)]))
        run_concurrent(tracks)

        tracks = []
        for g in range(n_half):
            q = left_mover + g
            _, r, c = posn[q]
            tracks.append((q, lane_steps(r, c, c + 1)))
        run_concurrent(tracks)

        tracks = []
        for g in range(n_half):
            q = right_mover + g
            _, r, c = posn[q]
            tracks.append((q, [("J", r, c), ("S", r, c)]))
        run_concurrent(tracks)

        x_side, z_side = z_side, x_side

    # ---- vertical realignment ------------------------------------------
    def v_tracks_species(anc0, cur_pos, side, di):
        if di % l_ring == 0:
            return [], []
        dv = di % l_ring
        dvs = dv if dv <= l_ring - dv else dv - l_ring
        tracks = []
        moved = []

        for g in range(n_half):
            p = cur_pos[g]
            i, j = cell(p)
            i2 = (i + dv) % l_ring
            p2 = i2 * m_ring + j
            moved.append((g, p2))

            c = side_col(j, side)
            r1, r2 = anc_row(i), anc_row(i2)

            if (i + dvs) % l_ring == i2 and 0 <= i + dvs < l_ring:
                tracks.append((anc0 + g, vpath(c, r1, r2)))
            else:
                lane = v_wrap_lane(c, side)
                path = lane_steps(r1, c, lane)
                path += vpath(lane, r1, r2)
                path += lane_steps(r2, lane, c)
                tracks.append((anc0 + g, path))

        return tracks, moved

    def v_phase(dX_i, dZ_i):
        x_tracks, x_moved = v_tracks_species(XANC0, x_pos, x_side, dX_i)
        z_tracks, z_moved = v_tracks_species(ZANC0, z_pos, z_side, dZ_i)

        if x_tracks and z_tracks:
            snap = snapshot()
            try:
                run_dynamic_tracks(x_tracks + z_tracks)
            except AssertionError:
                restore(snap)
                run_concurrent(x_tracks)
                run_concurrent(z_tracks)
        else:
            run_concurrent(x_tracks + z_tracks)

        for g, p2 in x_moved:
            x_pos[g] = p2
        for g, p2 in z_moved:
            z_pos[g] = p2

    def v_phase_with_extra(dX_i, dZ_i, extra_tracks):
        """Overlap previous data return with vertical ancilla realignment."""
        extra_tracks = [(q, list(p)) for q, p in extra_tracks if p]
        x_tracks, x_moved = v_tracks_species(XANC0, x_pos, x_side, dX_i)
        z_tracks, z_moved = v_tracks_species(ZANC0, z_pos, z_side, dZ_i)
        v_tracks = x_tracks + z_tracks

        if extra_tracks and v_tracks:
            snap = snapshot()
            try:
                run_dynamic_tracks(extra_tracks + v_tracks)
            except AssertionError:
                restore(snap)
                run_concurrent(extra_tracks)
                v_phase(dX_i, dZ_i)
                return
        else:
            run_concurrent(extra_tracks + v_tracks)

        for g, p2 in x_moved:
            x_pos[g] = p2
        for g, p2 in z_moved:
            z_pos[g] = p2

    # ---- horizontal realignment ----------------------------------------
    def h_phase(anc0, cur_pos, side, other0, dj):
        if dj % m_ring == 0:
            return

        djp = dj % m_ring
        djs = djp if djp <= m_ring - djp else djp - m_ring

        wrap, main = [], []
        for g in range(n_half):
            p = cur_pos[g]
            i, j = cell(p)
            j2 = (j + djp) % m_ring
            if (j + djs) % m_ring == j2 and 0 <= j + djs < m_ring:
                main.append((g, i, j, j2))
            else:
                wrap.append((g, i, j, j2))

        initial_hide = []
        for g in range(n_half):
            q = other0 + g
            _, r, c = posn[q]
            initial_hide.append((q, hide_up_path(r, c)))
        for g, i, j, _ in wrap:
            c = side_col(j, side)
            initial_hide.append((anc0 + g, hide_up_path(anc_row(i), c)))
        run_concurrent(initial_hide)

        run_concurrent([(anc0 + g,
                         lane_steps(anc_row(i), side_col(j, side),
                                    side_col(j2, side)))
                        for g, i, j, j2 in main])

        run_concurrent([(anc0 + g,
                         hide_down_path(anc_row(i), side_col(j2, side)))
                        for g, i, _j, j2 in main])

        tracks = []
        for g, i, j, j2 in wrap:
            r = anc_row(i)
            c1, c2 = side_col(j, side), side_col(j2, side)
            tracks.append((anc0 + g, emerge_up_path(r, c1) + lane_steps(r, c1, c2)))
        run_concurrent(tracks)

        final_emerge = []
        for g, i, _j, j2 in main:
            c = side_col(j2, side)
            final_emerge.append((anc0 + g, emerge_down_path(anc_row(i), c)))
        for g in range(n_half):
            q = other0 + g
            _, r, c = posn[q]
            final_emerge.append((q, emerge_up_path(r, c)))
        run_concurrent(final_emerge)

        for g, i, _j, j2 in wrap + main:
            cur_pos[g] = i * m_ring + j2

    def split_approach_for_tail(approach_tracks):
        """Keep data one or two edges short of the merge junction until
        ancilla emergence is scheduled.  This avoids parking data on a junction
        that a hidden ancilla still needs to traverse.
        """
        pref = []
        suff = []
        for q, p in approach_tracks:
            p = list(p)
            cut = max(0, len(p) - 2)
            if cut:
                pref.append((q, p[:cut]))
            if cut < len(p):
                suff.append((q, p[cut:]))
        return pref, suff

    def fused_horizontal_or_fallback(dX_j, dZ_j, flip, approach_tracks=None):
        """Fuse side flip, both species' j-ring shifts, and next data approach.

        The next data approach is not allowed to finish before the ancilla
        emergence stage.  Its prefix is run while rails are otherwise occupied;
        its suffix is dynamically interleaved with final emergence, so an
        ancilla can vacate J exactly as the data ion enters J for the merge.
        """
        nonlocal x_side, z_side
        approach_tracks = [(q, list(p)) for q, p in (approach_tracks or []) if p]

        if (dX_j % m_ring == 0) and (dZ_j % m_ring == 0) and not flip:
            run_concurrent(approach_tracks)
            return

        snap = snapshot()
        old_x_side, old_z_side = x_side, z_side
        new_x_side = other_side(x_side) if flip else x_side
        new_z_side = other_side(z_side) if flip else z_side

        def build_group(anc0, cur_pos, old_side, new_side, dj):
            active = flip or (dj % m_ring != 0)
            if not active:
                return {"active": False, "main": [], "wrap": [], "updates": [],
                        "anc0": anc0, "old_side": old_side, "new_side": new_side}

            djp = dj % m_ring
            djs = djp if djp <= m_ring - djp else djp - m_ring
            main, wrap, updates = [], [], []

            for g in range(n_half):
                p = cur_pos[g]
                i, j = cell(p)
                j2 = (j + djp) % m_ring
                rec = (g, i, j, j2)
                updates.append((g, i * m_ring + j2))

                if djp == 0:
                    main.append(rec)
                elif (j + djs) % m_ring == j2 and 0 <= j + djs < m_ring:
                    main.append(rec)
                else:
                    wrap.append(rec)

            return {"active": True, "main": main, "wrap": wrap,
                    "updates": updates, "anc0": anc0,
                    "old_side": old_side, "new_side": new_side}

        gx = build_group(XANC0, x_pos, old_x_side, new_x_side, dX_j)
        gz = build_group(ZANC0, z_pos, old_z_side, new_z_side, dZ_j)
        groups = [gx, gz]

        try:
            active_count = sum(1 for gr in groups if gr["active"])

            initial_hide = []
            if active_count == 1:
                passive0 = ZANC0 if gx["active"] else XANC0
                for g in range(n_half):
                    q = passive0 + g
                    _, r, c = posn[q]
                    initial_hide.append((q, hide_up_path(r, c)))

            for gr in groups:
                if not gr["active"]:
                    continue
                for g, i, j, _j2 in gr["wrap"]:
                    c = side_col(j, gr["old_side"])
                    initial_hide.append((gr["anc0"] + g, hide_up_path(anc_row(i), c)))
            run_concurrent(initial_hide)

            main_tracks = []
            for gr in groups:
                if not gr["active"]:
                    continue
                for g, i, j, j2 in gr["main"]:
                    c1 = side_col(j, gr["old_side"])
                    c2 = side_col(j2, gr["new_side"])
                    main_tracks.append((gr["anc0"] + g,
                                        lane_steps(anc_row(i), c1, c2)))
            run_dynamic_tracks(main_tracks)

            main_hide = []
            for gr in groups:
                if not gr["active"]:
                    continue
                for g, i, _j, j2 in gr["main"]:
                    c2 = side_col(j2, gr["new_side"])
                    main_hide.append((gr["anc0"] + g,
                                      hide_down_path(anc_row(i), c2)))
            run_concurrent(main_hide)

            approach_prefix, approach_suffix = split_approach_for_tail(approach_tracks)

            wrap_tracks = []
            for gr in groups:
                if not gr["active"]:
                    continue
                for g, i, j, j2 in gr["wrap"]:
                    r = anc_row(i)
                    c1 = side_col(j, gr["old_side"])
                    c2 = side_col(j2, gr["new_side"])
                    wrap_tracks.append((gr["anc0"] + g,
                                        emerge_up_path(r, c1) + lane_steps(r, c1, c2)))

            # Run approach prefixes here.  They stop short of the ancilla-row
            # merge junction, so they cannot block final hidden-ion emergence.
            run_dynamic_tracks(wrap_tracks + approach_prefix)

            final_emerge = []
            for gr in groups:
                if not gr["active"]:
                    continue
                for g, i, _j, j2 in gr["main"]:
                    c2 = side_col(j2, gr["new_side"])
                    final_emerge.append((gr["anc0"] + g,
                                         emerge_down_path(anc_row(i), c2)))

            if active_count == 1:
                passive0 = ZANC0 if gx["active"] else XANC0
                for g in range(n_half):
                    q = passive0 + g
                    _, r, c = posn[q]
                    final_emerge.append((q, emerge_up_path(r, c)))

            # Tail-couple data approach suffixes with final ancilla emergence.
            run_dynamic_tracks(final_emerge + approach_suffix)

            for g, p2 in gx["updates"]:
                x_pos[g] = p2
            for g, p2 in gz["updates"]:
                z_pos[g] = p2
            x_side, z_side = new_x_side, new_z_side

        except AssertionError:
            restore(snap)

            if flip:
                flip_sides()
            h_phase(XANC0, x_pos, x_side, ZANC0, dX_j)
            h_phase(ZANC0, z_pos, z_side, XANC0, dZ_j)
            run_concurrent(approach_tracks)

    def realign(gp):
        v_phase(gp["dX"][0], gp["dZ"][0])
        fused_horizontal_or_fallback(gp["dX"][1], gp["dZ"][1], gp["flip"])

    def realign_with_extra(gp, return_tracks, next_gate=None):
        v_phase_with_extra(gp["dX"][0], gp["dZ"][0], return_tracks)
        approach = approach_tracks_for_gate(next_gate) if next_gate is not None else []
        fused_horizontal_or_fallback(gp["dX"][1], gp["dZ"][1], gp["flip"], approach)

    # ---- gate round -----------------------------------------------------
    def gate_round_split_only(t, prepositioned=False):
        pairs = required_pairs(t)

        if not prepositioned:
            run_concurrent(approach_tracks_for_gate(t))
        else:
            # Local sanity check: every mobile data ion should already be at
            # the junction adjacent to its ancilla host.
            for dq, aq in pairs:
                ds = posn[dq]
                asite = posn[aq]
                assert asite[0] == "S"
                assert legal_edge(ds, asite), ("bad preposition", t, dq, aq, ds, asite)

        timeline.append({"t": "merge", "pairs": [[dq, aq] for dq, aq in pairs]})
        for dq, aq in pairs:
            del occupied[posn[dq]]
            posn[dq] = posn[aq]

        timeline.append({"t": "gate", "round": t})

        split_pairs = [[dq, ["J", posn[aq][1], posn[aq][2]]] for dq, aq in pairs]
        timeline.append({"t": "split", "pairs": split_pairs})
        for (dq, _aq), sp in zip(pairs, split_pairs):
            posn[dq] = tuple(sp[1])
            occupied[posn[dq]] = dq

        return_tracks = []
        for dq, aq in pairs:
            home = layout["data"][dq - DATA0]
            _, dr, c = home
            return_tracks.append((dq, vpath(c, posn[aq][1], dr)[1:]))
        return return_tracks

    # ---- assemble SEC ---------------------------------------------------
    all_anc = [XANC0 + g for g in range(n_half)] + [ZANC0 + g for g in range(n_half)]
    timeline.append({"t": "prep", "ancillas": all_anc})

    prepositioned = [False] * n_rounds

    for t in range(n_rounds):
        return_tracks = gate_round_split_only(t, prepositioned=prepositioned[t])

        if t < n_rounds - 1:
            realign_with_extra(gaps[t], return_tracks, next_gate=t + 1)
            prepositioned[t + 1] = True
        else:
            # Measure immediately after the final split, while all ancillas are
            # on optical S sites.  The last data return is hidden in the cyclic
            # wrap-back; no next-gate prepositioning is allowed because the SEC
            # must finish exactly at the static layout.
            timeline.append({"t": "measure", "ancillas": all_anc})
            realign_with_extra(gaps[t], return_tracks, next_gate=None)

    return {
        "grid": {"rows": ROWS, "cols": COLS},
        "layout": layout,
        "timeline": timeline,
    }
# EVOLVE-BLOCK-END


def run_experiment(spec, **kwargs):
    """Entrypoint called by evaluate.py; returns the plan dict."""
    return build_embedding_and_shuttle(spec)
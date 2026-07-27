"""FOLDED seed plan generator for q70_ring_shuttle (second basin seed).

Same contract as initial.py, different strategy: a compact 2D folded embedding
(the layout family run q70ring_v1's discovery round found, here given the
parallel router that candidate lacked):

  - cell (i, j): ancillas on optical row 2i+1; left-block data (+beacon) on
    row 2i directly above, right-block data (+beacon) on row 2i+2 directly
    below; X ancillas on side columns 4j+CB, Z ancillas on 4j+CB+2;
  - A<->B family change = a local +-2-column SIDE FLIP (the species moving
    left hides in legs while the other slides through) — no block swaps;
  - medium ring (i, period l) = per-column vertical conveyors through the
    junction legs; wrapping ions detour concurrently through the free odd
    lane columns (X lanes ≡ CB+1, Z lanes ≡ CB+3 mod 4 — disjoint);
  - short ring (j, period m) = Fig.-61 embedded shift inside each ancilla
    row: wrap group hides UP, main group slides and hides DOWN, wrap group
    crosses the emptied rail, everyone re-emerges;
  - gating = data hop one row through the legs (4 moves), merge, gate,
    split, hop back; prep/measure in place on the optical rows.

All routines parametric in (l, m, schedule). Rows needed = 2l+1, cols =
4m + CB + margin (Q70: 15 x 28 — roughly one third of the unfolded seed's
footprint, and about two thirds of its transport rounds).
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

    CB = 4                       # first cell column
    ROWS = 2 * l_ring + 1        # rows 0..2l
    COLS = CB + 4 * m_ring + 4   # cell span CB..CB+4m-1, right margin for parking
    assert ROWS <= spec["grid_max_rows"] and COLS <= spec["grid_max_cols"]

    def pos(i, j):
        return (i % l_ring) * m_ring + (j % m_ring)

    def cell(p):
        return divmod(p, m_ring)

    def anc_row(i):
        return 2 * (i % l_ring) + 1          # odd -> optical

    def side_col(j, side):
        return CB + 4 * (j % m_ring) + (0 if side == "L" else 2)

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

    # ---- initial state -------------------------------------------------
    x_side = "R" if fam[0] == "B" else "L"   # family B: X gates the right block
    z_side = "L" if x_side == "R" else "R"
    e0x, e0z = ex[0], ez[0]
    x_pos = [pos(g // m_ring + e0x[0], g % m_ring + e0x[1]) for g in range(n_half)]
    z_pos = [pos(g // m_ring - e0z[0], g % m_ring - e0z[1]) for g in range(n_half)]

    posn = {}
    for p in range(n_half):
        i, j = cell(p)
        posn[DATA0 + p] = ("S", 2 * i, CB + 4 * j)               # left block
        posn[BEAC0 + p] = ("S", 2 * i, CB + 4 * j + 1)
        posn[DATA0 + n_half + p] = ("S", 2 * i + 2, CB + 4 * j + 2)  # right block
        posn[BEAC0 + n_half + p] = ("S", 2 * i + 2, CB + 4 * j + 3)
    for g in range(n_half):
        i, j = cell(x_pos[g])
        posn[XANC0 + g] = ("S", anc_row(i), side_col(j, x_side))
        i, j = cell(z_pos[g])
        posn[ZANC0 + g] = ("S", anc_row(i), side_col(j, z_side))
    res_sites = [("S", r, c) for r in (1, 3, 5) for c in range(COLS - 4, COLS)]
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

    def do_round(moves):
        if not moves:
            return
        tgt = {}
        for q, fr, to in moves:
            assert posn[q] == fr, (q, posn[q], fr)
            assert to not in tgt, ("double target", to)
            tgt[to] = q
        movers = {q for q, _, _ in moves}
        for to, q in tgt.items():
            holder = occupied.get(to)
            assert holder is None or holder in movers, ("collision", to, holder, q)
        for q, fr, to in moves:
            del occupied[fr]
        for q, fr, to in moves:
            assert to not in occupied, ("post-collision", to)
            occupied[to] = q
            posn[q] = to
        timeline.append({"t": "move",
                         "moves": [[q, list(f), list(t)] for q, f, t in moves]})

    def run_concurrent(tracks):
        maxlen = max((len(p) for _, p in tracks), default=0)
        for step in range(maxlen):
            moves = []
            for q, p in tracks:
                if step < len(p):
                    moves.append((q, posn[q], p[step]))
            do_round(moves)

    # ---- path helpers --------------------------------------------------
    def vpath(c, r_from, r_to):
        """S(r_from,c) -> S(r_to,c) through column c's legs (exclusive of start)."""
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

    # ---- side flip (family change): left-mover hides, right-mover slides
    def flip_sides():
        nonlocal x_side, z_side
        left_mover = XANC0 if x_side == "R" else ZANC0
        right_mover = ZANC0 if left_mover == XANC0 else XANC0
        # 1) left-movers hide in the U leg of the J to their LEFT
        tracks = []
        for g in range(n_half):
            q = left_mover + g
            _, r, c = posn[q]
            tracks.append((q, [("J", r, c - 1), ("U", r, c - 1)]))
        run_concurrent(tracks)
        # 2) right-movers slide +2 columns
        tracks = []
        for g in range(n_half):
            q = right_mover + g
            _, r, c = posn[q]
            tracks.append((q, lane_steps(r, c, c + 2)))
        run_concurrent(tracks)
        # 3) left-movers descend and settle 2 columns left of their old site
        tracks = []
        for g in range(n_half):
            q = left_mover + g
            _, r, c = posn[q]          # at U(r, c) with c = old_col - 1
            tracks.append((q, [("J", r, c), ("S", r, c),
                               ("J", r, c - 1), ("S", r, c - 1)]))
        run_concurrent(tracks)
        x_side, z_side = z_side, x_side

    # ---- vertical realign (medium ring), both species concurrent -------
    def v_phase(dX_i, dZ_i):
        tracks = []
        commits = []
        for anc0, cur_pos, side, di in ((XANC0, x_pos, lambda: x_side, dX_i),
                                        (ZANC0, z_pos, lambda: z_side, dZ_i)):
            if di % l_ring == 0:
                continue
            dv = di % l_ring
            dvs = dv if dv <= l_ring - dv else dv - l_ring
            moved = []
            for g in range(n_half):
                p = cur_pos[g]
                i, j = cell(p)
                i2 = (i + dv) % l_ring
                p2 = i2 * m_ring + j
                moved.append((g, p2))
                c = side_col(j, side())
                r1, r2 = anc_row(i), anc_row(i2)
                if (i + dvs) % l_ring == i2 and 0 <= i + dvs < l_ring:
                    tracks.append((anc0 + g, vpath(c, r1, r2)))     # main
                else:
                    lane = c + 1                                     # wrap
                    path = [("J", r1, c), ("S", r1, lane), ("J", r1, lane)]
                    if r2 > r1:
                        for r in range(r1, r2):
                            path += [("D", r, lane), ("U", r + 1, lane),
                                     ("J", r + 1, lane)]
                    else:
                        for r in range(r1, r2, -1):
                            path += [("U", r, lane), ("D", r - 1, lane),
                                     ("J", r - 1, lane)]
                    path += [("S", r2, lane), ("J", r2, c), ("S", r2, c)]
                    tracks.append((anc0 + g, path))
            commits.append((cur_pos, moved))
        run_concurrent(tracks)
        for cur_pos, moved in commits:
            for g, p2 in moved:
                cur_pos[g] = p2

    # ---- horizontal realign (short ring), Fig.-61 embedded shift -------
    def h_phase(anc0, cur_pos, side, other0, dj):
        if dj % m_ring == 0:
            return
        djp = dj % m_ring
        djs = djp if djp <= m_ring - djp else djp - m_ring
        # other species hides UP in its own legs for the duration
        run_concurrent([(other0 + g,
                         [("J", posn[other0 + g][1], posn[other0 + g][2]),
                          ("U", posn[other0 + g][1], posn[other0 + g][2])])
                        for g in range(n_half)])
        wrap, main = [], []
        for g in range(n_half):
            p = cur_pos[g]
            i, j = cell(p)
            j2 = (j + djp) % m_ring
            if (j + djs) % m_ring == j2 and 0 <= j + djs < m_ring:
                main.append((g, i, j, j2))
            else:
                wrap.append((g, i, j, j2))
        # 1) wrap group hides UP at its own J
        run_concurrent([(anc0 + g,
                         [("J", anc_row(i), side_col(j, side)),
                          ("U", anc_row(i), side_col(j, side))])
                        for g, i, j, _ in wrap])
        # 2) main group slides to its targets, then hides DOWN there
        run_concurrent([(anc0 + g,
                         lane_steps(anc_row(i), side_col(j, side),
                                    side_col(j2, side)))
                        for g, i, j, j2 in main])
        run_concurrent([(anc0 + g,
                         [("J", anc_row(i), side_col(j2, side)),
                          ("D", anc_row(i), side_col(j2, side))])
                        for g, i, j, j2 in main])
        # 3) wrap group crosses the emptied rail to its targets
        tracks = []
        for g, i, j, j2 in wrap:
            r = anc_row(i)
            c1, c2 = side_col(j, side), side_col(j2, side)
            tracks.append((anc0 + g, [("J", r, c1), ("S", r, c1)]
                          + lane_steps(r, c1, c2)))
        run_concurrent(tracks)
        # 4) main group re-emerges
        run_concurrent([(anc0 + g,
                         [("J", anc_row(i), side_col(j2, side)),
                          ("S", anc_row(i), side_col(j2, side))])
                        for g, i, j, j2 in main])
        # other species re-emerges
        run_concurrent([(other0 + g,
                         [("J", posn[other0 + g][1], posn[other0 + g][2]),
                          ("S", posn[other0 + g][1], posn[other0 + g][2])])
                        for g in range(n_half)])
        for g, i, j, j2 in wrap + main:
            cur_pos[g] = i * m_ring + j2

    def realign(gp):
        if gp["flip"]:
            flip_sides()
        v_phase(gp["dX"][0], gp["dZ"][0])
        h_phase(XANC0, x_pos, x_side, ZANC0, gp["dX"][1])
        h_phase(ZANC0, z_pos, z_side, XANC0, gp["dZ"][1])

    # ---- gating --------------------------------------------------------
    def gate_round(t):
        f = fam[t]
        pairs = []   # (data_qid, anc_qid, data_row, anc_row)
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
        # data hop one row to the ancilla row (4 moves), stop on the J
        tracks = []
        for dq, aq in pairs:
            _, dr, c = posn[dq]
            _, ar, _ = posn[aq]
            path = vpath(c, dr, ar)[:-1]
            tracks.append((dq, path))
        run_concurrent(tracks)
        timeline.append({"t": "merge", "pairs": [[dq, aq] for dq, aq in pairs]})
        for dq, aq in pairs:
            del occupied[posn[dq]]
            posn[dq] = posn[aq]
        timeline.append({"t": "gate", "round": t})
        split_pairs = [[dq, ["J", posn[aq][1], posn[aq][2]]] for dq, aq in pairs]
        timeline.append({"t": "split", "pairs": split_pairs})
        for (dq, aq), sp in zip(pairs, split_pairs):
            posn[dq] = tuple(sp[1])
            occupied[posn[dq]] = dq
        # return hop
        tracks = []
        for dq, aq in pairs:
            home = layout["data"][dq - DATA0]
            _, dr, c = home
            path = vpath(c, posn[aq][1], dr)[1:]
            tracks.append((dq, path))
        run_concurrent(tracks)

    # ---- assemble one SEC ----------------------------------------------
    timeline.append({"t": "prep",
                     "ancillas": [XANC0 + g for g in range(n_half)]
                     + [ZANC0 + g for g in range(n_half)]})
    for t in range(n_rounds):
        if t > 0:
            realign(gaps[t - 1])
        gate_round(t)
    timeline.append({"t": "measure",
                     "ancillas": [XANC0 + g for g in range(n_half)]
                     + [ZANC0 + g for g in range(n_half)]})
    realign(gaps[n_rounds - 1])   # wrap back to round-0 alignment

    return {
        "grid": {"rows": ROWS, "cols": COLS},
        "layout": layout,
        "timeline": timeline,
    }
# EVOLVE-BLOCK-END


def run_experiment(spec, **kwargs):
    """Entrypoint called by evaluate.py; returns the plan dict."""
    return build_embedding_and_shuttle(spec)

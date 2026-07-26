"""Seed plan generator for q70_ring_shuttle.

Produces the PLAN consumed by evaluate.py: a static layout on the junction grid
plus a timeline of primitive phases (parallel transport rounds, merge/split,
gate layers, prep/measure) realizing ONE syndrome extraction cycle (SEC) of the
Q70 = [[70,6,9]] three-ring memory block with the paper's frozen Table X
schedule (arXiv:2604.19481).

Seed strategy (paper-faithful, deliberately simple "unfolded" realization of
Fig. 60's abstract layout):
  - one row per register: X/Z-ancilla blocks on the two optical ancilla rows,
    left/right data (+ beacons) on two data rows, empty rail rows outside for
    cyclic-shift wrap traffic;
  - long ring (A<->B family change) = physical block swap of the two ancilla
    rows through vertical junction-leg corridors (odd/even column parity keeps
    the two blocks collision-free);
  - medium ring = conveyor shift of a whole ancilla row, with wrapping ions
    routed over the adjacent rail row;
  - short ring = Fig.-61-style embedded shift inside each m-position group,
    wrap ions hidden over the rail;
  - gating = data qubits hop one row through the junction legs, merge with
    their ancilla, gate, split, hop back;
  - prep/measure = in place on the optical ancilla rows, single batches.

The paper's folded Fig.-62 embedding achieves ~424 transport rounds/SEC; this
unfolded seed is intentionally simpler and slower — improving on it (e.g. by
folding, smarter wrap routing, overlapping gaps with gating) is the point of
the evolution. All routines are parametric in (l, m, schedule) so a plan
builder that works here can be re-run on other three-ring codes.
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

    # ---- geometry -------------------------------------------------------
    # rows: 0 rail | 1 ancilla (optical) | 2 data-L | 3 rail (optical, unused)
    #       4 data-R | 5 ancilla (optical) | 6 rail
    ROWS = 7
    COL0 = 2                          # first data column
    COLS = COL0 + 2 * n_half + 4      # data at even cols COL0..COL0+2n-2
    R_TOP_ANC, R_DATA_L, R_DATA_R, R_BOT_ANC = 1, 2, 4, 5
    RAIL = {R_TOP_ANC: 0, R_BOT_ANC: 6}

    def col_of(p):
        return COL0 + 2 * p

    def pos(i, j):
        return (i % l_ring) * m_ring + (j % m_ring)

    # ---- schedule-derived transport table -------------------------------
    fam = [t[0] for t in schedule]
    ex = [exps[t[0]][t[1]] for t in schedule]      # X-side term exponents
    ez = [exps[t[2]][t[3]] for t in schedule]      # Z-side term exponents

    def delta(e_new, e_old, sign=1):
        return (sign * (e_new[0] - e_old[0]) % l_ring,
                sign * (e_new[1] - e_old[1]) % m_ring)

    # gap g sits before gate round g+1 (mod n_rounds; last entry = wrap gap)
    gaps = []
    for t in range(n_rounds):
        t2 = (t + 1) % n_rounds
        gaps.append({
            "swap": fam[t2] != fam[t],
            "dX": delta(ex[t2], ex[t]),
            "dZ": delta(ez[t2], ez[t], sign=-1),
        })

    # ---- mutable machine state -----------------------------------------
    # X ancillas start facing the block their first-round family requires:
    # family "B" -> right data block -> bottom ancilla row.
    x_row = R_BOT_ANC if fam[0] == "B" else R_TOP_ANC
    z_row = R_TOP_ANC if x_row == R_BOT_ANC else R_BOT_ANC
    e0x, e0z = ex[0], ez[0]
    # x_pos[g] = ring position whose column X-ancilla g currently occupies
    x_pos = [pos(g // m_ring + e0x[0], g % m_ring + e0x[1]) for g in range(n_half)]
    z_pos = [pos(g // m_ring - e0z[0], g % m_ring - e0z[1]) for g in range(n_half)]

    posn = {}
    for p in range(n_half):
        posn[DATA0 + p] = ("S", R_DATA_L, col_of(p))
        posn[DATA0 + n_half + p] = ("S", R_DATA_R, col_of(p))
        posn[BEAC0 + p] = ("S", R_DATA_L, col_of(p) + 1)
        posn[BEAC0 + n_half + p] = ("S", R_DATA_R, col_of(p) + 1)
    for g in range(n_half):
        posn[XANC0 + g] = ("S", x_row, col_of(x_pos[g]))
        posn[ZANC0 + g] = ("S", z_row, col_of(z_pos[g]))
    # reservoir: park on free rail sites in the data rows (outside the data
    # + beacon span), derived from the spec's count rather than hardcoded
    data_cols = {col_of(p) for p in range(n_half)} | \
                {col_of(p) + 1 for p in range(n_half)}
    free_cols = [c for c in range(COLS) if c not in data_cols]
    res_sites = [("S", rr, cc) for cc in free_cols
                 for rr in (R_DATA_L, R_DATA_R)]
    assert len(res_sites) >= spec["n_reservoir"], "not enough reservoir sites"
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

    def _edge(a, b):
        """Mirror of the evaluator's one-primitive-step adjacency rule."""
        (ka, ra, ca), (kb, rb, cb) = a, b
        if {ka, kb} == {"S", "J"}:
            (rs, cs), (rj, cj) = ((ra, ca), (rb, cb)) if ka == "S" else ((rb, cb), (ra, ca))
            return rs == rj and cj in (cs, cs - 1)
        if ka == "J" and kb in ("U", "D") or kb == "J" and ka in ("U", "D"):
            return (ra, ca) == (rb, cb)
        if {ka, kb} == {"D", "U"}:
            (rd, cd), (ru, cu) = ((ra, ca), (rb, cb)) if ka == "D" else ((rb, cb), (ra, ca))
            return ru == rd + 1 and cu == cd
        return False

    def do_round(moves):
        """Emit one parallel transport round, verifying it locally with the
        same rules the evaluator applies (edges, collisions, head-on swaps)."""
        if not moves:
            return
        tgt = {}
        for q, fr, to in moves:
            assert posn[q] == fr, (q, posn[q], fr)
            assert _edge(fr, to), ("not one primitive step", q, fr, to)
            assert to not in tgt, ("double target", to)
            tgt[to] = q
        movers = {q for q, _, _ in moves}
        for to, q in tgt.items():
            holder = occupied.get(to)
            assert holder is None or holder in movers, ("collision", to, holder)
        for q, fr, to in moves:
            q2 = tgt.get(fr)
            assert q2 is None or posn[q2] != to, ("head-on swap", q, q2)
        for q, fr, to in moves:
            del occupied[fr]
        for q, fr, to in moves:
            assert to not in occupied, ("post-collision", to)
            occupied[to] = q
            posn[q] = to
        timeline.append({"t": "move",
                         "moves": [[q, list(f), list(t)] for q, f, t in moves]})

    # ---- movement primitives -------------------------------------------
    def hop_steps(row_a, row_b, c):
        """Site path (exclusive of start S) from S(row_a,c) to S(row_b,c)."""
        path = [("J", row_a, c)]
        if row_b > row_a:       # downward
            for r in range(row_a, row_b):
                path.append(("D", r, c))
                path.append(("U", r + 1, c))
                path.append(("J", r + 1, c))
        else:                   # upward
            for r in range(row_a, row_b, -1):
                path.append(("U", r, c))
                path.append(("D", r - 1, c))
                path.append(("J", r - 1, c))
        path.append(("S", row_b, c))
        return path

    def lane_steps(row, c_from, c_to):
        """Site path along one row from S(row,c_from) to S(row,c_to)."""
        path = []
        c = c_from
        while c != c_to:
            if c_to > c:
                path.append(("J", row, c))
                c += 1
            else:
                path.append(("J", row, c - 1))
                c -= 1
            path.append(("S", row, c))
        return path

    def run_concurrent(tracks):
        """tracks: list of (qid, [site path]). Advance all one step per round."""
        maxlen = max((len(p) for _, p in tracks), default=0)
        for step in range(maxlen):
            moves = []
            for q, p in tracks:
                if step < len(p):
                    moves.append((q, posn[q], p[step]))
            do_round(moves)

    def shift_tracks(anc0, cur_pos, row, dpos_map, signed_step):
        """Tracks realizing ion-at-position-p -> dpos_map[p] on one ancilla row.

        signed_step is the global rotation in ring positions (sign = direction).
        Ions whose signed displacement equals signed_step conveyor along the
        row in lockstep; the wrapping ions (displacement signed_step -/+ period)
        detour over the adjacent rail row. Returns (tracks, commit_fn).
        """
        rail = RAIL[row]
        moved = []
        tracks = []
        for g in range(n_half):
            p = cur_pos[g]
            p2 = dpos_map[p]
            if p2 == p:
                continue
            moved.append((g, p2))
            if p2 - p == signed_step:
                # main conveyor ion
                tracks.append((anc0 + g, lane_steps(row, col_of(p), col_of(p2))))
            else:
                # wrapping ion: over the rail row
                tracks.append((anc0 + g,
                               hop_steps(row, rail, col_of(p))
                               + lane_steps(rail, col_of(p), col_of(p2))
                               + hop_steps(rail, row, col_of(p2))))

        def commit():
            for g, p2 in moved:
                cur_pos[g] = p2
        return tracks, commit

    def _medium_map(di):
        n_pos = l_ring * m_ring
        dpos = (m_ring * di) % n_pos
        step = dpos if dpos <= n_pos - dpos else dpos - n_pos
        return {p: (p + dpos) % n_pos for p in range(n_half)}, step

    def _short_map(dj):
        djp = dj % m_ring
        step = djp if djp <= m_ring - djp else djp - m_ring
        return {p: (p // m_ring) * m_ring + (p % m_ring + djp) % m_ring
                for p in range(n_half)}, step

    def realign_both(dX, dZ):
        """Realign the X and Z ancilla rows concurrently (disjoint rows)."""
        for kind in ("medium", "short"):
            tracks, commits = [], []
            for anc0, cur_pos, row, d in ((XANC0, x_pos, x_row, dX),
                                          (ZANC0, z_pos, z_row, dZ)):
                comp = d[0] if kind == "medium" else d[1]
                if comp == 0:
                    continue
                dmap, step = (_medium_map(comp) if kind == "medium"
                              else _short_map(comp))
                t, c = shift_tracks(anc0, cur_pos, row, dmap, step)
                tracks += t
                commits.append(c)
            run_concurrent(tracks)
            for c in commits:
                c()

    def block_swap():
        nonlocal x_row, z_row
        # X ions sidestep to odd columns
        for _ in range(1):
            run_concurrent([(XANC0 + g,
                             [("J", x_row, col_of(x_pos[g])),
                              ("S", x_row, col_of(x_pos[g]) + 1)])
                            for g in range(n_half)])
        # both blocks transit vertically on disjoint column parities
        tracks = [(XANC0 + g, hop_steps(x_row, z_row, col_of(x_pos[g]) + 1))
                  for g in range(n_half)]
        tracks += [(ZANC0 + g, hop_steps(z_row, x_row, col_of(z_pos[g])))
                   for g in range(n_half)]
        run_concurrent(tracks)
        x_row, z_row = z_row, x_row
        # X ions sidestep back to even columns
        run_concurrent([(XANC0 + g,
                         [("J", x_row, col_of(x_pos[g])),
                          ("S", x_row, col_of(x_pos[g]))])
                        for g in range(n_half)])

    def gate_round(t):
        """Data hop to their ancilla row, merge, gate, split, hop back."""
        f = fam[t]
        # which data row faces which ancilla row this round
        x_data_row = R_DATA_L if f == "A" else R_DATA_R
        z_data_row = R_DATA_R if f == "A" else R_DATA_L
        pairs = []   # (data_qid, anc_qid, data_row, anc_row)
        for g in range(n_half):
            i, j = divmod(g, m_ring)
            if f == "A":
                xd = DATA0 + pos(i + ex[t][0], j + ex[t][1])
                zd = DATA0 + n_half + pos(i - ez[t][0], j - ez[t][1])
            else:
                xd = DATA0 + n_half + pos(i + ex[t][0], j + ex[t][1])
                zd = DATA0 + pos(i - ez[t][0], j - ez[t][1])
            pairs.append((xd, XANC0 + g, x_data_row, x_row))
            pairs.append((zd, ZANC0 + g, z_data_row, z_row))
        # approach: 4 transport rounds (S -> J -> U/D -> D/U -> J(anc row)),
        # then the merge move takes the data into the ancilla's S site
        tracks = []
        for dq, aq, drow, arow in pairs:
            c = posn[dq][2]
            assert posn[aq][2] == c, ("gating misalignment", dq, aq)
            path = hop_steps(drow, arow, c)[:-1]   # stop on J(arow,c)
            tracks.append((dq, path))
        run_concurrent(tracks)
        timeline.append({"t": "merge",
                         "pairs": [[dq, aq] for dq, aq, _, _ in pairs]})
        for dq, aq, _, _ in pairs:
            del occupied[posn[dq]]
            posn[dq] = posn[aq]
        timeline.append({"t": "gate", "round": t})
        split_pairs = []
        for dq, aq, drow, arow in pairs:
            back = ("J", arow, posn[aq][2])
            split_pairs.append([dq, list(back)])
        timeline.append({"t": "split", "pairs": split_pairs})
        for (dq, aq, drow, arow), sp in zip(pairs, split_pairs):
            posn[dq] = tuple(sp[1])
            occupied[posn[dq]] = dq
        # return: reverse of approach
        tracks = []
        for dq, aq, drow, arow in pairs:
            c = posn[dq][2]
            path = hop_steps(arow, drow, c)
            path = path[1:]  # currently ON J(arow,c), skip it
            tracks.append((dq, path))
        run_concurrent(tracks)

    # ---- assemble one SEC ----------------------------------------------
    timeline.append({"t": "prep",
                     "ancillas": [XANC0 + g for g in range(n_half)]
                     + [ZANC0 + g for g in range(n_half)]})
    for t in range(n_rounds):
        if t > 0:
            gp = gaps[t - 1]
            if gp["swap"]:
                block_swap()
            realign_both(gp["dX"], gp["dZ"])
        gate_round(t)
    timeline.append({"t": "measure",
                     "ancillas": [XANC0 + g for g in range(n_half)]
                     + [ZANC0 + g for g in range(n_half)]})
    # wrap gap: restore round-0 alignment so the SEC tiles
    gp = gaps[n_rounds - 1]
    if gp["swap"]:
        block_swap()
    realign_both(gp["dX"], gp["dZ"])

    return {
        "grid": {"rows": ROWS, "cols": COLS},
        "layout": layout,
        "timeline": timeline,
    }
# EVOLVE-BLOCK-END


def run_experiment(spec, **kwargs):
    """Entrypoint called by evaluate.py; returns the plan dict."""
    return build_embedding_and_shuttle(spec)

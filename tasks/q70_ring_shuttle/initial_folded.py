"""FOLDED seed -- folded 2D embedding + inlined parallel round packer.

300 transport rounds / 1.30x its own distance floor / score +2.6565, at 283
rail sections -- BEST OF THE THREE SEEDS since the 2b router repair, and below
the paper's own ~288 sections. Hand-written folded embedding: cells of three
rows (data-L / ancillas / data-R), an A/B family change as a local +-2-column
side flip rather than a block swap, per-column vertical conveyors, embedded
shifts along the rows. Kept as a structurally DISTINCT basin from the pitch-4
cell seed -- different cell geometry and flip convention -- so the two islands
explore different layout families.
700 -> 455 -> 446 -> 375 -> 300 rounds (the last step is the 2b repair: the
router used to walk a five-family subgraph and pay 5 steps for a one-row hop
the chip charges 3 for).

evaluate.py's SHIPPED_SEED_SCORE still tracks initial_evolved.py (+2.6300), so
`gain_over_seed` reads -0.0265 for this file even though it is the stronger
plan. That is bookkeeping, not a regression.

THE FILE IS THREE INDEPENDENT SECTIONS, each with its own banner listing what
it owns and what a mutation could try. You can rewrite ONE of them without
reading the other two:

  SECTION 1  GEOMETRY  grid size, where every ion rests, the per-gate-round
                       ion -> site tables.  The layout's distance FLOOR (230
                       rounds here) is decided entirely here.
  SECTION 2  ROUTER    shortest paths, congestion pricing, and the round
                       packer (stalls, cycle rotation, shove-aside,
                       replanning) that turns routes into parallel rounds.
                       All its knobs are in the ROUTER_POLICY block at 2a.
  SECTION 3  ASSEMBLY  prep, the 7 merge/gate/split rounds, measure, the
                       cyclicity wrap-back, and the phase emission.

WHERE THE REMAINING HEADROOM IS: two halves, both in this file.
  * 70 rounds of PACKING slack over this layout's floor -- LESS slack than the
    pitch-4 cell seed now carries (84). SECTION 2 walks exactly the
    eight-family graph the evaluator prices, so what is left is stalls, shoves,
    replans and SECTION 3's phase boundaries, not a wrong chip map.
  * the floor ITSELF (230), a property of the layout alone, and 4 rounds worse
    than the pitch-4 seed's 226. A CRT/sheared-torus embedding (l=7, m=5
    coprime, so the ring torus is Z_35 and every realignment becomes ONE 1-D
    rotation) collapses each realignment to a single pass.
"""

import heapq
from collections import deque

# Everything below the EVOLVE-BLOCK marker is fair game: the geometry, the
# router and the SEC assembly all live inside it. `heapq` and `deque` are kept
# out here so a rewrite of the block cannot lose them; the stdlib is the only
# dependency.


# EVOLVE-BLOCK-START
# ===========================================================================
# SECTION 1 - GEOMETRY
# ---------------------------------------------------------------------------
# OWNS: the grid size, WHERE EVERY ION RESTS (the static layout), and the
# per-gate-round ion -> site assignment tables.  Nothing here knows how an ion
# travels -- that is SECTION 2 -- so this section can be replaced wholesale.
#
# THIS LAYOUT (the "folded" 2D embedding): the torus is folded into cells of
# three rows -- data-L / ancillas / data-R -- one cell per ring row i.  Column
# band j occupies CELL_PITCH = 4 consecutive columns --
#   col 4j+0 left data | 4j+1 left beacon | 4j+2 right data | 4j+3 right beacon
# -- so every data ion sits next to its beacon and the two blocks are two
# columns apart.  Ancillas live on the OPTICAL row 2i+1 between the two data
# rows (odd rows are the only place prep/measure is allowed), on the left-block
# column when their schedule family is A and the right-block column when it is
# B.  A FAMILY CHANGE IS THEREFORE A +-2-COLUMN SIDE FLIP -- no block swap, no
# long-ring shuffle, which is what makes this a different basin from the
# pitch-4 cell layout.  The whole array is offset CELL_BASE = 4 columns from
# the left edge, leaving a free margin the router uses as a wrap lane.
#
# WHAT THIS COSTS: this layout's per-gap distance floor totals 230 rounds and
# the router realises 300 of them, over 283 rail sections (below the paper's
# own ~288).  Against the pitch-4 cell layout (226 floor / 310 realised / 277
# sections) this one has the WORSE floor and the BIGGER footprint but packs
# noticeably tighter -- 1.30x vs 1.37x -- which is why it now scores higher.
# The floor is a property of the GEOMETRY ALONE, so it is the thing to attack
# HERE; the 70 rounds of slack above it belong to SECTION 2 and SECTION 3.
#
# ONE CONSTANT WORTH RE-EXAMINING (deliberately NOT changed): CELL_BASE = 4
# leaves columns 0-3 free as a wrap lane, but COLUMN 0 IS A DEAD END on this
# chip -- S(r,0)'s only neighbour is J(r,0) -- so only three of those four
# columns are genuinely useful lane.  CELL_BASE = 3 would keep the whole usable
# lane and buy back a column of width; nobody has measured that trade.
#
# WHAT A MUTATION COULD TRY HERE:
#   * a CRT / sheared-torus embedding: l = 7 and m = 5 are coprime, so the ring
#     torus is Z_35 and every realignment collapses to ONE 1-D rotation instead
#     of two per-axis passes -- analytically ~194 rounds of rotation;
#   * drop the beacon column by pairing each data ion with a beacon ACROSS the
#     row (they only have to share a row), buying back a quarter of the width;
#   * a different cell pitch, or a pitch that varies with j;
#   * fold on the OTHER axis (cells of three columns instead of three rows);
#   * pick the ancilla -> site assignment per round by min-cost matching
#     against the previous round's sites instead of by formula;
#   * shear the row index with j so a column shift also advances the row.
# ===========================================================================

CELL_BASE = 4        # first column of ring column j = 0 (free left margin)
CELL_PITCH = 4       # columns consumed by one ring column j


def build_geometry(spec):
    """Static layout + the frozen per-gate-round ion -> site tables.

    Returns a dict consumed by SECTION 3; every entry is plain data or a pure
    function of the round index t.
    """
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
    n_sim = 2 * (2 * n_half)                       # data + ancillas = 140

    CB = CELL_BASE
    PITCH = CELL_PITCH
    ROWS = 2 * l_ring + 1
    COLS = CB + PITCH * m_ring + 4
    assert ROWS <= spec["grid_max_rows"] and COLS <= spec["grid_max_cols"]

    def pos(i, j):
        return (i % l_ring) * m_ring + (j % m_ring)

    def cell(p):
        return divmod(p, m_ring)

    def anc_row(i):
        return 2 * (i % l_ring) + 1                # odd rows are optical

    def side_col(j, side):
        return CB + PITCH * (j % m_ring) + (0 if side == "L" else 2)

    def other_side(s):
        return "R" if s == "L" else "L"

    # ---- schedule-derived tables (FROZEN: the code's published Table X) ----
    fam = [t[0] for t in schedule]
    ex = [exps[t[0]][t[1]] for t in schedule]
    ez = [exps[t[2]][t[3]] for t in schedule]

    def x_side_at(t):
        return "R" if fam[t] == "B" else "L"

    # ---- static layout ----------------------------------------------------
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
        posn[BEAC0 + p] = ("S", 2 * i, side_col(j, "L") + 1)
        posn[DATA0 + n_half + p] = ("S", 2 * i + 2, side_col(j, "R"))
        posn[BEAC0 + n_half + p] = ("S", 2 * i + 2, side_col(j, "R") + 1)
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

    # ---- per-gate-round ion -> site tables --------------------------------
    def anc_sites(t):
        """{ancilla qid: rail section it must occupy for gate round t}."""
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
        """[(data qid, ancilla qid)] the evaluator REQUIRES merged at round t."""
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

    data_ids = list(range(DATA0, DATA0 + 2 * n_half))
    anc_ids = ([XANC0 + g for g in range(n_half)]
               + [ZANC0 + g for g in range(n_half)])
    return {
        "rows": ROWS, "cols": COLS,
        "n_sim": n_sim, "n_rounds": n_rounds,
        "posn": posn, "layout": layout,
        "data_ids": data_ids, "anc_ids": anc_ids,
        "static_ids": list(range(BEAC0, RES0 + spec["n_reservoir"])),
        # rows that hold data/beacon ions -- the router charges extra to use a
        # fresh rail section on one of them (see FRESH_DATA_ROW_TOLL)
        "data_rows": sorted(set(posn[q][1] for q in data_ids)),
        "anc_sites": anc_sites,
        "required_pairs": required_pairs,
    }


# ===========================================================================
# SECTION 2 - ROUTER
# ---------------------------------------------------------------------------
# OWNS: turning "these ions must reach these sites" into a near-minimal number
# of LEGAL parallel transport rounds.  Knows nothing about the code, the
# schedule or the layout -- pure grid logistics, replaceable on its own.
#
# A round costs the same whether 1 ion moves or 140, so the only quantity that
# counts is the ROUND COUNT, never the ion-steps.  Two evaluator rules are
# exploited hard: a target VACATED by an ion moving in the SAME round is legal
# (so dense trains pipeline and a closed loop of >=3 ions rotates in ONE
# round), and only head-on swaps through a single edge are forbidden.  Emission
# styles that route groups SEQUENTIALLY (per-axis passes, X-then-Z,
# hide/slide/emerge) land at 2.1-3.4x the layout's distance floor; this lands
# at ~1.3x.  The router walks the chip's FULL eight-family graph (2b), the same
# one the evaluator prices the floor on, so what is left above the floor is
# PACKING slack -- see the 2b banner for where.
#
# Structure: 2a policy knobs | 2b grid primitives | 2c path finding |
#            2d round packer | 2e entry points.
#
# WHAT A MUTATION COULD TRY HERE:
#   * retune ROUTER_POLICY / ROUTER_ATTEMPTS -- the cheapest experiment here;
#   * a different priority rule than farthest-first (most-constrained-first,
#     or re-ranking every round instead of once per call);
#   * rolling-horizon replanning instead of plan-once-then-pack;
#   * a conflict-based-search (CBS) or push-and-swap layer above the packer;
#   * let a stalled ion SWAP goals with its blocker when the two are
#     interchangeable, instead of shoving it aside;
#   * charge the FIRST round of each journey so groups launch in lockstep and
#     none finishes early into rounds another group still needs.
# ===========================================================================

# --- 2a. TUNABLE ROUTER POLICY ---------------------------------------------
# Every knob the packer reads lives in this one block.  Changing a number here
# changes the round count without touching a line of logic.

ROUTER_POLICY = {
    # -- route search ------------------------------------------------------
    "opposed_cost": 3.0,   # extra A* cost to traverse a directed edge AGAINST
                           # an already-planned ion (same-way sharing is free)
    # -- round packer ------------------------------------------------------
    "push_after": 1,       # stalled rounds before an ion shoves its blocker
    "aside_radius": 2,     # how far a shoved ion may be sent.  SMALL IS BETTER:
                           # long evacuations disturb more traffic than they free
    "aside_preview": 4,    # sites ahead of the stalled ion a shove must not use
    "aside_depth": 3,      # recursion depth of the cascading shove
    "max_aside": 6,        # times one ion may be shoved during a single call
    "max_pushes": 64,      # shoves attempted per round
    "replan_after": 6,     # stalled rounds before an ion re-routes around its
                           # blocker instead of pushing it
    "replan_slack": 10,    # extra steps a reroute may cost before it is refused
}

# Attempt portfolio: each entry overrides ROUTER_POLICY for one try and the
# SHORTEST legal schedule wins.  These two span the aside_radius axis, which is
# the one that matters; four further settings were measured (opposed_cost 0 and
# 12 at both radii) and NONE of them ever wins on either shipped layout, while
# they triple the build time -- opposed_cost 0 in particular deadlocks outright.
# Measured end-to-end here (v6.1, on the repaired eight-family graph of 2b):
# these two give 310 rounds / 277 rail sections on the pitch-4 cell layout and
# 300 / 283 on the folded one, in ~3.7 s and ~4.0 s of build time.  Before the
# 2b repair the SAME two entries gave 358 / 301 and 375 / 287.
# Adding entries trades build time for rounds; a single entry is legitimate.
# Dropping to aside_radius 2 alone costs 5 rounds (evolved) / 1 round (folded)
# and NO extra rail sections, at ~60% of the build time; aside_radius 3 alone
# costs 2 / 10 rounds and +34 rail sections on the pitch-4 layout, so radius 2
# is the one to keep if the portfolio is ever cut to one.
ROUTER_ATTEMPTS = (
    {"aside_radius": 2, "opposed_cost": 3.0},
    {"aside_radius": 3, "opposed_cost": 3.0},
)

# Soft tolls for standing on a rail section the layout does not already occupy:
# every FRESH S site an ion touches is a new trap zone in the score's footprint
# term, so a detour across an unused row can cost more score than the rounds it
# saves.  These three measured best here.
FRESH_DATA_ROW_TOLL = 28.0   # unused S site on a data/beacon row
FRESH_RAIL_ROW_TOLL = 8.0    # unused S site on an ancilla row
PARKED_SITE_TOLL = 8.0       # another ion's resting site


class RouteError(Exception):
    """Raised when a routing request cannot be satisfied legally."""


_KIND_ORDER = {"S": 0, "J": 1, "U": 2, "D": 3}
_INF = float("inf")
_WAIT = "__wait__"


# --- 2b. GRID PRIMITIVES ---------------------------------------------------
# The chip's FULL edge set -- all EIGHT families, exactly what the evaluator
# accepts as one primitive transport step:
#   inside a horizontal section : S(r,c)-J(r,c)
#   inside a vertical section   : D(r,c)-U(r+1,c)
#   the junction-(r,c) CLIQUE   : J(r,c)-S(r,c+1) | J(r,c)-U(r,c) |
#                                 J(r,c)-D(r,c)   | S(r,c+1)-U(r,c) |
#                                 S(r,c+1)-D(r,c) | U(r,c)-D(r,c)
# A junction is a ZERO-LENGTH CROSSING, so the four wells around it are
# MUTUALLY one step apart.  That clique is what makes one COLUMN cost 2 steps
# (S(r,c)-J(r,c)-S(r,c+1)) and one ROW cost 3 (S(r,c)-D(r,c-1)-U(r+1,c-1)-
# S(r+1,c)); the rest-site metric is 2*|dr| + max(2*|dc|, 1).
#
# WHAT IS LEFT NOW THAT THE MAP IS RIGHT:
#   * this router used to walk a FIVE-family subgraph and pay 5 steps for a
#     one-row hop the chip charges 3 for.  It no longer does, so every round
#     above `floor_total` is PACKING slack -- stalls, shoves, replans and the
#     phase boundaries SECTION 3 imposes -- not a wrong chip map.  That makes
#     the packer and SECTION 3's overlap the places left to attack;
#   * COLUMN 0 IS A DEAD END.  S(r,0)'s only neighbour is J(r,0) (there is no
#     junction to its left), so a one-row hop starting or ending there costs 5
#     steps while the evaluator's floor still charges 3.  Neither shipped
#     layout puts MOVING ions on column 0 -- the folded seed keeps CELL_BASE=4
#     columns of free margin, the pitch-4 seed puts only STATIC beacons there
#     -- but a SECTION 1 mutation that parks live ions on column 0 would pay
#     for it invisibly.  Shifting the pitch-4 layout one column right
#     (CELL_BASE = 1) would also free column 0 as a wrap lane; it is NOT done
#     here because that is a layout change, not a router repair;
#   * `site_dist` is EXACT on the obstacle-free graph.  Any rewrite of it must
#     stay a LOWER bound on the true distance or A* stops returning shortest
#     paths -- brute-force it against `neighbors` before shipping.

def as_site(x):
    """Normalise ["S", r, c] / ("S", r, c) to a hashable tuple."""
    return (x[0], int(x[1]), int(x[2]))


def is_edge(a, b):
    """True iff a -> b is exactly one primitive transport step."""
    ka, ra, ca = a
    kb, rb, cb = b
    if ka == "S" and kb == "J":
        return ra == rb and (cb == ca or cb == ca - 1)
    if ka == "J" and kb == "S":
        return ra == rb and (ca == cb or ca == cb - 1)
    if ka == "J" and kb in ("U", "D"):
        return ra == rb and ca == cb
    if ka in ("U", "D") and kb == "J":
        return ra == rb and ca == cb
    # zero-length junction crossing: its two leg wells touch the NEXT
    # section's left well, and each other, directly
    if ka == "S" and kb in ("U", "D"):
        return ra == rb and cb == ca - 1
    if ka in ("U", "D") and kb == "S":
        return ra == rb and ca == cb - 1
    if ka == "D" and kb == "U":
        # rb == ra: across junction (r,c);  rb == ra+1: the vertical section
        return ca == cb and (rb == ra or rb == ra + 1)
    if ka == "U" and kb == "D":
        return ca == cb and (rb == ra or rb == ra - 1)
    return False


def neighbors(site, rows, cols):
    """All sites one primitive edge away from ``site`` inside the grid."""
    k, r, c = site
    if k == "S":
        out = [("J", r, c)]
        if c >= 1:                       # the junction (r, c-1) clique
            out.append(("J", r, c - 1))
            out.append(("U", r, c - 1))
            out.append(("D", r, c - 1))
        return out
    if k == "J":
        out = [("S", r, c), ("U", r, c), ("D", r, c)]
        if c + 1 < cols:
            out.append(("S", r, c + 1))
        return out
    if k == "U":
        out = [("J", r, c), ("D", r, c)]
        if c + 1 < cols:
            out.append(("S", r, c + 1))
        if r >= 1:
            out.append(("D", r - 1, c))
        return out
    out = [("J", r, c), ("U", r, c)]
    if c + 1 < cols:
        out.append(("S", r, c + 1))
    if r + 1 < rows:
        out.append(("U", r + 1, c))
    return out


def _cell(site):
    """(junction row, junction col, clique-member tag, pendant steps).

    Every well belongs to exactly ONE junction clique -- J(r,c), U(r,c) and
    D(r,c) to junction (r,c), S(r,c) to junction (r,c-1) -- and the tag says
    WHICH of the four members it is.  S(r,0) is the sole exception: column 0
    has no junction to its left, so it hangs off J(r,0) as a PENDANT, one
    extra step away from everything.
    """
    k, r, c = site
    if k == "S":
        if c == 0:
            return r, 0, "J", 1
        return r, c - 1, "S", 0
    if k == "J":
        return r, c, "J", 0
    return r, c, k, 0


def site_dist(a, b, rows=10 ** 9, cols=10 ** 9):
    """EXACT obstacle-free graph distance between any two sites.

    Both the A* heuristic AND the router's per-ion journey bound, computed on
    the chip's full eight-family graph (2b banner), so it agrees with the
    metric the evaluator prices the distance floor with.

    Derivation: the chip is a rectangular grid of junction CLIQUES joined by
    ONE edge each -- clique (r,c) reaches (r,c+1) through S(r,c+1)-J(r,c+1)
    and (r+1,c) through D(r,c)-U(r+1,c).  A route therefore crosses
    k = |dr| + |dc| portal edges and pays one step inside each of the k-1
    cliques it merely transits (a transit always enters and leaves by
    DIFFERENT members), plus one step at each END unless that endpoint already
    IS the member the route departs from / arrives at.  Which end members are
    available depends on the first and last direction of travel, and the two
    are NOT independent when the route can turn only once -- hence ``combos``.
    A longer-than-Manhattan clique route costs >= 2 more, so Manhattan wins.

    ``rows``/``cols`` are accepted for call compatibility only: the clique grid
    is rectangular, so a shortest route never leaves its endpoints' bounding
    box and the bounds cannot change the answer.

    Exactness is not cosmetic -- A* needs a lower bound, and a TIGHT one is
    what keeps expansions in the hundreds.  Verified against brute-force BFS
    over `neighbors` for EVERY ordered pair of sites (all four kinds) on grids
    from 1x4 to 8x3: exact on all of them, boundary columns included.
    """
    a, b = as_site(a), as_site(b)
    if a == b:
        return 0
    ra, ca, ta, pa = _cell(a)
    rb, cb, tb, pb = _cell(b)
    dr, dc = rb - ra, cb - ca
    if dr == 0 and dc == 0:                        # same clique
        return pa + pb + (0 if ta == tb else 1)
    # the member a step in a given direction leaves from is the same one a
    # step in that direction arrives at
    h_out, h_in = ("S", "J") if dc > 0 else ("J", "S")
    v_out, v_in = ("D", "U") if dr > 0 else ("U", "D")
    if dr == 0:
        combos = ((h_out, h_in),)
    elif dc == 0:
        combos = ((v_out, v_in),)
    else:
        combos = [(h_out, v_in), (v_out, h_in)]    # one turn, either order
        if abs(dc) >= 2:
            combos.append((h_out, h_in))           # room to start AND end flat
        if abs(dr) >= 2:
            combos.append((v_out, v_in))
    end = min((0 if ta == f else 1) + (0 if tb == l else 1) for f, l in combos)
    return pa + pb + 2 * (abs(dr) + abs(dc)) - 1 + end


# --- 2c. PATH FINDING ------------------------------------------------------

def bfs_field(goal, rows, cols, blocked=()):
    """{site: distance-to-goal} over the grid minus ``blocked``.

    Sites unreachable from ``goal`` are simply absent from the dict.
    """
    goal = as_site(goal)
    blocked = set(blocked)
    field = {goal: 0}
    dq = deque((goal,))
    while dq:
        s = dq.popleft()
        d = field[s] + 1
        for n in neighbors(s, rows, cols):
            if n in field or n in blocked:
                continue
            field[n] = d
            dq.append(n)
    return field


def astar_path(start, goal, rows, cols, blocked=(), site_cost=None,
               edge_use=None, opposed_cost=3.0, nb_cache=None):
    """Cheapest route start -> goal (EXCLUDING start) under soft costs.

    blocked   : hard obstacles, never entered.
    site_cost : {site: extra} charged for ENTERING a site -- how traffic is
                kept off other ions' rest sites and off rail sections the plan
                does not otherwise occupy.
    edge_use  : {(a, b): n} directed edges already claimed by previously
                planned ions.  Traversing (b, a), i.e. HEAD-ON against them,
                costs ``opposed_cost`` each; travelling the same way is FREE,
                so trains still share a corridor and only genuine counter-flow
                is pushed onto a parallel lane.  That is what discovers "wrap
                lane" detours without anybody hard-coding them.

    A* on the exact obstacle-free distance, so a few hundred node expansions
    per call.  Deterministic: ties break on sorted (kind, row, col).
    """
    start, goal = as_site(start), as_site(goal)
    if start == goal:
        return []
    blocked = blocked if isinstance(blocked, (set, frozenset)) else set(blocked)
    site_cost = site_cost or {}
    if nb_cache is None:
        nb_cache = {}

    def nb(s):
        n = nb_cache.get(s)
        if n is None:
            n = tuple(sorted(neighbors(s, rows, cols),
                             key=lambda x: (_KIND_ORDER[x[0]], x[1], x[2])))
            nb_cache[s] = n
        return n

    gsc = {start: 0.0}
    came = {}
    seq = 0
    heap = [(site_dist(start, goal, rows, cols), 0.0, 0, start)]
    found = False
    while heap:
        _f, gc, _s, s = heapq.heappop(heap)
        if s == goal:
            found = True
            break
        if gc > gsc.get(s, _INF) + 1e-9:
            continue
        for n in nb(s):
            if n in blocked:
                continue
            w = 1.0
            if edge_use:
                u = edge_use.get((n, s))
                if u:
                    w += opposed_cost * u
            e = site_cost.get(n)
            if e:
                w += e
            ng = gc + w
            if ng + 1e-9 < gsc.get(n, _INF):
                gsc[n] = ng
                came[n] = s
                seq += 1
                heapq.heappush(
                    heap, (ng + site_dist(n, goal, rows, cols), ng, seq, n))
    if not found:
        raise RouteError(f"no obstacle-free path {start} -> {goal}")
    path = []
    cur = goal
    while cur != start:
        path.append(cur)
        cur = came[cur]
    path.reverse()
    return path


def shortest_path(start, goal, rows, cols, blocked=()):
    """Sites from ``start`` to ``goal``, EXCLUDING ``start``; no soft costs."""
    start, goal = as_site(start), as_site(goal)
    if start == goal:
        return []
    field = bfs_field(goal, rows, cols, blocked)
    if start not in field:
        raise RouteError(f"no obstacle-free path {start} -> {goal}")
    path = []
    cur = start
    while cur != goal:
        d = field[cur]
        nxt = None
        for n in sorted(neighbors(cur, rows, cols),
                        key=lambda s: (_KIND_ORDER[s[0]], s[1], s[2])):
            if field.get(n, _INF) == d - 1:
                nxt = n
                break
        if nxt is None:
            raise RouteError(f"field descent stuck at {cur}")
        path.append(nxt)
        cur = nxt
    return path


# --- 2d. ROUND PACKER ------------------------------------------------------

class _Plan(object):
    """One ion's route: sites EXCLUDING its start; ``None`` = wait one round.

    Waits are how a group is made to leave early and come back late, so its
    rounds hide inside another group's journey instead of forming a phase of
    their own.  ``nleft`` counts only REAL steps: padding a route with waits
    must NOT promote the ion in the farthest-first priority order.
    """

    __slots__ = ("goal", "seq", "idx", "nleft")

    def __init__(self, seq, goal=None):
        seq = list(seq)
        if goal is None:
            for s in reversed(seq):
                if s is not None:
                    goal = s
                    break
        self.goal = goal
        self.seq = seq
        self.idx = 0
        self.nleft = sum(1 for s in seq if s is not None)


class _Engine(object):
    """Packs the per-ion routes into legal parallel rounds."""

    def __init__(self, rows, cols, pos, blocked, plans, push_after=1,
                 max_rounds=20000, max_pushes=64, aside_radius=2,
                 aside_preview=4, aside_depth=3, max_aside=6, replan_after=6,
                 replan_slack=10, site_use=None, site_cost=None):
        self.rows, self.cols = rows, cols
        self.pos = dict(pos)
        self.blocked = set(blocked)
        self.plans = plans
        self.push_after = max(1, int(push_after))
        self.max_rounds = int(max_rounds)
        self.max_pushes = int(max_pushes)
        self.aside_radius = int(aside_radius)
        self.aside_preview = int(aside_preview)
        self.aside_depth = int(aside_depth)
        self.max_aside = int(max_aside)
        self.replan_after = int(replan_after)
        self.replan_slack = int(replan_slack)
        # how many planned routes cross each site: a shove should park where
        # nobody is coming, NOT in the nearest U/D leg (a leg well is one of the
        # four members of its junction's clique, so parking there chokes EVERY
        # route across that junction, not just the vertical one)
        self.site_use = site_use or {}
        self.site_cost = site_cost or {}
        self.aside_count = {}
        self.goal_sites = set(p.goal for p in plans.values()
                              if p.goal is not None)
        self.occ = {}
        for q, s in self.pos.items():
            if s in self.occ:
                raise RouteError(f"two ions ({self.occ[s]}, {q}) start on {s}")
            if s in self.blocked:
                raise RouteError(f"ion {q} starts on a blocked site {s}")
            self.occ[s] = q
        self.stall = dict.fromkeys(self.pos, 0)
        self._nb = {}

    def _neigh(self, s):
        n = self._nb.get(s)
        if n is None:
            n = tuple(sorted(neighbors(s, self.rows, self.cols),
                             key=lambda x: (_KIND_ORDER[x[0]], x[1], x[2])))
            self._nb[s] = n
        return n

    def _done(self, q):
        p = self.plans[q]
        return p.idx >= len(p.seq)

    def _propose(self, q):
        p = self.plans[q]
        if p.idx >= len(p.seq):
            return None
        nxt = p.seq[p.idx]
        return _WAIT if nxt is None else nxt

    def _preview(self, q, k):
        """The next ``k`` sites ion ``q`` wants to occupy."""
        p = self.plans[q]
        return [s for s in p.seq[p.idx:p.idx + k] if s is not None]

    # -- one round -------------------------------------------------------
    def _step(self):
        active = [q for q in self.pos if not self._done(q)]
        if not active:
            return None
        # farthest-first: the critical ion never yields, so the makespan
        # tracks the single longest journey instead of the sum of them
        rank_src = sorted(active, key=lambda q: (-self.plans[q].nleft, q))
        rank = {q: i for i, q in enumerate(rank_src)}
        desired = {}
        waiting = []
        for q in rank_src:
            t = self._propose(q)
            if t is _WAIT:
                waiting.append(q)
            elif t is not None:
                desired[q] = t

        # -- target contention: highest priority claims the site -----------
        winner = {}
        for q in rank_src:
            t = desired.get(q)
            if t is None or t in self.blocked:
                continue
            if t not in winner:
                winner[t] = q
        sel = set(winner.values())

        # -- propagate "blocked": a chain only moves if its head can --------
        dep = {}
        rdep = {}
        work = []
        for q in sel:
            o = self.occ.get(desired[q])
            if o is None:
                continue
            dep[q] = o
            rdep.setdefault(o, []).append(q)
            if o not in sel:
                work.append(q)

        def _kill(seed):
            stack = [seed]
            while stack:
                x = stack.pop()
                if x not in sel:
                    continue
                sel.discard(x)
                for y in rdep.get(x, ()):
                    if y in sel:
                        stack.append(y)

        for q in work:
            _kill(q)

        # -- break 2-cycles (head-on swaps through one edge) ----------------
        # Cycles of length >= 3 are deliberately LEFT ALONE: they are legal and
        # rotate in a single round.  That is where the parallelism comes from.
        again = True
        while again:
            again = False
            for q in sorted(sel, key=lambda x: rank[x]):
                if q not in sel:
                    continue
                o = dep.get(q)
                if o is not None and o in sel and dep.get(o) == q:
                    _kill(q if rank[q] > rank[o] else o)
                    again = True
                    break

        # -- commit ---------------------------------------------------------
        moves = [(q, self.pos[q], desired[q])
                 for q in sorted(sel, key=lambda x: rank[x])]
        for q, fr, _to in moves:
            del self.occ[fr]
        for q, _fr, to in moves:
            self.occ[to] = q
            self.pos[q] = to
            p = self.plans[q]
            p.idx += 1
            p.nleft -= 1
        for q in waiting:
            self.plans[q].idx += 1
        moved = set(q for q, _, _ in moves)
        for q in active:
            self.stall[q] = 0 if (q in moved or q in waiting) \
                else self.stall[q] + 1
        self._unblock(rank_src, rank, desired, moved)
        return moves

    # -- deadlock breaking -----------------------------------------------
    def _unblock(self, rank_src, rank, desired, moved):
        """Make blockers step aside for ions that have been waiting."""
        budget = self.max_pushes
        pushed = set()
        for q in rank_src:
            if budget <= 0:
                return
            if q in moved or self.stall[q] < self.push_after:
                continue
            t = desired.get(q)
            if t is None:
                continue        # everything ahead is a static obstacle
            o = self.occ.get(t)
            if o is None or o in moved or o in pushed:
                continue
            if rank.get(o, len(rank)) < rank[q]:
                continue        # blocker outranks us and is itself stuck
            keep_clear = set(self._preview(q, self.aside_preview))
            if self._force_aside(o, {self.pos[q]}, keep_clear, pushed, 0):
                budget -= 1
            elif self.stall[q] >= self.replan_after and \
                    self._replan(q, self.pos[o]):
                budget -= 1

    def _replan(self, q, forbid):
        """Re-route ion ``q`` to its goal avoiding the site ``forbid``.

        Last resort when the blocker will not move again -- this is what breaks
        mutual push livelocks between two ions whose goals sit next to each
        other on a one-wide rail.  Refused when the detour is much longer than
        what is left of the route: a big reroute usually costs more than
        waiting.
        """
        p = self.plans[q]
        if p.goal is None or p.goal == forbid:
            return False
        block = set(self.blocked)
        block.add(forbid)
        try:
            newp = astar_path(self.pos[q], p.goal, self.rows, self.cols,
                              block, self.site_cost, None, 0.0, self._nb)
        except RouteError:
            return False
        if len(newp) > p.nleft + self.replan_slack:
            return False
        p.seq = list(newp)
        p.idx = 0
        p.nleft = len(newp)
        return True

    def _force_aside(self, o, avoid, keep_clear, pushed, depth):
        """Step ``o`` aside; if it is itself boxed in, step ITS blockers aside
        first (counter-flow on a one-wide rail needs this cascade)."""
        if o in pushed or self.aside_count.get(o, 0) >= self.max_aside:
            return False
        if self._step_aside(o, avoid, keep_clear):
            pushed.add(o)
            return True
        if depth >= self.aside_depth:
            return False
        deeper = avoid | {self.pos[o]}
        for n in self._neigh(self.pos[o]):
            if n in avoid or n in self.blocked:
                continue
            o2 = self.occ.get(n)
            if o2 is not None and \
                    self._force_aside(o2, deeper, keep_clear, pushed, depth + 1):
                return True
        return False

    def _step_aside(self, o, avoid, keep_clear):
        """Shove ion ``o`` out of the way, splicing the detour AND the return
        trip into its own route so it still reaches its goal.

        A rail section's neighbours are its own junction plus the three
        other wells of the junction on its left -- exactly the wells the pusher
        itself needs -- so a one-step shove is often impossible: BFS over
        currently FREE sites for the nearest parking spot outside the pusher's
        near-term corridor, preferring quiet sites and avoiding other ions'
        goals.
        ``avoid`` is hard (never entered), ``keep_clear`` is soft (traversable,
        never parked on).
        """
        here = self.pos[o]
        prev = {here: None}
        dq = deque(((here, 0),))
        best = None
        while dq:
            s, d = dq.popleft()
            if d >= self.aside_radius:
                continue
            for n in self._neigh(s):
                if n in prev or n in avoid or n in self.blocked or n in self.occ:
                    continue
                prev[n] = s
                if n not in keep_clear:
                    key = (self.site_cost.get(n, 0.0),
                           self.site_use.get(n, 0),
                           1 if n in self.goal_sites else 0,
                           d + 1, _KIND_ORDER[n[0]], n[1], n[2])
                    if best is None or key < best[0]:
                        best = (key, n)
                dq.append((n, d + 1))
            if best is not None and best[0][0] == 0 and best[0][1] == 0 \
                    and best[0][3] <= d + 1:
                break
        if best is None:
            return False
        path = []
        cur = best[1]
        while cur != here:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        p = self.plans[o]
        self.aside_count[o] = self.aside_count.get(o, 0) + 1
        back = list(reversed(path[:-1])) + [here]
        p.seq = p.seq[:p.idx] + path + back + p.seq[p.idx:]
        p.nleft += len(path) + len(back)
        return True

    def diagnose(self, k=6):
        """Which ions never finished, and who is holding them up."""
        stuck = sorted(q for q in self.pos if not self._done(q))
        bits = []
        for q in stuck[:k]:
            want = self._propose(q)
            blk = self.occ.get(want) if isinstance(want, tuple) else None
            bits.append(f"{q}@{self.pos[q]} wants {want}"
                        + (f" (held by {blk}@{self.pos.get(blk)})"
                           if blk is not None else " (blocked/none)"))
        return f"{len(stuck)} ions short of their goal: " + "; ".join(bits)

    def run(self):
        rounds = []
        idle = 0
        while True:
            if len(rounds) > self.max_rounds:
                raise RouteError(f"router exceeded {self.max_rounds} rounds; "
                                 + self.diagnose())
            mv = self._step()
            if mv is None:
                return rounds
            if mv:
                rounds.append(mv)
                idle = 0
            else:
                idle += 1
                if idle > 4 * max(8, self.push_after):
                    raise RouteError(
                        f"deadlock: no ion could move for {idle} rounds; "
                        + self.diagnose())


# --- 2e. ROUTER ENTRY POINTS ------------------------------------------------

def plan_moves(starts, targets, blocked, rows, cols, paths=None,
               site_cost=None, attempts=None, **opts):
    """Route every ion in ``starts`` to its site in ``targets``.

    starts    : {qid: site} current position of every MOBILE ion.
    targets   : {qid: site} desired position.  A qid absent from ``targets``
                (or targeting its own start) HOLDS its site: it will not
                travel, but traffic may shove it aside and it is guaranteed
                back before the last returned round.  Holding an ion on a busy
                junction is expensive -- give it a real target instead.
    blocked   : sites permanently occupied (beacons, reservoir, frozen ions).
    rows,cols : grid dimensions; they bound where ions may go.
    paths     : {qid: [site|None, ...]} explicit routes OVERRIDING the search
                for those ions (``None`` = wait one round).
    site_cost : {site: extra} soft cost for entering a site.
    attempts  : override ROUTER_ATTEMPTS; **opts override ROUTER_POLICY.

    Returns [[(qid, from, to), ...], ...]: one inner list per parallel round.
    """
    starts = {int(q): as_site(s) for q, s in starts.items()}
    targets = {int(q): as_site(s) for q, s in (targets or {}).items()}
    paths = {int(q): [None if s is None else as_site(s) for s in p]
             for q, p in (paths or {}).items()}
    blocked = set(as_site(s) for s in blocked)
    rows, cols = int(rows), int(cols)

    seen = {}
    for q, g in targets.items():
        if q in paths:
            continue
        if g in seen:
            raise RouteError(f"ions {seen[g]} and {q} share the target {g}")
        seen[g] = q
        if g in blocked:
            raise RouteError(f"ion {q} targets a blocked site {g}")

    costs = dict((as_site(x), float(w)) for x, w in (site_cost or {}).items())
    if attempts is None:
        attempts = ROUTER_ATTEMPTS
    # the floor: parallel rounds >= the single longest journey
    floor = max([site_dist(s, targets.get(q, s), rows, cols)
                 for q, s in starts.items()] or [0])
    floor = max(floor, max([sum(1 for x in p if x is not None)
                            for p in paths.values()] or [0]))
    base = dict(ROUTER_POLICY)
    base.update(opts)
    # Fail fast: a schedule many times the longest single journey is a WEDGED
    # heuristic, not a slow one.  Bounding it keeps the portfolio cheap.
    base.setdefault("max_rounds", max(64, 6 * floor + 120))
    # Soft costs are advisory: if every attempt wedges under them, relax and
    # retry -- a preference must never turn a routable request into a failure.
    profiles = [costs]
    if costs:
        profiles.append(dict((s, w * 0.25) for s, w in costs.items()))
        profiles.append({})
    best = None
    last_err = None
    for prof in profiles:
        for cfg in attempts:
            kw = dict(base)
            kw.update(cfg)
            if best is not None:
                kw["max_rounds"] = min(kw["max_rounds"], len(best))
            try:
                got = _plan_once(starts, targets, blocked, rows, cols, paths,
                                 prof, kw)
            except RouteError as e:
                last_err = e
                continue
            if best is None or len(got) < len(best):
                best = got
            if len(best) <= floor:
                break
        if best is not None:
            break
    if best is None:
        raise last_err if last_err else RouteError("routing failed")
    return best


def _plan_once(starts, targets, blocked, rows, cols, paths, costs, opts):
    """One prioritised-planning pass, then one packing run.

    PRIORITISED PLANNING: ions are routed one at a time, FARTHEST FIRST, each
    telling the next which directed edges it claimed, so counter-flow is priced
    onto a parallel lane instead of deadlocking a one-wide rail.
    """
    opts = dict(opts)
    opposed_cost = float(opts.pop("opposed_cost", 3.0))
    plans = {}
    edge_use = {}
    site_use = {}
    for q, s in starts.items():                 # caller-supplied routes first
        if q not in paths:
            continue
        cur = s
        for nxt in paths[q]:
            if nxt is None:
                continue
            if not is_edge(cur, nxt):
                raise RouteError(
                    f"ion {q}: {cur} -> {nxt} is not one primitive step")
            if nxt in blocked:
                raise RouteError(f"ion {q} routes through blocked site {nxt}")
            edge_use[(cur, nxt)] = edge_use.get((cur, nxt), 0) + 1
            site_use[nxt] = site_use.get(nxt, 0) + 1
            cur = nxt
        plans[q] = _Plan(paths[q])

    nb_cache = {}
    todo = [q for q in starts if q not in paths]
    todo.sort(key=lambda q: (-site_dist(starts[q], targets.get(q, starts[q]),
                                        rows, cols), q))
    for q in todo:
        s = starts[q]
        goal = targets.get(q, s)
        if s == goal:
            plans[q] = _Plan([])
            continue
        p = astar_path(s, goal, rows, cols, blocked, costs, edge_use,
                       opposed_cost, nb_cache)
        cur = s
        for nxt in p:
            edge_use[(cur, nxt)] = edge_use.get((cur, nxt), 0) + 1
            site_use[nxt] = site_use.get(nxt, 0) + 1
            cur = nxt
        plans[q] = _Plan(p)

    opts.setdefault("site_use", site_use)
    opts.setdefault("site_cost", costs)
    return _Engine(rows, cols, starts, blocked, plans, **opts).run()


def emit_moves(timeline, rounds):
    """Append ``rounds`` to ``timeline`` as the evaluator's 'move' phases."""
    timeline.extend(
        {"t": "move",
         "moves": [[int(q), [f[0], int(f[1]), int(f[2])],
                    [t[0], int(t[1]), int(t[2])]] for q, f, t in rnd]}
        for rnd in rounds if rnd)
    return timeline


def apply_rounds(rounds, positions):
    """Advance a {qid: site} map by replaying ``rounds``."""
    for rnd in rounds:
        for q, _fr, to in rnd:
            positions[q] = as_site(to)
    return positions


def footprint_site_cost(rows, cols, occupied, parked, data_rows):
    """Soft-cost map keeping traffic inside the layout's own footprint.

    Every FRESH rail section an ion stands on is a new trap zone in the score's
    footprint term, so a detour across an otherwise-unused row can cost more
    score than the rounds it saves.  ``parked`` (the other ions' resting sites)
    gets a small toll too, which keeps routes on corridors that are free
    instead of wandering into bounded pockets.
    """
    cost = dict((as_site(s), PARKED_SITE_TOLL) for s in parked)
    occupied = set(as_site(s) for s in occupied)
    data_rows = set(data_rows)
    for r in range(rows):
        toll = FRESH_DATA_ROW_TOLL if r in data_rows else FRESH_RAIL_ROW_TOLL
        for c in range(cols):
            s0 = ("S", r, c)
            if s0 not in occupied:
                cost.setdefault(s0, toll)
    return cost


# ===========================================================================
# SECTION 3 - ASSEMBLY
# ---------------------------------------------------------------------------
# OWNS: the SEC script.  Takes the tables from SECTION 1, drives the router
# from SECTION 2, and emits the evaluator's phase list:
#     prep -> [ approach, merge, gate t, split ] x 7 -> measure -> wrap-back
# The gate rounds, their order and the required merge pairs are FROZEN by the
# code's published schedule; what this section chooses is HOW each gap between
# two gate layers is travelled, and how the phases overlap.
#
# The one non-obvious trick is the gap>0 "duck out and come back" move: between
# gate layers the 70 data ions are parked on ancilla-row JUNCTIONS, where they
# chop every rail the ancillas need.  Each data ion is therefore handed an
# explicit route home -> idle -> back out, with the idle window sized by a dry
# run, so its rounds hide INSIDE the ancillas' journey instead of costing a
# phase of their own.  If that does not come out shorter, the plain two-phase
# emission is used instead.
#
# WHAT A MUTATION COULD TRY HERE:
#   * overlap MORE: start the next gap's approach before the previous split,
#     or let ancillas already in place set off early;
#   * the wrap-back is now DATA-ONLY (4 rounds): each ancilla species need
#     only restore its own occupied SET, which this geometry already does, so
#     the ancillas do not walk home at all.  What is left to try is choosing
#     the ancilla -> site assignment at the boundary so the NEXT cycle starts
#     from a cheaper arrangement;
#   * choose the merge SIDE per pair (data moves to ancilla, or the reverse)
#     to halve the longest journey in a gap;
#   * prep/measure in several smaller batches placed where they hide idle time;
#   * search: try several parkings / tolls per gap and keep the cheapest --
#     the evaluation budget is minutes and a build takes seconds.
# ===========================================================================

def build_embedding_and_shuttle(spec):
    geo = build_geometry(spec)
    rows, cols = geo["rows"], geo["cols"]
    posn = geo["posn"]
    n_sim = geo["n_sim"]
    n_rounds = geo["n_rounds"]
    anc_ids = geo["anc_ids"]

    # beacons + reservoir never move: hard obstacles for every route
    static = set(posn[q] for q in geo["static_ids"])
    live = {q: posn[q] for q in range(n_sim)}      # router's view of the world
    home = dict(live)                              # cyclicity target
    # data rest sites are SOFT obstacles for the ancilla routes: together with
    # the beacons they fill the data rows, so honouring them keeps ancilla
    # routes on the ancilla rails and the vertical ladders.
    parked = set(posn[q] for q in geo["data_ids"])
    site_cost = footprint_site_cost(rows, cols, posn.values(), parked,
                                    geo["data_rows"])

    timeline = [{"t": "prep", "ancillas": list(anc_ids)}]

    def transport(goals, paths=None):
        rounds = plan_moves(live, goals, static, rows, cols, paths=paths,
                            site_cost=site_cost)
        emit_moves(timeline, rounds)
        apply_rounds(rounds, live)
        return len(rounds)

    for t in range(n_rounds):
        sites = geo["anc_sites"](t)               # where each ancilla must be
        pairs = geo["required_pairs"](t)          # (data qid, ancilla qid)
        # the data ion meets its ancilla on the JUNCTION next to the ancilla's
        # rail section, one edge away, which is what a merge requires
        dgoal = {}
        for dq, aq in pairs:
            _k, r, c = sites[aq]
            dgoal[dq] = ("J", r, c)
        assert len(set(sites.values()) | set(dgoal.values())) == n_sim

        if t == 0:
            # the layout already stands the ancillas on their round-0 sites,
            # so this gap is only the data ions' approach hop
            transport(dgoal)
        else:
            outp, backp, n_out, n_back = {}, {}, 0, 0
            for dq, gsite in dgoal.items():
                outp[dq] = shortest_path(live[dq], home[dq], rows, cols, static)
                backp[dq] = shortest_path(home[dq], gsite, rows, cols, static)
                n_out = max(n_out, len(outp[dq]))
                n_back = max(n_back, len(backp[dq]))
            park = dict(sites)
            dry = plan_moves(live, park, static, rows, cols, paths=outp,
                             site_cost=site_cost)
            merged = None
            if len(dry) - n_out - n_back >= 0:
                # pad each data route with waits so it comes back out exactly
                # as the ancillas arrive
                mp = {dq: outp[dq]
                      + [None] * (len(dry) - len(outp[dq]) - len(backp[dq]))
                      + backp[dq] for dq in dgoal}
                try:
                    merged = plan_moves(live, park, static, rows, cols,
                                        paths=mp, site_cost=site_cost,
                                        max_rounds=len(dry) + n_back - 1)
                except RouteError:
                    merged = None
            if merged is not None and len(merged) < len(dry) + n_back:
                emit_moves(timeline, merged)
                apply_rounds(merged, live)
            else:                                  # two-phase fallback
                emit_moves(timeline, dry)
                apply_rounds(dry, live)
                transport(dgoal)

        timeline.append({"t": "merge", "pairs": [[dq, aq] for dq, aq in pairs]})
        timeline.append({"t": "gate", "round": t})
        timeline.append({"t": "split",
                         "pairs": [[dq, ["J", sites[aq][1], sites[aq][2]]]
                                   for dq, aq in pairs]})
        # merge + split are a no-op on the router's state: every data ion
        # leaves the junction it merged from and returns to exactly that one.
        if t == n_rounds - 1:
            timeline.append({"t": "measure", "ancillas": list(anc_ids)})

    # ---- cycle boundary -------------------------------------------------
    # Data (and the static beacons/reservoir) must end on their OWN layout
    # site. Each ANCILLA SPECIES only has to restore its own occupied SET --
    # the residual permutation is absorbed by relabelling the ancilla in
    # software, which is what the paper itself does (Alg. 1 line 2, p.30) and
    # is invisible to a circuit keyed on the check index. This geometry's last
    # round already stands each species on its own site set, so only the data
    # ions walk home; the greedy fallback keeps the builder correct for a
    # schedule (or another code) whose residual is NOT set-preserving.
    goals = {q: home[q] for q in geo["data_ids"]}
    half = len(anc_ids) // 2
    for species in (anc_ids[:half], anc_ids[half:]):
        want = sorted(home[q] for q in species)
        if sorted(live[q] for q in species) == want:
            continue                     # set already restored -- no travel
        free = list(want)
        for q in sorted(species,
                        key=lambda x: (-min(site_dist(live[x], s, rows, cols)
                                            for s in want), x)):
            pick = min(free, key=lambda s: (site_dist(live[q], s, rows, cols),
                                            s))
            free.remove(pick)
            goals[q] = pick
    transport(goals)

    return {
        "grid": {"rows": rows, "cols": cols},
        "layout": geo["layout"],
        "timeline": timeline,
    }
# EVOLVE-BLOCK-END


def run_experiment(spec, **kwargs):
    """Entrypoint called by evaluate.py; returns the plan dict."""
    return build_embedding_and_shuttle(spec)

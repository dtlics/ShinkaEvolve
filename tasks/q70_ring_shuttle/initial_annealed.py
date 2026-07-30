"""ANNEALED seed -- affine embedding found by evolution + the shipped router.

BEST PLAN IN THE TASK: 244 transport rounds / 1.47x its own distance floor /
score +2.9075, at 304 rail sections. IonQ's published hand design runs the same
SEC in 424 rounds, so this is 42% fewer -- but it uses MORE chip area than they
do (304 vs ~288 sections) and than the other two seeds (283 / 277), and its
measured LER is statistically indistinguishable from theirs. It is a SPEED and
AREA result, and only the speed half is a win.

PROVENANCE, and why this file exists. Run q70ring_v3 ($59.66 of a $100 budget,
11 windows, 113 programs) produced one structural discovery: a grounded
SECTION-1 rewrite -- an ANNEALED AFFINE EMBEDDING (row permutation, column
multiplier, shear, side mask, data flip, pitch and base) scored by a per-gate
Hungarian ancilla-to-site matching against an analytic floor -- which moved the
layout's distance FLOOR for the first time in 62 programs. That run predates
three model corrections, so its own numbers do not transfer; what transfers is
the SECTION 1 code. Re-running that same annealer against the CORRECTED
distance oracle re-optimised the floor with no human input, and four unrelated
islands had converged on the same optimum. This file is that SECTION 1 and
SECTION 3, with SECTION 2 replaced by the router shared byte-for-byte with the
other two seeds.

  floor 226/230 (other seeds)  ->  166 here
  rounds 300/310               ->  244 here

THE FILE IS THREE INDEPENDENT SECTIONS, each with its own banner listing what
it owns and what a mutation could try. You can rewrite ONE without reading the
other two:

  SECTION 1  GEOMETRY  grid size, where every ion rests, the per-gate-round
                       ion -> site tables. This layout's FLOOR (166) is decided
                       entirely here, and this section is the evolved asset.
  SECTION 2  ROUTER    shortest paths, congestion pricing and the round packer.
                       Byte-identical to the other seeds; knobs in ROUTER_POLICY.
  SECTION 3  ASSEMBLY  prep, the 7 merge/gate/split rounds, measure, the cycle
                       boundary, and phase emission.

WHERE THE REMAINING HEADROOM IS, largest first:
  * THE WRAP FLOOR, ~40 rounds. This layout pays wrap_floor 40 where both other
    seeds pay 1, purely because its data HOME sites are not where data stands at
    the last gate round. Two mechanical fixes, both in SECTION 1: let it choose
    data homes that coincide with the final gate round's data positions; and
    note its internal annealing objective still maxes the wrap term over BOTH
    data and ancillas, although the evaluator's wrap_floor has been DATA-ONLY
    since the cycle-boundary correction -- i.e. it is optimising a stale
    objective. Either could take the floor from 166 toward ~127.
  * PACKING SLACK, 78 rounds (244 used vs 166 floor, 1.47x). 59 of the 244
    rounds still move fewer than 20 of 140 ions.
  * AREA, 304 sections. The worst of the three seeds and above the paper's ~288;
    the score's footprint term is paying for that.
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
# ===========================================================================

import random

CELL_BASE = 4
CELL_PITCH = 4


def _coprime_units(n):
    out = []
    for a in range(1, n):
        ok = True
        for d in range(2, min(a, n) + 1):
            if a % d == 0 and n % d == 0:
                ok = False
                break
        if ok:
            out.append(a)
    return out or [1]


def _hungarian(cost):
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    assert n <= m
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        way = [0] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            row = cost[i0 - 1]
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = float(row[j - 1]) - u[i0] - v[j]
                if cur < minv[j] - 1e-12:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta - 1e-12:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    ans = [-1] * n
    for j in range(1, m + 1):
        if p[j] > 0:
            ans[p[j] - 1] = j - 1
    return ans


def build_geometry(spec):
    l_ring = int(spec["l"])
    m_ring = int(spec["m"])
    n_half = l_ring * m_ring
    exps = {"A": [tuple(e) for e in spec["A_exps"]],
            "B": [tuple(e) for e in spec["B_exps"]]}
    schedule = [tuple(t) for t in spec["schedule"]]
    n_rounds = len(schedule)
    qb = spec["qid_bases"]
    DATA0, XANC0, ZANC0 = qb["data"], qb["x_anc"], qb["z_anc"]
    BEAC0, RES0 = qb["beacon"], qb["reservoir"]
    n_sim = 2 * (2 * n_half)

    fam = [t[0] for t in schedule]
    ex = [exps[t[0]][t[1]] for t in schedule]
    ez = [exps[t[2]][t[3]] for t in schedule]
    units_m = _coprime_units(m_ring)
    units_l = _coprime_units(l_ring)

    def pos(i, j):
        return (i % l_ring) * m_ring + (j % m_ring)

    def cell(p):
        return divmod(p, m_ring)

    def required_pairs_raw(t):
        f = fam[t]
        xp, zp = [], []
        for g in range(n_half):
            i, j = divmod(g, m_ring)
            if f == "A":
                xd = DATA0 + pos(i + ex[t][0], j + ex[t][1])
                zd = DATA0 + n_half + pos(i - ez[t][0], j - ez[t][1])
            else:
                xd = DATA0 + n_half + pos(i + ex[t][0], j + ex[t][1])
                zd = DATA0 + pos(i - ez[t][0], j - ez[t][1])
            xp.append((xd, XANC0 + g))
            zp.append((zd, ZANC0 + g))
        return xp, zp

    def make_initial():
        return {
            "pitch": 4,
            "base": 4,
            "row_a": 1,
            "row_b": 0,
            "row_perm": list(range(l_ring)),
            "col_a": 1,
            "col_b": 0,
            "shear": 0,
            "data_flip": 0,
            "side_mask": sum((1 if fam[t] == "B" else 0) << t
                             for t in range(n_rounds)),
        }

    def canonicalize(p):
        p = dict(p)
        p["pitch"] = int(max(4, min(6, p.get("pitch", 4))))
        max_base = max(1, spec["grid_max_cols"] - p["pitch"] * m_ring - 8)
        p["base"] = int(max(1, min(max_base, p.get("base", 4))))
        p["row_a"] = units_l[int(p.get("row_a", 1)) % len(units_l)]
        p["row_b"] = int(p.get("row_b", 0)) % l_ring
        if "row_perm" not in p or sorted(p["row_perm"]) != list(range(l_ring)):
            p["row_perm"] = [((p["row_a"] * i + p["row_b"]) % l_ring)
                             for i in range(l_ring)]
        p["col_a"] = units_m[int(p.get("col_a", 1)) % len(units_m)]
        p["col_b"] = int(p.get("col_b", 0)) % m_ring
        p["shear"] = int(p.get("shear", 0)) % m_ring
        p["data_flip"] = int(p.get("data_flip", 0)) & 1
        p["side_mask"] = int(p.get("side_mask", 0)) & ((1 << n_rounds) - 1)
        return p

    def mutate(p, rng, temp):
        q = dict(p)
        q["row_perm"] = list(p["row_perm"])
        k = rng.randrange(10)
        if k == 0:
            a, b = rng.sample(range(l_ring), 2)
            q["row_perm"][a], q["row_perm"][b] = q["row_perm"][b], q["row_perm"][a]
        elif k == 1:
            cut = rng.randrange(1, l_ring)
            q["row_perm"] = q["row_perm"][cut:] + q["row_perm"][:cut]
        elif k == 2:
            q["row_perm"].reverse()
        elif k == 3:
            q["col_a"] = rng.choice(units_m)
        elif k == 4:
            q["col_b"] = (q["col_b"] + rng.choice((-2, -1, 1, 2))) % m_ring
        elif k == 5:
            q["shear"] = (q["shear"] + rng.choice((-2, -1, 1, 2))) % m_ring
        elif k == 6:
            q["side_mask"] ^= (1 << rng.randrange(n_rounds))
        elif k == 7:
            a = rng.randrange(n_rounds)
            b = rng.randrange(a, n_rounds)
            for t in range(a, b + 1):
                q["side_mask"] ^= (1 << t)
        elif k == 8:
            q["data_flip"] ^= 1
        else:
            q["pitch"] = 4 if q["pitch"] != 4 else 5
            q["base"] = 4
        return canonicalize(q)

    def build_tables(p):
        p = canonicalize(p)
        pitch = p["pitch"]
        base = p["base"]
        ROWS = 2 * l_ring + 1
        COLS = base + pitch * m_ring + 4
        if ROWS > spec["grid_max_rows"] or COLS > spec["grid_max_cols"]:
            return None

        if p["data_flip"]:
            side_off = {"L": 2, "R": 0}
            beacon_off = {"L": 3, "R": 1}
        else:
            side_off = {"L": 0, "R": 2}
            beacon_off = {"L": 1, "R": 3}

        row_band = list(p["row_perm"])

        def band(i):
            return row_band[i % l_ring]

        def slot(i, j):
            return (p["col_a"] * (j % m_ring)
                    + p["shear"] * (i % l_ring)
                    + p["col_b"]) % m_ring

        def side_col(i, j, side):
            return base + pitch * slot(i, j) + side_off[side]

        def beacon_col(i, j, side):
            return base + pitch * slot(i, j) + beacon_off[side]

        def data_site(dq):
            idx = dq - DATA0
            half = 0 if idx < n_half else 1
            pp = idx if half == 0 else idx - n_half
            i, j = cell(pp)
            b = band(i)
            if half == 0:
                side = "R" if p["data_flip"] else "L"
                return ("S", 2 * b, side_col(i, j, side))
            else:
                side = "L" if p["data_flip"] else "R"
                return ("S", 2 * b + 2, side_col(i, j, side))

        def beacon_site(dq):
            idx = dq - DATA0
            half = 0 if idx < n_half else 1
            pp = idx if half == 0 else idx - n_half
            i, j = cell(pp)
            b = band(i)
            if half == 0:
                side = "R" if p["data_flip"] else "L"
                return ("S", 2 * b, beacon_col(i, j, side))
            else:
                side = "L" if p["data_flip"] else "R"
                return ("S", 2 * b + 2, beacon_col(i, j, side))

        def anc_pool(side):
            return [("S", 2 * band(i) + 1, side_col(i, j, side))
                    for i in range(l_ring) for j in range(m_ring)]

        data_home = {DATA0 + d: data_site(DATA0 + d)
                     for d in range(2 * n_half)}

        posn0 = {}
        occ = {}
        for d in range(2 * n_half):
            q = DATA0 + d
            bq = BEAC0 + d
            posn0[q] = data_home[q]
            posn0[bq] = beacon_site(q)
        for q, s in posn0.items():
            if s in occ:
                return None
            occ[s] = q

        anc_tables = []
        prev_anc = {}
        prev_data = dict(data_home)
        total_floor = 0
        max_gap = 0

        for t in range(n_rounds):
            xside = "R" if ((p["side_mask"] >> t) & 1) else "L"
            zside = "L" if xside == "R" else "R"
            xpairs, zpairs = required_pairs_raw(t)
            table = {}
            gap_floor = 0
            next_data = {}

            for pairs, pool in ((xpairs, anc_pool(xside)),
                                (zpairs, anc_pool(zside))):
                rows_cost = []
                for dq, aq in pairs:
                    row = []
                    for s in pool:
                        jsite = ("J", s[1], s[2])
                        dd = site_dist(prev_data[dq], jsite, ROWS, COLS)
                        da = 0 if t == 0 else site_dist(prev_anc[aq], s, ROWS, COLS)
                        mx = max(dd, da)
                        row.append(1000 * mx * mx + 13 * mx + 5 * dd + da)
                    rows_cost.append(row)
                assign = _hungarian(rows_cost)
                used = set()
                for ri, ci in enumerate(assign):
                    if ci in used or ci < 0:
                        return None
                    used.add(ci)
                    dq, aq = pairs[ri]
                    s = pool[ci]
                    jsite = ("J", s[1], s[2])
                    table[aq] = s
                    next_data[dq] = jsite
                    dd = site_dist(prev_data[dq], jsite, ROWS, COLS)
                    da = 0 if t == 0 else site_dist(prev_anc[aq], s, ROWS, COLS)
                    gap_floor = max(gap_floor, dd, da)

            if len(table) != 2 * n_half or len(set(table.values())) != 2 * n_half:
                return None
            if len(next_data) != 2 * n_half:
                return None

            anc_tables.append(table)
            prev_anc = dict(table)
            prev_data = dict(next_data)
            total_floor += gap_floor
            max_gap = max(max_gap, gap_floor)

        wrap_floor = 0
        for q, s in prev_data.items():
            wrap_floor = max(wrap_floor, site_dist(s, data_home[q], ROWS, COLS))
        for aq, s in prev_anc.items():
            wrap_floor = max(wrap_floor, site_dist(s, anc_tables[0][aq], ROWS, COLS))
        total_floor += wrap_floor
        max_gap = max(max_gap, wrap_floor)

        posn = dict(posn0)
        for g in range(n_half):
            posn[XANC0 + g] = anc_tables[0][XANC0 + g]
            posn[ZANC0 + g] = anc_tables[0][ZANC0 + g]

        used = set(posn.values())
        candidates = []
        for c in range(COLS - 4, COLS):
            for r in range(1, ROWS, 2):
                candidates.append(("S", r, c))
        for r in range(1, ROWS, 2):
            for c in range(COLS):
                candidates.append(("S", r, c))
        k = 0
        for rr in range(spec["n_reservoir"]):
            while k < len(candidates) and candidates[k] in used:
                k += 1
            if k >= len(candidates):
                return None
            q = RES0 + rr
            posn[q] = candidates[k]
            used.add(candidates[k])
            k += 1

        if len(set(posn.values())) != len(posn):
            return None

        side_churn = sum(1 for t in range(1, n_rounds)
                         if ((p["side_mask"] >> t) & 1)
                         != ((p["side_mask"] >> (t - 1)) & 1))
        objective = float(total_floor) + 0.10 * max_gap + 0.035 * COLS + 0.20 * side_churn
        return {"params": p, "rows": ROWS, "cols": COLS, "posn": posn,
                "anc_tables": anc_tables, "objective": objective,
                "floor": total_floor, "wrap_floor": wrap_floor}

    rng = random.Random(12345)
    starts = []
    base = canonicalize(make_initial())
    starts.append(base)

    for ra in units_l:
        for rb in range(l_ring):
            for ca in units_m:
                for sh in range(m_ring):
                    if len(starts) >= 48:
                        break
                    p = dict(base)
                    p["row_perm"] = [((ra * i + rb) % l_ring)
                                     for i in range(l_ring)]
                    p["col_a"] = ca
                    p["shear"] = sh
                    p["col_b"] = (rb + sh) % m_ring
                    p["side_mask"] = base["side_mask"]
                    starts.append(canonicalize(p))
                if len(starts) >= 48:
                    break
            if len(starts) >= 48:
                break
        if len(starts) >= 48:
            break

    for flip in (0, 1):
        p = dict(base)
        p["data_flip"] = flip
        p["row_perm"] = list(reversed(range(l_ring)))
        p["side_mask"] ^= ((1 << n_rounds) - 1) if flip else 0
        starts.append(canonicalize(p))

    best = None
    best_val = float("inf")

    def consider(p):
        nonlocal best, best_val
        tab = build_tables(p)
        if tab is None:
            return None, float("inf")
        val = tab["objective"]
        if val < best_val - 1e-12:
            best = tab
            best_val = val
        return tab, val

    scored = []
    seen = set()
    for p in starts:
        key = (tuple(p["row_perm"]), p["pitch"], p["base"], p["col_a"],
               p["col_b"], p["shear"], p["data_flip"], p["side_mask"])
        if key in seen:
            continue
        seen.add(key)
        tab, val = consider(p)
        if tab is not None:
            scored.append((val, p))
    scored.sort(key=lambda x: x[0])
    if not scored:
        tab, _ = consider(base)
        if tab is None:
            raise RuntimeError("geometry could not build any layout")
        scored = [(best_val, best["params"])]

    for si, start in enumerate([p for _v, p in scored[:10]]):
        cur = canonicalize(start)
        cur_tab, cur_val = consider(cur)
        if cur_tab is None:
            continue
        temp0 = 18.0 + 4.0 * si
        for it in range(360):
            frac = it / 359.0
            temp = temp0 * (1.0 - frac) + 0.25 * frac
            cand = mutate(cur, rng, temp)
            cand_tab, cand_val = consider(cand)
            if cand_tab is None:
                continue
            accept = cand_val <= cur_val
            if not accept:
                margin = cand_val - cur_val
                if margin < temp and rng.random() < (1.0 - margin / temp):
                    accept = True
            if accept:
                cur, cur_val = cand, cand_val

    chosen = best
    rows, cols = chosen["rows"], chosen["cols"]
    posn = chosen["posn"]
    anc_tables = chosen["anc_tables"]

    layout = {
        "data": [list(posn[DATA0 + i]) for i in range(2 * n_half)],
        "x_anc": [list(posn[XANC0 + g]) for g in range(n_half)],
        "z_anc": [list(posn[ZANC0 + g]) for g in range(n_half)],
        "beacon": [list(posn[BEAC0 + i]) for i in range(2 * n_half)],
        "reservoir": [list(posn[RES0 + i]) for i in range(spec["n_reservoir"])],
    }

    def anc_sites(t):
        return dict(anc_tables[int(t)])

    def required_pairs(t):
        xp, zp = required_pairs_raw(int(t))
        return xp + zp

    data_ids = list(range(DATA0, DATA0 + 2 * n_half))
    anc_ids = [XANC0 + g for g in range(n_half)] + [ZANC0 + g for g in range(n_half)]

    return {"rows": rows, "cols": cols, "n_sim": n_sim, "n_rounds": n_rounds,
            "posn": posn, "layout": layout, "data_ids": data_ids,
            "anc_ids": anc_ids,
            "static_ids": list(range(BEAC0, RES0 + spec["n_reservoir"])),
            "data_rows": sorted(set(posn[q][1] for q in data_ids)),
            "anc_sites": anc_sites, "required_pairs": required_pairs}


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
# ===========================================================================

def build_embedding_and_shuttle(spec):
    geo = build_geometry(spec)
    rows, cols = geo["rows"], geo["cols"]
    posn = geo["posn"]
    n_sim = geo["n_sim"]
    n_rounds = geo["n_rounds"]
    anc_ids = geo["anc_ids"]

    static = set(posn[q] for q in geo["static_ids"])
    live = {q: posn[q] for q in range(n_sim)}
    home = dict(live)
    parked = set(posn[q] for q in geo["data_ids"])
    site_cost = footprint_site_cost(rows, cols, posn.values(), parked, geo["data_rows"])
    used_s = set(s for s in posn.values() if s[0] == "S")

    hot_attempts = (
        {"aside_radius": 2, "swap_radius": 6, "opposed_cost": 1.5,
         "dense_passes": 5, "age_cap": 12, "age_weight": 0, "stall_weight": 0, "unlock_bias": 0},
        {"aside_radius": 3, "swap_radius": 6, "opposed_cost": 1.5,
         "dense_passes": 5, "age_cap": 12, "age_weight": 0, "stall_weight": 0, "unlock_bias": 0},
        {"aside_radius": 2, "swap_radius": 5, "opposed_cost": 6.0,
         "dense_passes": 5, "age_cap": 12, "age_weight": 0, "stall_weight": 0, "unlock_bias": 0},
        {"aside_radius": 3, "swap_radius": 0, "opposed_cost": 6.0,
         "dense_passes": 5, "age_cap": 12, "age_weight": 0, "stall_weight": 0, "unlock_bias": 0},
        {"aside_radius": 2, "swap_radius": 6, "opposed_cost": 1.5,
         "dense_passes": 8, "age_cap": 18, "age_weight": 1, "stall_weight": 3, "unlock_bias": 1},
        {"aside_radius": 3, "swap_radius": 6, "opposed_cost": 1.5,
         "dense_passes": 8, "age_cap": 18, "age_weight": 1, "stall_weight": 3, "unlock_bias": 1},
        {"aside_radius": 2, "swap_radius": 5, "opposed_cost": 4.5,
         "dense_passes": 8, "age_cap": 20, "age_weight": 2, "stall_weight": 3, "unlock_bias": 1},
        {"aside_radius": 3, "swap_radius": 0, "opposed_cost": 6.0,
         "dense_passes": 7, "age_cap": 18, "age_weight": 2, "stall_weight": 2, "unlock_bias": 1},
    )

    # ---- TRANSPLANT ADAPTER (added by the v3 salvage) -------------------
    # SECTION 2 has been replaced by the shipped seeds' corrected 8-family
    # router. Its _Engine takes a SMALLER knob set than the v3 router this
    # SECTION 3 was written against and has no **kwargs, so the v3-only knobs
    # (swap_radius, dense_passes, age_cap, age_weight, stall_weight,
    # unlock_bias) would raise TypeError. Filter each attempt down to the
    # knobs ROUTER_POLICY actually defines, then de-duplicate what is left so
    # plan_moves still receives a real portfolio instead of N copies of one
    # config. Nothing else in SECTION 3 is touched.
    _KNOBS = set(ROUTER_POLICY) | {"max_rounds"}
    _seen_cfg, _kept = set(), []
    for _cfg in hot_attempts:
        _c = dict((k, v) for k, v in _cfg.items() if k in _KNOBS)
        _key = tuple(sorted(_c.items()))
        if _key not in _seen_cfg:
            _seen_cfg.add(_key)
            _kept.append(_c)
    hot_attempts = tuple(_kept)
    # ---- end adapter ----------------------------------------------------

    timeline = [{"t": "prep", "ancillas": list(anc_ids)}]

    def route_floor(starts, goals, paths=None):
        f = max([site_dist(s, goals.get(q, s), rows, cols)
                 for q, s in starts.items()] or [0])
        if paths:
            f = max(f, max([sum(1 for x in p if x is not None)
                            for p in paths.values()] or [0]))
        return f

    def scaled_cost(scale):
        if scale == 1.0:
            return site_cost
        if scale <= 0.0:
            return {}
        return dict((s, w * scale) for s, w in site_cost.items())

    def rail_sites(rounds):
        out = set()
        for rnd in rounds:
            for _q, fr, to in rnd:
                if fr[0] == "S":
                    out.add(fr)
                if to[0] == "S":
                    out.add(to)
        return out

    def route_key(rounds):
        fresh = len(rail_sites(rounds) - used_s)
        return (len(rounds), fresh, len(rounds) + 0.08 * fresh)

    def note_rounds(rounds):
        used_s.update(rail_sites(rounds))

    def ends_at(starts, rounds, goals):
        tmp = apply_rounds(rounds, dict(starts))
        for q, s in goals.items():
            if tmp.get(q) != as_site(s):
                return False
        return True

    def route_options(starts, goals, paths=None, effort=False, max_rounds=None):
        floor = route_floor(starts, goals, paths)
        opts = []
        seen = set()

        def one(cost_scale, attempts):
            key = (cost_scale, id(attempts))
            if key in seen:
                return
            seen.add(key)
            kw = {}
            if max_rounds is not None:
                kw["max_rounds"] = max_rounds
            if opts:
                lim = max(1, min(len(x) for x in opts) - 1)
                kw["max_rounds"] = min(kw.get("max_rounds", lim), lim)
            got = plan_moves(starts, goals, static, rows, cols, paths=paths,
                             site_cost=scaled_cost(cost_scale), attempts=attempts, **kw)
            if ends_at(starts, got, goals):
                opts.append(got)

        for scale in (0.0, 1.0):
            try:
                one(scale, None)
            except RouteError:
                pass

        slack = (min(len(x) for x in opts) - floor) if opts else 10 ** 9
        if effort or slack > 6:
            for scale in (0.0, 0.5, 1.0):
                try:
                    one(scale, hot_attempts)
                except RouteError:
                    pass
            if effort:
                for scale in (0.0, 0.5, 1.0):
                    try:
                        one(scale, ROUTER_ATTEMPTS)
                    except RouteError:
                        pass
        return opts

    def commit(rounds):
        emit_moves(timeline, rounds)
        apply_rounds(rounds, live)
        note_rounds(rounds)
        return len(rounds)

    def squeeze_rounds(starts, goals, incumbent, paths=None, effort=False):
        if incumbent is None:
            return None
        best = incumbent
        floor = route_floor(starts, goals, paths)
        if len(best) <= floor:
            return best
        attempt_sets = [ROUTER_ATTEMPTS, hot_attempts]
        if effort:
            attempt_sets = [hot_attempts, ROUTER_ATTEMPTS]
        scales = (0.0, 1.0, 0.5) if not effort else (0.0, 0.5, 1.0)
        steps = (6, 3, 1) if not effort else (10, 6, 3, 1)
        for step in steps:
            while True:
                cap = len(best) - step
                if cap < floor:
                    break
                improved = False
                for attempts in attempt_sets:
                    for scale in scales:
                        try:
                            got = plan_moves(starts, goals, static, rows, cols,
                                             paths=paths, site_cost=scaled_cost(scale),
                                             attempts=attempts, max_rounds=cap)
                        except RouteError:
                            continue
                        if not ends_at(starts, got, goals):
                            continue
                        if len(got) < len(best) or (len(got) == len(best) and route_key(got) < route_key(best)):
                            best = got
                            improved = True
                            break
                    if improved:
                        break
                if not improved:
                    break
                if len(best) <= floor:
                    return best
        return best

    def transport(goals, paths=None, effort=False):
        opts = route_options(live, goals, paths=paths, effort=effort)
        if not opts:
            try:
                got = plan_moves(live, goals, static, rows, cols,
                                 paths=paths, site_cost=site_cost,
                                 attempts=ROUTER_ATTEMPTS)
            except RouteError as e:
                raise RouteError("no legal transport strategy for gap") from e
            got = squeeze_rounds(live, goals, got, paths=paths, effort=effort)
        else:
            got = min(opts, key=route_key)
            got = squeeze_rounds(live, goals, got, paths=paths, effort=effort)

        commit(got)

        # Hard safety crossover: if an aggressive search returned a legal move
        # stream but did not leave every gate-critical ion exactly on its target,
        # correct before emitting merge/gate phases.
        bad = {q: s for q, s in goals.items() if live.get(q) != as_site(s)}
        if bad:
            fix = plan_moves(live, bad, static, rows, cols,
                             site_cost=site_cost, attempts=ROUTER_ATTEMPTS)
            commit(fix)
        return len(got)

    round_sites = [geo["anc_sites"](t) for t in range(n_rounds)]
    round_pairs = [geo["required_pairs"](t) for t in range(n_rounds)]

    def _choose_split_pairs(t, pairs, sites):
        # Default split used by older plans: return to the host-side junction.
        default = [[dq, ["J", int(sites[aq][1]), int(sites[aq][2])]]
                   for dq, aq in pairs]

        # Build "where this data wants to be next" map.
        ref_goal = {}
        if t + 1 < n_rounds:
            for ndq, naq in round_pairs[t + 1]:
                ns = round_sites[t + 1][naq]
                ref_goal[ndq] = ("J", int(ns[1]), int(ns[2]))
        for dq, _aq in pairs:
            ref_goal.setdefault(dq, home[dq])

        # Candidate split sites: only host-adjacent junctions (always legal).
        all_cols = []
        col_idx = {}
        allowed_by_row = []
        for dq, aq in pairs:
            _k, r, c = sites[aq]
            cand = []
            if c < cols:
                cand.append(("J", int(r), int(c)))
            if c > 0:
                cand.append(("J", int(r), int(c - 1)))
            # De-dup while preserving deterministic order.
            uniq = []
            seen_local = set()
            for s in cand:
                if s not in seen_local:
                    uniq.append(s)
                    seen_local.add(s)
                if s not in col_idx:
                    col_idx[s] = len(all_cols)
                    all_cols.append(s)
            allowed_by_row.append(uniq)

        # If the shared candidate pool is too small, stay with safe default.
        if len(all_cols) < len(pairs):
            return default

        BIG = 10 ** 7
        cost = [[BIG] * len(all_cols) for _ in pairs]
        for i, (dq, aq) in enumerate(pairs):
            _k, r, c = sites[aq]
            canonical = ("J", int(r), int(c))
            for s in allowed_by_row[i]:
                j = col_idx[s]
                d_next = site_dist(s, ref_goal[dq], rows, cols)
                d_home = site_dist(s, home[dq], rows, cols)
                stay_bias = 0 if s == canonical else 1
                # Prioritize next-gap shortening, then cyclic drift to home.
                cost[i][j] = 100 * d_next + 8 * d_home + stay_bias

        assign = _hungarian(cost)
        if len(assign) != len(pairs):
            return default
        out = []
        used_targets = set()
        for i, j in enumerate(assign):
            if j < 0 or j >= len(all_cols) or cost[i][j] >= BIG:
                return default
            s = all_cols[j]
            if s in used_targets:
                return default
            used_targets.add(s)
            dq, _aq = pairs[i]
            out.append([dq, [s[0], int(s[1]), int(s[2])]])
        return out

    for t in range(n_rounds):
        sites = round_sites[t]
        pairs = round_pairs[t]

        dgoal = {}
        for dq, aq in pairs:
            _k, r, c = sites[aq]
            dgoal[dq] = ("J", r, c)
        assert len(set(sites.values()) | set(dgoal.values())) == n_sim

        if t == 0:
            transport(dgoal)
        else:
            goals_all = dict(sites)
            goals_all.update(dgoal)
            direct_floor = route_floor(live, goals_all)
            hot_gap = (t in (2, 4, 5)) or direct_floor >= 45

            candidates = []
            candidates.extend(route_options(live, goals_all, effort=hot_gap))

            outp, backp, n_out, n_back = {}, {}, 0, 0
            for dq, gsite in dgoal.items():
                outp[dq] = shortest_path(live[dq], home[dq], rows, cols, static)
                backp[dq] = shortest_path(home[dq], gsite, rows, cols, static)
                n_out = max(n_out, len(outp[dq]))
                n_back = max(n_back, len(backp[dq]))

            park = dict(sites)
            dry_opts = route_options(live, park, paths=outp, effort=hot_gap)
            dry_opts = sorted(dry_opts, key=route_key)[:2]

            for dry in dry_opts:
                if len(dry) - n_out - n_back >= 0:
                    mp = {
                        dq: outp[dq]
                        + [None] * (len(dry) - len(outp[dq]) - len(backp[dq]))
                        + backp[dq]
                        for dq in dgoal
                    }
                    candidates.extend(route_options(
                        live, park, paths=mp, effort=hot_gap,
                        max_rounds=len(dry) + n_back - 1))

                try:
                    after_dry = apply_rounds(dry, dict(live))
                    back_opts = route_options(after_dry, dgoal, effort=hot_gap)
                    for back in sorted(back_opts, key=route_key)[:2]:
                        combo = dry + back
                        if ends_at(live, combo, goals_all):
                            candidates.append(combo)
                except RouteError:
                    pass

            candidates = [c for c in candidates if ends_at(live, c, goals_all)]
            if not candidates:
                base_gap = plan_moves(live, goals_all, static, rows, cols,
                                      site_cost=site_cost, attempts=ROUTER_ATTEMPTS)
                best_gap = squeeze_rounds(live, goals_all, base_gap,
                                          paths=None, effort=hot_gap)
            else:
                best_gap = min(candidates, key=route_key)
                best_gap = squeeze_rounds(live, goals_all, best_gap,
                                          paths=None, effort=hot_gap)

            commit(best_gap)
            bad = {q: s for q, s in goals_all.items() if live.get(q) != as_site(s)}
            if bad:
                fix = plan_moves(live, bad, static, rows, cols,
                                 site_cost=site_cost, attempts=ROUTER_ATTEMPTS)
                commit(fix)

        # Alignment precheck before frozen merge emission.
        for dq, aq in pairs:
            if live.get(aq) != as_site(sites[aq]):
                fix = plan_moves(live, {aq: sites[aq]}, static, rows, cols,
                                 site_cost=site_cost, attempts=ROUTER_ATTEMPTS)
                commit(fix)
            _k, r, c = sites[aq]
            if live.get(dq) != ("J", r, c):
                fix = plan_moves(live, {dq: ("J", r, c)}, static, rows, cols,
                                 site_cost=site_cost, attempts=ROUTER_ATTEMPTS)
                commit(fix)

        split_pairs = _choose_split_pairs(t, pairs, sites)

        timeline.append({"t": "merge", "pairs": [[dq, aq] for dq, aq in pairs]})
        timeline.append({"t": "gate", "round": t})
        timeline.append({"t": "split", "pairs": split_pairs})

        # Keep simulator state aligned with emitted split targets so subsequent
        # inter-gate routing amortizes wrap-back work across the cycle.
        seen_split = set()
        for dq, tgt in split_pairs:
            st = as_site(tgt)
            if st in seen_split:
                raise RouteError("duplicate split target in emitted split round")
            seen_split.add(st)
            live[dq] = st

        if t == n_rounds - 1:
            timeline.append({"t": "measure", "ancillas": list(anc_ids)})


    # ---- cycle boundary (shipped-seed rule, added by the v3 salvage) -----
    # v3 was evolved when EVERY ion had to end on its exact site, so its
    # SECTION 3 ended with transport(home) -- a full walk home for all 140
    # ions. The current evaluator only requires each ancilla SPECIES to
    # restore its own occupied SET. This is the shipped seeds' block verbatim.
    _goals = dict((q, home[q]) for q in geo["data_ids"])
    _half = len(anc_ids) // 2
    for _species in (anc_ids[:_half], anc_ids[_half:]):
        _want = sorted(home[q] for q in _species)
        if sorted(live[q] for q in _species) == _want:
            continue                     # set already restored -- no travel
        _free = list(_want)
        for _q in sorted(_species,
                         key=lambda x: (-min(site_dist(live[x], s, rows, cols)
                                             for s in _want), x)):
            _pick = min(_free, key=lambda s: (site_dist(live[_q], s, rows,
                                                        cols), s))
            _free.remove(_pick)
            _goals[_q] = _pick
    try:
        transport(_goals, effort=True)
    except RouteError:
        commit(plan_moves(live, _goals, static, rows, cols,
                          site_cost=site_cost, attempts=ROUTER_ATTEMPTS))
    _bad = dict((q, s) for q, s in _goals.items() if live.get(q) != as_site(s))
    if _bad:
        commit(plan_moves(live, _bad, static, rows, cols,
                          site_cost=site_cost, attempts=ROUTER_ATTEMPTS))
    # ---- end cycle boundary ---------------------------------------------

    return {
        "grid": {"rows": rows, "cols": cols},
        "layout": geo["layout"],
        "timeline": timeline,
    }
# EVOLVE-BLOCK-END


def run_experiment(spec, **kwargs):
    """Entrypoint called by evaluate.py; returns the plan dict."""
    return build_embedding_and_shuttle(spec)
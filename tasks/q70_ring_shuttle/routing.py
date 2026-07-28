"""routing.py -- shared, NON-EVOLVED parallel-transport router for
``q70_ring_shuttle``.  Import it; do not copy it into an EVOLVE-BLOCK.

WHY THIS EXISTS
---------------
A candidate's timeline is a sequence of parallel transport rounds, and a round
costs the same whether one ion moves or 140.  The only quantity that matters is
therefore how many ROUNDS a plan needs -- never how many ion-steps.  Every
hand-written emission style tried on this task (per-axis passes, X-then-Z,
hide/slide/emerge barriers) routes groups SEQUENTIALLY: mean occupancy ~30 of
140 ions per round, and 1.7-2.8x the plan's own distance floor.

This module turns "these ions need to get to these sites" into a near-minimal
number of LEGAL parallel rounds, so a candidate can spend its creativity on the
GEOMETRY -- where ions live, which ring maps to which motion -- and get the
round packing for free.  Measured on the two shipped seeds, keeping their
layouts and their per-gate ion->site assignment and replacing only the movement
emission:

    initial_evolved  566 -> 419 rounds (1.72x -> 1.27x floor), score +1.71 -> +2.21
    initial_folded   700 -> 455 rounds (2.10x -> 1.37x floor), score +1.30 -> +2.10

LEGALITY GUARANTEED (identical rules to ``evaluate.compile_plan``)
-----------------------------------------------------------------
1. every move traverses exactly ONE primitive edge:
       S(r,c)-J(r,c) | J(r,c)-S(r,c+1) | J(r,c)-U(r,c) | J(r,c)-D(r,c)
       | D(r,c)-U(r+1,c)
2. one ion per site at all times; an ion moves at most once per round;
3. a target held by an ion that is NOT moving in the same round is a collision,
   but a target VACATED by an ion that IS moving in the same round is legal.
   The scheduler exploits this hard: dense trains pipeline at full speed and a
   whole closed-loop rotation advances in ONE round;
4. two ions never swap head-on through one edge;
5. sites in ``blocked`` / ``occupied_static`` (beacons 140-209, reservoir
   210-219, or any ion the caller wants frozen) are never entered.

``check_rounds`` re-verifies all five on the produced rounds.

PUBLIC API
----------
``plan_moves(starts, targets, blocked, rows=, cols=, ...) -> rounds``
    Let the router find the routes itself.  ``starts`` / ``targets`` are
    ``{qid: site}``; a site is ``("S"|"J"|"U"|"D", row, col)`` (lists work
    too).  An ion missing from ``targets`` -- or whose target equals its start
    -- HOLDS its site: it will not travel, but traffic may shove it aside and
    it is guaranteed back before the returned rounds end.
      Useful keywords: ``avoid`` (sites that cost extra to enter),
      ``site_cost`` ({site: extra} for finer control), ``paths`` (explicit
      routes for a subset of ions), ``attempts`` (the search portfolio).

``route(paths, occupied_static, starts=None, rows=, cols=) -> rounds``
    Schedule caller-supplied routes.  ``paths`` is ``{qid: [site, ...]}``
    EXCLUDING the start site; a ``None`` entry means "wait one round here",
    which is how you make a group leave early and come back late so its rounds
    hide inside another group's journey.  ``occupied_static`` may be the
    caller's whole ``{site: qid}`` occupancy map (starts are read off it and
    every ion not in ``paths`` becomes a static obstacle) or a plain iterable
    of blocked sites (then pass ``starts``).

``rounds`` is ``[[(qid, from_site, to_site), ...], ...]`` -- one inner list per
parallel transport round.

``to_timeline(rounds)`` -> ``[{"t": "move", "moves": [[qid, from, to], ...]}]``
    the evaluator's phase format, plain JSON data.
``emit_moves(timeline, rounds)`` appends them to a timeline in place.
``apply_rounds(rounds, positions)`` advances a ``{qid: site}`` map.
``check_rounds(rounds, positions, blocked, rows, cols)`` replays and asserts.
``ShuttleRouter(rows, cols, positions, static_ids=...)`` keeps live positions
    so a whole SEC can be written as a sequence of ``r.goto({qid: site})``.

Helpers: ``site_dist`` (exact obstacle-free distance -- the same lower bound
the evaluator uses for its per-gap floor), ``shortest_path``, ``astar_path``,
``bfs_field``, ``neighbors``, ``is_edge``.

HOW IT WORKS
------------
Prioritised planning plus a stall-tolerant, rotation-aware round packer.

1. ROUTES.  Ions are planned one at a time, FARTHEST FIRST, by A* with the
   exact obstacle-free distance as heuristic (a few hundred node expansions
   each).  Each planned ion tells the next which directed edges it has claimed;
   traversing one of those BACKWARDS costs ``opposed_cost`` extra.  Same-way
   sharing stays free, so dense trains still pipeline down one corridor while
   genuine counter-flow is priced onto a parallel lane.  That is what discovers
   "wrap-lane" style detours without anybody hard-coding them.
2. PACKING.  Each round every active ion proposes its next edge; proposals are
   resolved farthest-first.  They form a functional graph (ion -> the ion
   sitting on its target).  Chains that end at a free site all advance
   together; CYCLES of length >= 3 rotate in a single round (rule 3 above);
   2-cycles are head-on swaps and are broken.  This is where the parallelism
   comes from -- the makespan tracks the single longest journey, because the
   critical ion never yields.
3. UNBLOCKING.  An ion blocked for ``push_after`` rounds makes its blocker STEP
   ASIDE -- a short BFS to the nearest free parking spot that no planned route
   crosses, cascading through blockers that are themselves boxed in.  Aside
   moves are deliberately SHORT (``aside_radius``, default 2): long evacuations
   disturb more traffic than they free.  If the blocker cannot or may not move
   again, the stalled ion instead REPLANS around it, which is what breaks
   mutual push livelocks.
4. PORTFOLIO.  The packer is a heuristic, so ``plan_moves`` runs a small fixed
   set of complementary settings (``DEFAULT_ATTEMPTS``) and keeps the SHORTEST
   legal schedule, stopping early if one reaches the distance floor.  Each
   attempt costs milliseconds and its round budget is capped relative to that
   floor, so a wedged setting fails fast instead of hanging.  Soft costs are
   advisory: if every attempt fails under them they are relaxed and retried, so
   a preference can never turn a routable request into an error.

Everything is deterministic (all tie-breaks are on sorted (kind, row, col)
tuples and qids), pure Python, no dependencies, no randomness.  A full 8-gap
SEC plan builds in well under a minute.

TYPICAL USE
-----------
    import routing as rt

    static = set(posn[q] for q in beacons_and_reservoir)     # never move
    live   = {q: posn[q] for q in range(140)}                # data + ancillas

    rounds = rt.plan_moves(live, goals, static, rows=ROWS, cols=COLS,
                           site_cost=toll)
    rt.emit_moves(timeline, rounds)
    rt.apply_rounds(rounds, live)

Two things worth doing in the caller -- both worth real score on this task:

* Do not leave an ion parked on a junction another group has to cross.  Give it
  a real target out of the way (its layout site) and bring it back with an
  explicit ``paths`` entry padded with ``None`` waits, so its rounds overlap the
  other group's journey instead of forming a phase of their own.
* Charge a ``site_cost`` for rail sections (S sites) the plan does not already
  occupy: every fresh S site is a new trap zone in the footprint metric, so a
  shortcut across an otherwise-unused row can cost more score than the rounds
  it saves.  On the shipped layouts, ~28 for an unused S site on a data row and
  ~8 on an ancilla row measured best.
"""

from collections import deque

__all__ = [
    "RouteError",
    "as_site", "is_edge", "neighbors", "site_dist", "bfs_field",
    "shortest_path", "astar_path",
    "route", "plan_moves", "to_timeline", "emit_moves", "apply_rounds",
    "check_rounds", "ShuttleRouter", "DEFAULT_ATTEMPTS",
]


class RouteError(Exception):
    """Raised when a routing request cannot be satisfied legally."""


# ---------------------------------------------------------------------------
# Grid primitives (must agree with evaluate.py exactly)
# ---------------------------------------------------------------------------

_KIND_ORDER = {"S": 0, "J": 1, "U": 2, "D": 3}
_INF = float("inf")


def as_site(x):
    """Normalise ``["S", r, c]`` / ``("S", r, c)`` to a hashable tuple."""
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
    if ka == "D" and kb == "U":
        return rb == ra + 1 and ca == cb
    if ka == "U" and kb == "D":
        return rb == ra - 1 and ca == cb
    return False


def neighbors(site, rows, cols):
    """All sites one primitive edge away from ``site`` inside the grid."""
    k, r, c = site
    out = []
    if k == "S":
        if 0 <= c < cols:
            out.append(("J", r, c))
        if 0 <= c - 1 < cols:
            out.append(("J", r, c - 1))
    elif k == "J":
        out.append(("S", r, c))
        if c + 1 < cols:
            out.append(("S", r, c + 1))
        out.append(("U", r, c))
        out.append(("D", r, c))
    elif k == "U":
        out.append(("J", r, c))
        if r - 1 >= 0:
            out.append(("D", r - 1, c))
    elif k == "D":
        out.append(("J", r, c))
        if r + 1 < rows:
            out.append(("U", r + 1, c))
    return out


def _anchor(site):
    """(row, key) of an S/J site.  key = 2c for S(r,c), 2c+1 for J(r,c).

    Along one row the graph is the path S(r,0) J(r,0) S(r,1) J(r,1) ..., so
    horizontal distance inside a row is just |key difference|.
    """
    k, r, c = site
    return (r, 2 * c) if k == "S" else (r, 2 * c + 1)


def _dist_sj(a, b):
    """Exact obstacle-free distance between two S/J sites.

    Rows are joined only through the 3-step ladder J(r,c)-D(r,c)-U(r+1,c)-
    J(r+1,c), available at every column, so a cross-row journey costs
    3*|dr| plus the horizontal key distance (or 2 when both endpoints are the
    same S column, because you must step onto a junction and back).
    For S-S pairs this reproduces evaluate.py's ``_dist_lb`` exactly.
    """
    ra, ka = _anchor(a)
    rb, kb = _anchor(b)
    if ra == rb:
        return abs(ka - kb)
    if ka == kb and ka % 2 == 0:      # same S column: nearest junction is 1 away
        h = 2
    else:
        h = abs(ka - kb)
    return 3 * abs(ra - rb) + h


def _portals(site, rows):
    """S/J entry points of a site with their step cost (identity for S/J)."""
    k, r, c = site
    if k in ("S", "J"):
        return ((site, 0),)
    if k == "U":
        if r - 1 >= 0:
            return ((("J", r, c), 1), (("J", r - 1, c), 2))
        return ((("J", r, c), 1),)
    if r + 1 < rows:
        return ((("J", r, c), 1), (("J", r + 1, c), 2))
    return ((("J", r, c), 1),)


def site_dist(a, b, rows=10 ** 9, cols=10 ** 9):
    """Exact obstacle-free graph distance between any two sites."""
    a = as_site(a)
    b = as_site(b)
    if a == b:
        return 0
    if is_edge(a, b):
        return 1
    best = _INF
    for pa, ca in _portals(a, rows):
        for pb, cb in _portals(b, rows):
            d = ca + cb + _dist_sj(pa, pb)
            if d < best:
                best = d
    return best


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
    """Cheapest route ``start`` -> ``goal`` (EXCLUDING start) under soft costs.

    * ``blocked``      : hard obstacles.
    * ``site_cost``    : {site: extra} charged for ENTERING that site.  Use it
                         to keep traffic off other ions' rest sites, and off
                         rail sections the plan does not otherwise occupy (each
                         fresh S site is a new trap zone in the footprint
                         metric, so a small cost there buys real score).
    * ``edge_use``     : {(a, b): n} directed edges already claimed by
                         previously planned ions.  Traversing (b, a) -- i.e.
                         HEAD-ON against them -- costs ``opposed_cost`` each.
                         Travelling the SAME way is free, so dense trains still
                         share a corridor and pipeline; only counter-flow is
                         pushed onto a parallel lane.  This is what discovers
                         "wrap lane" style detours without hard-coding them.

    A* with the exact obstacle-free distance as heuristic, so it expands only
    a few hundred nodes per call.  Deterministic.
    """
    import heapq
    start = as_site(start)
    goal = as_site(goal)
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
    """List of sites from ``start`` to ``goal``, EXCLUDING ``start``."""
    start = as_site(start)
    goal = as_site(goal)
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
        if nxt is None:                          # pragma: no cover
            raise RouteError(f"field descent stuck at {cur}")
        path.append(nxt)
        cur = nxt
    return path


# ---------------------------------------------------------------------------
# Scheduling engine
# ---------------------------------------------------------------------------

_WAIT = "__wait__"


class _Plan(object):
    """Per-ion movement intent: either a distance FIELD or an explicit PATH."""

    __slots__ = ("field", "goal", "seq", "idx", "override", "nleft")

    def __init__(self, field=None, goal=None, seq=None):
        self.field = field
        if goal is None and seq:
            for s in reversed(seq):
                if s is not None:
                    goal = s
                    break
        self.goal = goal
        self.seq = seq
        self.idx = 0
        self.override = []
        # real (non-wait) steps still owed -- the priority number for a path
        # plan.  Padding an ion's route with waits must NOT promote it.
        self.nleft = 0 if seq is None else sum(1 for s in seq if s is not None)


class _Engine(object):

    def __init__(self, rows, cols, pos, blocked, plans,
                 push_after=1, max_rounds=20000, max_pushes=64,
                 aside_radius=2, aside_preview=4, aside_depth=3, max_aside=6,
                 replan_after=6, replan_slack=10, site_use=None,
                 site_cost=None):
        self.rows = rows
        self.cols = cols
        self.pos = dict(pos)
        self.blocked = set(blocked)
        self.plans = plans
        self.push_after = max(1, int(push_after))
        self.max_rounds = int(max_rounds)
        self.max_pushes = int(max_pushes)
        self.aside_radius = int(aside_radius)
        self.aside_preview = int(aside_preview)
        self.aside_depth = int(aside_depth)
        # how many planned routes cross each site: a side-step should park
        # where nobody is coming, NOT in the nearest U/D leg (legs are ladder
        # rungs -- parking there causes head-on jams in the vertical passage)
        self.site_use = site_use or {}
        self.site_cost = site_cost or {}
        self.max_aside = int(max_aside)
        self.replan_after = int(replan_after)
        self.replan_slack = int(replan_slack)
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

    # -- helpers ---------------------------------------------------------
    def _neigh(self, s):
        n = self._nb.get(s)
        if n is None:
            n = tuple(sorted(neighbors(s, self.rows, self.cols),
                             key=lambda x: (_KIND_ORDER[x[0]], x[1], x[2])))
            self._nb[s] = n
        return n

    def _remaining(self, q):
        p = self.plans[q]
        base = len(p.override)
        if p.seq is not None:
            return base + p.nleft
        d = p.field.get(self.pos[q])
        if d is None:
            raise RouteError(
                f"ion {q} at {self.pos[q]} cannot reach its goal {p.goal}")
        return base + d

    def _done(self, q):
        p = self.plans[q]
        if p.override:
            return False
        if p.seq is not None:
            return p.idx >= len(p.seq)
        return self.pos[q] == p.goal

    def _descend(self, q):
        """Candidate next sites (all on a shortest route), best first."""
        p = self.plans[q]
        cur = self.pos[q]
        d = p.field.get(cur)
        if d is None or d == 0:
            return ()
        out = []
        for n in self._neigh(cur):
            if p.field.get(n, _INF) == d - 1:
                out.append(n)
        # free targets first, then targets held by a mobile ion (a chain or a
        # rotation may still let us in this round).
        out.sort(key=lambda n: (0 if n not in self.occ else 1,
                                _KIND_ORDER[n[0]], n[1], n[2]))
        return tuple(out)

    def _propose(self, q):
        p = self.plans[q]
        if p.override:
            return p.override[0]
        if p.seq is not None:
            if p.idx >= len(p.seq):
                return None
            nxt = p.seq[p.idx]
            return _WAIT if nxt is None else nxt
        cands = self._descend(q)
        return cands[0] if cands else None

    # -- one round -------------------------------------------------------
    def _step(self):
        active = [q for q in self.pos if not self._done(q)]
        if not active:
            return None
        rank_src = sorted(active, key=lambda q: (-self._remaining(q), q))
        rank = {q: i for i, q in enumerate(rank_src)}

        desired = {}
        waiting = []
        for q in rank_src:
            t = self._propose(q)
            if t is _WAIT:
                waiting.append(q)
            elif t is not None:
                desired[q] = t

        # -- avoid gratuitous head-on pairs by re-picking an alternative ---
        for q in rank_src:
            t = desired.get(q)
            if t is None:
                continue
            o = self.occ.get(t)
            if o is None or o not in desired or desired[o] != self.pos[q]:
                continue
            loser = q if rank[q] > rank[o] else o
            p = self.plans[loser]
            if p.override or p.seq is not None:
                continue
            for alt in self._descend(loser):
                if alt != desired[loser] and self.occ.get(alt) != o:
                    desired[loser] = alt
                    break

        # -- target contention: highest priority claims the site -----------
        winner = {}
        for q in rank_src:
            t = desired.get(q)
            if t is None:
                continue
            if t in self.blocked:
                continue
            if t not in winner:
                winner[t] = q
        sel = set(winner.values())

        # -- propagate "blocked": chains only move if their head can -------
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

        # -- break 2-cycles (head-on swaps through one edge) ---------------
        again = True
        while again:
            again = False
            for q in sorted(sel, key=lambda x: rank[x]):
                if q not in sel:
                    continue
                o = dep.get(q)
                if o is not None and o in sel and dep.get(o) == q:
                    drop = q if rank[q] > rank[o] else o
                    _kill(drop)
                    again = True
                    break

        # -- commit --------------------------------------------------------
        moves = []
        for q in sorted(sel, key=lambda x: rank[x]):
            moves.append((q, self.pos[q], desired[q]))
        for q, fr, _to in moves:
            del self.occ[fr]
        for q, _fr, to in moves:
            self.occ[to] = q
            self.pos[q] = to
            p = self.plans[q]
            if p.override:
                p.override.pop(0)
            elif p.seq is not None:
                p.idx += 1
                p.nleft -= 1
        for q in waiting:
            self.plans[q].idx += 1

        moved = set(q for q, _, _ in moves)
        for q in active:
            if q in moved or q in waiting:
                self.stall[q] = 0
            else:
                self.stall[q] += 1

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
                # No descending move at all: everything one step ahead is a
                # static obstacle.  Nothing to push; the field will have to be
                # re-descended once neighbours clear.
                continue
            o = self.occ.get(t)
            if o is None or o in moved or o in pushed:
                continue
            if self.plans[o].override:
                continue
            if rank.get(o, len(rank)) < rank[q]:
                # blocker outranks us and is itself stuck -> push ITS blocker
                continue
            keep_clear = set(self._preview(q, self.aside_preview))
            if self._force_aside(o, {self.pos[q]}, keep_clear, pushed, 0):
                budget -= 1
            elif self.stall[q] >= self.replan_after and \
                    self._replan(q, self.pos[o]):
                # Last resort: the blocker will not move again, so go round it.
                # This is what breaks mutual push livelocks between two ions
                # whose goals sit next to each other on a one-wide rail.
                budget -= 1

    def _replan(self, q, forbid):
        """Re-route ion ``q`` to its goal avoiding the site ``forbid``.

        Refused when the detour is much longer than what is left of the
        current route -- a big reroute usually costs more than waiting.
        """
        p = self.plans[q]
        if p.seq is None or p.goal is None or p.override or p.goal == forbid:
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
        if o in pushed or self.plans[o].override:
            return False
        if self.aside_count.get(o, 0) >= self.max_aside:
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
            if o2 is None:
                continue
            if self._force_aside(o2, deeper, keep_clear, pushed, depth + 1):
                return True
        return False

    def _preview(self, q, k):
        """The next ``k`` sites ion q would like to occupy (best-effort)."""
        p = self.plans[q]
        out = []
        if p.seq is not None:
            for s in p.seq[p.idx:p.idx + k]:
                if s is not None:
                    out.append(s)
            return out
        cur = self.pos[q]
        for _ in range(k):
            d = p.field.get(cur)
            if not d:
                break
            nxt = None
            for n in self._neigh(cur):
                if p.field.get(n, _INF) == d - 1:
                    nxt = n
                    break
            if nxt is None:
                break
            out.append(nxt)
            cur = nxt
        return out

    def _step_aside(self, o, avoid, keep_clear):
        """Shove ion ``o`` out of the way.

        An S site's only two neighbours are the junctions the pusher itself
        needs, so a one-step shove is often impossible: this searches (BFS over
        currently FREE sites) for the nearest parking spot outside the pusher's
        near-term corridor, preferring dead-end U/D legs and avoiding other
        ions' goal sites.  ``avoid`` is hard (never entered), ``keep_clear`` is
        soft (may be traversed, never parked on).
        """
        here = self.pos[o]
        # never strand an ion somewhere its own route cannot resume from
        fld = self.plans[o].field
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
                if fld is not None and n not in fld:
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
            if best is not None and best[0][0] == 0 and best[0][1] == 0                     and best[0][3] <= d + 1:
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
        if p.seq is not None:
            back = list(reversed(path[:-1])) + [here]
            p.seq = p.seq[:p.idx] + path + back + p.seq[p.idx:]
            p.nleft += len(path) + len(back)
        else:
            p.override = path
        return True

    def diagnose(self, k=6):
        """Human-readable report on the ions that have not finished."""
        stuck = sorted(q for q in self.pos if not self._done(q))
        bits = []
        for q in stuck[:k]:
            p = self.plans[q]
            want = self._propose(q)
            blk = self.occ.get(want) if isinstance(want, tuple) else None
            bits.append(
                f"{q}@{self.pos[q]}->{p.goal if p.seq is None else 'path'}"
                f" wants {want}"
                + (f" (held by {blk}@{self.pos.get(blk)})" if blk is not None
                   else " (blocked/none)"))
        return f"{len(stuck)} ions short of their goal: " + "; ".join(bits)

    # -- driver ----------------------------------------------------------
    def run(self):
        rounds = []
        idle = 0
        while True:
            if len(rounds) > self.max_rounds:
                raise RouteError(
                    f"router exceeded {self.max_rounds} rounds; "
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


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def _infer_grid(sites, rows, cols):
    if rows is not None and cols is not None:
        return int(rows), int(cols)
    mr = mc = 0
    for s in sites:
        if s[1] > mr:
            mr = s[1]
        if s[2] > mc:
            mc = s[2]
    return (int(rows) if rows is not None else mr + 1,
            int(cols) if cols is not None else mc + 1)


def plan_moves(starts, targets, blocked=(), rows=None, cols=None, paths=None,
               avoid=(), site_cost=None, **opts):
    """Route every ion in ``starts`` to its site in ``targets``.

    Parameters
    ----------
    starts   : {qid: site}   current position of every MOBILE ion.
    targets  : {qid: site}   desired position.  A qid absent from ``targets``
                             (or whose target equals its start) HOLDS its site:
                             it will not travel, but traffic may shove it aside
                             and it is guaranteed to be back before the last
                             round returned.  NOTE: an ion parked on a busy
                             junction is expensive to hold -- give it a real
                             target out of the way instead.
    blocked  : iterable of sites permanently occupied (beacons, reservoir, any
               ion the caller froze).  Never entered.
    rows,cols: grid dimensions.  PASS THEM -- they bound where ions may go and
               must match the plan's declared grid.  If omitted they are
               inferred tightly from the sites mentioned, which needlessly
               forbids empty margin columns.
    paths    : optional {qid: [site|None, ...]} explicit routes that OVERRIDE
               the field for those ions (``None`` = wait one round).  Use this
               to make a group leave early and come back late -- e.g. duck out
               of a corridor, idle while the corridor is used, then return --
               so its round budget hides inside another group's journey.
    avoid    : SOFT obstacles -- sites that cost ``soft_cost`` (default 8)
               extra to enter and that a side-step will not park on.  Not hard
               obstacles: a route may still cross one when there is no
               alternative.  This is the lever for "keep the ancillas off the
               data/beacon rows": pass the parked ions' rest sites and the
               routes stay on the corridors that are actually free.
    site_cost: {site: extra} for finer control than ``avoid`` -- e.g. a small
               cost on rail sections the plan does not otherwise occupy, since
               every fresh S site is a new trap zone in the footprint metric.
               Merged on top of ``avoid``.

    Returns ``[[(qid, from, to), ...], ...]``.
    """
    starts = {int(q): as_site(s) for q, s in starts.items()}
    targets = {int(q): as_site(s) for q, s in (targets or {}).items()}
    paths = {int(q): [None if s is None else as_site(s) for s in p]
             for q, p in (paths or {}).items()}
    blocked = set(as_site(s) for s in blocked)
    rows, cols = _infer_grid(
        list(starts.values()) + list(targets.values()) + list(blocked)
        + [s for p in paths.values() for s in p if s is not None], rows, cols)

    seen = {}
    for q, g in targets.items():
        if q in paths:
            continue
        if g in seen:
            raise RouteError(f"ions {seen[g]} and {q} share the target {g}")
        seen[g] = q
        if g in blocked:
            raise RouteError(f"ion {q} targets a blocked site {g}")

    cache = opts.pop("field_cache", None)
    if cache is None:
        cache = {}
    soft_cost = float(opts.pop("soft_cost", 8.0))
    costs = dict((as_site(x), soft_cost) for x in avoid)
    for x, w in (site_cost or {}).items():
        costs[as_site(x)] = float(w)
    attempts = opts.pop("attempts", None)
    if attempts is None:
        attempts = DEFAULT_ATTEMPTS
    floor = max([site_dist(s, targets.get(q, s), rows, cols)
                 for q, s in starts.items()] or [0])
    floor = max(floor, max([sum(1 for x in p if x is not None)
                            for p in paths.values()] or [0]))
    # Fail fast: a schedule many times the single-longest journey is a wedged
    # heuristic, not a slow one.  Bounding it keeps the portfolio cheap.
    opts.setdefault("max_rounds", max(64, 6 * floor + 120))
    # Soft costs are advisory.  If every attempt wedges under them, relax and
    # retry -- a preference must never turn a routable request into a failure.
    profiles = [costs]
    if costs:
        profiles.append(dict((s, w * 0.25) for s, w in costs.items()))
        profiles.append({})
    best = None
    last_err = None
    for prof in profiles:
        for cfg in attempts:
            kw = dict(opts)
            kw.update(cfg)
            if best is not None:
                kw["max_rounds"] = min(kw["max_rounds"], len(best))
            try:
                got = _plan_once(starts, targets, blocked, rows, cols, paths,
                                 prof, cache, kw)
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


# Deterministic portfolio: the packer is a heuristic, so try a few
# complementary settings and keep the SHORTEST schedule that is legal.
# Cheap (each attempt is milliseconds) and it removes most of the tail risk
# of any single heuristic wedging on a particular geometry.
DEFAULT_ATTEMPTS = (
    {"aside_radius": 2, "opposed_cost": 3.0},
    {"aside_radius": 3, "opposed_cost": 3.0},
    {"aside_radius": 2, "opposed_cost": 0.0},
    {"aside_radius": 3, "opposed_cost": 0.0},
    {"aside_radius": 2, "opposed_cost": 12.0},
    {"aside_radius": 3, "opposed_cost": 12.0},
)
# ``opposed_cost`` = how hard counter-flow is pushed onto a parallel lane;
# ``aside_radius`` = how far a shoved ion may be sent (small is much better:
# long evacuations disturb more traffic than they free).  Neither setting wins
# everywhere, hence the portfolio.  Pass attempts=({...},) for a single run.


def _plan_once(starts, targets, blocked, rows, cols, paths, costs, cache,
               opts):
    mode = opts.pop("mode", "path")
    opposed_cost = float(opts.pop("opposed_cost", 3.0))
    plans = {}

    for q, s in starts.items():
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
                raise RouteError(
                    f"ion {q} routes through blocked site {nxt}")
            cur = nxt
        plans[q] = _Plan(seq=list(paths[q]))

    todo = [q for q in starts if q not in paths]
    if mode == "field":
        fblock = frozenset(blocked | set(costs))
        for q in todo:
            s = starts[q]
            goal = targets.get(q, s)
            key = (goal, fblock)
            f = cache.get(key)
            if f is None:
                f = bfs_field(goal, rows, cols, fblock)
                if costs and s not in f:
                    hkey = (goal, frozenset(blocked))
                    f = cache.get(hkey)
                    if f is None:
                        f = bfs_field(goal, rows, cols, blocked)
                        cache[hkey] = f
                else:
                    cache[key] = f
            if s not in f:
                raise RouteError(f"ion {q}: no obstacle-free path {s} -> {goal}")
            plans[q] = _Plan(field=f, goal=goal)
    else:
        # PRIORITISED PLANNING: farthest ion first, each one telling the next
        # which directed edges it has claimed, so counter-flow is priced onto
        # a parallel lane instead of deadlocking a one-wide rail.
        nb_cache = {}
        edge_use = {}
        site_use = {}
        for q, p in plans.items():                 # caller-supplied routes too
            cur = starts[q]
            for nxt in p.seq:
                if nxt is None:
                    continue
                edge_use[(cur, nxt)] = edge_use.get((cur, nxt), 0) + 1
                site_use[nxt] = site_use.get(nxt, 0) + 1
                cur = nxt
        todo.sort(key=lambda q: (-site_dist(starts[q],
                                            targets.get(q, starts[q]),
                                            rows, cols), q))
        for q in todo:
            s = starts[q]
            goal = targets.get(q, s)
            if s == goal:
                plans[q] = _Plan(seq=[])
                continue
            p = astar_path(s, goal, rows, cols, blocked, costs,
                           edge_use, opposed_cost, nb_cache)
            cur = s
            for nxt in p:
                edge_use[(cur, nxt)] = edge_use.get((cur, nxt), 0) + 1
                site_use[nxt] = site_use.get(nxt, 0) + 1
                cur = nxt
            plans[q] = _Plan(seq=p)
        opts.setdefault("site_use", site_use)
    opts.setdefault("site_cost", costs)
    return _Engine(rows, cols, starts, blocked, plans, **opts).run()


def route(paths, occupied_static, starts=None, rows=None, cols=None,
          site_cost=None, **opts):
    """Schedule caller-supplied per-ion routes into parallel rounds.

    ``paths``           : {qid: [site, ...]} EXCLUDING the start site.  A
                          ``None`` entry means "wait one round here".
    ``occupied_static`` : either the caller's full ``{site: qid}`` occupancy
                          map -- starts are then read off it and every ion NOT
                          in ``paths`` becomes a static obstacle -- or a plain
                          iterable of blocked sites (then pass ``starts``).
    """
    paths = {int(q): [None if s is None else as_site(s) for s in p]
             for q, p in paths.items()}
    if isinstance(occupied_static, dict):
        occ_map = {as_site(s): q for s, q in occupied_static.items()}
        if starts is None:
            starts = {}
            for s, q in occ_map.items():
                if int(q) in paths:
                    starts[int(q)] = s
        blocked = set(s for s, q in occ_map.items() if int(q) not in paths)
    else:
        blocked = set(as_site(s) for s in occupied_static)
        if starts is None:
            raise RouteError(
                "route(): pass starts={qid: site} (or hand occupied_static "
                "the full {site: qid} occupancy map so starts can be read "
                "off it)")
    starts = {int(q): as_site(s) for q, s in starts.items()}
    missing = [q for q in paths if q not in starts]
    if missing:
        raise RouteError(f"route(): no start site for qid(s) {missing[:4]}")

    rows, cols = _infer_grid(
        list(starts.values()) + list(blocked)
        + [s for p in paths.values() for s in p if s is not None], rows, cols)

    # validate the supplied routes are edge-legal before scheduling
    for q, p in paths.items():
        cur = starts[q]
        for s in p:
            if s is None:
                continue
            if not is_edge(cur, s):
                raise RouteError(
                    f"route(): ion {q}: {cur} -> {s} is not one primitive step")
            if s in blocked:
                raise RouteError(
                    f"route(): ion {q} routes through blocked site {s}")
            cur = s

    plans = {q: _Plan(seq=list(p)) for q, p in paths.items()}
    site_use = {}
    for q, p in plans.items():
        for nxt in p.seq:
            if nxt is not None:
                site_use[nxt] = site_use.get(nxt, 0) + 1
    for q, s in starts.items():
        if q not in plans:
            plans[q] = _Plan(seq=[])
    opts.setdefault("site_use", site_use)
    if site_cost:
        opts.setdefault("site_cost",
                        dict((as_site(x), float(w))
                             for x, w in site_cost.items()))
    return _Engine(rows, cols, starts, blocked, plans, **opts).run()


def to_timeline(rounds):
    """Rounds -> the evaluator's ``move`` phases (plain JSON data)."""
    return [{"t": "move",
             "moves": [[int(q), [f[0], int(f[1]), int(f[2])],
                        [t[0], int(t[1]), int(t[2])]] for q, f, t in rnd]}
            for rnd in rounds if rnd]


def emit_moves(timeline, rounds):
    """Append ``rounds`` to ``timeline`` in the evaluator's phase format."""
    timeline.extend(to_timeline(rounds))
    return timeline


def apply_rounds(rounds, positions, occupied=None):
    """Advance ``positions`` (and ``occupied``) by replaying ``rounds``."""
    for rnd in rounds:
        for q, fr, _to in rnd:
            if occupied is not None:
                occupied.pop(as_site(fr), None)
        for q, _fr, to in rnd:
            positions[q] = as_site(to)
            if occupied is not None:
                occupied[as_site(to)] = q
    return positions


def check_rounds(rounds, positions, blocked, rows, cols):
    """Replay ``rounds`` under evaluate.compile_plan's rules; raise on any
    violation.  Returns the final ``{qid: site}`` map."""
    pos = {int(q): as_site(s) for q, s in positions.items()}
    blocked = set(as_site(s) for s in blocked)
    occ = {}
    for q, s in pos.items():
        if s in occ or s in blocked:
            raise RouteError(f"initial state: site {s} doubly occupied")
        occ[s] = q
    for ri, rnd in enumerate(rounds):
        if not rnd:
            raise RouteError(f"round {ri}: empty move phase (illegal)")
        seen = set()
        tgt = {}
        for q, fr, to in rnd:
            fr = as_site(fr)
            to = as_site(to)
            if q not in pos:
                raise RouteError(f"round {ri}: unknown ion {q}")
            if q in seen:
                raise RouteError(f"round {ri}: ion {q} moved twice")
            seen.add(q)
            if pos[q] != fr:
                raise RouteError(
                    f"round {ri}: ion {q} is at {pos[q]}, not {fr}")
            if not is_edge(fr, to):
                raise RouteError(
                    f"round {ri}: {fr} -> {to} is not one primitive step")
            if not (0 <= to[1] < rows and 0 <= to[2] < cols):
                raise RouteError(f"round {ri}: {to} is outside the grid")
            if to in blocked:
                raise RouteError(
                    f"round {ri}: ion {q} enters blocked site {to}")
            if to in tgt:
                raise RouteError(f"round {ri}: two ions target {to}")
            tgt[to] = q
        for to, q in tgt.items():
            holder = occ.get(to)
            if holder is not None and holder not in seen:
                raise RouteError(
                    f"round {ri}: target {to} held by stationary ion {holder}")
        for to, q in tgt.items():
            other = tgt.get(pos[q])
            if other is not None and pos[other] == to:
                raise RouteError(
                    f"round {ri}: ions {q} and {other} swap through one edge")
        for q in seen:
            del occ[pos[q]]
        for to, q in tgt.items():
            occ[to] = q
            pos[q] = to
    return pos


# ---------------------------------------------------------------------------
# Stateful convenience wrapper
# ---------------------------------------------------------------------------

class ShuttleRouter(object):
    """Keeps live positions so a candidate can write ``goto`` after ``goto``.

    >>> r = ShuttleRouter(rows, cols, posn, static_ids=range(140, 220))
    >>> rounds = r.goto({anc: new_site, ...})
    >>> emit_moves(timeline, rounds)
    """

    def __init__(self, rows, cols, positions, static_ids=(), **defaults):
        self.rows = int(rows)
        self.cols = int(cols)
        self.static_ids = set(int(q) for q in static_ids)
        self.pos = {int(q): as_site(s) for q, s in positions.items()}
        self.defaults = dict(defaults)
        self._cache = {}

    # -- introspection ---------------------------------------------------
    @property
    def mobile(self):
        return {q: s for q, s in self.pos.items() if q not in self.static_ids}

    @property
    def blocked(self):
        return set(s for q, s in self.pos.items() if q in self.static_ids)

    def site_of(self, q):
        return self.pos[int(q)]

    # -- movement --------------------------------------------------------
    def goto(self, targets, extra_blocked=(), **opts):
        """Move the listed ions to their sites; everyone else holds station.

        Returns the rounds AND advances this router's positions.
        """
        kw = dict(self.defaults)
        kw.update(opts)
        kw.setdefault("field_cache", self._cache)
        blocked = self.blocked | set(as_site(s) for s in extra_blocked)
        rounds = plan_moves(self.mobile, targets, blocked,
                            rows=self.rows, cols=self.cols, **kw)
        apply_rounds(rounds, self.pos)
        return rounds

    def follow(self, paths, **opts):
        """Schedule caller-supplied ``{qid: [site, ...]}`` routes."""
        kw = dict(self.defaults)
        kw.update(opts)
        occ = {s: q for q, s in self.pos.items()}
        rounds = route(paths, occ, rows=self.rows, cols=self.cols, **kw)
        apply_rounds(rounds, self.pos)
        return rounds

    def teleport(self, q, site):
        """Record a position change the router did not make (merge/split)."""
        self.pos[int(q)] = as_site(site)


# ---------------------------------------------------------------------------
# Self-test: ``python routing.py`` -- the four packing behaviours that matter
# ---------------------------------------------------------------------------

def _selftest():
    import time

    def run(label, starts, targets, rows, cols, floor, blocked=()):
        t0 = time.time()
        rnds = plan_moves(starts, targets, blocked, rows=rows, cols=cols)
        dt = (time.time() - t0) * 1000
        final = check_rounds(rnds, starts, blocked, rows, cols)
        for q, g in targets.items():
            assert final[q] == as_site(g), (label, q, final[q], g)
        print(f"  {label:<46} {len(rnds):>4} rounds "
              f"(floor {floor})  [{dt:.0f} ms]")
        return len(rnds)

    # a closed loop rotates in ONE round -- the whole point of the
    # "vacated by a mover is legal" rule
    cyc = [("J", 0, 3), ("D", 0, 3), ("U", 1, 3), ("J", 1, 3), ("S", 1, 4),
           ("J", 1, 4), ("U", 1, 4), ("D", 0, 4), ("J", 0, 4), ("S", 0, 4)]
    n = run("closed 10-loop rotation",
            {i: cyc[i] for i in range(10)},
            {i: cyc[(i + 1) % 10] for i in range(10)}, 3, 8, 1)
    assert n == 1, n

    n = run("dense train, 5 ions shift +4 columns",
            {i: ("S", 1, 1 + 4 * i) for i in range(5)},
            {i: ("S", 1, 5 + 4 * i) for i in range(5)}, 3, 28, 8)
    assert n == 8, n

    cs = [1, 5, 9, 13, 17]
    run("rail ring shift: 1 wrap ion vs 4 counter-flow",
        {i: ("S", 1, cs[i]) for i in range(5)},
        {i: ("S", 1, cs[(i + 1) % 5]) for i in range(5)}, 3, 28, 32)

    run("column ring shift: 7 ions, 1 wraps 12 rows",
        {i: ("S", 2 * i + 1, 5) for i in range(7)},
        {i: ("S", 2 * ((i + 1) % 7) + 1, 5) for i in range(7)}, 15, 12, 38)

    sites = [1, 2, 5, 6, 9, 10, 13, 14, 17, 18]
    run("two-species 10-ion row, +1 cell ring shift",
        {i: ("S", 1, sites[i]) for i in range(10)},
        {i: ("S", 1, 4 * (((i // 2) + 1) % 5) + 1 + (i % 2))
         for i in range(10)}, 3, 28, 32)

    # explicit routes with timed waits, plus a static obstacle
    rows, cols = 3, 12
    st = {0: ("S", 1, 1), 1: ("S", 1, 5)}
    occ = {("S", 1, 1): 0, ("S", 1, 5): 1, ("S", 0, 0): 99}
    p = {0: shortest_path(st[0], ("S", 1, 9), rows, cols),
         1: [None, None] + shortest_path(st[1], ("S", 1, 3), rows, cols)}
    r = route(p, occ, rows=rows, cols=cols)
    fin = check_rounds(r, st, [("S", 0, 0)], rows, cols)
    assert fin[0] == ("S", 1, 9) and fin[1] == ("S", 1, 3), fin
    print(f"  {'route() with explicit paths + None waits':<46} "
          f"{len(r):>4} rounds (floor 16)")
    print("routing.py self-test OK")


if __name__ == "__main__":
    _selftest()

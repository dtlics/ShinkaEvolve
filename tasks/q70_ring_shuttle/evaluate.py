"""Evaluator for q70_ring_shuttle.

Task: evolve the QCCD junction-grid embedding + shuttle plan for one syndrome
extraction cycle (SEC) of the Walking Cat Q70 = [[70,6,9]] BB memory block
(arXiv:2604.19481), with the paper's published Table X schedule FROZEN.

The candidate program returns a PLAN: a static layout (which trap site every ion
occupies) plus a timeline of primitive phases (parallel transport rounds,
merge/split rounds, gate layers, prep/measure batches) realizing one full SEC.
This evaluator:
  1. compiles + validates the plan exactly (grid legality, collision freedom,
     required data-ancilla alignments per schedule round, zone rules, the
     cycle boundary — exact sites for data/beacon/reservoir, per-species site
     SETS for the X and Z ancillas — anti-teleport merge/split restrictions);
  2. prices it in POC under the paper's moving-qubit model (Table III) and in
     NOISE EXPOSURE — the expected number of physical fault events per SEC,
     with p factored out (2q gate weight 1, prep/measure 1/10, idle 1/100 per
     POC, transport or merge/split round 1/2000 on ALL simulated qubits);
  3. verifies the assembled stim circuit is noiseless-deterministic;
  4. scores DETERMINISTICALLY on the PLAN-DEPENDENT costs only (v2 score,
     re-shaped after run q70ring_v1 measured the v1 headline term 85%-saturated
     by the frozen 490-point gate cost and the v1 zone weight too weak to keep
     genuine footprint discoveries alive in the archive):

        score = W_E * log2(SEED_VAR_EXPOSURE / (exposure - 504))
              + W_T * log2(SEED_T_CORE / t_core_poc)
              + W_Z * log2(SEED_ZONES / zones)

     The honest physical reliability readout — the p = 1e-4 operating-point
     LER shift, ceil(d_circ/2) * log10(total exposure ratio) per the paper's
     own extrapolation ansatz — is reported as the public metric
     `ler_shift_log10` and verified at certification; it is deliberately NOT
     the fitness, because its frozen part carries no search gradient. Real
     Monte-Carlo LER is measured OUT OF LOOP by certify.py — BP-OSD on this
     circuit costs seconds per shot, far too slow for the inner loop, and at
     sampleable p the frozen gate noise drowns the plan-dependent signal.

The candidate can ONLY move ions. Gates, observables, noise, decoding, and all
cost accounting are owned by this file; scoring runs through a pristine
re-import of this module from disk, so rebinding names in the running module
does not alter the score. Invalid plans are archived at the sentinel score
with text_feedback naming the exact broken rule.

PROVISIONAL CONSTANTS v2 (re-checked after each run — see README "Score"):
score weights W_E / W_T / W_Z, and the certification operating points /
budgets in certify-mode ler_sample.
"""

import argparse
import json
import math
import os

import numpy as np

from shinka.core import run_shinka_eval

# ----------------------------------------------------------------------------
# Frozen task constants
# ----------------------------------------------------------------------------
INVALID_SCORE = -2.0
LL_OVERHEAD_POC = 7.05  # loss/leakage checks, charged as fixed time (Table XI)
# Score weights, v2 (re-shaped after run q70ring_v1 — see README "Score"):
# all three inputs are PLAN-DEPENDENT parts only, so no term is saturated by
# frozen costs. Weights re-checked between runs, never mid-run.
W_E = 1.0              # plan-dependent noise exposure (transport+ms+idle)
W_T = 0.5              # core SEC time (transport/20 + op phases, no LL overhead)
W_Z = 1.0              # zones (trap footprint) — raised from 0.25 after v1
GRID_MAX_ROWS = 24
GRID_MAX_COLS = 96

# Certification-only knobs (NOT in the scoring hot path; used by certify.py /
# selfcheck.py --ler). PROVISIONAL.
P_CERT_DEFAULT = 2e-3
TARGET_ERRORS = 100
MIN_SHOTS = 256
MAX_SHOTS = 100_000
NC_SECS = 9            # number of SECs simulated (= d, paper convention)

# Seed anchors, calibrated against the shipped initial.py (the UNFOLDED seed)
# under THIS evaluator (selfcheck.py prints all; that seed scores 0.0 by
# construction; the folded seed initial_folded.py boots positive).
FROZEN_EXPOSURE = 70.0 * 7 + 14.0   # 504: gates + prep/meas, identical for all
# v4 anchors (2026-07-29): re-measured after the two grounded model
# corrections — the chip's junction is a zero-length crossing (a one-row hop
# costs 3 primitive steps, not 5) and the cycle boundary only requires each
# ancilla SPECIES to restore its own occupied SET. The anchor seed went
# 1012 -> 896 transport rounds under them; nothing else about it changed.
SEED_VAR_EXPOSURE = 65.10           # unfolded seed's exposure - FROZEN_EXPOSURE
SEED_T_CORE = 53.80                 # unfolded seed's t_core_poc (no LL overhead)
SEED_ZONES = 428                    # v3: distinct RAIL SECTIONS (S sites) of
                                    # the anchor seed. Was 1612 when transit
                                    # vertices were counted too — that both
                                    # mis-compared against the paper's ~288
                                    # sections by ~3.7x and paid the search to
                                    # funnel traffic through few corridors.
                                    # Unmoved by the v4 corrections.
SHIPPED_SEED_SCORE = 2.2542  # best seed handed to the run (initial_evolved).
                             # Reported per candidate as `gain_over_seed` so a
                             # run's own contribution is separable from what it
                             # was given. Update whenever the seeds change.
SEED_EXPOSURE_TOTAL = FROZEN_EXPOSURE + SEED_VAR_EXPOSURE   # for the public
                                                            # LER-shift readout

# Moving-qubit noise model (Table III of the paper)
def _p_gate(p):
    return p            # two-qubit gate
def _p_1q(p):
    return p / 10.0     # prep / measurement flip
def _p_idle(p):
    return p / 100.0    # idle, per POC
def _p_step(p):
    return p / 2000.0   # transport step, ALL qubits

# ----------------------------------------------------------------------------
# Code assets (pinned; the candidate cannot redefine any of this)
# ----------------------------------------------------------------------------
_TASK_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_TASK_DIR, "qecc", "q70.json"), encoding="utf-8") as _f:
    _CODE = json.load(_f)

L_RING = _CODE["l"]            # 7
M_RING = _CODE["m"]            # 5
N_HALF = L_RING * M_RING       # 35
N_DATA = 2 * N_HALF            # 70
A_EXPS = [tuple(e) for e in _CODE["A_exps"]]
B_EXPS = [tuple(e) for e in _CODE["B_exps"]]
SCHEDULE = [tuple(t) for t in _CODE["schedule"]]   # 7 rounds
N_ROUNDS = len(SCHEDULE)
HX_SUPPORTS = [tuple(r) for r in _CODE["hx"]]
HZ_SUPPORTS = [tuple(r) for r in _CODE["hz"]]
LOGICAL_XS = [tuple(r) for r in _CODE["logical_xs"]]
LOGICAL_ZS = [tuple(r) for r in _CODE["logical_zs"]]
K_LOG = _CODE["k"]

# Qubit id ranges
DATA0 = 0                       # 0..69   data (0..34 left block, 35..69 right)
XANC0 = 70                      # 70..104 X ancillas
ZANC0 = 105                     # 105..139 Z ancillas
BEAC0 = 140                     # 140..209 beacons (partner of data i = 140+i)
RES0 = 210                      # 210..219 reservoir
N_SIM = 140                     # simulated qubits (data + ancillas)
N_BEACON = 70
N_RESERVOIR = 10
N_PHYS = N_SIM + N_BEACON + N_RESERVOIR   # 220, matches Table XI


def _gidx(i, j):
    return (i % L_RING) * M_RING + (j % M_RING)


_EXPS = {"A": A_EXPS, "B": B_EXPS}


def required_pairs(round_idx):
    """Frozen per-round required (ancilla_qid, data_qid, gate_kind) triples."""
    fx, ix, fz, iz = SCHEDULE[round_idx]
    a1, b1 = _EXPS[fx][ix]
    a2, b2 = _EXPS[fz][iz]
    pairs = []
    for g in range(N_HALF):
        i, j = divmod(g, M_RING)
        if fx == "A":
            xd = _gidx(i + a1, j + b1)              # left block
            zd = N_HALF + _gidx(i - a2, j - b2)     # right block
        else:
            xd = N_HALF + _gidx(i + a1, j + b1)     # right block
            zd = _gidx(i - a2, j - b2)              # left block
        pairs.append((XANC0 + g, xd, "CX"))
        pairs.append((ZANC0 + g, zd, "CZ"))
    return pairs


_REQUIRED = [required_pairs(t) for t in range(N_ROUNDS)]
_REQUIRED_SETS = [frozenset((a, d) for a, d, _ in rp) for rp in _REQUIRED]

# ----------------------------------------------------------------------------
# Grid model  (CORRECTED 2026-07-29 against Fig. 62's well census -- see below)
# ----------------------------------------------------------------------------
# Every node is a POTENTIAL WELL, and one edge = one primitive transport step
# (a well-to-well hop).  The chip's own census, read off Fig. 62's vector
# content, is: exactly TWO wells per horizontal rail section, exactly TWO wells
# per vertical section, and NO well at a junction -- a junction is a
# ZERO-LENGTH CROSSING, so the (up to four) wells around it are mutually one
# step apart.  Nodes:
#   ("S", r, c)  left  well of horizontal section (r,c)
#   ("J", r, c)  right well of horizontal section (r,c)  [junction-adjacent]
#   ("D", r, c)  upper well of the vertical section below junction (r,c)
#   ("U", r, c)  lower well of the vertical section above junction (r,c)
# so junction (r,c) is surrounded by the four wells J(r,c), S(r,c+1), U(r,c),
# D(r,c), which form a CLIQUE, and the vertical section between rows r and r+1
# at column c carries exactly the two wells D(r,c), U(r+1,c).
# Edges (1 primitive transport step each):
#   inside a horizontal section : S(r,c)-J(r,c)
#   inside a vertical section   : D(r,c)-U(r+1,c)
#   across the junction (r,c)   : J(r,c)-S(r,c+1) ; J(r,c)-U(r,c) ;
#                                 J(r,c)-D(r,c)   ; S(r,c+1)-U(r,c) ;
#                                 S(r,c+1)-D(r,c) ; U(r,c)-D(r,c)
# Consequences: one COLUMN costs 2 steps (S(r,c)-J(r,c)-S(r,c+1) — the paper
# states this directly, p.78 "two shuttling steps to increment its column
# index", and that statement is also what forces TWO wells per section), and
# one ROW costs 3 steps (S(r,c)-D(r,c-1)-U(r+1,c-1)-S(r+1,c)). The rest-site
# metric is therefore d(S,S) = 2*dr + max(2*dc, 1) for dr > 0 and 2*dc for
# dr = 0 (exact away from the c = 0 boundary column, a lower bound on it),
# which _dist_lb reproduces.
#
# The evidence is the STRUCTURAL census, not a fit to the paper's transport
# total: that total alone is degenerate — a 5-step row hop with the paper's own
# routing reproduces Table XXVI's 424 for Q70, and so does a 3-step row hop
# with ~19% of overhead the reconstruction does not model. The well census is
# what settles it. (Do NOT cite p.85's "v = 10" here: in context v counts the
# 10 VERTICAL SECTIONS an 11-row block spans, not a step cost.)
#
# WHAT THIS REPLACED, and why it was wrong: the model used to make J both the
# section's second well AND the junction, with the legs reachable only through
# it.  That charged 5 steps for a one-row hop and 3*dr + 2*max(dc,1) in general
# -- i.e. it over-charged every vertical move by dr + 1 steps against the chip
# the paper actually draws.
# Chip rule: odd rows are optical (measure/reset allowed there only).


class PlanError(Exception):
    pass


def _site_ok(site, rows, cols):
    if (not isinstance(site, (list, tuple))) or len(site) != 3:
        return False
    kind, r, c = site
    if kind not in ("S", "J", "U", "D"):
        return False
    if not (isinstance(r, int) and isinstance(c, int)):
        return False
    if not (0 <= r < rows and 0 <= c < cols):
        return False
    return True


def _is_edge(a, b):
    """One primitive transport step (one well-to-well hop). See "Grid model"."""
    ka, ra, ca = a
    kb, rb, cb = b
    # inside a horizontal section, and the two ways out of its right well
    if ka == "S" and kb == "J":
        return (ra == rb) and (cb == ca or cb == ca - 1)
    if ka == "J" and kb == "S":
        return _is_edge(b, a)
    if ka == "J" and kb in ("U", "D"):
        return (ra, ca) == (rb, cb)
    if ka in ("U", "D") and kb == "J":
        return (ra, ca) == (rb, cb)
    # the junction is a ZERO-LENGTH crossing: the leg wells of junction (r,c)
    # touch the section-(r,c+1) left well directly, and each other directly
    if ka == "S" and kb in ("U", "D"):
        return (ra == rb) and (cb == ca - 1)
    if ka in ("U", "D") and kb == "S":
        return _is_edge(b, a)
    if ka == "D" and kb == "U":
        # rb == ra  : across junction (r,c);  rb == ra+1 : the vertical section
        return (ca == cb) and (rb == ra or rb == ra + 1)
    if ka == "U" and kb == "D":
        return _is_edge(b, a)
    return False


def _optical(site):
    return site[0] == "S" and site[1] % 2 == 1


# ----------------------------------------------------------------------------
# Plan compilation + validation
# ----------------------------------------------------------------------------

def compile_plan(plan):
    """Validate the plan exactly; return costs + the noise-segment timeline."""
    # Sanitize: force plain JSON data. This kills list subclasses / stateful
    # __iter__ tricks (the validated object IS the simulated object) and any
    # non-plain payload types.
    try:
        plan = json.loads(json.dumps(plan))
    except (TypeError, ValueError) as e:
        raise PlanError(f"plan must be plain JSON-serializable data: {e}")
    if not isinstance(plan, dict):
        raise PlanError("plan must be a dict")
    grid = plan.get("grid")
    layout = plan.get("layout")
    timeline = plan.get("timeline")
    if not isinstance(grid, dict) or not isinstance(layout, dict) or \
            not isinstance(timeline, list):
        raise PlanError("plan must have dict 'grid', dict 'layout', list 'timeline'")
    rows, cols = grid.get("rows"), grid.get("cols")
    if not (isinstance(rows, int) and isinstance(cols, int)):
        raise PlanError("grid.rows/cols must be ints")
    if not (2 <= rows <= GRID_MAX_ROWS and 2 <= cols <= GRID_MAX_COLS):
        raise PlanError(f"grid must fit within {GRID_MAX_ROWS}x{GRID_MAX_COLS}")

    groups = {"data": N_DATA, "x_anc": N_HALF, "z_anc": N_HALF,
              "beacon": N_BEACON, "reservoir": N_RESERVOIR}
    pos = {}
    order = ["data", "x_anc", "z_anc", "beacon", "reservoir"]
    base = {"data": DATA0, "x_anc": XANC0, "z_anc": ZANC0,
            "beacon": BEAC0, "reservoir": RES0}
    for gname in order:
        arr = layout.get(gname)
        if not isinstance(arr, list) or len(arr) != groups[gname]:
            raise PlanError(f"layout.{gname} must be a list of {groups[gname]} sites")
        for i, s in enumerate(arr):
            if not _site_ok(s, rows, cols):
                raise PlanError(f"layout.{gname}[{i}] = {s} is not a valid site")
            if s[0] != "S":
                raise PlanError(f"layout.{gname}[{i}] must rest on an 'S' rail site")
            pos[base[gname] + i] = tuple(s)
    if len(set(pos.values())) != len(pos):
        raise PlanError("layout places two ions on the same site")
    for i in range(N_DATA):
        if pos[DATA0 + i][1] != pos[BEAC0 + i][1]:
            raise PlanError(
                f"beacon {i} must sit in the same row as its partner data qubit {i}")

    init_pos = dict(pos)
    occupied = {s: q for q, s in pos.items()}
    merged = {}          # mobile qid -> host qid
    merged_hosts = {}    # host qid -> mobile qid
    gates_done = 0
    prepped = set()
    measured = set()
    measure_order = []
    transport_rounds = 0
    merge_rounds = 0
    prep_phases = 0
    measure_phases = 0
    idle_slots = 0.0     # sum over prep/measure phases of (N_SIM - batch size)
    # FOOTPRINT (v3): count distinct RAIL SECTIONS (S sites) only — that is the
    # unit the paper reports (~288 sections for the Q70 block). Counting J/U/D
    # transit vertices too (the v2 definition) both mis-compared against the
    # paper by ~3.7x AND rewarded funnelling all traffic through one corridor,
    # i.e. it paid for serialization. Vertices are still reported as a
    # diagnostic.
    zones = {s for s in pos.values() if s[0] == "S"}
    vertices = set(pos.values())
    segments = []        # ordered: ("transport", k) | ("ms", k) | ("gate", t) |
                         #          ("prep", [ancs]) | ("measure", [ancs])
    per_gap_rounds = []  # transport rounds between consecutive gate layers
    gap_count = 0
    gate_snapshots = [{q: (pos[q][1], pos[q][2]) for q in range(N_SIM)}]
    # snapshot 0 = layout; one more appended at every gate phase

    def seg_add(kind, k=1):
        if segments and segments[-1][0] == kind:
            segments[-1] = (kind, segments[-1][1] + k)
        else:
            segments.append((kind, k))

    anc_ids = set(range(XANC0, ZANC0 + N_HALF))
    # anti-teleport rule: merge/split rounds are free in POC (folded into the
    # gate cycle, paper p.85), so restrict them to their physical purpose —
    # at most one merge and one split per ion between consecutive gate layers,
    # and only pairs the NEXT gate layer actually requires may merge. They DO
    # carry transport-round noise (an "ms" segment) — physically a merge/split
    # takes 50-100us (Table XXV).
    merge_used = set()
    split_used = set()
    ion_steps = 0          # total (ion, round) movements — parallelism numerator
    low_occ_rounds = 0     # move phases in which fewer than 20 ions move
    wrap_rounds = 0        # transport rounds AFTER the first measure phase —
                           # the walk home that closes the cycle. Since v4 the
                           # ancillas do not owe one (their species set is what
                           # must repeat), so this should be the DATA ions'
                           # return leg, which the paper pays too.
    measured_started = False

    def check_site(obj, what, pi):
        if not _site_ok(obj, rows, cols):
            raise PlanError(f"timeline[{pi}]: {what} {obj!r} is not a valid site")
        return tuple(obj)

    for pi, phase in enumerate(timeline):
        if not isinstance(phase, dict) or "t" not in phase:
            raise PlanError(f"timeline[{pi}]: must be a dict with key 't'")
        t = phase["t"]

        if t == "move":
            moves = phase.get("moves")
            if not isinstance(moves, list) or not moves:
                raise PlanError(f"timeline[{pi}]: 'moves' must be a non-empty list")
            seen_q = set()
            tgt = {}
            for mv in moves:
                if not (isinstance(mv, list) and len(mv) == 3):
                    raise PlanError(f"timeline[{pi}]: move must be [qid, from, to]")
                q, fr, to = mv
                if not isinstance(q, int) or q not in pos:
                    raise PlanError(f"timeline[{pi}]: unknown qubit id {q}")
                if q >= N_SIM:
                    raise PlanError(
                        f"timeline[{pi}]: qubit {q} is a beacon/reservoir ion — "
                        f"those are static and may not move")
                if q in seen_q:
                    raise PlanError(f"timeline[{pi}]: qubit {q} moved twice in one round")
                seen_q.add(q)
                if q in merged or q in merged_hosts:
                    raise PlanError(f"timeline[{pi}]: qubit {q} is merged and cannot move")
                fr = check_site(fr, "move origin", pi)
                to = check_site(to, "move target", pi)
                if pos[q] != fr:
                    raise PlanError(
                        f"timeline[{pi}]: qubit {q} is at {pos[q]}, not {fr}")
                if not _is_edge(fr, to):
                    raise PlanError(
                        f"timeline[{pi}]: {fr} -> {to} is not one primitive step")
                if to in tgt:
                    raise PlanError(f"timeline[{pi}]: two ions target {to}")
                tgt[to] = q
            for to, q in tgt.items():
                holder = occupied.get(to)
                if holder is not None and holder not in seen_q:
                    raise PlanError(
                        f"timeline[{pi}]: target {to} occupied by stationary ion {holder}")
            # forbid head-on swap: q at a->b while q' at b->a
            for to, q in tgt.items():
                fr = pos[q]
                q2 = tgt.get(fr)
                if q2 is not None and pos[q2] == to:
                    raise PlanError(
                        f"timeline[{pi}]: ions {q} and {q2} swap through one edge")
            for q in seen_q:
                del occupied[pos[q]]
            for to, q in tgt.items():
                if to in occupied:
                    raise PlanError(
                        f"timeline[{pi}]: target {to} still occupied after round")
                occupied[to] = q
                pos[q] = to
                vertices.add(to)
                if to[0] == "S":
                    zones.add(to)
            transport_rounds += 1
            gap_count += 1
            ion_steps += len(seen_q)
            if len(seen_q) < 20:
                low_occ_rounds += 1
            if measured_started:
                wrap_rounds += 1
            seg_add("transport")

        elif t == "merge":
            pairs = phase.get("pairs")
            if not isinstance(pairs, list) or not pairs:
                raise PlanError(f"timeline[{pi}]: 'pairs' must be a non-empty list")
            used = set()
            for pr in pairs:
                if not (isinstance(pr, list) and len(pr) == 2):
                    raise PlanError(f"timeline[{pi}]: merge pair must be [mobile, host]")
                mob, host = pr
                for q in (mob, host):
                    if not isinstance(q, int) or q not in pos:
                        raise PlanError(f"timeline[{pi}]: unknown qubit id {q}")
                    if q in used:
                        raise PlanError(f"timeline[{pi}]: qubit {q} in two merges")
                    used.add(q)
                    if q in merged or q in merged_hosts:
                        raise PlanError(f"timeline[{pi}]: qubit {q} already merged")
                is_ad = (DATA0 <= mob < N_DATA and
                         XANC0 <= host < ZANC0 + N_HALF) or \
                        (DATA0 <= host < N_DATA and
                         XANC0 <= mob < ZANC0 + N_HALF)
                if not is_ad:
                    raise PlanError(
                        f"timeline[{pi}]: merge must pair one data with one ancilla")
                if gates_done >= N_ROUNDS:
                    raise PlanError(
                        f"timeline[{pi}]: no merges allowed after the last gate round")
                anc_q, dat_q = (mob, host) if mob in anc_ids else (host, mob)
                if (anc_q, dat_q) not in _REQUIRED_SETS[gates_done]:
                    raise PlanError(
                        f"timeline[{pi}]: merge pair (anc {anc_q}, data {dat_q}) is "
                        f"not a required pair of the next gate round {gates_done}")
                for q in (mob, host):
                    if q in merge_used:
                        raise PlanError(
                            f"timeline[{pi}]: qubit {q} already merged once in this "
                            f"inter-gate interval (merge/split are POC-free and "
                            f"restricted to one per ion per gate layer)")
                    merge_used.add(q)
                if pos[host][0] != "S":
                    raise PlanError(
                        f"timeline[{pi}]: merge host {host} must be on an 'S' site")
                if not _is_edge(pos[mob], pos[host]):
                    raise PlanError(
                        f"timeline[{pi}]: mobile {mob} at {pos[mob]} is not one step "
                        f"from host {host} at {pos[host]}")
                del occupied[pos[mob]]
                pos[mob] = pos[host]
                merged[mob] = host
                merged_hosts[host] = mob
            merge_rounds += 1
            seg_add("ms")

        elif t == "split":
            pairs = phase.get("pairs")
            if not isinstance(pairs, list) or not pairs:
                raise PlanError(f"timeline[{pi}]: 'pairs' must be a non-empty list")
            tgt = {}
            for pr in pairs:
                if not (isinstance(pr, list) and len(pr) == 2):
                    raise PlanError(
                        f"timeline[{pi}]: split pair must be [mobile, target_site]")
                mob, to = pr
                if not isinstance(mob, int) or mob not in merged:
                    raise PlanError(f"timeline[{pi}]: qubit {mob} is not merged")
                if mob in split_used:
                    raise PlanError(
                        f"timeline[{pi}]: qubit {mob} already split once in this "
                        f"inter-gate interval")
                split_used.add(mob)
                to = check_site(to, "split target", pi)
                if not _is_edge(pos[mob], to):
                    raise PlanError(
                        f"timeline[{pi}]: split target {to} is not one step from "
                        f"{pos[mob]}")
                if to in tgt:
                    raise PlanError(f"timeline[{pi}]: two splits target {to}")
                if to in occupied:
                    raise PlanError(f"timeline[{pi}]: split target {to} occupied")
                tgt[to] = mob
            for to, mob in tgt.items():
                host = merged.pop(mob)
                del merged_hosts[host]
                pos[mob] = to
                occupied[to] = mob
                vertices.add(to)
                if to[0] == "S":
                    zones.add(to)
            merge_rounds += 1
            seg_add("ms")

        elif t == "gate":
            rnd = phase.get("round")
            if gates_done >= N_ROUNDS:
                raise PlanError(
                    f"timeline[{pi}]: all {N_ROUNDS} gate rounds already executed")
            if rnd != gates_done:
                raise PlanError(
                    f"timeline[{pi}]: gate rounds must run in order; expected "
                    f"round {gates_done}, got {rnd}")
            if len(prepped) != 2 * N_HALF:
                raise PlanError(
                    f"timeline[{pi}]: all ancillas must be prepped before gates")
            cur = frozenset(
                (host, mob) if host in anc_ids else (mob, host)
                for mob, host in merged.items())
            if cur != _REQUIRED_SETS[rnd]:
                missing = list(_REQUIRED_SETS[rnd] - cur)[:3]
                extra = list(cur - _REQUIRED_SETS[rnd])[:3]
                raise PlanError(
                    f"timeline[{pi}]: gate round {rnd} alignment wrong; e.g. "
                    f"missing pairs (anc,data) {missing}, unexpected {extra}. "
                    f"Round {rnd} = ({SCHEDULE[rnd][0]}{SCHEDULE[rnd][1]+1}, "
                    f"{SCHEDULE[rnd][2]}{SCHEDULE[rnd][3]+1}^T)")
            gates_done += 1
            per_gap_rounds.append(gap_count)
            gate_snapshots.append({q: (pos[q][1], pos[q][2])
                                   for q in range(N_SIM)})
            segments.append(("gate", rnd))
            gap_count = 0
            merge_used = set()
            split_used = set()

        elif t in ("prep", "measure"):
            ancs = phase.get("ancillas")
            if not isinstance(ancs, list) or not ancs:
                raise PlanError(f"timeline[{pi}]: 'ancillas' must be non-empty list")
            if t == "prep" and gates_done > 0:
                raise PlanError(
                    f"timeline[{pi}]: prep phases must precede all gate rounds")
            if t == "measure" and gates_done != N_ROUNDS:
                raise PlanError(
                    f"timeline[{pi}]: measurement only after all {N_ROUNDS} gate rounds")
            book = prepped if t == "prep" else measured
            for a in ancs:
                if not isinstance(a, int) or a not in anc_ids:
                    raise PlanError(f"timeline[{pi}]: {a} is not an ancilla id")
                if a in book:
                    raise PlanError(f"timeline[{pi}]: ancilla {a} {t}ed twice")
                if a in merged or a in merged_hosts:
                    raise PlanError(f"timeline[{pi}]: ancilla {a} is merged")
                if not _optical(pos[a]):
                    raise PlanError(
                        f"timeline[{pi}]: ancilla {a} at {pos[a]} is not in an "
                        f"optical row (odd rows); {t} needs an optical zone")
                book.add(a)
                if t == "measure":
                    measure_order.append(a)
                    measured_started = True
            idle_slots += N_SIM - len(ancs)
            if t == "prep":
                prep_phases += 1
            else:
                measure_phases += 1
            segments.append((t, list(ancs)))

        else:
            raise PlanError(f"timeline[{pi}]: unknown phase type '{t}'")

    # end-of-timeline checks
    if gates_done != N_ROUNDS:
        raise PlanError(f"only {gates_done}/{N_ROUNDS} gate rounds executed")
    if len(prepped) != 2 * N_HALF:
        raise PlanError(f"only {len(prepped)}/70 ancillas prepped")
    if len(measured) != 2 * N_HALF:
        raise PlanError(f"only {len(measured)}/70 ancillas measured")
    if merged:
        raise PlanError("ions still merged at end of SEC")
    # ---- cycle boundary ------------------------------------------------
    # The SEC must TILE. Two different rules, for two different reasons:
    #
    #  (a) every ion that is NOT an ancilla -- data, beacon, reservoir -- must
    #      end on its EXACT layout site. Permuting the data would relabel the
    #      code's qubits and so the logical frame, and nothing downstream would
    #      catch it (the circuit is built from the frozen schedule, not from
    #      positions). Beacons/reservoir are static anyway.
    #
    #  (b) each ANCILLA SPECIES need only end on its OWN SET of layout sites,
    #      compared SEPARATELY for X and for Z. Nothing pins an ancilla ion to
    #      a position: beacons and cooling partners attach to DATA, the loss
    #      protocol's ancilla is dynamic, and the reservoir swaps ancilla ions
    #      in and out. This is the paper's own convention -- its Algorithm 1
    #      (p.30) runs 6 of 7 shift legs and ends each SEC with all ancillas
    #      displaced by a uniform group shift, absorbing the mismatch with
    #      "Relabel the ancilla in software" (Alg. 1 line 2; p.28: "no physical
    #      transport"). It is sound here because build_circuit is entirely
    #      position-blind and every detector is keyed on the CHECK index, so a
    #      relabel is invisible to the circuit -- and because each ancilla is
    #      reset at prep and measured before the boundary, so no state crosses
    #      it. The comparison is a MULTISET comparison and is verified by
    #      replay of the timeline, never taken on the candidate's word.
    #
    # The species are compared separately on purpose: swapping an X-ancilla
    # position with a Z-ancilla one is NOT a relabel, it changes which check a
    # given optical site serves and would not tile.
    anc_lo, anc_hi = XANC0, ZANC0 + N_HALF
    bad = [q for q in sorted(pos) if not (anc_lo <= q < anc_hi)
           and pos[q] != init_pos[q]]
    if bad:
        kind = ("data" if bad[0] < N_DATA else
                "beacon" if bad[0] < RES0 else "reservoir")
        raise PlanError(
            f"plan is not cyclic: {len(bad)} non-ancilla ions end away from "
            f"their layout position (e.g. {kind} qubit {bad[0]}: "
            f"{init_pos[bad[0]]} -> {pos[bad[0]]}); data/beacon/reservoir ions "
            f"carry the logical frame, so each must return to its OWN site")
    for label, lo in (("X", XANC0), ("Z", ZANC0)):
        end_sites = sorted(pos[q] for q in range(lo, lo + N_HALF))
        home_sites = sorted(init_pos[q] for q in range(lo, lo + N_HALF))
        if end_sites != home_sites:
            # multiset difference, so a duplicated end site is reported too
            # (unreachable given the collision rules, but never assumed)
            spare = list(home_sites)
            stray = []
            for s in end_sites:
                if s in spare:
                    spare.remove(s)
                else:
                    stray.append(s)
            raise PlanError(
                f"plan is not cyclic: the set of {label}-ancilla end positions "
                f"is not the set of {label}-ancilla layout sites "
                f"({len(stray)} end site(s) outside it, e.g. "
                f"{stray[0] if stray else end_sites[0]}); an "
                f"ancilla may be relabelled onto ANOTHER site of its OWN "
                f"species, but the species' occupied set must repeat so the "
                f"SEC tiles")

    t_core = transport_rounds / 20.0 + N_ROUNDS + prep_phases + measure_phases
    t_sec = t_core + LL_OVERHEAD_POC

    # Per-gap transport-round floors: max over ions of a sound graph-distance
    # lower bound between consecutive gate-time positions (all ions rest on S
    # sites at gate time), minus 2 for the POC-free merge+split edges an ion
    # may use per interval. Parallel rounds >= the longest single-ion journey.
    # The metric is the corrected well graph's (see "Grid model"): one column
    # costs 2 steps, one row costs 3.
    def _dist_lb(a, b):
        dr = abs(a[0] - b[0])
        dc = abs(a[1] - b[1])
        if dr == 0:
            return 2 * dc
        return 2 * dr + max(2 * dc, 1)

    def _gap_floor(snap_a, snap_b, ids=range(N_SIM)):
        best = 0
        for q in ids:
            lb = _dist_lb(snap_a[q], snap_b[q]) - 2
            if lb > best:
                best = lb
        return best

    per_gap_floors = [
        _gap_floor(gate_snapshots[g], gate_snapshots[g + 1])
        for g in range(N_ROUNDS)]
    # The wrap-back floor is a DATA-ONLY quantity: under the paper's
    # cycle-boundary convention an ancilla need only end on some site of its
    # own species (the residual permutation is absorbed by relabelling the
    # ancilla in software), so its distance back to its own layout site is not
    # a journey anybody has to make. Counting it would make rounds_over_floor
    # measure a constraint the validator no longer imposes.
    wrap_floor = _gap_floor(gate_snapshots[N_ROUNDS], gate_snapshots[0],
                            range(N_DATA))

    # Noise exposure per SEC (expected fault events, with p factored out):
    #   2q gates: 70 pairs x N_ROUNDS layers x weight 1
    #   prep + measure flips: 140 x 1/10
    #   idle during prep/measure POCs: (N_SIM - batch) x 1/100 per phase
    #   transport + merge/split rounds: N_SIM x 1/2000 per round
    exposure = (70.0 * N_ROUNDS
                + 2 * 70.0 / 10.0
                + idle_slots / 100.0
                + N_SIM * (transport_rounds + merge_rounds) / 2000.0)

    return {
        "segments": segments,
        "measure_order": measure_order,
        "transport_rounds": transport_rounds,
        "merge_rounds": merge_rounds,
        "prep_phases": prep_phases,
        "measure_phases": measure_phases,
        "per_gap_rounds": per_gap_rounds,
        "per_gap_floors": per_gap_floors,
        "wrap_floor": wrap_floor,
        "floor_total": sum(per_gap_floors) + wrap_floor,
        "wrap_rounds": wrap_rounds,
        "ions_per_round": (ion_steps / transport_rounds) if transport_rounds else 0.0,
        "low_occ_rounds": low_occ_rounds,
        "zones": len(zones),
        "vertices": len(vertices),
        "t_core_poc": t_core,
        "t_sec_poc": t_sec,
        "exposure": exposure,
        "grid": (rows, cols),
    }


# ----------------------------------------------------------------------------
# Stim circuit construction (noiseless determinism check + certification)
# ----------------------------------------------------------------------------

def _append_mpp(circ, pauli, support):
    import stim
    targets = []
    for q in support:
        targets.append(stim.target_x(q) if pauli == "X" else stim.target_z(q))
        targets.append(stim.target_combiner())
    circ.append("MPP", targets[:-1])


def build_circuit(compiled, observable, p):
    """MPP-bracketed circuit: brackets + NC_SECS noisy SECs from the timeline."""
    import stim
    circ = stim.Circuit()
    all_sim = list(range(N_SIM))

    # --- initial bracket: 70 stabilizers + k logicals (noiseless MPP) ---
    for s in range(N_HALF):
        _append_mpp(circ, "X", HX_SUPPORTS[s])
    for s in range(N_HALF):
        _append_mpp(circ, "Z", HZ_SUPPORTS[s])
    logs = LOGICAL_XS if observable == "X" else LOGICAL_ZS
    for lv in logs:
        _append_mpp(circ, observable, lv)

    n_bracket = 2 * N_HALF + K_LOG
    meas_count = n_bracket
    anc_rec_prev = {}   # ancilla qid -> absolute measurement index (previous SEC)

    p_step = _p_step(p)
    for sec in range(NC_SECS):
        anc_rec_cur = {}
        for seg in compiled["segments"]:
            kind = seg[0]
            if kind in ("transport", "ms"):
                k = seg[1]
                # k depolarizing rounds consolidated; union prob is exact to
                # O((k*p_step)^2) — Pauli cancellation is a 4.5e-5 relative
                # effect at this operating point, identically for all plans.
                q_eff = 1.0 - (1.0 - p_step) ** k
                if q_eff > 0:
                    circ.append("DEPOLARIZE1", all_sim, q_eff)
            elif kind == "prep":
                ancs = seg[1]
                circ.append("RX", ancs)
                if p > 0:
                    circ.append("Z_ERROR", ancs, _p_1q(p))
                    others = [q for q in all_sim if q not in set(ancs)]
                    if others:
                        circ.append("DEPOLARIZE1", others, _p_idle(p))
            elif kind == "gate":
                rnd = seg[1]
                cx_t, cz_t = [], []
                for a, d, gk in _REQUIRED[rnd]:
                    (cx_t if gk == "CX" else cz_t).extend([a, d])
                if cx_t:
                    circ.append("CX", cx_t)
                if cz_t:
                    circ.append("CZ", cz_t)
                if p > 0:
                    if cx_t:
                        circ.append("DEPOLARIZE2", cx_t, _p_gate(p))
                    if cz_t:
                        circ.append("DEPOLARIZE2", cz_t, _p_gate(p))
            elif kind == "measure":
                ancs = seg[1]
                if p > 0:
                    circ.append("MX", ancs, _p_1q(p))
                    others = [q for q in all_sim if q not in set(ancs)]
                    if others:
                        circ.append("DEPOLARIZE1", others, _p_idle(p))
                else:
                    circ.append("MX", ancs)
                for a in ancs:
                    anc_rec_cur[a] = meas_count
                    meas_count += 1
        # detectors: compare each check outcome with its previous value
        for s in range(2 * N_HALF):
            anc = XANC0 + s
            cur = anc_rec_cur[anc]
            if sec == 0:
                prev = s  # bracket order: X-checks 0..34 then Z-checks 35..69
            else:
                prev = anc_rec_prev[anc]
            circ.append(
                "DETECTOR",
                [stim.target_rec(cur - meas_count),
                 stim.target_rec(prev - meas_count)])
        anc_rec_prev = anc_rec_cur

    # --- final bracket ---
    final_base = meas_count
    for s in range(N_HALF):
        _append_mpp(circ, "X", HX_SUPPORTS[s])
    for s in range(N_HALF):
        _append_mpp(circ, "Z", HZ_SUPPORTS[s])
    for lv in logs:
        _append_mpp(circ, observable, lv)
    meas_count = final_base + 2 * N_HALF + K_LOG

    for s in range(2 * N_HALF):
        anc = XANC0 + s
        circ.append(
            "DETECTOR",
            [stim.target_rec(final_base + s - meas_count),
             stim.target_rec(anc_rec_prev[anc] - meas_count)])
    for li in range(K_LOG):
        first = li + 2 * N_HALF                      # in initial bracket
        last = final_base + 2 * N_HALF + li
        circ.append(
            "OBSERVABLE_INCLUDE",
            [stim.target_rec(first - meas_count),
             stim.target_rec(last - meas_count)], li)
    return circ


def noiseless_ok(compiled):
    for obs in ("X", "Z"):
        circ = build_circuit(compiled, obs, 0.0)
        sampler = circ.compile_detector_sampler()
        dets, obs_flips = sampler.sample(64, separate_observables=True)
        if dets.any() or obs_flips.any():
            return False, obs
    return True, None


def ler_sample(circuit, target_errors, rng, max_shots=MAX_SHOTS,
               osd_order=0, progress=None):
    """CERTIFICATION-ONLY error-budget BP-OSD sampling (seconds/shot — far too
    slow for the inner loop). Starts with a small pilot batch and grows
    adaptively toward the error budget."""
    from stimbposd import BPOSD
    dem = circuit.detector_error_model()
    decoder = BPOSD(dem, max_bp_iters=30, osd_order=osd_order)
    sampler = circuit.compile_detector_sampler(seed=int(rng.integers(2**31)))
    shots = 0
    errors = 0
    batch = MIN_SHOTS
    while shots < max_shots:
        want = min(batch, max_shots - shots)
        dets, obs = sampler.sample(want, separate_observables=True)
        pred = decoder.decode_batch(dets)
        errors += int(np.any(pred != obs, axis=1).sum())
        shots += want
        if progress:
            progress(shots, errors)
        if errors >= target_errors:
            break
        rate = errors / shots if errors else 0.0
        if rate > 0:
            need = int((target_errors - errors) / rate * 1.10)
            batch = max(MIN_SHOTS, min(4 * batch, need))
        else:
            batch = min(4 * batch, 8192)
    return (errors / shots if shots else 0.0), shots, errors


# ----------------------------------------------------------------------------
# Scoring (runs through a pristine re-import of this module — see below)
# ----------------------------------------------------------------------------

def _fmt_gaps(per_gap):
    return ", ".join(f"r{t}:{g}" for t, g in enumerate(per_gap))


def _aggregate_impl(results):
    plan = results[0]
    try:
        compiled = compile_plan(plan)
    except PlanError as e:
        return {
            "combined_score": INVALID_SCORE,
            "public": {"valid": False},
            "private": {},
            "extra_data": {},
            "text_feedback": f"INVALID PLAN: {e}",
        }
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as e:
        return {
            "combined_score": INVALID_SCORE,
            "public": {"valid": False},
            "private": {},
            "extra_data": {},
            "text_feedback": f"INVALID PLAN (malformed): {type(e).__name__}: {e}",
        }

    ok, which = noiseless_ok(compiled)
    if not ok:
        return {
            "combined_score": INVALID_SCORE,
            "public": {"valid": False},
            "private": {},
            "extra_data": {},
            "text_feedback": (
                f"INVALID: assembled circuit ({which}-observable) is not "
                f"noiseless-deterministic. This should be impossible for a plan "
                f"that passed alignment checks — report this candidate."),
        }

    t_sec = compiled["t_sec_poc"]
    t_core = compiled["t_core_poc"]
    zones = compiled["zones"]
    exposure = compiled["exposure"]
    var_exp = exposure - FROZEN_EXPOSURE
    score = (W_E * math.log2(SEED_VAR_EXPOSURE / var_exp)
             + W_T * math.log2(SEED_T_CORE / t_core)
             + W_Z * math.log2(SEED_ZONES / zones))
    # honest operating-point reliability readout (NOT the fitness): the paper's
    # ansatz gives log10(LER) shift = ceil(d_circ/2) * log10(exposure ratio)
    ler_shift = 5.0 * math.log10(exposure / SEED_EXPOSURE_TOTAL)

    gaps_used_floor = ", ".join(
        f"r{t}:{u}/{f}" for t, (u, f) in enumerate(
            zip(compiled["per_gap_rounds"], compiled["per_gap_floors"])))
    ftot = compiled["floor_total"]
    rounds = compiled["transport_rounds"]
    ratio = rounds / max(ftot, 1)
    feedback = (
        f"Valid plan. SEC = {t_sec:.2f} POC "
        f"(transport {rounds} rounds = {rounds/20.0:.2f} POC, {N_ROUNDS} gate "
        f"layers, {compiled['prep_phases']} prep + {compiled['measure_phases']} "
        f"measure phases, +{LL_OVERHEAD_POC} POC fixed loss/leakage checks). "
        f"Plan-dependent noise exposure {var_exp:.2f} (frozen part "
        f"{FROZEN_EXPOSURE:.0f} excluded from the score). Rail sections used: "
        f"{zones} (transit vertices {compiled['vertices']} — NOT scored, so "
        f"spreading traffic over separate corridors is free). "
        f"Merge/split rounds: {compiled['merge_rounds']}.\n"
        f"SLACK MAP — transport rounds USED/DISTANCE-FLOOR per gap: "
        f"[{gaps_used_floor}], wrap-back after measurement "
        f"{compiled['wrap_rounds']}/{compiled['wrap_floor']}. YOUR LAYOUT "
        f"ADMITS {ftot} ROUNDS IN TOTAL AND YOU USE {rounds} ({ratio:.2f}x "
        f"floor). The floor is the longest single-ion journey per gap: it is "
        f"achievable when every ion moves every round without detours or "
        f"waiting, so the whole excess is parallelism loss.\n"
        f"PARALLELISM — mean {compiled['ions_per_round']:.1f} of 140 ions move "
        f"per transport round; {compiled['low_occ_rounds']} rounds move fewer "
        f"than 20 ions. A round costs the same whether 1 ion moves or 140, so "
        f"low-occupancy rounds are pure waste: routing one group to completion "
        f"before starting another (per-axis passes, X-then-Z, hide/slide/emerge "
        f"barriers) is the usual cause. Interleaving groups into shared rounds "
        f"is legal — a target vacated by another ion that moves in the SAME "
        f"round is allowed, and a full closed-loop rotation can advance every "
        f"ion simultaneously.\n"
        f"Score = {W_E}*log2(seed_var_exposure/var_exposure) "
        f"+ {W_T}*log2(seed_T_core/T_core) + {W_Z}*log2(seed_zones/zones) "
        f"= {score:+.4f}, which is {score - SHIPPED_SEED_SCORE:+.4f} against the "
        f"best plan this run started from. "
        f"Prefer strategies parametric in (l, m, schedule) over hard-coded "
        f"positions — plans are re-run on other three-ring codes after "
        f"evolution.")

    return {
        "combined_score": float(score),
        "public": {
            "valid": True,
            "gain_over_seed": float(score - SHIPPED_SEED_SCORE),
            "var_exposure": float(var_exp),
            "exposure": float(exposure),
            "ler_shift_log10": float(ler_shift),
            "sec_poc": float(t_sec),
            "t_core_poc": float(t_core),
            "transport_rounds": int(rounds),
            "floor_total": int(ftot),
            "rounds_over_floor": float(ratio),
            "ions_per_round": float(compiled["ions_per_round"]),
            "low_occ_rounds": int(compiled["low_occ_rounds"]),
            "zones": int(zones),
            "vertices": int(compiled["vertices"]),
            "wrap_rounds": int(compiled["wrap_rounds"]),
            "merge_rounds": int(compiled["merge_rounds"]),
            "prep_phases": int(compiled["prep_phases"]),
            "measure_phases": int(compiled["measure_phases"]),
        },
        "private": {
            "seed_var_exposure": SEED_VAR_EXPOSURE,
            "seed_t_core": SEED_T_CORE,
            "seed_zones": SEED_ZONES,
            "per_gap_rounds": list(compiled["per_gap_rounds"]),
            "per_gap_floors": list(compiled["per_gap_floors"]),
        },
        "extra_data": {},
        "text_feedback": feedback,
    }


def _pristine_module():
    """Re-import this module from disk under a private name.

    The candidate's EVOLVE-BLOCK executes inside this process before scoring,
    so names in the RUNNING module are rebindable by candidate code. Scoring
    through a fresh import of the on-disk source defeats that class of
    tampering (and certification re-runs elites in fresh processes, so any
    non-reproducible score is caught there regardless).
    """
    import importlib.util
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evaluate.py")
    spec = importlib.util.spec_from_file_location("_q70_eval_pristine", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def aggregate_fn(results):
    return _pristine_module()._aggregate_impl(results)


# ----------------------------------------------------------------------------
# run_shinka_eval hooks
# ----------------------------------------------------------------------------

def get_kwargs(run_idx):
    """The spec handed to the candidate builder. Code + chip facts only."""
    return {"spec": {
        "l": L_RING, "m": M_RING,
        "A_exps": [list(e) for e in A_EXPS],
        "B_exps": [list(e) for e in B_EXPS],
        "schedule": [list(t) for t in SCHEDULE],
        "n_data": N_DATA, "n_anc": 2 * N_HALF,
        "grid_max_rows": GRID_MAX_ROWS, "grid_max_cols": GRID_MAX_COLS,
        "optical_row_parity": 1,
        "qid_bases": {"data": DATA0, "x_anc": XANC0, "z_anc": ZANC0,
                      "beacon": BEAC0, "reservoir": RES0},
        "n_beacon": N_BEACON, "n_reservoir": N_RESERVOIR,
    }}


def validate_fn(result):
    if not isinstance(result, dict):
        return False, "run_experiment must return a dict"
    if "layout" not in result or "timeline" not in result:
        return False, "result must contain 'layout' and 'timeline'"
    return True, None


def main(program_path, results_dir):
    metrics, correct, err = run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="run_experiment",
        num_runs=1,
        get_experiment_kwargs=get_kwargs,
        aggregate_metrics_fn=aggregate_fn,
        validate_fn=validate_fn,
    )
    if not correct:
        raise RuntimeError(err or "evaluation failed")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--program_path", required=True)
    parser.add_argument("--results_dir", required=True)
    args = parser.parse_args()
    main(program_path=args.program_path, results_dir=args.results_dir)

"""ShinkaEvolve EVALUATOR — END-TO-END gauging measurement of the gross-code
logical X_alpha (v4: end-to-end LER + protocol fault-distance probe).

The candidate designs the complete GAUGING GADGET of Williamson & Yoder,
"Low-overhead fault-tolerant quantum computation by gauging logical operators"
(arXiv:2410.02213; Nature Physics 22:598, 2026) for the weight-12 logical
X_alpha of the [[144,12,12]] gross code:

    spec = {"edges": [(u, v), ...], "rounds": R}

  * labels 0..11  = the 12 data qubits in supp(X_alpha)  (graph vertices)
  * labels 12..35 = OPTIONAL dummy vertices (paper Remark 2: they carry NO
    physical qubit; their Gauss-law check is A_v = prod_{e:v} X_e). Dummy
    vertices are how the paper expresses Shor-style stars, surgery grids and
    the thickened/cellulated (layered) constructions — all inside this one
    (vertices, edges) design space.
  * each EDGE carries one new data qubit (init |0>); multigraph edges allowed
    (the paper's double-gross example uses a doubled edge); self-loops not.
  * R = number of deformed-code syndrome-extraction rounds (the time axis;
    paper Thm 2 uses d=12; Cross et al. arXiv:2407.18393 found R=7 < d optimal
    at p=1e-3, but note the measurement outcome's fault distance is capped at
    R — see WHY v4 below).

Everything else is derived DETERMINISTICALLY here (never evolved):
  * A_v Gauss-law checks; original Z-checks routed by exact minimum-weight
    T-joins (paper Def. 2 "minimum weight path" convention); flux checks B_p
    on a minimum-weight cycle basis (Horton), reduced by the BB-code Z-check
    redundancy exactly as in paper App. B (the paper graph yields 11 -> 7);
  * a canonical syndrome-extraction schedule via EXACT minimum edge coloring
    of the bipartite Tanner graph (Konig's theorem; constructive alternating-
    path recoloring) — the coloration-circuit construction of Tremblay-
    Delfosse-Beverland arXiv:2109.14609. The schedule is the SAME ALGORITHM
    for every candidate and its depth per phase EQUALS the deformed Tanner
    graph's max degree Delta exactly (a pure invariant of the gadget's
    degrees/check weights, independent of construction order);
  * the full end-to-end protocol circuit (stim), following Cross-He-Rall-Yoder
    arXiv:2407.18393 Sec. 3.2 adapted to gauging:
       ideal init MPPs -> N_BASE noisy base rounds -> gauge-in (edge qubits
       |0>) -> R noisy deformed rounds -> gauge-out (edge MZ) -> N_BASE noisy
       base rounds -> ideal final MPPs.
    First-round A_v outcomes are individually random (no detector; paper
    App. F Lemma 3) and their product x the initial ideal X_alpha MPP is the
    MEASUREMENT observable; B_p and deformed checks get boundary detectors at
    gauge-in/out; the ungauging byproduct enters the Z-logical observables as
    edge-readout parities.
  * circuit-level depolarizing noise sampled at a THREE-POINT NOISE CURVE
    P_GRID = (0.7, 1.0, 1.4) x P_GATE (DEPOLARIZE2 after each CNOT,
    measure/reset flips, aggregated idle noise per phase so SCHEDULE DEPTH is
    priced), BP+OSD-0 decoding (stimbposd/ldpc; see BP_ITERS/OSD_ORDER note),
    all six circuits sampled in one parallel sinter fan-out.

WHY v4 (the gcg1 postmortem — how the v2/v3 evaluator was beaten). v1 gated
on a BP+OSD distance estimate and was gameable; v2/v3 swung fully to measured
end-to-end LER and was beaten from the OTHER side: run gcg1 converged to an
11-edge SPANNING TREE (Q=23, R=5) that PASSED the reliability gate with
+0.57 decades of margin. The mechanism: at the simulable noise rates
(p ~ 1.4-2.8e-3) the protocol error is dominated by the bulk fault-location
count, so a smaller gadget WINS ON LEVEL — while its collapsed protection is
invisible. A tree violates the paper's expansion desideratum maximally
(balanced single-edge cut -> Cheeger h = 1/6 -> WY Lemma 2 guarantees only
d* >= 2; the measured dressed X-logical of that tree has weight 8 < 12), and
R=5 caps the measurement outcome's timelike fault distance at 5 (a chain of 5
A_v measurement flips is undetectable; Cross et al. Lemma 9: measurement
fault distance = min(R, ...)). A weight-5..8 tail fires at ~p^3..p^4 — orders
of magnitude below the measurable LER at the benchmark rates, and the
three-point d_eff fit measures the BULK slope, not the tail. Monte Carlo
cannot see this hole at any affordable budget; only a fault-set search can.

v4.1 therefore keeps end-to-end LER as the ONLY feasibility criterion but
makes the comparison honest by PRICING the invisible tail instead of gating
on a distance target (the v4.0 draft hard-gated d_hat >= 10, which walls off
the whole 23..35-element region and forces R >= 10; the run owner's goal is
explicitly to explore that region, accepting any design whose TOTAL error
stays within RATIO_LIMIT of the reference at the benchmark rates):
  (a) the probes (all upper-bound finders, fresh random seed per eval):
      * R — a chain of R measurement flips on one A_v silently flips the
        outcome (Cross et al. Lemma 9), n_av parallel chains;
      * a BP+OSD dressed-logical attack on the deformed code's X and Z sides
        (the quantities of Cross et al. Lemma 10; WY's own fast filter;
        arXiv:2603.22532 recipe — random column permutations + prior jitter
        across trials — plus a diversity schedule), which also counts the
        DISTINCT minimum-weight operators it finds;
      * budgeted stim searches (shortest_graphlike_error +
        search_for_undetectable_logical_errors at graphlike caps) on the
        actual protocol circuits.
  (b) TAIL PRICING: each found fault set of weight w contributes its
      first-order failure probability N * C(w, ceil(w/2)) * p^ceil(w/2) to
      the candidate's EFFECTIVE LER at each scored point — but only when the
      Monte Carlo could NOT have seen it (expected occurrences below
      TAIL_MIN_EXPECT in the shot budget; visible sets are already inside
      the measured number). A healthy design pays ~0; the gcg1 tree at R=5
      pays ~1e-6 at the gate point (negligible — it becomes FEASIBLE, as the
      run owner intends, but now with its tail on the record); an R=1 design
      pays its silent outcome-flip rate ~n_av*p (and MC measures it too).
  (c) the reported tail_crossover_p diagnostic: the physical rate below
      which the found tails overtake the reference's extrapolated curve —
      i.e. how far down in p the candidate's advantage claim survives. This
      is the "story" metric: a Q=23 tree that wins at today's hardware rates
      (p ~ 1e-3..3e-3) and dies below p ~ 1e-5 is reported exactly that way.
  Optional: set GAUGE_DTARGET > 0 to restore a hard fault-distance gate for
  a distance-preserving campaign (10 = the gross code's own circuit-level
  ceiling, Bravyi et al. Nature 2024; Cross et al. Sec 4.2).

================  SCORING (Shinka MAXIMISES combined_score)  ===================
  Q  = total added elements = edge qubits + A_v checks + B_p checks
       (the paper gadget: 22 + 12 + 7 = 41 — its "additional checks and
        qubits total 41", App. B; each check costs an ancilla qubit).
  overall(p) = 1 - (1-p_X)(1-p_Z) from the two protocol circuits (X basis:
       measurement-outcome observable + preservation of the 12 X logicals;
       Z basis: preservation of the 11 Z logicals that commute with X_alpha,
       byproduct-corrected), sampled at each p in P_GRID.
  eff(p)     = overall(p) + tail(p)            [tail pricing, see above]
  margin(p)  = log10(LER_REFS[p] / eff(p)) — TRUE headroom vs the calibrated
       PAPER-gadget curve at the same p (no free factor), clamped at each
       point's resolution bound (a zero-error point cannot claim more than
       its shot budget resolves, size-independently).

  FEASIBLE := at BOTH the low and gate points,
       margin >= -(log10(RATIO_LIMIT) + NOISE_Z * sqrt(sigma_pt^2 + SIGMA_REF^2))
       i.e. the tail-priced curve is not DEMONSTRABLY worse than
       RATIO_LIMIT (default 1.1x) times the reference. The noise allowance
       exists because 0.041 decades (1.1x) is ~1 sigma at the 10-minute
       budgets — without it the gate would flip on sampling luck; the strict
       1.1x verdict belongs to the offline high-budget certification of
       finalists (calibrate.py --compare with raised budgets).
  The LER bonus is WORST-CASE over the scored points (min of lo and gate
       margins, high-p diagnostic only) and only rewards TRUE dominance:
       max(0, min(margin_lo, margin_gate)) — a design that merely matches the
       reference earns no bonus. d_eff (bulk slope) is reported (diagnostic).

  candidate crashes / returns garbage          -> -1000   (correct=False)
  parseable but invalid gadget (SpecError)     ->  -100   (+ named reason)
  valid but INFEASIBLE                         ->  -8
                                                   + min(0, margin_gate+allow_gate)
                                                   + min(0, margin_lo+allow_lo)
                                                   (clamped to >= -30; smooth
                                                    gradient toward the bar;
                                                    with GAUGE_DTARGET set, an
                                                    unprotected design also
                                                    pays 1.5/unit shortfall)
  FEASIBLE                                     ->  (Q_REF - Q)
                                                   + 3.0 * min(2.0, max(0,
                                                     min(margin_lo, margin_gate)))
       Q_REF = 41. Reproducing the paper gadget scores ~0; every element saved
       below the paper while staying FEASIBLE is +1; the worst-case LER bonus
       (0.33 decades of true dominance = +1 element, cap +6) is how end-to-end
       error tie-breaks and can outweigh 1-2 elements of size. The frontier is
       the SMALLEST gadget whose tail-priced curve stays within RATIO_LIMIT of
       the reference — with every light fault set it carries priced and
       reported, so the result is a defensible LER-vs-size Pareto front over
       Q in [23, 41], not a sampling artifact.

================  ANTI-GAMING  ================================================
The candidate returns only the gadget spec. The code, the deformation, the
schedule, the circuit, the observables, the decoder, the sampling AND the
fault-set probes all live here. The score's measured quantities are the
stim-sampled logical error of an evaluator-built circuit and the analytic
price of fault sets the probes actually FIND — there is no oracle to
over-report to (v1's failure mode), and the LER-only blind spot (the gcg1
tree) is closed by pricing: a found light set raises the candidate's
effective LER by its first-order failure rate, so "cheap because invisible"
stops working while "cheap because genuinely negligible at the benchmark
rates" is allowed through — which is the run owner's stated criterion. The
probes are upper-bound finders: a miss under-prices the tail (never
over-prices a good design), its benefit is bounded by the tail's true size
at the benchmark p, and fresh per-eval probe seeds re-attack every lineage
each generation. Sampling noise is bounded by per-point error-budget
collection (sinter max_errors) with a fresh seed per eval; the feasibility
allowance is explicitly noise-aware (reject only when demonstrably beyond
RATIO_LIMIT at NOISE_Z sigma), and the worst-case LER bonus is bounded
(max +6 = 2 decades of TRUE dominance) under the integer element-count
ladder. Fresh-process-per-candidate isolation (the Shinka harness default)
must stay ON; sinter workers are fresh spawned processes that re-import this
module and stim from disk, so a candidate monkey-patching module globals in
the eval process does not reach the samplers/decoders. Do not reuse an eval
process across candidates.

================  RUNTIME (24-core Windows, shinka env, measured)  =============
  build + structural checks + 6 circuits      ~20 s
  fault-set probes (code attack + stim)       ~5-30 s
  sinter sampling, 6 circuits, 20 workers     ~10-14 min for reference-like
       gadgets (BP+OSD-0 is ~1.5-2 core-s/shot on these ~90k-mechanism DEMs, so
       shots drive the cost; per-point error budgets mean worse candidates
       finish sooner, excellent ones run to the shot caps; smaller gadgets
       have smaller DEMs and decode faster).
  worst case bounded by the mechanism-scaled shot caps: ~16 min. Set the
  harness eval_time generously (>= 00:20:00). Shrink P_BUDGET to trade score
  noise for throughput.
Env overrides: GAUGE_PHYS_P, GAUGE_WORKERS, GAUGE_LER_REF_LO/GATE/HI,
GAUGE_RATIO_LIMIT (default 1.1), GAUGE_DTARGET (default 0 = pricing only).
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
import time
import traceback
from typing import Optional

import numpy as np
import stim

from shinka.core import run_shinka_eval

# Bind third-party entry points privately at import, BEFORE any candidate
# module is loaded in this process (see ANTI-GAMING above).
import sinter as _sinter_mod
import stimbposd as _stimbposd_mod
import scipy.sparse as _scipy_sparse
from ldpc import BpOsdDecoder as _LDPC_BPOSD
_SINTER_COLLECT = _sinter_mod.collect
_SINTER_TASK = _sinter_mod.Task
_SINTER_OPTIONS = _sinter_mod.CollectionOptions
_SINTER_DEC_CLS = _stimbposd_mod.SinterDecoder_BPOSD
_CSR = _scipy_sparse.csr_matrix

# ----------------------------------------------------------------------
# Benchmark / scoring constants
# ----------------------------------------------------------------------
P_GATE      = float(os.environ.get("GAUGE_PHYS_P", "0.002"))
# Benchmark noise CURVE: three physical error rates, log-symmetric about the
# gate point (x0.7, x1, x1.4). The hard reliability gate lives at the CENTER
# point only; the bonus is the LOW-p-WEIGHTED headroom (low + gate, high-p
# dropped — a symmetric average would cancel the slope); and the fitted scaling
# exponent d_eff ~ 2 * dlog10(LER)/dlog10(p) is reported. So a gadget that
# squeaks by at one noise rate but scales badly (collapsed effective distance
# -> flat curve -> small low-p margin) is under-rewarded, while a steep
# distance-preserving curve earns the full bonus. GeneCS-style multi-p made
# quantitative.
P_GRID      = (0.7 * P_GATE, P_GATE, 1.4 * P_GATE)
IDLE_FRAC   = 0.1          # idle depolarize prob = IDLE_FRAC * p per tick
N_BASE      = 1            # base-code rounds before and after (Remark 9: constant ok;
                           # the ideal MPP brackets anchor the reference frames)
BP_ITERS    = 12           # decoder: BP+OSD-0 (osd_method="osd0"). Deliberately
OSD_ORDER   = 0            # fast-but-weak (~5x faster than BP+LSD here at equal
                           # observed accuracy); absolute LERs are NOT paper-
                           # comparable, but every candidate AND the reference
                           # gadget are decoded identically, so the relative
                           # gate is self-consistent (same philosophy as
                           # bb_syndrome_sched's osd_order=3 choice).
# Per-point sampling budgets (max_errors, max_shots) PER CIRCUIT. Low-p and gate
# both feed the worst-case margin (feasibility + bonus), so both get real
# budget; the high-p point is diagnostic only (reported curve + d_eff fit, NOT
# scored), so it is the lightest. A good gadget's low-p errors are rare — the
# shot cap bounds the tail cost and the resolution clamp handles the floored
# case. The low point stays the noisiest (~0.07-0.10 decades at these caps);
# the noise-aware feasibility allowance absorbs that automatically (its sigma
# term is computed from the point's OBSERVED error count). BP+OSD-0 is
# ~1.5-2 core-s/shot on these DEMs,
# so shots ARE the eval-time driver. All six circuits run in ONE sinter fan-out
# so the worker pool stays saturated. Bump these to tighten; recalibration is
# NOT needed (LER_REFS are budget-independent).
P_BUDGET    = ((55, 2600), (70, 2800), (25, 700))
N_WORKERS   = int(os.environ.get("GAUGE_WORKERS", str(max(2, min(20, (os.cpu_count() or 8) - 4)))))

Q_REF       = 41           # paper gadget: 22 edge qubits + 12 A_v + 7 B_p (App. B)
# --- v4.1 scoring constants (see module docstring SCORING) ---
RATIO_LIMIT = float(os.environ.get("GAUGE_RATIO_LIMIT", "1.1"))
                           # feasibility: tail-priced LER must stay within this
                           # RATIO of the reference at every scored point (the
                           # run owner's non-inferiority bar)
LOG_RATIO   = float(np.log10(RATIO_LIMIT))          # ~0.0414 decades at 1.1x
NOISE_Z     = 2.0          # feasibility is noise-aware: reject only when
                           # DEMONSTRABLY beyond the ratio, i.e. margin <
                           # -(LOG_RATIO + NOISE_Z*sigma). 1.1x is ~1 sigma at
                           # the 10-min budgets, so without this allowance the
                           # gate would flip on sampling luck.
SIGMA_REF   = 0.025        # decades: the reference curve's own calibration
                           # uncertainty (300-error calibrate.py run), added in
                           # quadrature to the candidate's per-point sigma
W_LER       = 3.0          # score per decade of TRUE worst-case dominance
BONUS_CAP   = 2.0          # decades of dominance that can earn score (max +6)
TAIL_MIN_EXPECT = 5.0      # price a probe-found fault set into the effective
                           # LER only if MC could NOT have seen it (< this many
                           # expected occurrences in the point's shot budget);
                           # sets MC already samples are in the measured number
D_TARGET    = int(os.environ.get("GAUGE_DTARGET", "0"))
                           # OPTIONAL hard fault-distance gate (0 = off, the
                           # default). Set e.g. 10 to run a distance-preserving
                           # campaign (10 = the gross code's own circuit-level
                           # ceiling, Bravyi et al. Nature 2024); by default
                           # v4.1 PRICES probed tails instead of gating — see
                           # WHY v4.1 in the docstring.
# Fault-distance probe budgets:
ATTACK_TRIALS = 48         # BP+OSD dressed-logical attack trials per witness
                           # (trial 0 clean; others alternate random column
                           # permutations, growing prior jitter, edge-biased
                           # priors and BP/OSD depth — the arXiv:2603.22532
                           # recipe plus a diversity schedule; rare light
                           # operators live in narrow OSD basins: 16 trials
                           # MISSED a weight-9 dressed logical in the GeneCS
                           # beta=0.35 graph that 48+ trials find on every
                           # seed). ~2-4 s per attack at deformed-code scale —
                           # trivial next to the ~10 min sampling
STIM_PROBE_CAPS = (2, 4)   # (det-set size, edge degree) exploration caps: the
                           # graphlike regime, ~0.1-1 s per circuit (measured);
                           # deeper caps belong in calibrate.py, not in-loop
# Overall end-to-end error of the PAPER gadget (18 matching + 4 expansion
# edges, R=12) under THIS harness, at each P_GRID point — calibrated by
# calibrate.py; MUST be recalibrated if the noise model, P grid, scheduler,
# protocol shape or decoder change. NaN refuses to score (calibrate.py can
# still import this module).
def _ref(env, default):
    try:
        return float(os.environ.get(env, default))
    except ValueError:
        return float("nan")
# calibrate.py 2026-07-08 (BP+OSD-0, Konig schedule, n_base=1, 300 errors/pt):
#   p=1.4e-3  X 34/9982   Z 59/9982    overall 9.30e-3  (+-0.045 decades)
#   p=2.0e-3  X 159/7985  Z 291/7985   overall 5.56e-2  (+-0.020, GATE point)
#   p=2.8e-3  X 304/3460  Z 314/2127   overall 2.23e-1  (+-0.017)
# -> reference d_eff ~ 8-10 (steep, distance-preserving). Replace these three
# default strings (NOT the _ref wrappers) to recalibrate; the env vars override.
LER_REFS    = (_ref("GAUGE_LER_REF_LO",   "9.2966e-3"),
               _ref("GAUGE_LER_REF_GATE", "5.5630e-2"),
               _ref("GAUGE_LER_REF_HI",   "2.2252e-1"))

INVALID_SCORE = -100.0   # below any buildable gadget's score (min feasible ~ -76)
CRASH_SCORE   = -1000.0

MAX_EDGES   = 60
MAX_DUMMIES = 24
MAX_ROUNDS  = 24

# ----------------------------------------------------------------------
# Gross code [[144,12,12]] and the logical X_alpha (paper App. B)
# ----------------------------------------------------------------------
L, M = 12, 6
N = L * M                       # 72
def _idx(a, b): return (a % L) * M + (b % M)
_A  = [(3, 0), (0, 2), (0, 1)]
_B  = [(0, 3), (2, 0), (1, 0)]
_BT = [((-c) % L, (-d) % M) for c, d in _B]
_AT = [((-c) % L, (-d) % M) for c, d in _A]

def _build_code():
    HX = np.zeros((N, 2 * N), np.int8); HZ = np.zeros((N, 2 * N), np.int8)
    for a in range(L):
        for b in range(M):
            r = _idx(a, b)
            for c, d in _A:  HX[r, _idx(a + c, b + d)] ^= 1
            for c, d in _B:  HX[r, N + _idx(a + c, b + d)] ^= 1
            for c, d in _BT: HZ[r, _idx(a + c, b + d)] ^= 1
            for c, d in _AT: HZ[r, N + _idx(a + c, b + d)] ^= 1
    return HX, HZ
HX0, HZ0 = _build_code()
assert (HX0 @ HZ0.T % 2 == 0).all()

F_TERMS  = [(0,0),(1,0),(2,0),(3,0),(6,0),(7,0),(8,0),(9,0),(1,3),(5,3),(7,3),(11,3)]
SUPPORT  = [_idx(a, b) for a, b in F_TERMS]     # data-qubit index of label 0..11

# ----------------------------------------------------------------------
# GF(2) helpers
# ----------------------------------------------------------------------
def _rank(Mx):
    Mx = Mx.copy() % 2; r = 0; rows, cols = Mx.shape
    for c in range(cols):
        piv = next((i for i in range(r, rows) if Mx[i, c]), None)
        if piv is None: continue
        Mx[[r, piv]] = Mx[[piv, r]]
        for i in range(rows):
            if i != r and Mx[i, c]: Mx[i] ^= Mx[r]
        r += 1
        if r == rows: break
    return r

def _nullspace(A):
    A = A.copy() % 2; rows, cols = A.shape; Mx = A.copy(); pc = {}; r = 0
    for c in range(cols):
        piv = next((i for i in range(r, rows) if Mx[i, c]), None)
        if piv is None: continue
        Mx[[r, piv]] = Mx[[piv, r]]
        for i in range(rows):
            if i != r and Mx[i, c]: Mx[i] ^= Mx[r]
        pc[c] = r; r += 1
        if r == rows: break
    free = [c for c in range(cols) if c not in pc]; B = []
    for f in free:
        v = np.zeros(cols, np.int8); v[f] = 1
        for c, rr in pc.items(): v[c] = Mx[rr, f]
        B.append(v)
    return np.array(B, np.int8) if B else np.zeros((0, cols), np.int8)

def _logical_reps(stab, kspace, seed_ops=()):
    cur = stab.copy() % 2; base = _rank(cur); reps = []
    for v in list(seed_ops) + list(kspace):
        test = np.vstack([cur, np.asarray(v, np.int8).reshape(1, -1)])
        if _rank(test) > base:
            cur = test; base += 1; reps.append(np.asarray(v, np.int8))
    return np.array(reps, np.int8)

XALPHA = np.zeros(2 * N, np.int8)
for _q in SUPPORT: XALPHA[_q] = 1
assert (HZ0 @ XALPHA % 2 == 0).all()

LOGX = _logical_reps(HX0, _nullspace(HZ0), seed_ops=[XALPHA])      # 12, X_alpha first
_LOGZ_raw = _logical_reps(HZ0, _nullspace(HX0))                    # 12
assert LOGX.shape[0] == 12 and _LOGZ_raw.shape[0] == 12

def _fix_z_basis():
    Lz = _LOGZ_raw.copy()
    ov = (Lz @ XALPHA) % 2
    piv = int(np.flatnonzero(ov)[0])
    for i in np.flatnonzero(ov)[1:]:
        Lz[i] ^= Lz[piv]
    keep = [i for i in range(12) if i != piv]
    return Lz[keep]
LOGZ_COMM = _fix_z_basis()      # the 11 Z logicals preserved by the measurement

# ----------------------------------------------------------------------
# Gadget spec -> deformed (gauged) code
# ----------------------------------------------------------------------
class SpecError(ValueError):
    pass

def parse_spec(spec):
    if not isinstance(spec, dict):
        raise SpecError(f"spec must be a dict {{'edges': [...], 'rounds': int}}, "
                        f"got {type(spec).__name__}")
    edges_in = spec.get("edges")
    rounds = spec.get("rounds", 12)
    try:
        rounds = int(rounds)
    except Exception:
        raise SpecError(f"rounds must be an int, got {rounds!r}")
    if not (1 <= rounds <= MAX_ROUNDS):
        raise SpecError(f"rounds must be in [1,{MAX_ROUNDS}], got {rounds}")
    if not isinstance(edges_in, (list, tuple)):
        raise SpecError("spec['edges'] must be a list of (u,v) label pairs")
    edges = []
    for e in edges_in:
        try:
            u, v = int(e[0]), int(e[1])
        except Exception:
            raise SpecError(f"edge {e!r} is not a pair of integer labels")
        if u == v:
            raise SpecError(f"self-loop edge {e!r} not allowed")
        if not (0 <= u < 12 + MAX_DUMMIES and 0 <= v < 12 + MAX_DUMMIES):
            raise SpecError(f"edge {e!r} uses a label outside 0..{12 + MAX_DUMMIES - 1} "
                            f"(0..11 = support vertices, 12..{12 + MAX_DUMMIES - 1} = dummies)")
        edges.append((min(u, v), max(u, v)))
    if not edges:
        raise SpecError("no edges — the graph must connect all 12 support vertices")
    if len(edges) > MAX_EDGES:
        raise SpecError(f"{len(edges)} edges exceeds the cap of {MAX_EDGES}")
    dummies = sorted({x for e in edges for x in e if x >= 12})
    verts = list(range(12)) + dummies
    adj = {v: set() for v in verts}
    for (u, v) in edges:
        adj[u].add(v); adj[v].add(u)
    seen, stack = set(), [0]
    while stack:
        x = stack.pop()
        if x in seen: continue
        seen.add(x); stack.extend(adj[x] - seen)
    missing = set(verts) - seen
    if missing:
        raise SpecError(f"graph disconnected: vertices {sorted(missing)} unreachable from "
                        f"vertex 0 — all 12 support vertices and every used dummy must lie "
                        f"in ONE connected component (Theorem 1 hypothesis)")
    return edges, dummies, rounds

def _shortest_paths(verts, edges):
    adj = {v: [] for v in verts}
    for j, (u, v) in enumerate(edges):
        adj[u].append((v, j)); adj[v].append((u, j))
    paths = {}
    for s in verts:
        prev = {s: None}; order = [s]; qi = 0
        while qi < len(order):
            x = order[qi]; qi += 1
            for (y, j) in adj[x]:
                if y not in prev:
                    prev[y] = (x, j); order.append(y)
        for t in verts:
            if t in prev:
                es = set(); x = t
                while prev[x] is not None:
                    px, j = prev[x]; es.add(j); x = px
                paths[(s, t)] = frozenset(es)
    return paths

def _min_tjoin(T, paths):
    """Exact min-weight T-join (unit weights): min over perfect matchings of T
    of the XOR of shortest paths; |T| <= 6 here so <= 15 matchings."""
    T = list(T)
    if not T:
        return frozenset()
    def matchings(rem):
        if not rem:
            yield []; return
        a = rem[0]
        for i in range(1, len(rem)):
            rest = rem[1:i] + rem[i + 1:]
            for m in matchings(rest):
                yield [(a, rem[i])] + m
    best = None
    for m in matchings(T):
        es = set(); ok = True
        for (a, b) in m:
            if (a, b) not in paths:
                ok = False; break
            es ^= set(paths[(a, b)])
        if ok and (best is None or len(es) < len(best)):
            best = es
    if best is None:
        raise SpecError("Z-check routing failed (graph not connected on required vertices)")
    return frozenset(best)

def _min_cycle_basis(verts, edges, paths):
    """Horton candidate set (SP(v,x) xor SP(v,y) xor {e}) + parallel-edge
    2-cycles; greedy independent by ascending weight -> minimum cycle basis."""
    E = len(edges)
    dim = E - len(verts) + 1
    if dim <= 0:
        return []
    cands = set()
    for j, (x, y) in enumerate(edges):
        for v in verts:
            if (v, x) in paths and (v, y) in paths:
                c = set(paths[(v, x)]) ^ set(paths[(v, y)])
                c.add(j)
                deg = {}
                for k in c:
                    for w in edges[k]:
                        deg[w] = deg.get(w, 0) + 1
                if all(d % 2 == 0 for d in deg.values()):
                    cands.add(frozenset(c))
    by_pair = {}
    for j, (x, y) in enumerate(edges):
        by_pair.setdefault((x, y), []).append(j)
    for js in by_pair.values():
        for a, b in itertools.combinations(js, 2):
            cands.add(frozenset({a, b}))
    cands = sorted(cands, key=lambda c: (len(c), sorted(c)))
    basis, cur = [], np.zeros((0, E), np.int8)
    for c in cands:
        v = np.zeros(E, np.int8)
        for j in c: v[j] = 1
        test = np.vstack([cur, v.reshape(1, -1)])
        if _rank(test) > cur.shape[0]:
            cur = test; basis.append(sorted(c))
            if len(basis) == dim:
                break
    if len(basis) != dim:
        raise SpecError("failed to construct a full cycle basis (internal error)")
    return basis

def build_gauged(edges, dummies):
    """Deformed code from the gadget graph. Data qubits: 0..2N-1 base,
    2N..2N+E-1 edges. Dummy vertices carry NO qubit (paper Remark 2)."""
    E = len(edges)
    verts = list(range(12)) + list(dummies)
    nq = 2 * N + E
    paths = _shortest_paths(verts, edges)
    pad = lambda H: np.concatenate([H, np.zeros((H.shape[0], E), np.int8)], 1)

    Av = np.zeros((len(verts), nq), np.int8)
    for i, v in enumerate(verts):
        if v < 12:
            Av[i, SUPPORT[v]] ^= 1
        for j, (a, b) in enumerate(edges):
            if v == a or v == b:
                Av[i, 2 * N + j] ^= 1
    prod = Av.sum(0) % 2
    assert (prod[:2 * N] == XALPHA).all() and (prod[2 * N:] == 0).all()

    HZroute = pad(HZ0).copy()
    route_sets = []
    for r in range(N):
        T = [i for i in range(12) if HZ0[r, SUPPORT[i]]]
        gamma = _min_tjoin(T, paths) if T else frozenset()
        for j in gamma:
            HZroute[r, 2 * N + j] ^= 1
        route_sets.append(sorted(gamma))

    basis = _min_cycle_basis(verts, edges, paths)
    stack = HZroute.copy(); base_rank = _rank(stack)
    Bp_rows, Bp_cycles = [], []
    for c in sorted(basis, key=len):
        v = np.zeros(nq, np.int8)
        for j in c: v[2 * N + j] = 1
        test = np.vstack([stack, v.reshape(1, -1)])
        rk = _rank(test)
        if rk > base_rank:
            stack = test; base_rank = rk
            Bp_rows.append(v); Bp_cycles.append(c)
    Bp = np.array(Bp_rows, np.int8) if Bp_rows else np.zeros((0, nq), np.int8)

    HXdef = np.concatenate([pad(HX0), Av], 0)
    HZdef = np.concatenate([HZroute, Bp], 0)
    assert (HXdef @ HZdef.T % 2 == 0).all(), "deformed code not CSS (internal error)"

    k_def = nq - _rank(HXdef) - _rank(HZdef)
    if k_def != 11:
        raise SpecError(f"deformed code has k={k_def}, expected 11 — this graph does not "
                        f"gauge exactly X_alpha")

    z_joins = []
    for ell in LOGZ_COMM:
        T = [i for i in range(12) if ell[SUPPORT[i]]]
        z_joins.append(sorted(_min_tjoin(T, paths) if T else frozenset()))

    return {
        "edges": edges, "dummies": dummies, "verts": verts, "nq": nq, "E": E,
        "Av": Av, "HZroute": HZroute, "Bp": Bp, "Bp_cycles": Bp_cycles,
        "route_sets": route_sets, "z_joins": z_joins,
        "HXdef": HXdef, "HZdef": HZdef,
        "n_av": Av.shape[0], "n_bp": Bp.shape[0],
        "overhead": E + Av.shape[0] + Bp.shape[0],
        "wz_max": int(HZdef.sum(1).max()), "wx_max": int(HXdef.sum(1).max()),
        "qdeg_max": int((HXdef.sum(0) + HZdef.sum(0)).max()),
        "route_w_max": max((len(s) for s in route_sets), default=0),
        "cycle_w_max": max((len(c) for c in Bp_cycles), default=0),
    }

# ----------------------------------------------------------------------
# Canonical syndrome-extraction schedule: EXACT minimum edge coloring of the
# bipartite Tanner graph (Konig's theorem: chromatic index = max degree Delta,
# via the classic alternating-path recoloring construction). This is the
# schedule the coloration circuit takes as input (Tremblay-Delfosse-Beverland
# arXiv:2109.14609, Algorithm 1: "a minimum edge coloration of T_X").
# Deterministic AND graph-agnostic: depth == Delta(Tanner graph) exactly, a
# pure invariant of the deformed code — greedy first-fit (the previous
# scheduler) could exceed Delta and depended on check iteration order, which
# muddies in-loop ablations. Edges are processed in canonical sorted order
# (check row, qubit index), so identical gadget specs give identical circuits.
# ----------------------------------------------------------------------
def min_edge_coloring(H_rows):
    """Color the 1-entries of a 0/1 check matrix (checks x qubits) with
    exactly Delta colors such that no check and no qubit sees a color twice.
    Returns (color dict {(r,q): color}, Delta)."""
    H = np.asarray(H_rows) % 2
    deg_r = H.sum(1); deg_q = H.sum(0)
    delta = int(max(deg_r.max(), deg_q.max()))
    check_col = [dict() for _ in range(H.shape[0])]   # row  -> {color: qubit}
    qubit_col = [dict() for _ in range(H.shape[1])]   # col  -> {color: row}
    color = {}
    for r in range(H.shape[0]):
        for q in np.flatnonzero(H[r]):
            q = int(q)
            a = next(c for c in range(delta) if c not in check_col[r])
            if a not in qubit_col[q]:
                c = a
            else:
                b = next(c for c in range(delta) if c not in qubit_col[q])
                if b not in check_col[r]:
                    c = b
                else:
                    # Kempe chain: walk the a/b alternating path from q (which
                    # has an a-edge but no b-edge) and swap colors along it;
                    # by parity it can never reach r, so a becomes usable.
                    path = []                       # [(row, col, old_color)]
                    side_q, cur, want = True, q, a
                    while True:
                        nxt = (qubit_col[cur] if side_q else check_col[cur]).get(want)
                        if nxt is None:
                            break
                        path.append((nxt, cur, want) if side_q else (cur, nxt, want))
                        side_q, cur, want = not side_q, nxt, (b if want == a else a)
                    for (rr, qq, old) in path:
                        del check_col[rr][old]; del qubit_col[qq][old]
                    for (rr, qq, old) in path:
                        new = b if old == a else a
                        check_col[rr][new] = qq; qubit_col[qq][new] = rr
                        color[(rr, qq)] = new
                    c = a
            color[(r, q)] = c
            check_col[r][c] = q; qubit_col[q][c] = r
    return color, delta

def color_schedule(H_rows, anc_ids):
    """Per-round CNOT ticks from the exact minimum edge coloring: tick t holds
    every (qubit, ancilla) pair whose Tanner edge got color t. len == Delta."""
    coloring, delta = min_edge_coloring(H_rows)
    ticks = [[] for _ in range(delta)]
    for (r, q), c in sorted(coloring.items()):
        ticks[c].append((int(q), int(anc_ids[r])))
    return ticks

# ----------------------------------------------------------------------
# End-to-end protocol circuit (see module docstring)
# ----------------------------------------------------------------------
def build_protocol_circuit(g, rounds, basis, p, n_base=N_BASE,
                           p_idle_frac=IDLE_FRAC):
    E, nq = g["E"], g["nq"]
    anc0 = nq
    ancX_orig = list(range(anc0, anc0 + N))
    ancAv     = list(range(anc0 + N, anc0 + N + g["n_av"]))
    ancZ_orig = list(range(anc0 + N + g["n_av"], anc0 + 2 * N + g["n_av"]))
    ancBp     = list(range(anc0 + 2 * N + g["n_av"],
                           anc0 + 2 * N + g["n_av"] + g["n_bp"]))
    n_all = anc0 + 2 * N + g["n_av"] + g["n_bp"]

    padE = lambda H: np.concatenate([H, np.zeros((H.shape[0], E), np.int8)], 1)
    HX0p, HZ0p = padE(HX0), padE(HZ0)

    base_x = color_schedule(HX0p, ancX_orig)
    base_z = color_schedule(HZ0p, ancZ_orig)
    def_x  = color_schedule(np.concatenate([HX0p, g["Av"]], 0), ancX_orig + ancAv)
    def_z  = color_schedule(np.concatenate([g["HZroute"], g["Bp"]], 0), ancZ_orig + ancBp)

    c = stim.Circuit()
    mctr = [0]
    recs = {}

    def note(tag, rnd, count):
        for i in range(count):
            recs[(tag, rnd, i)] = mctr[0] + i
        mctr[0] += count

    def mpp(vec, xtype):
        targ = []
        t = stim.target_x if xtype else stim.target_z
        for q in np.flatnonzero(vec[:nq]):
            if targ: targ.append(stim.target_combiner())
            targ.append(t(int(q)))
        c.append("MPP", targ)
        idx = mctr[0]; mctr[0] += 1
        return idx

    def noisy_phase(ticks, anc_list, xtype, tag, rnd):
        if xtype:
            c.append("RX", anc_list); c.append("Z_ERROR", anc_list, p)
        else:
            c.append("R", anc_list); c.append("X_ERROR", anc_list, p)
        active = {}
        for tick in ticks:
            for (q, a) in tick:
                c.append("CNOT", [a, q] if xtype else [q, a])
                c.append("DEPOLARIZE2", [a, q] if xtype else [q, a], p)
                active[q] = active.get(q, 0) + 1
                active[a] = active.get(a, 0) + 1
            c.append("TICK")
        k = len(ticks); p_id = p * p_idle_frac
        for q in range(nq):     # aggregated idle noise on all data+edge qubits
            idle = k - active.get(q, 0)
            if idle > 0:
                c.append("DEPOLARIZE1", q, 1 - (1 - p_id) ** idle)
        if xtype:
            c.append("Z_ERROR", anc_list, p); c.append("MX", anc_list)
        else:
            c.append("X_ERROR", anc_list, p); c.append("M", anc_list)
        note(tag, rnd, len(anc_list))

    def det(*ms):
        c.append("DETECTOR", [stim.target_rec(m - mctr[0]) for m in ms])

    c.append("R", list(range(n_all)))
    init_stabX = [mpp(HX0p[i], True) for i in range(N)]
    init_stabZ = [mpp(HZ0p[i], False) for i in range(N)]
    if basis == "X":
        init_logs = [mpp(padE(LOGX)[i], True) for i in range(12)]
    else:
        init_logs = [mpp(padE(LOGZ_COMM)[i], False) for i in range(11)]

    for r in range(n_base):
        noisy_phase(base_x, ancX_orig, True,  "bx", r)
        noisy_phase(base_z, ancZ_orig, False, "bz", r)
        if r == 0:
            for i in range(N): det(init_stabX[i], recs[("bx", 0, i)])
            for i in range(N): det(init_stabZ[i], recs[("bz", 0, i)])
        else:
            for i in range(N): det(recs[("bx", r - 1, i)], recs[("bx", r, i)])
            for i in range(N): det(recs[("bz", r - 1, i)], recs[("bz", r, i)])

    edge_q = list(range(2 * N, 2 * N + E))
    c.append("R", edge_q)
    c.append("X_ERROR", edge_q, p)

    for r in range(rounds):
        noisy_phase(def_x, ancX_orig + ancAv, True,  "dx", r)
        noisy_phase(def_z, ancZ_orig + ancBp, False, "dz", r)
        if r == 0:
            for i in range(N): det(recs[("bx", n_base - 1, i)], recs[("dx", 0, i)])
            # A_v round-1: individually random -> NO detector (their product is
            # the measurement observable; paper App. F Lemma 3)
            for i in range(N): det(recs[("bz", n_base - 1, i)], recs[("dz", 0, i)])
            for i in range(g["n_bp"]): det(recs[("dz", 0, N + i)])
        else:
            for i in range(N + g["n_av"]): det(recs[("dx", r - 1, i)], recs[("dx", r, i)])
            for i in range(N + g["n_bp"]): det(recs[("dz", r - 1, i)], recs[("dz", r, i)])

    c.append("X_ERROR", edge_q, p)
    c.append("M", edge_q)
    note("edge", 0, E)
    for i, cyc in enumerate(g["Bp_cycles"]):
        det(recs[("dz", rounds - 1, N + i)], *[recs[("edge", 0, j)] for j in cyc])

    for r in range(n_base):
        noisy_phase(base_x, ancX_orig, True,  "ax", r)
        noisy_phase(base_z, ancZ_orig, False, "az", r)
        if r == 0:
            for i in range(N): det(recs[("dx", rounds - 1, i)], recs[("ax", 0, i)])
            for i in range(N):
                det(recs[("dz", rounds - 1, i)], recs[("az", 0, i)],
                    *[recs[("edge", 0, j)] for j in g["route_sets"][i]])
        else:
            for i in range(N): det(recs[("ax", r - 1, i)], recs[("ax", r, i)])
            for i in range(N): det(recs[("az", r - 1, i)], recs[("az", r, i)])

    fin_stabX = [mpp(HX0p[i], True) for i in range(N)]
    fin_stabZ = [mpp(HZ0p[i], False) for i in range(N)]
    for i in range(N): det(recs[("ax", n_base - 1, i)], fin_stabX[i])
    for i in range(N): det(recs[("az", n_base - 1, i)], fin_stabZ[i])

    if basis == "X":
        fin_logs = [mpp(padE(LOGX)[i], True) for i in range(12)]
        obs = [init_logs[0]] + [recs[("dx", 0, N + i)] for i in range(g["n_av"])]
        c.append("OBSERVABLE_INCLUDE", [stim.target_rec(m - mctr[0]) for m in obs], 0)
        for i in range(12):
            c.append("OBSERVABLE_INCLUDE",
                     [stim.target_rec(init_logs[i] - mctr[0]),
                      stim.target_rec(fin_logs[i] - mctr[0])], 1 + i)
    else:
        fin_logs = [mpp(padE(LOGZ_COMM)[i], False) for i in range(11)]
        for i in range(11):
            targ = [init_logs[i], fin_logs[i]] + \
                   [recs[("edge", 0, j)] for j in g["z_joins"][i]]
            c.append("OBSERVABLE_INCLUDE",
                     [stim.target_rec(m - mctr[0]) for m in targ], i)

    meta = {"depth_base": (len(base_x), len(base_z)),
            "depth_def": (len(def_x), len(def_z))}
    return c, meta

def _noiseless_ok(circuit, shots=16):
    nl = circuit.without_noise()
    det, obs = nl.compile_detector_sampler().sample(shots, separate_observables=True)
    return (not det.any()) and (not obs.any())

# ----------------------------------------------------------------------
# Protocol fault-distance probes (v4). d_hat = min over
#   * R                        — the measurement observable's timelike cap: a
#                                chain of R measurement flips on ONE A_v check
#                                is undetectable and flips the recorded outcome
#                                (Cross et al. arXiv:2407.18393 Lemma 9:
#                                measurement fault distance = min(R, ...));
#   * dressed-logical attack   — BP+OSD low-weight-logical search on the
#                                deformed code's X and Z sides (the quantities
#                                d_X(L* S_X) / d_Z(...) of Cross et al.
#                                Lemma 10; a found weight-w dressed logical is
#                                an undetectable weight-w protocol fault set);
#   * stim circuit probe       — shortest_graphlike_error + budgeted
#                                search_for_undetectable_logical_errors on the
#                                ACTUAL protocol circuits (catches circuit-
#                                level, schedule-induced sets the code-level
#                                attack cannot see).
# All parts are UPPER-bound estimators: a small result certifies vulnerability;
# a large result proves nothing — which is the safe direction for a gate (see
# ANTI-GAMING). Randomization (fresh seed per eval, random column permutations
# + prior jitter across trials, arXiv:2603.22532 recipe) keeps the attack from
# being systematically blind to a lineage.
# ----------------------------------------------------------------------
def dressed_logical_attack(g, rng, trials=None, osd_order=6):
    """Min-weight dressed logical of the deformed code, per CSS side.
    Returns {"X": {"weight": int|None, "support": str}, "Z": {...}}."""
    trials = ATTACK_TRIALS if trials is None else trials
    nq = g["nq"]
    out = {}
    for side in ("X", "Z"):
        # side "X": light X-type logicals v with HZdef v = 0 that anticommute
        # with some Z-side logical witness w (v nontrivial in the deformed
        # code). Side "Z" symmetric.
        Hdual = g["HZdef"] if side == "X" else g["HXdef"]
        Hstab = g["HXdef"] if side == "X" else g["HZdef"]
        wits = _logical_reps(Hdual, _nullspace(Hstab))
        A = (np.vstack([Hdual, np.zeros((1, nq), np.int8)]) % 2).astype(np.uint8)
        syn = np.zeros(A.shape[0], np.uint8); syn[-1] = 1
        best, best_vec = None, None
        min_ops = set()      # distinct operators found AT the best weight
        for w in wits:
            A[-1] = (w % 2).astype(np.uint8)
            for t in range(trials):
                # Diversity schedule (rare light operators live in narrow OSD
                # basins — a fixed configuration repeatedly finds the SAME
                # heavier solution): alternate column permutations, growing
                # prior jitter, edge-biased priors (light dressed logicals
                # concentrate on cut edges), and BP/OSD depth.
                perm = rng.permutation(nq) if t % 2 else np.arange(nq)
                pr = np.full(nq, 0.05)
                if t >= 4 and t % 4 == 0:
                    pr[2 * N:] = 0.20
                if t >= 1:
                    pr = np.clip(pr * np.exp(rng.normal(0.0, 0.5 + 0.1 * t, nq)),
                                 1e-4, 0.4)
                try:
                    dec = _LDPC_BPOSD(_CSR(A[:, perm]),
                                      channel_probs=list(pr[perm]),
                                      max_iter=(20 if t % 3 == 0 else 60),
                                      bp_method="minimum_sum",
                                      ms_scaling_factor=0.625,
                                      osd_method="OSD_CS",
                                      osd_order=(osd_order if t % 2 == 0
                                                 else osd_order + 2))
                    sol = np.asarray(dec.decode(syn), np.uint8)
                except Exception:
                    continue
                e = np.zeros(nq, np.uint8)
                e[perm] = sol
                if ((Hdual @ e) % 2).any() or int(w @ e) % 2 != 1:
                    continue
                wgt = int(e.sum())
                if best is None or wgt < best:
                    best, best_vec = wgt, e.copy()
                    min_ops = {tuple(np.flatnonzero(e))}
                elif wgt == best:
                    min_ops.add(tuple(np.flatnonzero(e)))
        supp = ""
        if best_vec is not None:
            base_supp = [int(q) for q in np.flatnonzero(best_vec[:2 * N])]
            on_meas = sorted(i for i, q in enumerate(SUPPORT) if best_vec[q])
            eids = [int(j) for j in np.flatnonzero(best_vec[2 * N:])]
            supp = (f"{len(base_supp)} base-code qubits"
                    + (f" (touching support labels {on_meas})" if on_meas else "")
                    + (f" + gadget edges {[g['edges'][j] for j in eids]}" if eids else ""))
        out[side] = {"weight": best, "support": supp,
                     "n_min": max(1, len(min_ops))}
    return out


def stim_circuit_probe(circuit):
    """Budgeted undetectable-logical search on one protocol circuit.
    Returns the min found weight, or None if nothing was found within the
    exploration caps (no evidence — NOT a certificate of safety)."""
    found = []
    try:
        errs = circuit.shortest_graphlike_error(ignore_ungraphlike_errors=True)
        if errs:
            found.append(len(errs))
    except Exception:
        pass
    try:
        errs = circuit.search_for_undetectable_logical_errors(
            dont_explore_detection_event_sets_with_size_above=STIM_PROBE_CAPS[0],
            dont_explore_edges_with_degree_above=STIM_PROBE_CAPS[1],
            dont_explore_edges_increasing_symptom_degree=True,
            canonicalize_circuit_errors=False,
        )
        if errs:
            found.append(len(errs))
    except Exception:
        pass
    return min(found) if found else None


def estimate_fault_distance(g, rounds, circ_x, circ_z, rng):
    """Combine all probe parts. Returns (d_hat, parts, counts, weakest, attack).
    counts[k] = first-order multiplicity estimate for part k's fault sets
    (n_av parallel A_v chains for the timelike part; the number of DISTINCT
    minimum-weight operators the attack found for the dressed parts; 1 for
    the stim finds)."""
    attack = dressed_logical_attack(g, rng)
    parts = {
        "rounds": rounds,
        "dressed_x": attack["X"]["weight"],
        "dressed_z": attack["Z"]["weight"],
        "stim_x": stim_circuit_probe(circ_x),
        "stim_z": stim_circuit_probe(circ_z),
    }
    counts = {
        "rounds": g["n_av"],
        "dressed_x": attack["X"]["n_min"],
        "dressed_z": attack["Z"]["n_min"],
        "stim_x": 1,
        "stim_z": 1,
    }
    live = {k: v for k, v in parts.items() if v is not None}
    weakest = min(live, key=live.get)
    return int(live[weakest]), parts, counts, weakest, attack


def _comb(n, k):
    from math import comb
    return comb(n, k)


def tail_bound(parts, counts, p, shots=None):
    """First-order failure probability of the probe-found fault sets at
    physical rate p: sum over parts of N * C(w, ceil(w/2)) * p^ceil(w/2)
    (a weight-w undetectable set defeats the decoder once ceil(w/2) of its
    locations fault). If `shots` is given, sets Monte Carlo already samples
    (expected occurrences >= TAIL_MIN_EXPECT in the budget) are SKIPPED —
    their failures are in the measured number; pricing them again would
    double-count. Stim finds are included only when strictly lighter than
    every other part (else they duplicate the timelike/dressed sets)."""
    total = 0.0
    non_stim = [v for k, v in parts.items()
                if v is not None and not k.startswith("stim")]
    floor_w = min(non_stim) if non_stim else None
    for k, w in parts.items():
        if w is None:
            continue
        if k.startswith("stim") and floor_w is not None and w >= floor_w:
            continue
        contrib = counts.get(k, 1) * _comb(int(w), (int(w) + 1) // 2) \
            * p ** ((int(w) + 1) // 2)
        if shots is not None and contrib * shots >= TAIL_MIN_EXPECT:
            continue
        total += contrib
    return total


# Reference-curve slope (decades of LER per decade of p), from the calibrated
# three-point curve — used only for the reported "advantage floor" diagnostic.
def _ref_slope():
    try:
        return float((np.log10(LER_REFS[2]) - np.log10(LER_REFS[0]))
                     / (np.log10(P_GRID[2]) - np.log10(P_GRID[0])))
    except Exception:
        return 4.5


def tail_crossover_p(parts, counts):
    """The physical rate below which the probe-found tail overtakes 10% of the
    reference's extrapolated curve — i.e. how far DOWN in p the candidate's
    measured-LER advantage remains meaningful ("valid down to p ~ ...").
    The tail's leading order is lower than the reference slope whenever a
    light fault set exists, so tail/ref GROWS as p falls: scanning downward
    from the gate point, the first p where tail >= 0.1*ref is the crossover.
    Returns None if the tail never reaches that level above p = 1e-6 (no
    floor anywhere in the plausible hardware range)."""
    slope = _ref_slope()
    for lp in np.linspace(float(np.log10(P_GATE)), -6.0, 400):
        p = float(10.0 ** lp)
        ref = LER_REFS[1] * (p / P_GATE) ** slope
        if tail_bound(parts, counts, p) >= 0.1 * ref:
            return p
    return None

# ----------------------------------------------------------------------
# Sampling
# ----------------------------------------------------------------------
REF_MECHS = 90_000    # ~error mechanisms of the reference gate-point circuit;
                      # every point's shot cap scales inversely with a
                      # candidate's mechanism count so eval wall-clock stays
                      # ~flat as gadgets grow (statistics matter most near the
                      # frontier, where gadgets are small)

def sample_curve(circs, budget_scale=1.0):
    """circs: [(p, circ_x, circ_z)] in P_GRID order. Runs all six circuits in
    ONE sinter fan-out (per-point error budgets + mechanism-scaled shot caps),
    so the worker pool stays saturated through the tail. Returns a list of
    per-point dicts. budget_scale < 1 shrinks both budgets uniformly (used for
    candidates that already failed the fault-distance gate: their margins only
    shape the infeasible-branch gradient, so cheap estimates suffice)."""
    mechs = max(circs[1][1].detector_error_model().num_errors,
                circs[1][2].detector_error_model().num_errors)
    scale = min(1.0, REF_MECHS / max(1, mechs)) * budget_scale
    tasks = []
    nominals = []
    for i, ((p, cx, cz), (max_err, max_sh)) in enumerate(zip(circs, P_BUDGET)):
        cap = int(max(400, max_sh * scale))
        nominals.append(int(max(400, max_sh * budget_scale)))
        for basis, c in (("X", cx), ("Z", cz)):
            tasks.append(_SINTER_TASK(
                circuit=c, json_metadata={"i": i, "basis": basis},
                collection_options=_SINTER_OPTIONS(
                    max_errors=max(10, int(max_err * budget_scale)),
                    max_shots=cap)))
    results = _SINTER_COLLECT(
        num_workers=N_WORKERS,
        tasks=tasks,
        decoders=["bposd0"],
        custom_decoders={"bposd0": _SINTER_DEC_CLS(
            max_bp_iters=BP_ITERS, osd_order=OSD_ORDER, osd_method="osd0")},
        print_progress=False,
    )
    out = {}
    for r in results:
        out[(r.json_metadata["i"], r.json_metadata["basis"])] = (int(r.errors), int(r.shots))
    curve = []
    for i, (p, cx, cz) in enumerate(circs):
        ex, sx = out.get((i, "X"), (0, 0))
        ez, sz = out.get((i, "Z"), (0, 0))
        if sx == 0 or sz == 0:
            raise RuntimeError(f"sinter returned no shots for p={p}")
        px, pz = ex / sx, ez / sz
        overall = 1.0 - (1.0 - px) * (1.0 - pz)
        # Resolution floor keyed to the NOMINAL per-point shot budget, NOT the
        # mechanism-scaled actual shots — so a zero-error point contributes a
        # candidate-SIZE-INDEPENDENT value to the score (a big gadget that
        # sampled fewer shots is not penalised, nor a small one rewarded, purely
        # by the shot cap). "0 errors" means "at least this good"; we credit the
        # nominal resolution uniformly. (budget_scale shrinks the nominal too.)
        nominal = nominals[i]
        overall_eff = overall if overall > 0.0 else 1.0 / nominal
        curve.append({"p": p, "px": px, "pz": pz, "overall": overall,
                      "overall_eff": overall_eff, "resolution": 1.0 / nominal,
                      "ex": ex, "sx": sx, "ez": ez, "sz": sz})
    return curve

# ----------------------------------------------------------------------
# ShinkaEvolve entry point
# ----------------------------------------------------------------------
def _crash(text):
    return {"combined_score": CRASH_SCORE, "correct": False,
            "public": {"valid": 0}, "private": {}, "extra_data": {},
            "text_feedback": text}

def _invalid(reason):
    return {"combined_score": INVALID_SCORE, "correct": True,
            "public": {"valid": 0, "reason": reason}, "private": {},
            "extra_data": {},
            "text_feedback": f"INVALID GADGET (score {INVALID_SCORE:.0f}): {reason}"}

def aggregate_fn(results: list) -> dict:
    if not all(np.isfinite(r) and r > 0 for r in LER_REFS):
        return _crash("LER_REFS is not calibrated — run calibrate.py and set the "
                      "three constants in evaluate.py (or GAUGE_LER_REF_LO/GATE/HI)")
    if not results:
        return _crash("run_experiment returned no result.")
    propose = results[0]
    if not callable(propose):
        return _crash(f"run_experiment must return the propose_gadget callable, "
                      f"got {type(propose).__name__}.")
    try:
        spec = propose()
    except Exception:
        return _crash("Candidate propose_gadget() crashed:\n" + traceback.format_exc())

    try:
        edges, dummies, rounds = parse_spec(spec)
        g = build_gauged(edges, dummies)
    except SpecError as e:
        return _invalid(str(e))
    except Exception:
        return _crash("Gadget construction crashed:\n" + traceback.format_exc())

    t0 = time.time()
    circs = []
    meta = None
    for p in P_GRID:
        cx, m = build_protocol_circuit(g, rounds, "X", p)
        cz, _ = build_protocol_circuit(g, rounds, "Z", p)
        circs.append((p, cx, cz))
        if p == P_GRID[1]:
            meta = m
    if not (_noiseless_ok(circs[1][1]) and _noiseless_ok(circs[1][2])):
        # Should be impossible for a spec that passed build_gauged; treat as
        # an evaluator-side assertion, not a candidate mistake.
        return _crash("protocol circuit failed the noiseless determinism self-check "
                      "(evaluator invariant violated — report this)")
    build_s = time.time() - t0

    # ---- protocol fault-distance probes (the MC-invisible-tail PRICER) ----
    t0 = time.time()
    rng = np.random.default_rng()          # fresh probe seed per eval
    try:
        d_hat, d_parts, d_counts, weakest, attack = estimate_fault_distance(
            g, rounds, circs[1][1], circs[1][2], rng)
    except Exception:
        return _crash("fault-distance probe crashed:\n" + traceback.format_exc())
    probe_s = time.time() - t0

    t0 = time.time()
    try:
        curve = sample_curve(circs)
    except Exception:
        return _crash("sinter sampling crashed:\n" + traceback.format_exc())
    sim_s = time.time() - t0

    lo, mid, hi = curve
    n_err_gate = mid["ex"] + mid["ez"]
    est_std = 0.434 / np.sqrt(n_err_gate) if n_err_gate > 0 else None
    # Tail pricing: add each probe-found fault set's first-order failure to
    # the measured LER at every scored point — but only the sets Monte Carlo
    # could NOT have seen at that point's shot budget (the visible ones are
    # already inside the measured number). For healthy designs this adds ~0;
    # for a collapsed design it restores the failure probability the sampling
    # is blind to, so the 1.1x comparison stays honest without gating.
    shots_pt = [c["sx"] + c["sz"] for c in curve]
    tails = [tail_bound(d_parts, d_counts, P_GRID[i], shots=shots_pt[i])
             for i in range(3)]
    # Per-point TRUE margin vs the calibrated PAPER-gadget curve (positive =
    # strictly better than the reference at that p), on the tail-priced
    # effective LER, clamped at each point's candidate-independent resolution
    # bound so a zero-error (floored) point cannot claim more headroom than
    # the shot budget resolves.
    margins = [min(float(np.log10(LER_REFS[i] / (curve[i]["overall_eff"] + tails[i]))),
                   float(np.log10(LER_REFS[i] / curve[i]["resolution"])))
               for i in range(3)]
    worst_margin = min(margins[0], margins[1])   # worst case over scored points
    # Noise-aware feasibility: reject only when DEMONSTRABLY beyond the
    # RATIO_LIMIT — the allowance is the ratio (0.041 decades at 1.1x) plus
    # NOISE_Z sigma of the point's sampling std and the reference-calibration
    # std in quadrature. At the default budgets this is ~0.12-0.17 decades;
    # a strict 1.1x verdict needs the offline high-budget certification.
    def _sigma(c):
        n = c["ex"] + c["ez"]
        return float(0.434 / np.sqrt(max(1, n)))
    allow = [LOG_RATIO + NOISE_Z * float(np.hypot(_sigma(curve[i]), SIGMA_REF))
             for i in range(3)]
    ler_ok = (margins[1] >= -allow[1]) and (margins[0] >= -allow[0])
    protected = (D_TARGET <= 0) or (d_hat >= D_TARGET)
    cross_p = tail_crossover_p(d_parts, d_counts)
    # empirical scaling exponent d_eff ~ 2 * slope of log10(LER) vs log10(p),
    # over points with enough observed errors to mean anything (diagnostic)
    fit_pts = [(np.log10(P_GRID[i]), np.log10(curve[i]["overall_eff"]))
               for i in range(3) if curve[i]["ex"] + curve[i]["ez"] >= 5]
    if len(fit_pts) >= 2:
        xs, ys = zip(*fit_pts)
        d_eff = float(2.0 * np.polyfit(xs, ys, 1)[0])
    else:
        d_eff = None
    Q = g["overhead"]

    part_names = {"rounds": f"R={rounds} (timelike cap on the measurement outcome)",
                  "dressed_x": "dressed X-logical of the deformed code",
                  "dressed_z": "dressed Z-logical of the deformed code",
                  "stim_x": "circuit-level fault set (X-basis stim search)",
                  "stim_z": "circuit-level fault set (Z-basis stim search)"}
    cross_str = (f"tail-vs-reference crossover at p~{cross_p:.1e} (below that "
                 f"rate the lightest found fault sets dominate the comparison)"
                 if cross_p is not None else
                 "no tail crossover above p=1e-6 (found fault sets stay "
                 "negligible across the plausible hardware range)")
    dstr = (f"probed fault sets: lightest weight {d_hat} ({part_names[weakest]}) "
            f"[parts R/dressedX/dressedZ/stimX/stimZ = {d_parts['rounds']}/"
            f"{d_parts['dressed_x']}/{d_parts['dressed_z']}/{d_parts['stim_x']}/"
            f"{d_parts['stim_z']}; priced into the effective LER at "
            f"{tails[1]:.1e} @gate p, not gated]; {cross_str}")
    depths = (f"SE depth/round: X={meta['depth_def'][0]} Z={meta['depth_def'][1]} ticks "
              f"(= exact Tanner-graph max degree; base code: "
              f"{meta['depth_base'][0]}/{meta['depth_base'][1]})")
    wstr = (f"max deformed check weight Z={g['wz_max']} X={g['wx_max']}, "
            f"max qubit degree {g['qdeg_max']} (reference profile 7/7 — heavy "
            f"checks/degrees are priced through schedule depth and idle noise), "
            f"worst Z-check routing +{g['route_w_max']} edges, longest flux cycle "
            f"{g['cycle_w_max']}")
    deff_str = f"{d_eff:.1f}" if d_eff is not None else "n/a (too few errors)"
    lstr = (f"measured noise curve (overall protocol error): "
            f"{lo['overall_eff']:.2e} @p={P_GRID[0]:.4g} | "
            f"{mid['overall_eff']:.2e} @p={P_GRID[1]:.4g} [GATE point, "
            f"X {mid['ex']}/{mid['sx']}, Z {mid['ez']}/{mid['sz']}"
            + (f", +-{est_std:.3f} decades" if est_std else "") + f"] | "
            f"{hi['overall_eff']:.2e} @p={P_GRID[2]:.4g}; d_eff~{deff_str}; "
            f"tail-priced margins vs reference (low/gate/high p): "
            f"{margins[0]:+.2f}/{margins[1]:+.2f}/{margins[2]:+.2f} decades; "
            f"feasibility: within {RATIO_LIMIT}x of the reference at low+gate, "
            f"i.e. margin >= -(={LOG_RATIO:.3f} + {NOISE_Z}sigma) = "
            f"-{allow[0]:.2f}/-{allow[1]:.2f}; bonus = worst case of low+gate, "
            f"only above 0")

    if ler_ok and protected:
        bonus = W_LER * min(BONUS_CAP, max(0.0, worst_margin))
        score = float(Q_REF - Q) + float(bonus)
        verdict = (
            f"FEASIBLE at {Q} added elements ({g['E']} edge qubits + "
            f"{g['n_av']} A_v + {g['n_bp']} B_p checks), R={rounds}; score={score:+.2f} "
            f"(elements saved vs the 41-element paper reference {Q_REF - Q:+d}, "
            f"worst-case LER dominance bonus {bonus:+.2f}). {lstr}. {dstr}. {depths}; "
            f"{wstr}. To improve: remove elements (edges / dummies / implied checks) "
            f"while keeping the tail-priced curve within {RATIO_LIMIT}x of the "
            f"reference at BOTH scored points, or earn the bonus by genuinely beating "
            f"the reference curve (worst case counts). The probes price what sampling "
            f"cannot see: light fault sets (low R -> weight-R chains of A_v "
            f"measurement flips on the outcome; sparse cuts -> light dressed "
            f"logicals, support reported when found) are charged their analytic "
            f"failure rate, so a cheap design must be GENUINELY cheap at the "
            f"benchmark noise rates, not just below the sampling floor. Dummy "
            f"vertices can shorten routings / split heavy checks (depth = Tanner "
            f"max degree)."
        )
    elif not protected:
        # Optional distance-preserving campaign mode (GAUGE_DTARGET > 0 only).
        score = float(max(-30.0,
                          -8.0
                          + min(0.0, margins[1] + allow[1])
                          + min(0.0, margins[0] + allow[0])
                          - 1.5 * float(D_TARGET - d_hat)))
        verdict = (
            f"UNPROTECTED (distance-preserving mode, GAUGE_DTARGET={D_TARGET}): "
            f"lightest probed fault set has weight {d_hat} < {D_TARGET} "
            f"({part_names[weakest]}). score={score:.2f}. {dstr}. {lstr}. "
            f"Config: {Q} elements, R={rounds}. {depths}; {wstr}."
        )
    else:
        score = float(max(-30.0,
                          -8.0
                          + min(0.0, margins[1] + allow[1])
                          + min(0.0, margins[0] + allow[0])))
        which = ("gate point" if margins[1] < -allow[1] else "low point")
        tail_note = ""
        if tails[1] > 0.2 * mid["overall_eff"] or tails[0] > 0.2 * lo["overall_eff"]:
            tail_note = (f" NOTE: the priced tail ({tails[0]:.1e}/{tails[1]:.1e} at "
                         f"low/gate p) is a significant part of the effective error — "
                         f"the design's lightest fault sets, not its bulk, are what "
                         f"break the comparison; see the probed parts above.")
        verdict = (
            f"NOT WITHIN {RATIO_LIMIT}x OF THE REFERENCE: tail-priced protocol error "
            f"is demonstrably beyond the limit at the {which} (margins low/gate "
            f"{margins[0]:+.2f}/{margins[1]:+.2f} vs noise-aware allowances "
            f"-{allow[0]:.2f}/-{allow[1]:.2f}); score={score:.2f}.{tail_note} {dstr}. "
            f"{lstr}. Config: {Q} elements ({g['E']} edges, {g['n_av']} A_v, "
            f"{g['n_bp']} B_p), R={rounds}. {depths}; {wstr}. Likely causes: heavy "
            f"checks/degrees inflating schedule depth and idle noise (split long "
            f"routings/cycles with dummy vertices), far too many rounds adding pure "
            f"exposure, too few rounds letting outcome flips through (that cost is "
            f"measured AND priced), or simply too many noisy elements — the paper "
            f"reference sets this bar, so match its economy."
        )

    public = {
        "combined_score": round(score, 3), "valid": 1,
        "overall_ler": mid["overall_eff"], "x_ler": mid["px"], "z_ler": mid["pz"],
        "ler_lo": lo["overall_eff"], "ler_hi": hi["overall_eff"],
        "margin_lo": round(margins[0], 3), "margin_gate": round(margins[1], 3),
        "margin_hi": round(margins[2], 3),
        "d_eff_est": (round(d_eff, 2) if d_eff is not None else None),
        "fault_dist_est": d_hat,
        "tail_lo": float(f"{tails[0]:.3g}"), "tail_gate": float(f"{tails[1]:.3g}"),
        "tail_crossover_p": (float(f"{cross_p:.3g}") if cross_p is not None else None),
        "d_rounds": d_parts["rounds"],
        "d_dressed_x": d_parts["dressed_x"], "d_dressed_z": d_parts["dressed_z"],
        "d_stim_x": d_parts["stim_x"], "d_stim_z": d_parts["stim_z"],
        "elements": Q, "edge_qubits": g["E"], "av_checks": g["n_av"],
        "bp_checks": g["n_bp"], "dummies": len(dummies), "rounds": rounds,
        "depth_x": meta["depth_def"][0], "depth_z": meta["depth_def"][1],
        "wz_max": g["wz_max"], "wx_max": g["wx_max"], "qdeg_max": g["qdeg_max"],
        "route_w_max": g["route_w_max"], "cycle_w_max": g["cycle_w_max"],
    }
    private = {
        "ler_refs": list(LER_REFS),
        "ratio_limit": RATIO_LIMIT, "noise_z": NOISE_Z, "sigma_ref": SIGMA_REF,
        "allowances": [round(a, 4) for a in allow],
        "d_target": D_TARGET, "w_ler": W_LER,
        "q_ref": Q_REF, "p_grid": list(P_GRID),
        "tail_counts": d_counts,
        "attack_supports": {s: attack[s]["support"] for s in ("X", "Z")},
        "shots": [[c["sx"], c["sz"]] for c in curve],
        "errors": [[c["ex"], c["ez"]] for c in curve],
        "score_std_decades": est_std,
        "build_s": round(build_s, 1), "probe_s": round(probe_s, 1),
        "sim_s": round(sim_s, 1),
    }
    return {"combined_score": score, "correct": True, "public": public,
            "private": private, "extra_data": {}, "text_feedback": verdict}

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

def main(program_path: str, results_dir: str) -> None:
    _force_utf8_stdio()
    print(f"Evaluating program: {program_path}")
    print(f"Saving results to: {results_dir}")
    os.makedirs(results_dir, exist_ok=True)
    # Boot guard: refuse to run uncalibrated rather than silently scoring every
    # candidate -1000 (which would burn Azure mutation/meta budget on a run with
    # zero signal). Mirrors the harness's task_sys_msg boot refusal.
    if not all(np.isfinite(r) and r > 0 for r in LER_REFS):
        raise SystemExit(
            "gross_code_gauging is UNCALIBRATED: LER_REFS = "
            f"{LER_REFS}. Run `python tasks/gross_code_gauging/calibrate.py`, then set the "
            "three __CAL_*__ placeholder defaults in evaluate.py (or export "
            "GAUGE_LER_REF_LO/GATE/HI) before evolving. See the README Calibration section.")
    print(f"Benchmark: p grid={tuple(round(p, 5) for p in P_GRID)}, "
          f"feasibility = tail-priced LER within {RATIO_LIMIT}x (+{NOISE_Z} sigma) of "
          f"reference {tuple(f'{r:.2e}' for r in LER_REFS)}"
          + (f", HARD fault-distance gate {D_TARGET} (GAUGE_DTARGET)" if D_TARGET > 0
             else ", probed fault tails priced (no distance gate)")
          + f", budgets per circuit={P_BUDGET}, {N_WORKERS} workers")
    metrics, correct, err = run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="run_experiment",
        num_runs=1,
        get_experiment_kwargs=lambda i: {},
        aggregate_metrics_fn=aggregate_fn,
        validate_fn=None,
    )
    if not correct:
        print(f"Evaluation reported correct=False: {err}")
    else:
        print("Evaluation completed successfully.")
    print(f"combined_score = {metrics.get('combined_score')!r}")
    if isinstance(metrics.get("public"), dict):
        for k, v in metrics["public"].items():
            print(f"  public.{k} = {v!r}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gross_code_gauging end-to-end evaluator")
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)

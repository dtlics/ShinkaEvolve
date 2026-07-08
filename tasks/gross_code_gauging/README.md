# `gross_code_gauging` — end-to-end gauging-gadget design on the gross code (v2)

ShinkaEvolve task: design the complete **gauging-measurement gadget** for the
weight-12 logical X̄_α of the **[[144,12,12]] gross code** — the construction of
**Williamson & Yoder, "Low-overhead fault-tolerant quantum computation by gauging
logical operators"** ([arXiv:2410.02213](https://arxiv.org/abs/2410.02213),
*Nature Physics* **22**, 598, 2026) — evaluated **end-to-end at circuit level**:
the score is the measured error of the actual measurement protocol plus the
ancilla overhead, not a distance proxy.

## Why v2 (what was wrong with v1)

v1 evolved only *extra edges among the 12 fixed support vertices*, hard-gated on
a **BP+OSD estimate of the deformed-code distance**, and scored pure qubit count.
Three verified problems:

1. **The gate was an upper bound the paper never trusted.** The paper used BP+OSD
   only as a fast filter on random-edge trials and then *proved* d\*=12 with
   integer programming (App. B). v1 made the filter itself the gate, and its own
   docs admit the resulting reward-hack pressure (a drafted seed over-reported
   12 when the true distance was ≤10).
2. **Even exact distance is the wrong figure of merit for a measurement gadget.**
   The gross code's own depth-7 schedule has circuit-level distance ≤ 10 < d = 12
   (Bravyi et al., Nature 2024, Table 1); schedule choice alone moves circuit-level
   LER by 7–8× (ASC, arXiv:2603.21499) and more merge rounds — *higher* timelike
   distance — can *worsen* the total error (Cross–He–Rall–Yoder, arXiv:2407.18393
   §4.2: R=7 beats R=d=12 at p=10⁻³).
3. **The design space excluded the paper's own ideas.** Under distance+qubits,
   dummy vertices and thickening are strictly losing moves (they cost qubits and
   buy only check weight/degree, which v1 declared "not your job"). Evolution
   could never rediscover the paper's stacked/cellulated designs. End-to-end
   error is exactly the quantity those designs improve.

## The design space (the paper's, in full)

`propose_gadget()` in the EVOLVE-BLOCK of [initial.py](initial.py) returns

```python
{"edges": [(u, v), ...], "rounds": R}
```

- **Labels 0..11** — the 12 qubits of supp(X̄_α) (monomials of f, App. B).
- **Labels 12..35** — optional **dummy vertices** (Remark 2): no physical qubit;
  A_v = ∏ X on incident edge qubits. This is how the paper expresses Shor-style
  stars (Remark 12), surgery grids (Remark 11) and the **thickened / cellulated
  layer stacks** (Definition 3) — all reachable by evolution here.
- **Edges** — one new data qubit each (init |0⟩); multigraph allowed (the
  double-gross example doubles an edge); no self-loops. ≤ 60 edges, ≤ 24 dummies.
- **R ∈ [1,24]** — deformed-code syndrome rounds: the measurement outcome is
  protected in time only by the R repeats of the A_v checks (fault distance
  min(R, spatial)), while every extra round exposes all 12 logical qubits to
  more noise. Genuinely nontrivial optimum (see Cross et al. above).

The 18 matching edges (support pairs sharing a Z-check — the weight-1
deformation motif) are **provided but not forced**: any connected graph is
valid; the evaluator routes every Z-check by exact minimum-weight T-joins.

## What the evaluator does (all derived, never evolved)

[evaluate.py](evaluate.py), faithful to the paper:

1. **Deformed code**: A_v Gauss-law checks; original Z-checks routed by exact
   min-weight T-joins (Def. 2's "minimum weight path" convention); flux checks
   B_p on a minimum-weight cycle basis (Horton), reduced by the BB Z-check
   redundancy exactly as in App. B. *Validation:* fed the paper's 22-edge graph,
   this machinery reproduces the published gadget exactly — 12 A_v, **7 B_p
   (five triangles + two squares)**, max check weight 7, max qubit degree 7,
   41 added elements.
2. **Schedule**: the canonical deterministic scheduler — an **exact minimum
   edge coloring of the bipartite Tanner graph** (König's theorem, constructive
   alternating-path algorithm; property-tested proper and Δ-optimal in
   [test_coloring.py](test_coloring.py)). This is
   literally the input the coloration circuit asks for (Tremblay–Delfosse–
   Beverland arXiv:2109.14609, Algorithm 1: "a minimum edge coloration of
   T_X"). Depth per phase **equals the deformed Tanner graph's max degree Δ,
   exactly** — a pure invariant of the gadget, independent of construction
   order (the previous greedy first-fit could exceed Δ and depended on check
   iteration order, muddying in-loop ablations; it wasted 2 ticks/phase on the
   reference: 8/8 vs the true 6/7). Scheduling is *evaluated* (depth→idle
   noise, hook propagation), not evolved — schedule search is its own task
   ([../bb_syndrome_sched/](../bb_syndrome_sched/)).
3. **Protocol circuit** (stim), following Cross–He–Rall–Yoder §3.2 adapted to
   gauging: ideal MPP brackets → 1 noisy base round → gauge-in (edge qubits
   |0⟩) → R noisy deformed rounds → gauge-out (edge MZ) → 1 noisy base round →
   ideal MPPs. First-round A_v outcomes are individually random (no detector —
   App. F Lemma 3); their product × the initial ideal X̄_α MPP is the
   **measurement observable**. B_p and deformed checks get gauge-in/out boundary
   detectors; the ungauging byproduct enters the Z-logical observables as
   edge-readout parities. Both circuits are noiseless-deterministic (self-checked
   every eval).
4. **Noise — a three-point curve, not a single rate**: uniform circuit-level
   depolarizing sampled at **p ∈ {1.4, 2.0, 2.8}×10⁻³** (0.7×, 1×, 1.4× the
   gate rate `GAUGE_PHYS_P`): DEPOLARIZE2 after each CNOT, measure/reset flips,
   and per-phase aggregated idle noise (p/10 per idle tick) so **schedule depth
   is priced**. The hard gate lives at the center point; the score bonus
   averages the margin over the curve; and the fitted scaling exponent
   **d_eff ≈ 2·Δlog₁₀(LER)/Δlog₁₀(p)** is reported — a gadget that passes at
   one noise rate but has a flat curve (collapsed effective distance) is
   visible and under-rewarded. This is the GeneCS-style multi-p evaluation
   made quantitative.
5. **Decode + sample**: BP+OSD-0 (stimbposd/ldpc; 12 BP iterations, `osd0`) via
   one sinter fan-out over all six circuits on ~20 workers (per-point error
   budgets `P_BUDGET = (40,1500),(45,2000),(30,800)` errors/shot-cap per
   circuit; low-p and gate feed the score, high-p is diagnostic-only and
   lightest; caps scaled down for oversized gadgets), fresh seed per eval.
   Deliberately fast-but-weak (~5× faster than BP+LSD here at equal observed
   accuracy): absolute LERs are not paper-comparable, but candidates and the
   reference are decoded identically, so the relative gate is self-consistent
   (same philosophy as `bb_syndrome_sched`'s `osd_order=3`). Two circuits: X-basis (measurement observable + preservation
   of the 12 X̄ logicals) and Z-basis (preservation of the 11 Z̄ logicals that
   commute with X̄_α, byproduct-corrected).

## Score

```
Q          = edge qubits + A_v checks + B_p checks    (paper gadget: 22+12+7 = 41)
overall(p) = 1 - (1 - p_X)(1 - p_Z)                   (measured, per curve point)
margin(p)  = log10( 2 x LER_REFS[p] / overall(p) )    (headroom vs the calibrated
                                                       paper-gadget curve, clamped
                                                       at each point's resolution)

crash / garbage return                    ->  -1000  (correct=False)
invalid gadget (named reason)             ->  -100
valid, margin(p_gate) < 0  (unreliable)   ->  -8 + margin(p_gate)                 (>= -30)
valid, margin(p_gate) >= 0 (reliable)     ->  (41 - Q) + min(2, ½·margin(p_lo) + ½·margin(p_gate))
```

Reproducing the paper's gadget scores ~0..+2; every element saved below 41
while staying reliable is +1. The bonus is the **low-p-weighted** headroom
(low + gate points, high-p dropped) — *not* a symmetric average: over a
log-symmetric grid the mean of all three margins algebraically cancels the
candidate's slope (a steep design's low-p gain is offset by its high-p loss),
so it would reward only curve *level*. Weighting toward low p makes a flat
curve — whose low-p error barely drops below the paper's, which does drop —
score strictly lower at the same gate-point error, so real distance-like
protection is what earns the bonus. Each point's margin is clamped at its
candidate-independent resolution bound, so a zero-error (floored) point can't
claim more headroom than its shot budget resolves, nor couple the score to
circuit size. The hard gate uses only the well-budgeted center point (gate
flips from sampling noise stay ~7σ away); the whole bonus is a bounded ±2
tiebreaker under the integer element-count ladder. The frontier is the
**smallest reliable gadget** — and "reliable" is measured on the real
protocol, so check-weight-reducing structure (dummies, stacking) can now
*pay for itself*.

### Anti-gaming

The candidate returns only the spec; circuit, observables, decoder and sampling
are all evaluator-owned, so there is no oracle to over-report to. Sampling noise
is held ~constant by error-budget collection (score std ≈ 0.434/√errors ≈ 0.03
decades ≪ the 0.30-decade gate margin), with a fresh seed per eval. Shinka's
fresh-process-per-candidate isolation must stay ON; sinter workers are fresh
spawned processes that re-import stim from disk.

## Calibration (run owner)

`LER_REFS` in evaluate.py is the measured overall-error **curve** of the paper
reference gadget (18 matching + 4 expansion edges, R=12) at the three P_GRID
points under this exact harness (BP+OSD-0, König schedule). Recalibrate (env
`GAUGE_LER_REF_LO/GATE/HI`, or edit the constants) whenever `GAUGE_PHYS_P`, the
noise model, the scheduler, the decoder or the protocol shape changes:

```bash
python tasks/gross_code_gauging/calibrate.py     # prints the three-point curve
```

The evaluator **ships calibrated** (the three `LER_REFS` constants are baked
from a run of `calibrate.py`); the boot guard in `main()` refuses to run and
`aggregate_fn` scores every candidate −1000 only if the constants are ever
reset to the `__CAL_*__` sentinels. The pure-structure scheduler is checked by
`test_coloring.py` (`python tasks/gross_code_gauging/test_coloring.py`: 400
random colorings verified proper + Δ-optimal, and `preview_gadget`'s structural
fields verified equal to the evaluator's on the reference and matching-only
gadgets).

The paper's 4 expansion edges in label space: `(2,9) (2,4) (9,11) (10,11)`
(= (x²,x⁵y³), (x²,x⁶), (x⁵y³,x¹¹y³), (x⁷y³,x¹¹y³), Eq. 5). These are the
*reference*, not a proven optimum — in either direction: GeneCS
([arXiv:2605.21746](https://arxiv.org/abs/2605.21746)) compiles a comparable
gross-code gadget at 24 ancilla qubits/25 checks with LER "closely matching" its
gauging baseline, and nothing pins R=12 or a flat dummy-free graph as optimal.

## The seed

18 matching edges + 6 sparsest-cut-greedy expansion edges, no dummies, R=12:
Q=45 (score ≈ −4 + margin bonus) — a reliably-measuring flat design with obvious
pruning headroom (the reference reaches Q=41 with 4 expansion edges). Directions
the seed does not explore: pruning/replacing matching edges, dummy-vertex
structure (stars/layers/cellulation), round-count tuning, parallel edges.
`preview_gadget()` (fixed tool in initial.py) gives a millisecond structural
preview — element count, B_p count after redundancy reduction, check weights,
schedule depths — so evolution can screen ideas before paying for a simulation.

## How to run

### Smoke test

```bash
conda activate shinka      # or: conda run -n shinka python ...
cd "$(git rev-parse --show-toplevel)"
python tasks/gross_code_gauging/evaluate.py \
    --program_path tasks/gross_code_gauging/initial.py \
    --results_dir /tmp/gauge_smoke
```

Expected: `correct=True`, `valid=1`, `elements=45`, `rounds=12`,
`combined_score ≈ -3.7` (Q=45 → −4 elements + ~0.30 bonus), plus the
structural fields (exact depths, weights, cut, Fiedler) and the measured noise
curve (`ler_lo`/`overall_ler`/`ler_hi`, per-point margins, `d_eff_est`≈9). Depths
are `depth_x=6`, `depth_z=7` (the exact Tanner max degrees). Runtime ~9–11 min
(one sinter fan-out over 6 circuits on ~20 workers; BP+OSD-0 on ~90k-mechanism
DEMs is the cost; error-budget sampling means worse candidates finish faster;
worst case ~14 min at the shot caps). (If the `LER_REFS` constants have been
reset to sentinels, the run aborts with an "UNCALIBRATED" message — run
`calibrate.py` first; see Calibration.)

### Full evolution (as the orchestrator)

Author a run config (copy `configs/orchestrator_run.default.json`), point
`task.eval_program_path` / `task.init_program_path` at this task's files, set
the Azure `evo.llm_models` + `budget_usd`, and `eval_time >= 00:16:00`, then:

```bash
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

## Deps

`numpy`, `stim`, `sinter`, `stimbposd`, `ldpc`, all already in the `shinka`
conda env. No new installs.

## Files

| File | Role |
|---|---|
| [initial.py](initial.py) | Fixed problem data + graph/preview tools + EVOLVE-BLOCK (matching + greedy seed). |
| [evaluate.py](evaluate.py) | Deformed-code builder, König scheduler, protocol circuits, BP+OSD-0/sinter multi-p sampling, scorer. |
| [calibrate.py](calibrate.py) | Re-measures the `LER_REFS` curve on the paper reference gadget under the current harness. |
| [test_coloring.py](test_coloring.py) | Property test: König coloring proper+Δ-optimal, preview ↔ evaluator consistency, protocol determinism. |

## Sources the redesign is grounded in

- Williamson & Yoder, arXiv:2410.02213 (construction, dummy vertices Remark 2,
  thickening Def. 3, FT protocol Thm 2 / App. F, gross example App. B).
- Cross, He, Rall, Yoder, arXiv:2407.18393 (merge/split protocol shape, boundary
  detectors, R<d finding, fault-distance = min(R, spatial)).
- Tremblay, Delfosse, Beverland, arXiv:2109.14609 (coloration circuit).
- Bravyi et al., Nature 627:778 (2024) (BB codes; d_circ ≤ 10 for the gross code
  under the depth-7 schedule).
- Zhou, Javadi-Abhari, Li, arXiv:2605.21746 (GeneCS: expansion/congestion/degree
  balance as the spatial objective set; 24-qubit gross-code gadget scale).
  N.B. GeneCS itself contains **no scheduling or protocol treatment** — its LER
  check is a static deformed-code simulation; the schedule-sensitivity evidence
  is ASC (arXiv:2603.21499) / AlphaSyndrome (arXiv:2601.12509).

## Project context

See the project [CLAUDE.md](../../CLAUDE.md) for environment setup, Azure
credentials, and the rationale behind this Azure-only ShinkaEvolve fork.

# `gross_code_gauging` — end-to-end gauging-gadget design on the gross code (v4.1)

ShinkaEvolve task: design the complete **gauging-measurement gadget** for the
weight-12 logical X̄_α of the **[[144,12,12]] gross code** — the construction of
**Williamson & Yoder, "Low-overhead fault-tolerant quantum computation by gauging
logical operators"** ([arXiv:2410.02213](https://arxiv.org/abs/2410.02213),
*Nature Physics* **22**, 598, 2026) — evaluated **end-to-end at circuit level**:
feasibility is purely the measured LER of the actual measurement protocol
staying within **1.1×** of the hand-crafted 41-element reference, made honest
by **pricing** the probe-found fault tails Monte Carlo cannot see (not by
gating on a distance target); the score is the ancilla saving plus a
worst-case LER-dominance bonus. Spanning trees/forests (cycle rank 0 — no
flux checks, no gauge structure; the known-trivial corner run gcg1 found)
are **out of scope by construction**, so the goal is the **LER-vs-size
Pareto between the Q ≈ 25 scope floor and Q = 41 (the hand-crafted
reference)** — the genuinely open middle region.

## Why v4 (how run gcg1 beat the v2/v3 evaluator)

v2/v3 replaced v1's gameable distance gate with a **pure end-to-end LER gate**
(margin vs. the calibrated paper-reference curve at three noise points, plus a
low-p-weighted "steepness" bonus and a d_eff diagnostic). Run `gcg1` ($30,
40 windows) found the hole in two windows and spent the rest of the budget
refining it: the champion was the **provably minimal 11-edge spanning tree**
(Q = 23, R = 5), which **passed the reliability gate with +0.57 decades of
margin and showed d_eff ≈ 10**. The ending document then *proved* Q = 23
optimal and declared victory.

The postmortem (verified by re-attacking the champion):

1. **At simulable p (1.4–2.8×10⁻³) the protocol error is bulk-dominated.**
   Fewer elements ⇒ fewer fault locations ⇒ the tree beats the reference *on
   level* across the entire measured curve. The d_eff fit measures the bulk
   slope, not the tail.
2. **The tree's protection had actually collapsed.** Its weakest cut violates
   the paper's expansion desideratum maximally (WY Lemma 2 guarantees only
   d\* ≥ min(h(G),1)·d = 2 at h = 1/6); the v4 attack finds a **weight-7..8
   dressed X-logical** crossing a single tree edge. And R = 5 caps the
   measurement outcome's timelike fault distance at 5 (Cross et al.
   [arXiv:2407.18393](https://arxiv.org/abs/2407.18393) Lemma 9:
   measurement fault distance = min(R, ...); a chain of R measurement flips
   on one A_v is undetectable).
3. **A weight-5..8 fault set fires at ~p³..p⁴** — orders of magnitude below
   the measurable LER at the benchmark rates. No affordable shot budget can
   see it; only a fault-set search can. (This is the "hidden weight-6 logical"
   failure mode the task design discussion predicted for LER-only fitness.)

**v4.1's answer: keep LER as the only criterion, but price the invisible
tail.** The probes (grounded in Cross et al. Lemmas 9–11) hunt each design's
lightest fault sets; every found set of weight w adds its analytic
first-order failure rate `N·C(w,⌈w/2⌉)·p^⌈w/2⌉` to the candidate's
*effective* LER at each scored point — **only when the Monte Carlo could not
have seen it** (visible sets are already inside the measured number). So
"cheap because invisible" stops working, while "cheap because genuinely
negligible at the benchmark rates" is allowed through — which is the run
owner's stated criterion. A v4.0 draft instead hard-gated a fault-distance
target d̂ ≥ 10; that walls off the whole 23–35-element region (and forces
R ≥ 10), so it was demoted to an optional campaign mode (`GAUGE_DTARGET`).
The spanning-tree corner itself is excluded by **scope** (cycle rank ≥ 1
validity), not by punishment: a tuned tree is numerically defensible at the
benchmark rates (measured: R=5 tree beats the reference 1.6× at the gate
point, floor at p ≈ 8×10⁻⁶), but it is the degenerate no-flux limit the
field has already explored and abandoned — there is no discovery in
re-finding it. Every valid design's found fault sets go on the record: the
feedback names each operator's support and the **crossover rate** below
which the tails would dominate ("valid down to p ~ …"). Static graph
proxies (Fiedler, sparsest-cut conductance) stay **out of the metrics**
(they misled gcg1's inner loop into optimizing conductance *among trees*).

## The design space (unchanged — the paper's, in full)

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
- **R ∈ [1,24]** — deformed-code syndrome rounds. A chain of R measurement
  flips on one A_v silently flips the outcome (Cross et al. Lemma 9): that
  cost is *measured* when sampling can see it (small R) and *priced* when it
  cannot (larger R), while every extra round adds exposure on all 12
  logicals — a genuine optimum, near R ≈ 4–8 at these rates (Cross et al.
  Fig. 10b found the measurement-vs-logical crossover near R=7 at p=10⁻³).

Check weights and qubit degrees are **not capped** (the v4.0 draft capped
them at 9/9): they are priced through the schedule — depth per phase *is*
the Tanner max degree, and idle noise scales with depth — so a weight-13 hub
check pays its own way in the measured error. The reference profile (7/7) is
reported for orientation.

## What the evaluator does (all derived, never evolved)

[evaluate.py](evaluate.py), faithful to the paper:

1. **Deformed code**: A_v Gauss-law checks; original Z-checks routed by exact
   min-weight T-joins; flux checks B_p on a minimum-weight cycle basis
   (Horton), reduced by the BB Z-check redundancy exactly as in App. B.
   *Validation:* fed the paper's 22-edge graph, this machinery reproduces the
   published gadget exactly — 12 A_v, **7 B_p**, max check weight 7, max qubit
   degree 7, 41 added elements.
2. **Schedule**: exact **minimum edge coloring of the bipartite Tanner graph**
   (König; property-tested in [test_coloring.py](test_coloring.py)) — the
   coloration-circuit input (Tremblay–Delfosse–Beverland arXiv:2109.14609).
   Depth per phase **equals the deformed Tanner graph's max degree Δ exactly**.
   Scheduling is *evaluated* (depth→idle noise), not evolved.
3. **Protocol circuit** (stim), following Cross–He–Rall–Yoder §3.2 adapted to
   gauging: ideal MPP brackets → 1 noisy base round → gauge-in → R noisy
   deformed rounds → gauge-out → 1 noisy base round → ideal MPPs. Both bases'
   circuits are noiseless-deterministic (self-checked every eval).
4. **Fault-set probes + tail pricing (NEW)**: the lightest fault sets come
   from
   - `R` — the timelike chain on the measurement outcome (exact; Lemma 9;
     multiplicity n_av parallel chains);
   - `dressed_X/Z` — a **BP+OSD dressed-logical attack** on the deformed
     code's two CSS sides (the d_X(L\*S_X) / d_Z(...) of Cross et al.
     Lemma 10; the same attack style WY used as their fast filter). 48
     trials/witness alternating random column permutations, growing prior
     jitter, edge-biased priors and BP/OSD depth (the
     [arXiv:2603.22532](https://arxiv.org/abs/2603.22532) recipe + a
     diversity schedule; 16 trials missed a weight-9 operator that 48 finds
     on every seed); ~2–4 s per attack, counts the distinct minimum-weight
     operators, and reports each found operator's support;
   - `stim_X/Z` — `shortest_graphlike_error` + budgeted
     `search_for_undetectable_logical_errors` (exploration caps (2,4),
     ~0.1–1 s/circuit, measured) on the actual protocol circuits — catches
     circuit-level sets the code-level attack cannot represent.
   Each found set of weight w is **priced** into the effective LER at every
   scored point — `N·C(w,⌈w/2⌉)·p^⌈w/2⌉`, skipped when the point's shot
   budget would already have sampled it (≥5 expected occurrences) — and the
   **tail-crossover p** ("valid down to p ~ …") is reported. All finds are
   upper bounds: a miss under-prices a bad design (bounded by the tail's
   true size at benchmark p, and re-attacked with a fresh seed every eval);
   it never over-prices a good one.
5. **Noise — three-point curve**: uniform circuit-level depolarizing at
   **p ∈ {1.4, 2.0, 2.8}×10⁻³** (0.7×, 1×, 1.4× `GAUGE_PHYS_P`): DEPOLARIZE2
   after each CNOT, measure/reset flips, per-phase aggregated idle noise
   (p/10 per idle tick) so **schedule depth is priced**.
6. **Decode + sample**: BP+OSD-0 (stimbposd/ldpc; 12 BP iterations, `osd0`)
   via one sinter fan-out over all six circuits (~20 workers), per-point
   error budgets `P_BUDGET = (55,2600),(70,2800),(25,700)` errors/shot-cap
   per circuit, fresh seed per eval. Deliberately fast-but-weak: absolute
   LERs are **not** paper-comparable (Bravyi et al. ran min-sum/10k
   iterations/OSD-CS-7), but candidates and the reference are decoded
   identically, so the relative comparison is self-consistent. The
   methodology otherwise follows the IBM line: Cross et al. §3.2/§4.2
   protocol shape (merge window + ideal brackets), Bravyi-style error-budget
   stopping (~100+ observed failures at the gate point), identical decoder
   both arms. (GeneCS's own LER methodology is unpublished beyond
   "circuit-level depolarizing, 10⁶ samples/point" — no scheduler, decoder,
   rounds or protocol details, no code release — so IBM's is the concrete
   anchor.)

## Score

```
Q          = edge qubits + A_v checks + B_p checks     (paper gadget: 22+12+7 = 41)
eff(p)     = overall(p) + priced_tail(p)               (tail pricing, step 4)
margin(p)  = log10( LER_REF(p) / eff(p) )              (TRUE headroom, resolution-clamped)
allow(pt)  = log10(1.1) + 2·sqrt(σ_pt² + σ_ref²)       (noise-aware allowance,
                                                        ~0.12–0.17 decades at default budgets)

FEASIBLE   := margin(gate) ≥ −allow(gate)  AND  margin(lo) ≥ −allow(lo)
              (i.e. tail-priced LER not DEMONSTRABLY worse than 1.1× the reference)

crash / garbage return               →  −1000  (correct=False)
invalid spec                         →  −100   (+ named reason)
valid, infeasible                    →  −8 + min(0, margin_gate+allow)
                                             + min(0, margin_lo+allow)      (≥ −30)
FEASIBLE                             →  (41 − Q) + 3·min(2, max(0, min(margin_lo, margin_gate)))
```

The reference scores ~0. Every element saved while staying feasible is +1.
The LER bonus is **worst-case** over the scored points and pays only for
**true dominance** (matching the reference earns 0; a third of a decade of
across-the-board improvement = one element; cap +6) — so genuinely better
error can outweigh 1–2 elements of size, and the outcome is a defensible
LER-vs-size Pareto front over Q ∈ [23, 41].

Why 1.1× needs the noise allowance: 1.1× is 0.041 decades, and the per-point
sampling std at the ~10-minute budgets is 0.04–0.07 decades — the same size.
The in-loop gate therefore rejects only what is *demonstrably* beyond 1.1×
at 2σ; the strict 1.1× verdict belongs to the offline high-budget
certification of finalists (`calibrate.py --compare` with raised budgets).
`GAUGE_RATIO_LIMIT` moves the bar; `GAUGE_DTARGET` (default off) restores
the v4.0 hard fault-distance gate for a distance-preserving campaign.

### Anti-gaming

The candidate returns only the spec; circuit, observables, decoder, sampling
and probes are all evaluator-owned. Pricing closes the LER-only blind spot
(a found light fault set raises the candidate's effective LER by its
analytic failure rate), while probe misses only under-price a tail —
bounded by its true size at the benchmark rates — and fresh per-eval probe
seeds re-attack every lineage each generation. Sampling noise is bounded by
error-budget collection with a noise-aware feasibility allowance. Shinka's
fresh-process-per-candidate isolation must stay ON.

## GeneCS baseline (apple-to-apple)

[genecs.py](genecs.py) reimplements the **GeneCS** ancilla-graph synthesis
(Zhou, Javadi-Abhari, Li, [arXiv:2605.21746](https://arxiv.org/abs/2605.21746),
Algorithm 1: deficit-weighted random edge addition over the path-matching
graph — here exactly the 18-edge matching motif — with λ₂ recomputed after
every edge and acceptance at λ₂ ≥ 2β, then random perfect-matching layers;
restarts keep the smallest graph) so its output can be scored by **this**
evaluator with the same schedule, protocol, decoder and noise. Measured β →
size frontier: β=0.35 → Q=35, **β=0.46 → Q=37** (the spectral level the
paper's own 22-edge gadget certifies), β=0.65 → Q=41 (= the reference size).

**Reverse-engineering their unpublished config** (`genecs.py
--fit-published`): in mono-layer gauging accounting, generic check count =
E+1, so their published gross Full-Opt outcome (24 ancilla qubits, 25
checks, degrees 7/8) pins **E = 24 exactly** — no thickening fits (a second
layer would double the qubit count). The scan finds E=24 reproduced by
**β ∈ [0.9, 1.0]** on every seed, with λ₂ landing at exactly **2.000** at
β=0.9–0.95 — i.e. their effective gross-code acceptance is **λ₂ ≥ 2
(certified Cheeger ≥ 1, the full WY theory bar, mono-layer)**, with raw
cycle-basis check accounting (a BB-code-aware count gives 21 checks for the
same graph, Q = 45 elements in this task's accounting — larger than WY's
41). Their >85% headline reductions are vs. generic pipelines (Gauge
239/240, CKBB 348/342). Still unidentifiable from their text: scheduler,
decoder, protocol (no absolute LER numbers published); `calibrate.py
--ablate` brackets that — rankings are scheduler-robust, absolute LER moves
~1.5× with schedule depth.

GeneCS contains **no scheduling or protocol treatment** and gates on a
*spectral certificate*; this task measures and prices instead — and the
certificate turns out loose in **both** directions: the β=0.46 graph
certifies only Cheeger·d ≈ 5.6 yet measures dressed distance 10, while the
β=0.35 graph certifies 4.2 and actually hides a **weight-9 dressed
X-logical** (the attack finds it on every seed). Under v4.1 pricing both
are feasible (a weight-9 tail prices at ~10⁻¹¹ at the gate point) — the
weight-9 operator simply goes on β=0.35's record.

## Calibration & comparison (run owner)

`LER_REFS` in evaluate.py is the measured overall-error **curve** of the paper
reference gadget under this exact harness. Recalibrate whenever `GAUGE_PHYS_P`,
the noise model, the scheduler, the decoder or the protocol shape changes
(v3 → v4 changed none of these, so the v3 calibration carries over):

```bash
python tasks/gross_code_gauging/calibrate.py            # LER_REFS + reference d̂
python tasks/gross_code_gauging/calibrate.py --compare  # score the benchmark set
```

`--compare` runs the full v4.1 pipeline over {paper-41, gcg1 tree (R=5 and
R=12), matching-18, GeneCS β=0.46 and β=0.35, seed} and prints the score
table (size, lightest found fault set, tail price, crossover p, margins) —
the regression test for any scoring retune. Measured (budget-scale 0.8,
2026-07-16):

```
gadget               score   Q   R  w_min  tail@gate  cross_p   m_lo  m_gate  LER_gate
paper-41 (R=12)       0.00  41  12   12    1.4e-11    none     -0.13  -0.02   5.83e-02
gcg1-tree (R=5)      18.17  23   5    5    9.6e-07    8e-06    +0.06  +0.20   3.54e-02
gcg1-tree (R=12)     18.00  23  12    7    5.7e-10    none     -0.07  -0.09   6.88e-02
matching-18 (R=12)    8.00  33  12    8    2.2e-09    none     -0.11  -0.02   5.83e-02
genecs b=.46 (Q=37)   4.00  37  12   12    1.4e-11    none     -0.06  +0.08   4.66e-02
genecs b=.35 (Q=35)   6.00  35  12    9    9.4e-12    none     -0.01  +0.08   4.61e-02
seed (Q=45)          -3.99  45  12   12    1.5e-11    none      0.00   0.00   5.53e-02
```

(Measured before the tree scope-exclusion landed — the tree rows are kept
as the diagnostic that motivated it: a tuned tree wins the benchmark
numerically, floor at p ≈ 8×10⁻⁶, which is exactly the known-trivial result
the scope rule now removes from the search. Under the current evaluator the
two tree rows score −100 INVALID; everything else is unchanged.) The open
question for evolution is the Q ∈ [25, 33] region: whether cycle-bearing
structure can approach the tree-level curves, and whether any Q < 33 design
earns a real dominance bonus. Structural checks:
`python tasks/gross_code_gauging/test_coloring.py` (König coloring proper +
Δ-optimal, preview ↔ evaluator consistency, protocol determinism, probe
regression on the known gadgets).

The paper's 4 expansion edges in label space: `(2,9) (2,4) (9,11) (10,11)`
(= (x²,x⁵y³), (x²,x⁶), (x⁵y³,x¹¹y³), (x⁷y³,x¹¹y³), Eq. 5).

## The seed

18 matching edges + 6 sparsest-cut-greedy expansion edges, no dummies, R=12:
Q=45, feasible (score ≈ −4 + bonus) — a reliably-measuring flat design with
proven headroom below it (reference Q=41; GeneCS-style Q=37/Q=35; spanning
trees at Q=23 can be feasible at tuned R — the open question is what each
element buys in total error across that range). Directions the seed does
not explore: pruning/replacing matching edges, dummy-vertex structure
(stars/layers/cellulation), R tuning, parallel edges. `preview_gadget()`
gives a millisecond structural preview.

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
`fault_dist_est=12`, `tail_gate≈0`, `tail_crossover_p=None`,
`combined_score ≈ -4 + bonus`, plus the measured curve
(`ler_lo`/`overall_ler`/`ler_hi`, margins ≈ −0.15..+0.1), `d_eff_est` ≈
7–10, depths `depth_x=6`, `depth_z=7`. Runtime ~12–16 min (probes ~10–30 s;
BP+OSD-0 sampling dominates; worst case ~18 min at the shot caps). Set the
harness `eval_time >= 00:20:00`.

### Full evolution (as the orchestrator)

Author a run config (copy `configs/orchestrator_run.default.json`), point
`task.eval_program_path` / `task.init_program_path` at this task's files, set
the Azure `evo.llm_models` + `budget_usd`, and `eval_time >= 00:20:00`, then:

```bash
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

## Deps

`numpy`, `scipy`, `stim`, `sinter`, `stimbposd`, `ldpc`, all already in the
`shinka` conda env. No new installs.

## Files

| File | Role |
|---|---|
| [initial.py](initial.py) | Fixed problem data + graph/preview tools + EVOLVE-BLOCK (matching + greedy seed, Q=45). |
| [evaluate.py](evaluate.py) | Deformed-code builder, König scheduler, protocol circuits, fault-set probes + tail pricing, BP+OSD-0/sinter multi-p sampling, scorer. |
| [genecs.py](genecs.py) | GeneCS-style (arXiv:2605.21746 Alg. 1) reimplementation: baseline synthesizer + `--fit-published` (reverse-engineer their unpublished β from the published gross-code outcome). |
| [calibrate.py](calibrate.py) | Re-measures `LER_REFS` + reference probes; `--compare` scores the benchmark set; `--ablate` runs the scheduler-sensitivity study (König vs. greedy first-fit). |
| [test_coloring.py](test_coloring.py) | Property tests: coloring, preview consistency, determinism, probe/pricing regression, scope rule. |

## Sources the redesign is grounded in

- Williamson & Yoder, arXiv:2410.02213 (construction; Theorem 2 desiderata —
  incl. sparsity and Cheeger h(G) ≥ 1; Lemma 2: d\* ≥ min(h(G),1)·d; the gross
  example: 18 CKBB edges + 4 expansion edges → 22, d\*=12 certified by BP+OSD
  screening + integer programming).
- Cross, He, Rall, Yoder, arXiv:2407.18393 (merge/split protocol shape;
  Definition 7 + Lemmas 9–10: measurement fault distance = min(R, d_Z(·)) with
  the Z-side automatic ≥ d, logical fault distance = merged-code X-distance —
  exactly what the v4.1 attack estimates; Theorem 11: R ≥ d suffices; §4.2:
  merged circuits preserve circuit-level distance 10, measurement-vs-logical
  error crossover near R=7 at p=1e-3 — the R-region v4.1's pricing keeps
  open, charging the timelike chain its analytic rate).
- Bravyi et al., Nature 627:778 (2024) (BB codes; d_circ ≤ 10 for the gross
  code's depth-7 schedules; decoder anchor: min-sum BP 10⁴ iters + OSD-CS-7,
  ≥100 errors/point, p ≥ 10⁻³ simulated).
- Tremblay, Delfosse, Beverland, arXiv:2109.14609 (coloration circuit).
- Webster, Jacob, Higgott, arXiv:2603.22532 (distance-finding benchmark; the
  BP-OSD attack recipe — random column permutations across trials — and the
  UEStim/GEStim stim-search wrappers the v4 probes follow).
- Zhou, Javadi-Abhari, Li, arXiv:2605.21746 (GeneCS: Algorithm 1 expander
  conditioning, λ₂ ≥ 2β acceptance, congestion machinery; gross-code Full-Opt
  24 qubits + 25 checks; no scheduling/protocol treatment — its LER check is
  a static deformed-code simulation).
- ASC (arXiv:2603.21499) / AlphaSyndrome (arXiv:2601.12509): schedule choice
  moves circuit-level LER by ~7–8× — why the schedule is held fixed (König)
  for every candidate rather than co-evolved here.

## Project context

See the project [CLAUDE.md](../../CLAUDE.md) for environment setup, Azure
credentials, and the rationale behind this Azure-only ShinkaEvolve fork.

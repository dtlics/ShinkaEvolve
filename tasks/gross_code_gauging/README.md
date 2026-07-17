# `gross_code_gauging` — end-to-end gauging-gadget design on the gross code (v4)

ShinkaEvolve task: design the complete **gauging-measurement gadget** for the
weight-12 logical X̄_α of the **[[144,12,12]] gross code** — the construction of
**Williamson & Yoder, "Low-overhead fault-tolerant quantum computation by gauging
logical operators"** ([arXiv:2410.02213](https://arxiv.org/abs/2410.02213),
*Nature Physics* **22**, 598, 2026) — evaluated **end-to-end at circuit level**:
the score combines the measured error of the actual measurement protocol, an
**estimated protocol fault distance** (an adversarial probe of the fault tail
that Monte Carlo cannot see), and the ancilla overhead.

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

**v4's answer: score both measured quantities of the real protocol.** The LER
curve stays the primary objective; a **fault-distance estimate d̂** — built
from quantities with literature meaning (Cross et al. Lemmas 9–11) — becomes
the tail detector; static graph proxies (Fiedler, sparsest-cut conductance)
are **removed from the metrics entirely** (they misled gcg1's inner loop into
optimizing conductance *among trees*). Everything scored is either measured
on, or attacked on, the actual protocol.

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
- **R ∈ [1,24]** — deformed-code syndrome rounds. Since d̂ ≤ R always, R < 10
  is never feasible; above ~10 each round only adds exposure, so the useful
  band is narrow (the reference uses R = 12).

New in v4 — **LDPC hardware caps** (validity, score −100 when violated): max
deformed check weight ≤ 9, max qubit degree ≤ 9 (the reference profile is
7/7). These block the star-graph family — h(G) = 1, code-distance-preserving,
yet its weight-13 check is a hook-error channel (one ancilla fault spreads to
~w/2 qubits) that neither the probes nor affordable simulation can see. This
is WY Theorem 2 desideratum 1 (sparsity), not a distance proxy;
`preview_gadget()` predicts both numbers locally.

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
4. **Protocol fault-distance estimate (NEW)**:
   `d̂ = min(R, dressed_X, dressed_Z, stim_X, stim_Z)` where
   - `R` — the timelike cap on the measurement outcome (exact; Lemma 9);
   - `dressed_X/Z` — a **BP+OSD dressed-logical attack** on the deformed
     code's two CSS sides (the d_X(L\*S_X) / d_Z(...) of Cross et al.
     Lemma 10; the same attack style WY used as their fast filter). 16
     trials/witness alternating random column permutations, growing prior
     jitter, edge-biased priors and BP/OSD depth (the
     [arXiv:2603.22532](https://arxiv.org/abs/2603.22532) recipe + a
     diversity schedule); ~1–2 s per side, and when it bites, the found
     operator's support (base qubits + gadget edges) goes into the feedback;
   - `stim_X/Z` — `shortest_graphlike_error` + budgeted
     `search_for_undetectable_logical_errors` (exploration caps (2,4),
     ~0.1–1 s/circuit, measured) on the actual protocol circuits — catches
     circuit-level sets the code-level attack cannot represent.
   All parts are **upper-bound estimators**: a small result certifies
   vulnerability (safe gate direction); a large result proves nothing and
   earns nothing beyond the fixed target. Probe seeds are fresh per eval.
5. **Noise — three-point curve**: uniform circuit-level depolarizing at
   **p ∈ {1.4, 2.0, 2.8}×10⁻³** (0.7×, 1×, 1.4× `GAUGE_PHYS_P`): DEPOLARIZE2
   after each CNOT, measure/reset flips, per-phase aggregated idle noise
   (p/10 per idle tick) so **schedule depth is priced**.
6. **Decode + sample**: BP+OSD-0 (stimbposd/ldpc; 12 BP iterations, `osd0`)
   via one sinter fan-out over all six circuits (~20 workers), per-point
   error budgets `P_BUDGET = (50,2500),(55,2200),(25,700)` errors/shot-cap
   per circuit, fresh seed per eval. Deliberately fast-but-weak: absolute
   LERs are **not** paper-comparable (Bravyi et al. ran min-sum/10k
   iterations/OSD-CS-7), but candidates and the reference are decoded
   identically, so the relative gate is self-consistent. Candidates that
   already failed the d̂ gate sample at **1/4 budgets** (their margins only
   shape the infeasible-branch gradient) — a tree costs ~3 min, not ~10.

## Score

```
Q          = edge qubits + A_v checks + B_p checks     (paper gadget: 22+12+7 = 41)
margin(p)  = log10( LER_REF(p) / overall(p) )          (TRUE headroom, resolution-clamped)
d̂          = min(R, dressed_X, dressed_Z, stim_X, stim_Z)

FEASIBLE   := margin(gate) ≥ −0.30  AND  margin(lo) ≥ −0.45  AND  d̂ ≥ 10

crash / garbage return               →  −1000  (correct=False)
invalid (incl. LDPC caps)            →  −100   (+ named reason)
valid, infeasible                    →  −8 + min(0, margin_gate+0.30)
                                             + min(0, margin_lo+0.45)
                                             − 1.5·(10 − d̂)          (≥ −30)
FEASIBLE                             →  (41 − Q) + 3·min(2, max(0, min(margin_lo, margin_gate)))
```

The reference scores ~0. Every element saved while staying feasible is +1.
The LER bonus is **worst-case** over the scored points and pays only for
**true dominance** (matching the reference earns 0; a third of a decade of
across-the-board improvement = one element; cap +6) — end-to-end error is
what tie-breaks designs at the same size, but **protection can never be
traded away for it**. The frontier: **smallest gadget with ceiling-level
protection (d̂ ≥ 10) and a non-inferior measured curve**.

Why D_TARGET = 10, not 12: the gross code's own extraction circuit caps at
circuit-level distance 10 (Bravyi et al. Nature 2024, Table 1; Cross et al.
§4.2 find merged circuits preserve exactly that), so 10 *is* full protection
here, and demanding 12 would be unreachable over-constraint. This deliberate
relaxation (Cross et al. and certified-GeneCS both effectively require code
distance 12) is part of the discovery headroom: the v4 measurements already
show a 20-edge spectral graph with dressed distance 10 at Q = 37 — 4 elements
below the hand-crafted reference — and nothing says 37 is the floor.

### Anti-gaming

The candidate returns only the spec; circuit, observables, decoder, sampling
and probes are all evaluator-owned. The probes gate against **found** light
fault sets (certificates of badness) and never reward high estimates, so
probe misses cannot buy score — only survival at the same rung of the size
ladder — and fresh per-eval probe seeds re-attack every lineage each
generation. Sampling noise is bounded by error-budget collection; the
feasibility slacks (0.30/0.45 decades) are ≥4σ of the respective points'
sampling std at the default budgets. Shinka's fresh-process-per-candidate
isolation must stay ON.

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
For the record, the GeneCS paper's own *certified* gross-code result
(Full-Opt, with thickening) is 24 qubits + 25 checks = **49 elements** —
larger than WY's 41; its >85% headline reductions are vs. generic pipelines
(Gauge 239/240, CKBB 348/342). GeneCS contains **no scheduling or protocol
treatment** and gates on a *spectral certificate*; this task verifies
protection directly — and the certificate turns out loose in **both**
directions: the β=0.46 graph certifies only Cheeger·d ≈ 5.6 yet measures
dressed distance 10 (feasible, score **+4** — the benchmark to beat), while
the β=0.35 graph certifies 4.2 and actually hides a **weight-9 dressed
X-logical** (the attack finds it on every seed; the evaluator rejects the
gadget the certificate would have accepted).

## Calibration & comparison (run owner)

`LER_REFS` in evaluate.py is the measured overall-error **curve** of the paper
reference gadget under this exact harness. Recalibrate whenever `GAUGE_PHYS_P`,
the noise model, the scheduler, the decoder or the protocol shape changes
(v3 → v4 changed none of these, so the v3 calibration carries over):

```bash
python tasks/gross_code_gauging/calibrate.py            # LER_REFS + reference d̂
python tasks/gross_code_gauging/calibrate.py --compare  # score the benchmark set
```

`--compare` runs the full v4 pipeline over {paper-41, gcg1 tree (R=5 and
R=12), matching-18, GeneCS β=0.46 and β=0.35, seed} and prints the score
table — the regression test for any scoring retune. Measured v4 shape
(budget-scale 0.8): tree **INVALID** (weight-11 routed checks; with caps
relaxed it dies on d̂ = 5–7 instead); matching-18 **−11.0** (d̂ = 8);
paper **0.00** feasible; GeneCS β=0.46 **+4.00** feasible — the benchmark to
beat; GeneCS β=0.35 **rejected** (the attack finds its weight-9 dressed
logical); seed **−4.0** feasible. Structural checks:
`python tasks/gross_code_gauging/test_coloring.py` (König coloring proper +
Δ-optimal, preview ↔ evaluator consistency, protocol determinism, probe
regression on the known gadgets).

The paper's 4 expansion edges in label space: `(2,9) (2,4) (9,11) (10,11)`
(= (x²,x⁵y³), (x²,x⁶), (x⁵y³,x¹¹y³), (x⁷y³,x¹¹y³), Eq. 5).

## The seed

18 matching edges + 6 sparsest-cut-greedy expansion edges, no dummies, R=12:
Q=45, **feasible with d̂ = 12** (score ≈ −4 + bonus) — a reliably-measuring
flat design with proven pruning headroom (reference Q=41; GeneCS-style Q=37
at d̂=10). Directions the seed does not explore: pruning/replacing matching
edges, dummy-vertex structure (stars/layers/cellulation within the LDPC
caps), R tuning in the ≥10 band, parallel edges. `preview_gadget()` gives a
millisecond structural preview including the LDPC-cap numbers.

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
`fault_dist_est=12`, `protected=1`, `combined_score ≈ -4 + bonus`, plus the
measured curve (`ler_lo`/`overall_ler`/`ler_hi`, true margins ≈ +0.1..0.3 —
the seed beats the reference on level because Q=45 still samples fine),
`d_eff_est` ≈ 8–10, depths `depth_x=6`, `depth_z=7`. Runtime ~12–16 min
(probes ~10–30 s; BP+OSD-0 sampling dominates; worst case ~18 min at the
shot caps). Set the harness `eval_time >= 00:20:00`.

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
| [initial.py](initial.py) | Fixed problem data + graph/preview tools + EVOLVE-BLOCK (matching + greedy seed). |
| [evaluate.py](evaluate.py) | Deformed-code builder, König scheduler, protocol circuits, fault-distance probes, BP+OSD-0/sinter multi-p sampling, scorer. |
| [genecs.py](genecs.py) | GeneCS-style (arXiv:2605.21746 Alg. 1) spectral baseline synthesizer. |
| [calibrate.py](calibrate.py) | Re-measures `LER_REFS` + reference d̂; `--compare` scores the benchmark set. |
| [test_coloring.py](test_coloring.py) | Property tests: coloring, preview consistency, determinism, probe regression. |

## Sources the redesign is grounded in

- Williamson & Yoder, arXiv:2410.02213 (construction; Theorem 2 desiderata —
  incl. sparsity and Cheeger h(G) ≥ 1; Lemma 2: d\* ≥ min(h(G),1)·d; the gross
  example: 18 CKBB edges + 4 expansion edges → 22, d\*=12 certified by BP+OSD
  screening + integer programming).
- Cross, He, Rall, Yoder, arXiv:2407.18393 (merge/split protocol shape;
  Definition 7 + Lemmas 9–10: measurement fault distance = min(R, d_Z(·)) with
  the Z-side automatic ≥ d, logical fault distance = merged-code X-distance —
  exactly what the v4 attack estimates; Theorem 11: R ≥ d suffices; §4.2:
  merged circuits preserve circuit-level distance 10, measurement-vs-logical
  error crossover near R=7 at p=1e-3 — the region v4 deliberately gates out).
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

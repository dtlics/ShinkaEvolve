# DESIGN v2 — three-ring memory: hardware mapping + shuttle schedule for a fixed code

> **SUPERSEDED IN TWO PLACES (2026-07-29).** This file is a dated changelog and
> is kept as history. Two model facts stated below are now known to be wrong and
> have been corrected in `evaluate.py`; read
> [README.md](README.md) § "The chip model and the cycle boundary" and
> [HEAD_TO_HEAD.md](HEAD_TO_HEAD.md) Part 2 for the current position:
> (a) a one-row `S→S` hop costs **3** primitive steps, not 5 — the junction
> holds no potential well; (b) our cycle-boundary rule is **not** stricter than
> the paper's — each ancilla species need only restore its own occupied site
> set, exactly as IonQ's Algorithm 1 does, so the "+1.94 like-for-like" bar and
> the "second ancilla batch" reading below are both obsolete (the bar is now
> **+2.010** on a single, genuinely like-for-like convention).

> **v4 changelog (post-run q70ring_v2, 2026-07-28):** the $50 run reached 676
> rounds / +1.65 and stalled. Diagnosis (audited against the paper, the archive
> DB and the journals): (1) the **zone metric was mis-defined** — it counted
> S/J/U/D vertices and was compared against the paper's ~288 *trap sections*,
> a 3.7x granularity error that inflated the bar by ~2 points AND paid the
> search to serialize traffic through few corridors, which is exactly what it
> did (80% of its score gain came from the zone term while rounds moved 3.4%);
> (2) the real blocker is **router parallelism, not layout** — the winner's own
> distance floor is 287 rounds and it used 676, at a mean of 25 of 140 ions
> moving per round; (3) our **cyclicity rule is stricter than the paper**, which
> pipelines a second ancilla batch (15–19% of every plan's rounds). Applied:
> zones = rail sections only (anchor 428), honest paper bar restated as +1.94
> like-for-like, `floor_total` / `rounds_over_floor` / `ions_per_round` /
> `low_occ_rounds` / `wrap_rounds` / `vertices` added as public metrics and to
> the feedback, and a third seed `initial_evolved.py` (v2's best with its cell
> pitch repaired 3→4: 676 → 566 rounds, +1.711) shipped. The footprint race is
> now won outright (288 sections vs the paper's ~288); all remaining headroom is
> routing parallelism.
>
> **v3 changelog (post-run q70ring_v1, 2026-07-27):** the run confirmed the design's
> machinery (zero evaluator pathologies, all gains real) and falsified the v1 score
> weights. Applied fixes: (1) score v2 — plan-dependent exposure only, core time,
> zone weight raised to parity (the honest LER shift stays a public metric +
> certification claim); (2) second seed `initial_folded.py` — the folded 2D
> embedding the run's discovery round found, with a correct parallel router (700
> rounds / 51.05 POC / 1074 zones, boots +1.32, already above the run's best
> evolved plan); (3) per-gap transport floors in text_feedback (used/floor slack
> map); (4) a binding run playbook in the README (multi-seed, migration, early DR
> at 1.5× cost estimate, guidance scoping, model arms). See README for details.

**Status: IMPLEMENTED (see README.md for the as-built contract).** One deliberate
deviation from §2.4/§2.5 below, forced by measured facts during the build's
adversarial review: the **in-loop score is now fully deterministic** — reliability
is scored as `⌈d_circ/2⌉·log10(seed_exposure/exposure)` (the paper's own
extrapolation-ansatz logic at the p=1e-4 operating point, exact to leading order
because the fault structure is frozen) instead of sampled LER at p*=3e-3.
Measured reasons: BP-OSD costs ~0.1–4 s/shot on this circuit's DEM (a p*=3e-3
eval would take hours-to-days, not minutes), and at any sampleable p the circuit
sits near threshold where frozen gate noise outweighs the plan-controllable
signal ~7:1 while decoder-failure statistics distort ranking. Real Monte-Carlo
LER moved wholly to the certification tier (`certify.py`), where the head-to-head
against the paper's published numbers happens as planned. Everything else below
is as designed. (v2 after user feedback: code is FIXED; the design space is the
ring→hardware mapping + shuttle plan.)

Task, in one sentence: **take one BB code the Walking Cat paper fully evaluated
(recommended: Q70 = [[70,6,9]]), keep the paper's moving-qubit noise model and its
syndrome-extraction circuit as defaults, and evolve *how the three rings are laid out
and shuttled on the junction-grid QCCD chip* — scored head-to-head against the
paper's own published design.**

Sources: IonQ *Walking Cat* (arXiv:2604.19481), IBM BB codes (arXiv:2308.07915),
Quantinuum grid-trap junction transport (arXiv:2403.00756).

---

## Part 1 — Background primer (the 10-minute education)

### 1.1 The hardware picture: QCCD traps in four sentences

A QCCD ion trap is a chip with a fixed 2D grid of **junctions** (X-shaped
intersections) connected by **linear trap sections** where ions sit. Ions are
physically *shuttled* between sections (a few m/s), and a two-qubit gate requires
bringing two ions into the *same* section and **merging** them into one potential
well; afterwards they are **split** apart. Measurement/reset need **optical zones**
(every other row only); gates need RF **gate zones** (every row). So a QEC circuit on
this hardware is inseparable from a *transport plan*: who moves where, through which
junctions, between which gate layers.

Quantinuum's grid-trap paper supplies measured reality: junction "swap-or-stay" of
adjacent crystals ~400 µs (global shifts ~250 µs dominate; the junction transit itself
is 5–15 µs), heating at slow speed ≈ background rate (~100–200 quanta/s) × duration,
with a hard speed cliff near 3 m/s. Moral: **a mid-fidelity model can price a
transport plan by counting primitive steps and durations — no waveform physics.**

### 1.2 The Walking Cat "moving-qubit model" — the noise/cost model we keep as default

The paper's own abstraction (Table III, p.9; operating point p.11) — this is the
"sweet-spot" model, and per your steer we keep it unchanged:

| Operation | Error | Time |
|---|---|---|
| Two-qubit gate (nearest neighbor, merged) | `p` | 1 POC |
| Prep / 1q gate / measurement / leakage reset | `p/10` | 1 POC |
| Idle | `p/100` | 1 POC |
| **Transport step** (noise hits ALL qubits, moved or not) | `p/2000` | **1/20 POC** |

Operating point `p = 1e-4` (`p_leak = 1e-5`, `p_loss = 1e-7`), POC = 200 µs,
transport step = 10 µs. Below it sits the **micro-architecture** (§XIX–XX, Table XXV:
junction shuttle 5–15 µs, merge/split 50–100 µs, measurement 350 µs), used to justify
the abstract model and to count primitive rounds per syndrome cycle (Table XXVI).
Note on numbering: **Table XI** = memory-block summary (SEC time budget, loss
distributions); **Table XXV** = the junction-level operations table.

### 1.3 The three-ring construction — and why the *code* is not our design space

The memory block's 2n qubits (n data + n ancilla) are indexed by
`G = Z_a × Z_b × Z_c` (n = abc), laid out in rows. The "three rings" are the three
families of cyclic transport this supports: **short** (period c), **medium**
(period b·c), **long** (period a·b·c) — all ancilla motion in syndrome extraction is
composed of these. `a = 2` is required by the syndrome circuit, making the family
exactly the **BB/GB/cyclic-HGP codes**: `H_X = [A|B]`, `H_Z = [Bᵀ|Aᵀ]` with A, B
sums of monomials in commuting cyclic shifts x (order ℓ=b) and y (order m=c).

**Your reading of the paper is correct:** the code parameters (ℓ, m, A, B) were *the
paper's own* design space. Their Appendix C is a 62-code database ranked by k·d²/n —
the output of their code search — and Q102/Q70/Q54 are the winners they then
engineered blocks around (IBM did the same for BB codes with a BP-OSD-proxy search).
Re-evolving codes would replay their search and break head-to-head comparability
(different code ⇒ different d ⇒ different everything). **So for this task the code is
FROZEN, and the ring math (a, b, c / ℓ, m, A, B) is NOT mutable.** What remains
genuinely open — and untouched by their search — is *how a fixed code's rings are
realized on hardware*, which is exactly where you pointed.

**Recommended fixed code: Q70 = BB7 [[70,6,9]]** (ℓ=7, m=5, A=y²+x²+x³+x⁴,
B=y+x+x³), because among the paper's fully-evaluated blocks it is the one that uses
*all three* ring families (Q102/Q54 are GB, m=1: no short rings ⇒ a poorer mapping
playground), it has complete published numbers to compare against (below), and its
published embedding is the most constrained/transport-heavy — i.e., most headroom.

Published Q70 targets for head-to-head:

| Quantity | Paper value |
|---|---|
| Circuit-level distance d_circ (published schedule) | 9 |
| SEC time, abstract accounting (Table XI) | 27.70 POC (of which cyclic shift 11.65) |
| SEC primitive rounds, micro accounting (Table XXVI) | **424 transport**, 16 merge/split, 2 1q, 8 2q, 3 readout, 1 prep (≈ 34.2 POC) |
| LER/SEC, Pauli-only reference curve (Table XII) | 9.72e-7 at p=1e-3 → 7.27e-11 extrapolated at p=1e-4 (ansatz p⁵·exp(αp²+βp+ζ), (α,β,ζ)=(1.07e6, −3410, 23.0)) |
| Physical qubits (data+ancilla+beacons+reservoir) | 220 |
| Published embedding | Fig. 62 (≈12 junction rows; ancilla per row ≡ 0 mod 5) |

### 1.4 The syndrome-extraction cycle (SEC) and the schedule permutation Σ

One SEC = prep ancillas → `w` rounds of {cyclic-shift transport + one parallel
2q-gate layer} → measure ancillas (w = check weight = 7 for Q70). The circuit is
fully determined by a **schedule permutation** Σ: an ordering of the monomial terms
into pairs `(A_i, A_jᵀ)` / `(B_i, B_jᵀ)`; the required transport between rounds is
*derived arithmetically* from consecutive pairs (long-ring shift iff the A/B family
flips; medium/short shifts = exponent differences). Fig. 13 is the worked toy
example. Σ matters twice: it sets **hook-error propagation** (⇒ circuit-level
distance d_circ) and it sets **every shift distance** (⇒ the transport bill, ~half
of SEC wall time; sensitivity analysis Fig. 18: transport noise is the #3 lever at
×3.0 per doubling, after 2q gates ×21 and leakage ×4.9).

There is no min-weight-coloring stage in this framework — the cyclic symmetry gives
maximally-parallel depth-w schedules for free; "keeping the extraction circuit
default" = keeping the paper's published Σ (their Table X, one known schedule).

### 1.5 The ring→hardware mapping (Figs 60/61/62) — the design space you asked for

The abstract rings must be *realized* as motion on the junction grid. The paper's
default (Fig. 60): fold the block into two stacked row-groups; then

- **long ring ↦ block swaps** (swap top/bottom row-groups through vertical legs),
- **medium ring ↦ in-row racetrack cyclic shifts**,
- **short ring ↦ "embedded shifts"** — hide a subset of ions in vertical junction
  legs, slide the rest past, un-hide (Fig. 61) — efficient for small shifts; "dense
  ring embeddings are more efficient when there is a large number of ions" (their own
  caveat!),

plus layout choices: ancilla-per-row grouping (Q70's short-ring period forces
multiples of 5 in *their* layout), which rows are photonic, measurement in 1 vs 2
batches (an explicit space-time tradeoff they call out), vacant rows for pipelined
prep/reset and ion resupply, beacon placement. Result for Q70: 424 transport rounds
per SEC — *more* than the bigger Q102 (387), precisely because of the short rings.
The paper explicitly declines to claim optimality: "there can be more space-efficient
and/or time-efficient layouts", "an interesting and complex trade space
optimization", and "the correct figure of merit to cost a protocol is the number of
zones required". **This is the promising land your colleague pointed at, and it is
now the core of the task.**

### 1.6 What the paper benchmarks (and why v1 needs no cat states)

The memory-level headline metric is **logical error rate per SEC** of a pure memory
block, stim-simulated at p ≥ 1e-3 and extrapolated with
`LER(p) = p^⌈d_circ/2⌉·exp(αp² + βp + ζ)`. Cat states play **no role** in the memory
SEC (they serve *logical measurements* only — Fig. 22 is cat-factory verification
numerics); the application level (Tables VII/XXVIII/XXIX) is deterministic
SEC-counting arithmetic on top. So: benchmark pure memory; report application-flavored
readouts analytically if desired.

---

## Part 2 — The proposed task

### 2.1 Fixed vs mutable

| Layer | Status |
|---|---|
| Code (ℓ, m, A, B) = Q70 | **FROZEN** (fair head-to-head; ring math is not a design space for a fixed code) |
| Noise/cost model = moving-qubit model + junction-grid legality | **FROZEN** (evaluator-owned) |
| Syndrome schedule Σ = paper's Table X schedule | **FROZEN in v1.0** — unlock as v1.5 (see 2.6) |
| **Ring→hardware mapping**: placement/layout on the junction grid + per-round shuttle plans | **EVOLVED (v1.0 core)** |

With Σ frozen, every candidate runs the *identical* quantum circuit at the gate level
(same gates, same hooks, same d_circ = 9); candidates differ only in *where ions sit
and how they move*, which changes (i) SEC duration in POC, (ii) transport/idle noise
accumulated per cycle, (iii) trap footprint (zones). That makes the head-to-head
against the paper maximally clean — same code, same circuit, same noise model, only
the mapping differs — and it eliminates the entire class of "changed the circuit"
loopholes by construction.

### 2.2 The EVOLVE-BLOCK genome

The candidate is a *plan generator* (code, not a lookup table — the
`cnot_grid_synth` pattern proven in this repo): one function

```python
def build_embedding_and_shuttle(code, schedule, grid_spec):
    ...
    return {
      "layout": {...},        # site assignment: every data/ancilla/beacon ion -> grid site;
                              # declared photonic rows; declared vacant/buffer sites
      "rounds": [             # for each SEC phase (per schedule round + measurement phases):
        {"moves": [...],      #   ordered parallel transport rounds; each move = set of
                              #   (ion, edge-step) or hide/unhide into junction legs
         "merges": [...]},    #   which (data, ancilla) pairs merge for this gate layer
        ...
      ],
      "measure_batches": [...] # ancilla measurement batching onto photonic rows
    }
```

The evaluator re-derives from (code, Σ) which data–ancilla alignments each round
*must* produce, and checks the plan achieves them. The seed implements the paper's
own Fig. 60/61/62 strategy (two-block folding, embedded short shifts, block-swap long
ring, 2-batch measurement) and must reproduce ≈424 transport rounds / ≈34.2 POC —
so the baseline the evolution starts from IS the paper's design, and any positive
score means "beat IonQ's hand-crafted embedding at its own game".

### 2.3 The grid model (evaluator-owned, junction-aware)

Rectangular array of junction tiles: each tile = one horizontal section (holds ions;
gate zone) + up/down leg sites through junctions to adjacent rows; optical zones on
alternating rows only; at most one ion per site; a **parallel transport round** moves
any set of ions one legal step each (no collisions/swaps-through-each-other), costs
1/20 POC, and applies `p/2000` depolarizing to **all** qubits (the paper's rule — it
also kills any "park ions to dodge noise" exploit, since noise is charged globally
per round). Merges/splits are counted per Table XXVI conventions (absorbed into the
2q-gate POC arithmetic, as on paper p.85). Gate layer = 1 POC (`p` on merged pairs,
`p/100` idle elsewhere); measurement/prep = 1 POC (`p/10`), batching-induced waits
priced as idle POCs. Grid dimensions: generous fixed bound (candidates may use less;
footprint = zones actually used, per the paper's own space FOM).

Simplifications, stated honestly: no broadcast-wiring constraint (Quantinuum's data
says row-parallel moves with per-site holds are realistic), loss/leakage **not
simulated** — their checks charged as the fixed ~7 POC time block and beacons/
reservoir counted in qubits (the paper's sensitivity analysis shows loss/measurement
are ×1.10/×1.13 levers — negligible — once the beacon protocol runs; we compare
against their Pauli-only *reference* curve, which is computed exactly the same way).

### 2.4 Evaluator pipeline, runtime, and score

1. **Compile & validate** (seconds, exact): legality of every move; every round
   achieves its required alignments; measurement batches reach optical zones; all
   ions accounted for. Violations → archived sentinel score (−2.0) with
   text_feedback naming the exact broken rule (repo convention — invalid candidates
   teach the LLM).
2. **Cost** (closed form): SEC time in POC; transport rounds; zones used.
3. **Noiseless determinism check** (stim, seconds): assembled circuit fires no
   detectors/observables noise-free.
4. **LER at elevated p\* = 3e-3** (the budgeted step): N_c = 9 SECs (= d, paper
   convention), MPP-bracketed stim circuit, observables pinned by the evaluator;
   per-round transport noise consolidated (k rounds → one `DEPOLARIZE1` at
   ≈ k·p/2000) to keep the DEM small; decoded with BP-OSD (`stimbposd`, in the env);
   **error-budget sampling** (~300 target errors, MIN/MAX shot caps, fresh seed per
   eval) for constant score noise — the `bb_syndrome_sched` anti-winner's-curse
   recipe. Estimated 2–5 min typical; **MAX_SHOTS caps worst case well under your
   30-min bound** (no in-loop distance search is even needed in v1.0 — d and d_circ
   are constants of the frozen circuit, the usual QEC eval-time killer is absent).
5. **Score** (seed-anchored; seed = paper design ⇒ 0):

```
score = log10(LER_seed / LER_cand)          # reliability, the headline term
      + λ_t · log2(T_SEC,seed / T_SEC,cand) # SEC wall time in POC
      + λ_z · log2(Z_seed / Z_cand)         # trap zones used (space)
```

proposed λ_t = 0.5, λ_z = 0.25 (LER and T are correlated through transport noise —
by design, that's the physics; the extra terms reward time/space wins the LER can't
see). Public: LER, SEC POC, transport rounds, zones, per-phase POC breakdown (rich
text_feedback: which rounds are transport-fat). Private: raw shots/errors, seed
anchors.

### 2.5 Anti-loophole inventory (your point 3)

- **Illegal plans** — caught exactly by the compiler validator (adjacency, collision,
  zone, alignment, conservation checks); the candidate never self-reports costs, the
  evaluator counts everything from the plan.
- **Circuit tampering** — impossible in v1.0: gates/order/observables are derived
  from the frozen (code, Σ) by the evaluator; the candidate only moves ions.
- **Noise dodging** — transport noise is global per round (paper rule), so
  "hide ions" tricks change nothing; idle noise is charged per POC from the computed
  timeline, not from candidate claims.
- **Sampling luck** — error-budget sampling + fresh seeds; top archive candidates
  re-evaluated before any beyond-paper claim (de-noise step, repo convention).
- **Score-metric gaming à la gross_code_gauging v4** (fault tails below sampling
  resolution) — structurally absent in v1.0 since the fault structure is frozen; if
  v1.5 unlocks Σ, we import the mitigations wholesale: budgeted d_circ probes with a
  floor gate + analytic tail pricing into an effective LER.
- **Post-hoc guarantee**: any high scorer is, by construction, a *legal, fully
  costed transport program* for the exact published circuit — the certification tier
  (2.7) then re-measures it at multiple p with more shots before we believe it.

### 2.6 v1.5 unlock (optional, later): co-evolve Σ

Once mapping gains saturate, unfreeze the schedule permutation (a deliberate config
lever flip mid-run, or a v2 task): Σ changes shift distances AND d_circ, so the
evaluator adds budgeted circuit-distance probes + tail pricing (machinery exists in
`bb_syndrome_sched`/`gross_code_gauging`). This also opens IBM's untouched open
question #4 (alternate different schedules across consecutive SECs to *raise*
d_circ). Kept out of v1.0 to protect the clean head-to-head and the <30-min budget.

### 2.7 Head-to-head protocol (your point 3, second half)

- **In-loop**: seed = paper's own design, calibrated to reproduce Table XXVI counts
  (424 transport rounds) and ≈34.2 POC before any run starts; score 0 ⇔ paper
  parity, positive ⇔ beating the paper's embedding under its own model.
- **Out-of-loop certification** (`calibrate.py --certify`, run on elites between
  windows / at run end, evolution never sees it): re-measure LER at
  p ∈ {1e-3, 2e-3, 3e-3} with ≥100 errors per point (the paper's own bar), fit their
  ansatz `p⁵·exp(αp² + βp + ζ)`, extrapolate to p = 1e-4, and emit a comparison
  table directly against the published Q70 row: LER/SEC 9.72e-7 @ 1e-3 and
  7.27e-11 @ 1e-4 (reference curve), SEC 27.70 POC (abstract) / ≈34.2 POC (micro),
  424 transport rounds, 220 physical qubits, Fig.-62 footprint. Same code, same
  circuit, same noise model ⇒ the comparison is apples-to-apples by construction.

### 2.8 Code-choice narrative: Q70 primary, gross-code transfer chapter

The paper has **no single winning code — it's a regime map**: Q102 is the density
flagship (k·d²/n 17.5, rate 1/5, LER 1e-11/SEC; "the high density of logical qubits
offered by Q102 is clear") and wins at scale (120-site Heisenberg: 33h vs 53h);
**Q70 is the early-FT workhorse** — fastest SEC, ~2× faster on 80/100-site workloads
at fixed 10k qubits, the MEK magic factory is built on it, and the single-code
N_T×MEK architecture (their simplest, most near-term instance, from ~2,210 qubits)
is pure Q70. Q54 is factory-internal. The gross code [[144,12,12]] appears only as
"compatible" (Table IX, w=6, d_circ ≤10 imported from IBM) — no schedule, embedding,
POC budget, or LER on this stack — and Q102 dominates it statically (k·d²/n 12.0,
rate 1/12).

**Selling strategy:** evolve on Q70 (the only code with a complete published
walking-cat treatment ⇒ the only rigorous head-to-head), then a **transfer chapter at
certification time**: run elite plan-generators on the gross-code spec (a true BB
code, ℓ=12, m=6 — all three rings active, same rich mapping space; IBM's depth-7
gate order slots into the three-ring transport formalism) against a baseline we
construct by faithfully applying the paper's §XX embedding recipe to gross. Claim
shape: "beats IonQ's hand-crafted design for their early-FT workhorse code, and the
evolved strategies transfer to the community's flagship gross code" (with the honest
caveat that the gross baseline is our recipe-faithful reconstruction — that gap is
what makes the transfer novel). Design implication: the score stays purely Q70, but
`text_feedback` discourages hard-coding Q70 magic numbers (e.g. the mod-5 row trick)
when a parametric strategy scores identically — keeping elites transfer-ready
without polluting the head-to-head.

### 2.9 Why this design space is right for evolution

Structured: placement + routing on a constrained grid — combinatorial, decomposable
(per-ring strategies, per-round routing), with meaningful building blocks an LLM can
reason about (embedded vs dense shifts, folding, batching). Not already solved: the
paper hand-crafted one embedding and explicitly flags better ones as open; the
Q70-needs-more-transport-than-Q102 inversion shows the default is not optimal-shaped.
Not trivially solvable either: the seed is a strong expert baseline, and improvements
must survive an exact legality compiler — no free lunch from sloppy accounting.

---

## Part 3 — Updated decision list

1. **Fixed code = Q70** (all three rings active, full published numbers, most
   transport headroom, backbone of their early-FT single-code architecture), with a
   **gross-code [[144,12,12]] transfer chapter at certification** (§2.8). Alternative:
   gross-144 as primary (community brand, but every comparison becomes "vs our
   reconstruction" — no published walking-cat baseline exists for it). Confirm
   Q70-primary + gross-transfer?
2. **v1.0 freezes Σ** (paper's schedule) for the cleanest head-to-head; Σ unlock as
   v1.5. OK, or do you want Σ mutable from the start?
3. **Score weights** λ_t = 0.5, λ_z = 0.25 on top of the LER term — OK to start?
4. **p\* = 3e-3, ~300-error budget** (~2–5 min/eval, hard-capped ≪ 30 min) — OK?
5. **Task name** once pinned: proposal `q70_ring_shuttle` (rename the folder from
   `threering_memory_codesign` when we scaffold).

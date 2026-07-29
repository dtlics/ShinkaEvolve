# How a solution is built, and how it compares to the paper

Plain-language companion to README.md. Part 1 explains the stages a candidate
goes through; Part 2 is the head-to-head against IonQ's published Q70 design
with every comparability caveat stated; Part 3 is what we actually found.

---

## Part 1 — The four stages of a solution

Everything below happens inside one candidate program, which is handed a `spec`
(the code, the schedule, the chip limits) and returns a `plan`.

### Stage 0 — What is GIVEN, and never changes

The code (Q70 = [[70,6,9]]) and its 7-round syndrome schedule are frozen. Together
they say, in pure math with no hardware in sight:

> in gate round *t*, ancilla *g* must touch data qubit *f(g,t)*

for all 70 ancillas and all 7 rounds. That is the *only* requirement. Nothing in
it mentions positions, movement or time. Every candidate must satisfy exactly
this, so every candidate runs a byte-identical quantum circuit with the same code
distance. **This is why the task is a pure hardware-mapping problem.**

### Stage 1 — GEOMETRY: map the rings onto the chip *(SECTION 1 of each seed)*

Decide where all 220 ions physically rest on the junction grid, and where each
ancilla has to be standing at each of the 7 gate rounds.

This is the "ring → hardware" mapping. The code's qubits are indexed by three
cyclic rings; a layout chooses how those rings become rows, columns and junction
legs — folded into 3-row cells, spread on a line, sheared, etc.

**What Stage 1 decides: the DISTANCE FLOOR.** Once you fix where things are, you
have fixed how far each ion must travel between consecutive gate rounds. Take the
longest single-ion journey in each gap and add them up — that is the minimum
number of transport rounds any router could ever achieve on that layout. The
evaluator computes and reports it as `floor_total` (currently 329 for the pitch-4
cell layout, 333 for the folded one).

> A better layout is the *only* way to lower the floor. No amount of clever
> movement can beat it.

### Stage 2 — ROUTING: turn "get from A to B" into actual parallel moves *(SECTION 2)*

Stage 1 said *where* every ion must be. Stage 2 works out *how they all get
there at once* without colliding: which ions step in which round, who waits, who
detours, who rotates around a loop together.

The unit of cost is a **round**: one parallel transport step in which any number
of ions each move one edge. A round costs the same whether 1 ion moves or 140 —
so the entire game is packing as many ions as possible into each round.

**What Stage 2 decides: how close you get to the floor.** It cannot lower the
floor; it decides whether you pay 1.0× it or 2.8× it. Reported as
`rounds_over_floor`, with `ions_per_round` showing the packing quality.

> This is the stage everyone underestimated. See Part 3.

### Stage 3 — ASSEMBLY: stitch one complete cycle *(SECTION 3)*

Put it together in the order physics demands: prepare all 70 ancillas (only on
"optical" rows) → for each of the 7 gate rounds { move into place, merge each
data–ancilla pair into one trap well, fire the gate, split them apart } →
measure all ancillas → **move everything back home** so the next cycle can start
identically (the "wrap-back", ~67 rounds).

### Stage 4 — The EVALUATOR scores it (not part of the candidate)

1. **Validate** every move against the chip rules — illegal plans get −2.0 with the
   broken rule named.
2. **Assemble the Stim circuit** and run it *noiselessly* (64 shots, no decoder,
   ~0.5 s) to confirm the plan really implements the syndrome extraction.
3. **Price** three deterministic numbers: noise exposure (fault events/cycle),
   cycle time in POC, and footprint in rail sections.
4. **Score** as a weighted sum of log-ratios against the anchor seed.

Real logical-error-rate simulation happens **only after evolution**, in
`certify.py`, on the finalists.

### The one formula worth remembering

```
transport rounds  =  distance floor  ×  routing slack
                     └─ Stage 1 ─┘     └── Stage 2 ──┘
```

---

## Part 2 — Head-to-head vs the paper

IonQ report their hand-designed Q70 memory block at **424 transport rounds** over
roughly **288 trap sections** (Table XXVI / Fig. 62, arXiv:2604.19481).

### What is identical (verified, not assumed)

| aspect | status |
|---|---|
| Error-correcting code | same — Q70 [[70,6,9]], same defining polynomials |
| Syndrome schedule | same — the paper's published Table X permutation |
| Circuit-level distance | same — d_circ = 9 |
| Gate set / circuit | same — byte-identical gate sequence, evaluator-derived |
| Noise model | same — the paper's moving-qubit model (Table III) |
| Physical qubits | same — 220 (70 data + 70 ancilla + 70 beacon + 10 reservoir) |
| Cost unit | same — a "round" is one parallel one-edge transport step; their own arithmetic `424/20 + 13 = 34.2 POC` confirms it |
| Horizontal cost | same — 2 primitive steps per column, stated twice in the paper |

### The comparison

Normalising **into the paper's own accounting formula** (`rounds/20 + 2 one-qubit
layers + 8 two-qubit layers + 3 readout`), so only the transport count differs:

| | transport rounds | SEC time (their formula) | exposure → LER | rail sections |
|---|---|---|---|---|
| **paper Q70** | 424 | 34.20 POC | 536.20 | ~288 |
| **ours — `initial_evolved`** | **421** | **34.05 POC** (−0.4%) | 535.85 (**+0.3% LER**) | 301 (+4.5%) |
| **ours — `initial_folded`** | 446 (+5%) | 35.30 POC (+3.2%) | 537.60 (−1.0% LER) | **287** (−0.3%) |

**Read this as a tie.** We are 0.7% fewer rounds and 0.4% faster on the best
seed — inside any reasonable modelling error. We have **not** meaningfully beaten
the paper, and specifically **not on LER**, which differs by 0.3%.

### Two caveats that could move the verdict, both in our favour

1. **Cyclicity convention.** Our rules require every ancilla to walk back to its
   starting site so the cycle repeats with a *single* ancilla batch — 67 of our
   421 rounds. The paper instead pipelines a **second** ancilla batch, which
   removes measurement/reset from the clock cycle (p. 81) but needs ~70 more
   qubits than the 220 their own Table XI counts. Under *their* convention we
   would be at **354 vs 424 rounds — 17% fewer**. Under *ours*, at equal qubit
   count, their 424 is arguably missing a return leg. The paper does not give
   enough detail to settle this, so both numbers are quoted and neither is
   claimed as the headline.
2. **Vertical cost.** We charge 5 rounds per one-row hop; the paper's only
   numeric vertical statement implies ~3. If so, our model is harsher than
   theirs and our 421 is an over-estimate.

### Why LER barely moves, and what that means

A transport round costs `p/2000` on each qubit; a two-qubit gate layer costs `p`
on 70 pairs. So the entire plan-dependent share of the error budget is **~6%** of
the total, and the frozen circuit dominates. Even *free* transport would improve
LER by only ~21% versus the paper.

> **A shuttle plan buys time and chip area, not fidelity.** The honest claim for
> this task is a faster, no-larger logical clock cycle at unchanged fidelity —
> which still matters, because every logical operation in the architecture is
> priced in cycles.

---

## Part 3 — What we actually found

1. **The paper's 424 was never the obstacle.** Both layouts we had by run 2 have
   distance floors of 329/333 — well under 424. A perfect router on the geometry
   already in hand would have beaten the published number.
2. **The failure was entirely in Stage 2 (routing).** Run q70ring_v2's best plan
   used 676 rounds against its own 287-round floor: 287 floor + 83 detour +
   **306 rounds of pure stalling**, with only ~25 of 140 ions moving in a typical
   round. Two runs and 175 evolved programs all routed groups sequentially,
   because interleaving risks a collision and an instant −2.0 — a safe-but-slow
   attractor.
3. **The scoreboard was actively pushing the wrong way.** The footprint metric
   counted every junction vertex an ion touched, so the cheapest way to score was
   to funnel all traffic down one corridor — i.e. to serialize. 80% of run 2's
   score gain came from that term while transport rounds moved 3.4%. Its winner
   had squeezed the cell pitch 4→3, which aliased the two ion species' lanes and
   caused the sequential fallback above. Fixing the metric (rail sections only)
   and reverting that one constant: 676 → 566 rounds.
4. **Routing, done properly, closed the gap.** A prioritised-planning packer with
   stalls, one-round loop rotation and shove-aside took the same layouts from
   1.7–2.1× floor to 1.27–1.34×: 566 → 421 and 700 → 446 rounds. That is the
   whole difference between "34% worse than the paper" and "level with it".
5. **The comparison itself was mis-stated** in two places, both now corrected: the
   footprint bar compared vertices against trap sections (a 3.7× error), and an
   LER win was implied where there is a 0.3% tie.
6. **The next lever is Stage 1, not Stage 2.** Routing slack is down to ~1.27×,
   so the remaining large win must come from a layout with a lower floor. The
   concrete candidate: since l = 7 and m = 5 are coprime, the ring torus is
   isomorphic to Z₃₅, so every realignment collapses to **one** 1-D rotation
   instead of two per-axis passes — analytically ~194 rounds of rotation versus
   the current 329-round floor.

### Attribution, stated plainly

No result in the current seeds is purely evolution's. Run 2's evolved layout is
in there, but its headline change (the pitch compression) was a regression caused
by the broken metric; the pitch repair and the router were hand-written. That is
why every candidate now reports `gain_over_seed` — so the next run's own
contribution is unambiguous.

# How a solution is built, and how it compares to the paper

Plain-language companion to README.md. **Part 0 defines every term from
scratch** — read it first if "SEC", "ancilla" or "floor" are not already
familiar. Part 1 explains the stages a candidate goes through; Part 2 is the
head-to-head against IonQ's published Q70 design with every comparability caveat
stated; Part 3 is what we actually found.

---

## Part 0 — The vocabulary, from zero

### Why a quantum computer needs a heartbeat

Quantum data is fragile and you are not allowed to look at it — measuring a data
qubit destroys the information you are storing. So error correction works
indirectly. You keep **data qubits** (the ones holding the information) and add
**ancilla qubits** (helpers). Each ancilla is wired to touch a handful of data
qubits, and then the *ancilla* — not the data — is measured. Its outcome tells
you "something went wrong near here" without revealing what the data is.

Errors never stop happening, so you must repeat this forever. One complete
sweep, in which **every ancilla checks its assigned data qubits once and is then
measured**, is called a **syndrome extraction cycle** — **SEC** for short. It is
the heartbeat of the machine. Everything in the architecture is priced in SECs:
in IonQ's paper, one logical operation costs some number of SECs, so a shorter
SEC makes the entire computer proportionally faster.

**"Syndrome"** is just the name for the pattern of ancilla measurement outcomes —
the error report.

### The Q70 code, concretely

Q70 is one specific error-correcting code, written `[[70, 6, 9]]`:

- **70 data qubits** — the physical qubits holding information
- **6 logical qubits** — what those 70 actually store, error-protected
- **distance 9** — it takes at least 9 things going wrong to cause an
  uncorrectable error

It also uses **70 ancillas** (35 checking for one error type, 35 for the other),
**70 beacons** (extra ions parked next to data qubits to detect ion loss — in our
model they never move, they are just obstacles), and a **10-ion reservoir** of
spares. **220 ions in total**, all sitting on one chip.

The 70 data qubits are labelled by two coordinates that wrap around: `i` from 0
to 6 (7 values) and `j` from 0 to 4 (5 values), giving 7 × 5 = 35 positions, in
two blocks → 70. Those wrap-around coordinates are the "rings" in the task name.

### The 7-round schedule — yes, this is the syndrome-extraction schedule

Each ancilla in Q70 must touch **7 different data qubits** per cycle (that is what
"check weight 7" means). An ancilla can only do one two-qubit gate at a time, so
it cannot touch all 7 at once. Instead the cycle is split into **7 rounds**: in
round 1 every ancilla touches its 1st assigned partner, in round 2 its 2nd, and
so on.

**The order matters** — a different order spreads errors differently — so IonQ
searched for a good one and published it (their Table X). **We freeze exactly
that published order.** So yes: the "7-round schedule" *is* the syndrome
extraction schedule for the Q70 BB code, taken verbatim from the paper.

Crucially, the schedule is pure mathematics. It says *which ancilla must touch
which data qubit in which round* — and nothing about where anything is or how it
gets there. That gap is the entire task.

### Why the ions have to travel

On a trapped-ion QCCD chip there are no wires. Two qubits can interact **only if
they are physically brought into the same little trap and merged into one well**.
So a two-qubit gate is really: bring the two ions together → **merge** the wells →
fire the laser/RF gate → **split** them back apart.

That means between round *t* and round *t+1*, every ancilla must physically
*travel* across the chip to reach its next partner. **That travel is what this
task optimises.**

Two more chip facts that constrain the layout:

- **Optical rows.** Preparing and measuring an ion needs laser access, which
  exists only on every other row. Ancillas must be standing on one of those rows
  when they are prepared and when they are measured.
- **The grid.** The chip is rails (`S` sites, where ions rest) joined by junctions
  (`J`), with short parking stubs above and below each junction (`U`/`D`). Moving
  one position sideways costs 2 steps; moving one row up or down costs 5.

### The two units of cost

- A **transport round** is one parallel movement beat: *any number of ions each
  slide one step at the same time*. One ion moving costs exactly as much as 140
  ions moving — which is why packing ions into shared rounds is the whole game.
- A **POC** ("physical operation cycle") is the time of one layer of quantum
  operations — a gate layer, a measurement — about 200 µs. A transport round is
  much cheaper: 1/20 of a POC, about 10 µs.

So: `SEC time = (transport rounds)/20 + (number of gate/prep/measure layers)`,
measured in POC.

### The "floor" — the key idea in this whole task

Between two consecutive gate rounds, every ion has somewhere it must be. Work out
how many steps each individual ion needs, and take **the largest one**. That
single number is the *minimum* possible number of transport rounds for that gap:
even if everything else were perfectly organised, that one unlucky ion still has
to take all of its steps, one per round.

Add those minimums over all 7 gaps (plus the walk home at the end) and you get the
plan's **distance floor** — the fewest transport rounds this layout could *ever*
use.

> **Analogy.** 140 people in a hall must each move to a new seat, everyone
> stepping simultaneously on a drumbeat. If the person with the longest walk needs
> 46 steps, the reshuffle takes **at least** 46 beats — no matter how brilliantly
> you choreograph it. That 46 is the floor for that reshuffle.

Two consequences that drive everything below:

1. **The floor is set purely by *where you put things*** — the layout. Change the
   layout and the floor changes.
2. **Routing can never beat the floor**; it only decides whether you pay 1× it or
   3× it. Our folded layout has a gap whose floor is 46 rounds and originally
   spent 102 on it — that 56-round difference was pure disorganisation.

### CRT — the promising idea we have not exploited yet

**CRT** = the Chinese Remainder Theorem, an old piece of arithmetic. The version
that matters here:

The data qubits are labelled `(i, j)` on a 7 × 5 wrap-around grid. Because **7 and
5 share no common factor**, you can relabel every one of those 35 cells with a
*single* number 0…34 arranged so that "step once in the i direction" and "step
once in the j direction" both become "**move along one ring by a fixed amount**".

Why we care: the movement between gate rounds is always "shift everything by
`di` in the i direction *and* `dj` in the j direction". Our current layouts do
that as **two separate passes** — shuffle rows, then shuffle columns — and pay for
both. With the CRT relabelling, both collapse into **one single rotation** of a
35-position ring.

> **Analogy.** Instead of a grid where you walk up-down and then left-right,
> thread all 35 seats onto one necklace in a clever order. Then any required
> reshuffle is just "rotate the necklace by *k*" — one motion, everybody moving
> together, no second pass.

Arithmetic on the actual Q70 schedule says all seven rotations total **~194
rounds**, against our current best layout's 329-round floor. That is the single
biggest unexploited idea in the task.

---

## Part 1 — The four stages of a solution

Everything below happens inside one candidate program, which is handed a `spec`
(the code, the schedule, the chip limits) and returns a `plan`.

### Stage 0 — What is GIVEN, and never changes

The code (Q70 = [[70,6,9]]) and its 7-round syndrome-extraction schedule — the
paper's published Table X — are frozen. Together they say, in pure math with no
hardware in sight:

> in gate round *t*, ancilla *g* must touch data qubit *f(g,t)*

for all 70 ancillas and all 7 rounds. That is the *only* requirement. Nothing in
it mentions positions, movement or time.

Every candidate must satisfy exactly this, so every candidate ends up running a
byte-identical quantum circuit with the same error-correcting power. **Two plans
differ only in where the ions live and how they walk** — which is why this is a
pure hardware-mapping problem, and why fidelity barely moves between plans while
speed and chip area move a lot.

### Stage 1 — GEOMETRY: map the rings onto the chip *(SECTION 1 of each seed)*

Decide where all 220 ions physically rest on the junction grid, and where each
ancilla has to be standing at each of the 7 gate rounds.

This is the "ring → hardware" mapping. The code's qubits are indexed by three
cyclic rings; a layout chooses how those rings become rows, columns and junction
legs — folded into 3-row cells, spread on a line, sheared, etc.

**What Stage 1 decides: the DISTANCE FLOOR** (defined in Part 0). Once you fix
where things are, you have fixed how far each ion must travel between
consecutive gate rounds — so you have fixed the minimum number of transport
rounds any router could ever achieve on that layout. The evaluator computes it
and reports it as `floor_total`: **329** for the pitch-4 cell layout, **333** for
the folded one.

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

### Stage 3 — ASSEMBLY: write out the actual cycle, in order *(SECTION 3)*

This is the least glamorous stage and the easiest to misread: it is not an
algorithm, it is the **"main program"** of the plan. Stage 1 knows *where* things
go, Stage 2 knows *how to move them* — Stage 3 calls them in the right order and
writes down the resulting list of phases. In full, one SEC is:

```
1.  PREP        reset all 70 ancillas          (they must be on optical rows)
2.  for t = 0..6:                              ← the 7 schedule rounds
      a. MOVE   route every ion to its round-t partner   ← calls Stage 2
      b. MERGE  fuse each data+ancilla pair into one trap well
      c. GATE   fire the two-qubit gate on all 70 pairs at once
      d. SPLIT  pull each pair back apart
3.  MEASURE     read out all 70 ancillas       (optical rows again)  → the syndrome
4.  WRAP-BACK   route every ion home           ← calls Stage 2, ~67 rounds
```

Step 4 exists because the next cycle must start from the same arrangement — the
heartbeat has to repeat identically forever. That "walk home" is real work we
pay for and, as Part 2 explains, is one of the two places our rules are stricter
than the paper's.

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

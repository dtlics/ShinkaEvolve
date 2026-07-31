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
- **The grid.** The chip is a lattice of **potential wells**. Each horizontal
  rail section holds exactly two of them (`S(r,c)` and `J(r,c)`); each vertical
  section between two rows holds exactly two (`D(r,c)` and `U(r+1,c)`); and a
  **junction holds none** — it is a zero-length crossing, so the four wells
  around it are all one step from each other. One step = one hop between
  neighbouring wells. Consequence: **moving one column sideways costs 2 steps,
  moving one row up or down costs 3.**

  > This is a **correction applied on 2026-07-29**. The model used to make the
  > junction a well in its own right, which charged **5** steps for a one-row
  > hop — a 67% over-charge on every vertical move. Part 2 explains how the
  > correction was grounded.

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
   3× it. Our folded layout has a gap whose floor is 50 rounds and spends 61 on
   it — that 11-round difference is pure disorganisation. It used to be 72
   rounds, and the difference was mostly one identified thing: the router walked
   the *old* five-edge graph and paid 5 steps per row where the chip charges 3.
   That was repaired (v6.1); what is left is genuine packing loss.

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
rounds**, against our current best layouts' 226- and 230-round floors. It is now
**the** biggest unexploited idea in the task: the other one that used to sit
beside it — teaching the router the junction-crossing steps it ignored — was
cashed in at v6.1 (Part 3, finding 7) and is worth no more.

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
pure hardware-mapping problem, and why speed and chip area move much more between
plans than fidelity does. *Much more*, not *only*: fidelity does move, and by a
measured 14–16% between our best plan and the paper-equivalent one — see
"The LER head-to-head" in Part 2.

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
and reports it as `floor_total`: **226** for the pitch-4 cell layout, **230** for
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
`rounds_over_floor`, with `ions_per_round` showing the packing quality. The two
shipped routers are at **1.37×** (pitch-4) and **1.30×** (folded) — they were at
1.58× and 1.63× until v6.1 taught them the chip's real step rules.

> This is the stage everyone underestimated, twice. See Part 3.

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
4.  WALK HOME   route the DATA ions home       ← calls Stage 2, 5 rounds
```

Step 4 exists because the next cycle must start from the same arrangement — the
heartbeat has to repeat identically forever. But "the same arrangement" is a
weaker requirement than it looks, and getting it right is the second of the two
corrections in Part 2:

- **Data, beacons and reservoir ions must each end on their own exact site.**
  The data qubits *are* the code; permuting them silently relabels the logical
  frame, and nothing downstream would notice.
- **Ancillas only have to restore their species' occupied *set*.** Which
  particular ion sits on which ancilla site does not matter — an ancilla is
  reset before every cycle and read out at the end of it, so it carries nothing
  across the boundary, and the circuit is built from the check index, never from
  a position. Any leftover shuffle is fixed by renaming the ancillas in
  software, which is exactly what IonQ's own algorithm does.

This used to cost 67 rounds of the best seed's 421 (every ancilla walking back
to its personal site). It now costs 5.

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
| **Vertical cost** | **same — 3 primitive steps per row. Was 5 on our side; corrected 2026-07-29, see below** |
| **Cycle boundary** | **same — ancillas end on their species' site set and are relabelled in software. We used to demand exact sites; corrected 2026-07-29, see below** |

### The two corrections that made this like-for-like

This section used to be headed *"Two caveats that could move the verdict"*. They
are no longer caveats: both were run down against the paper and **applied**.

**How they were grounded.** Rebuilding IonQ's SEC from their published Table X
schedules alone reproduces every *non-transport* row of their Table XXVI
**exactly, for BOTH the Q70 and the Q102 block, with zero free parameters** —
merge/split 16 / 18, two-qubit layers 8 / 9, readout 3 / 3, state preparation
1 / 1. Getting the two-qubit row right required identifying the extra layer
(Q70's schedule has 7 rounds but the table says 8) as the **data
leakage-detection unit**, the beacon-based check that runs at the start of the
SEC and costs a gate layer but no transport. Four independent integer rows
landing on two different codes is what licenses the rest.

1. **Vertical cost — the junction holds no well.** Fig. 62's vector well census
   is exactly **2 wells per horizontal section, 2 per vertical section, and none
   at a junction**. With the paper's own "two shuttling steps to increment its
   column index", that forces a one-row well-to-well hop to be **3** steps. Our
   grid charged 5, because our junction node was doubling as the section's
   second well. Adding the three junction-crossing edges makes the junction the
   zero-length crossing it actually is, and BFS then gives
   `d = 2·dr + max(2·dc, 1)` — verified exact against the evaluator's own
   `_dist_lb` on every interior pair.

   > **Honest caveat, stated because it matters.** The transport *total* alone
   > cannot decide this. A 5-step row hop with the paper's routing reconstructs
   > their 424 for Q70; so does a 3-step row hop plus ~19% of overhead the
   > reconstruction doesn't model (and the same 0.837 ratio shows up for Q102,
   > so it is code-independent, not a Q70 fudge). **The well census settles it**
   > — it is a structural reading of the figure, not a fit to a number.

2. **Cycle boundary — IonQ relabel, so may we.** Their **Algorithm 1** (p. 30)
   executes only **6 of the 7** shift legs and ends each SEC with every ancilla
   displaced by a single uniform group shift, absorbing the mismatch on line 2:
   *"Relabel the ancilla in software"* (p. 28: "no physical transport"). That
   residual was derived **two independent ways and they agree**: summing
   Algorithm 1's six executed legs gives `(long 0, medium 3, short 4)`, and
   tracking the alignment cell of every ancilla through this repo's own
   `required_pairs()` gives the same `(0, 3, 4)` — one single residual shared by
   all 35 X ancillas and all 35 Z ancillas, i.e. genuinely a group shift.
   Nothing pins an ancilla ion to a position: beacons and cooling partners
   attach to **data**, the loss protocol's ancilla is dynamic, and the reservoir
   swaps ancilla ions in and out. Here the circuit builder is position-blind and
   every detector is keyed on the check index, so the relabel is invisible.
   Data, beacons and reservoir ions still must end on their **exact** sites.

### The comparison

Normalising **into the paper's own accounting formula** (`rounds/20 + 2 one-qubit
layers + 8 two-qubit layers + 3 readout`), so only the transport count differs:

| | transport rounds | SEC time (their formula) | exposure | rail sections |
|---|---|---|---|---|
| **paper Q70** | 424 | 34.20 POC | 536.06 | ~288 |
| **ours — `initial_annealed` (best)** | **244** (−42%) | **25.20 POC** (−26.3%) | **523.46** (−2.35%) | 304 (+5.6%) |
| ours — `initial_folded` | 300 (−29%) | 28.00 POC (−18.1%) | 527.38 (−1.62%) | **283** (−1.7%) |
| ours — `initial_evolved` | 310 (−27%) | 28.50 POC (−16.7%) | 528.08 (−1.49%) | **277** (−3.8%) |

*(The two routed seeds moved at v6.1, when their routers were taught the chip's
real step rules — before that they read 375 / 287 and 358 / 301. No layout
constant changed; the plans simply stopped taking 5 steps where 3 were
available. `initial_annealed` arrived later, salvaged from run `q70ring_v3`.)*

**Read this as a real speed win.** Same chip graph, same cycle-boundary
convention, same 220 ions, same circuit: 42% fewer transport rounds and a 26%
shorter core cycle on the best plan. Two things keep it honest:

- 424 is *their published number for their own hand design*. An idealized
  mechanical reconstruction of that same design on the corrected chip lands near
  **355** — so "42% fewer" is a comparison against a published figure, not a
  claim that their strategy could not do better. Against that 355 reconstruction
  the margin is ~31%;
- the area picture is **split**. The two routed seeds sit under the paper's ~288
  rail sections (283 and 277); `initial_annealed` sits at 304, i.e. it buys its
  rounds with ~6% more trap area.

### The LER head-to-head — measured, not asserted

> This section used to be headed *"Why LER barely moves"* and concluded that a
> shuttle plan buys time and area **but not fidelity**. That conclusion was
> never measured — it was read off the exposure arithmetic with an assumed
> exponent. It has now been measured, and the qualitative part of it was wrong.

**Under one fixed decoder, `initial_annealed` has a 14–16% lower logical error
rate than the paper-equivalent 424-round plan, at every physical error rate we
can sample, at 7.6σ to 17σ.** What remains genuinely uncertain is not *whether* there
is a gap but *how big it stays* at IonQ's p = 1e-4 operating point, where the
LER is ~1e-10 and direct sampling is impossible.

#### The baseline, and why it is a fair one

`evaluate.build_circuit` reads exactly one key of a compiled plan —
`compiled["segments"]`. Every other `compiled[...]` access in the file is in the
scoring path. So the stim circuit, hence the DEM, hence the LER, is a function
of the plan **only** through the ordered run-lengths of transport/merge
segments, the gate order, and the prep/measure batch sizes. Layout, geometry,
routing and footprint are invisible to the sampler. Reproducing Table XXVI's
counts is therefore not an approximation of a fair baseline — for LER purposes
it *is* the baseline.

The baseline arm is our own 244-round plan padded to **424 transport rounds** with
legal no-op "out and back" move phases (one ion steps to a free neighbouring
well and steps back next round, restoring position before any other phase runs).
`compile_plan` accepts it, it is noiseless-deterministic on both observables,
and its exposure is **536.06 exactly**. Padding is charged exactly like real
transport, because the noise model charges `140·p/2000` per round regardless of
how many ions move.

This is a **noise-profile equivalent, not a reconstruction of IonQ's embedding**.
Its `zones`, `floor_total` and `combined_score` are meaningless as statements
about their design; only exposure, transport/merge counts and the resulting LER
curve carry meaning.

#### How it was measured

All seven arms are decoded from the **same shots**. Because the arms' DEMs have
identical mechanism supports (31,710 of them) and differ only in per-mechanism
probabilities, one fault configuration sampled from the reference arm's DEM can
be reweighted to every other arm, `w(x) = P_arm(x)/P_ref(x)`. The ratio
`R = LER_arm/LER_ref` is then the sample mean of `w` over failing shots, with a
standard error set by `sd(w|F)` rather than by `sqrt(2/N)` — about 100× cheaper
than sampling the arms independently. The DEM sampler was checked against stim's
own circuit sampler: mean detector rate agrees to 0.04σ, per-detector z-scores
are N(0,1) over all 700 detectors.

One decoder, **relay-BP** (IBM `RelayDecoderF64`), is used for every arm. That
is both the protocol the comparison requires — absolute LERs are not comparable
across decoders, only a ratio under one decoder is — and a practical necessity,
since relay-BP draws its relay gammas at random and per-arm decoders would make
the failure indicator differ between arms for reasons unrelated to the plans.
BP-OSD (`osd_order=0`) was measured here to be 84× weaker (LER/shot 3.52e-1 vs
4.17e-3 at p = 4e-3) and 23× slower; and the DEM is **not decomposable**, so
matching decoders (pymatching, beliefmatching) cannot be built at all.

#### What was measured

X observable, 9 SECs, relay-BP. `LER/SEC = 1 − (1 − LER/shot)^(1/9)`.

| p | shots | failures | LER/SEC ours (244) | LER/SEC paper-equiv (424) | ratio paper/ours | ours lower by |
|---|---|---|---|---|---|---|
| 3.0e-3 | 45,000 | 19 | 4.69e-5 | 5.60e-5 | 1.1941 ± 0.0254 | 16.3% ± 1.8% (7.6σ) |
| 3.5e-3 | 94,208 | 91 | 1.074e-4 | 1.273e-4 | 1.1855 ± 0.0132 | 15.6% ± 0.9% (14.1σ) |
| 4.0e-3 | 36,864 | 128 | 3.864e-4 | 4.553e-4 | 1.1779 ± 0.0108 | 15.1% ± 0.8% (16.5σ) |
| 4.5e-3 | 14,336 | 142 | 1.105e-3 | 1.287e-3 | 1.1631 ± 0.0104 | 14.0% ± 0.8% (15.7σ) |
| 5.0e-3 | 6,144 | 169 | 3.094e-3 | 3.625e-3 | 1.1691 ± 0.0101 | 14.5% ± 0.7% (16.7σ) |
| 5.0e-3 **(Z)** | 6,144 | 131 | 2.392e-3 | 2.777e-3 | 1.1594 ± 0.0123 | 13.8% ± 0.9% (12.9σ) |

Three independent checks on that table:

- **Reproducibility.** p = 4e-3 was measured twice with different seeds and
  different decoder instances. The absolute LERs differ by 20% (1.3σ); the
  *ratios* agree to 0.84σ (1.1951 ± 0.0173 vs 1.1779 ± 0.0108). Exactly the
  behaviour the paired design predicts.
- **Observable.** The Z observable is a different circuit and gives the same
  answer (1.1594 ± 0.0123 vs 1.1691 ± 0.0101 for X).
- **Decoder priors.** The single decoder is built on *our* DEM, which understates
  the higher-transport arms' noise and so should flatter us. Rebuilding it on the
  paper arm's DEM moves the ratio from 1.1691 to 1.1640 — a 0.005 shift against
  a 0.010 statistical error, i.e. below the noise.

#### The response law

Five pure "lever" arms (300 / 424 / 424-with-16-merge/split / 600 / 1000
transport rounds — the same plan padded by different amounts) span a 13× range
of exposure change, from +0.75% to +10.1%, and are decoded from the same shots at
zero extra cost. Within that family, LER is an **exact** power law in noise
exposure:

| p | η = d ln LER / d ln exposure | χ²/dof over 5 lever arms |
|---|---|---|
| 3.0e-3 | 7.43 ± 0.44 | 0.03 |
| 3.5e-3 | 7.15 ± 0.21 | 0.00 |
| 4.0e-3 | 6.85 ± 0.17 | 0.06 |
| 4.5e-3 | 6.31 ± 0.17 | 0.13 |
| 5.0e-3 | 6.50 ± 0.16 | 0.12 |

χ²/dof ≈ 0.1 across arms spanning +0.75% to +10.1% exposure means the exposure
scalar predicts LER, within this family, to better than the measurement error.
That validates the substitution the score relies on: **exposure is not a proxy
for LER, it is the control variable.** The 14→16 merge/split ambiguity in
Table XXVI is measurable and negligible: exposure 536.06 vs 536.20 gives ratios
1.1779 vs 1.1799, a 0.2% difference in LER.

#### The falsifiable prediction, and how it came out

The 300-round lever and the real `initial_folded` plan have **identical exposure
(527.38)** and identical segment structure, but very different per-gap transport
distributions (`[4,42,53,32,61,48,55,5]` vs `[4,32,43,42,39,40,47,53]`) and
completely different layouts (283 vs 304 rail sections). If LER were a function
of exposure alone they would have to agree.

**They do not.** Combined over five independent points (four p values plus the Z
observable):

> R(`initial_folded`) − R(300-round lever) = **+0.0153 ± 0.0030 → 5.2σ**

The real plan's LER is **1.5% ± 0.3% higher** than an equal-exposure padded plan.
Expressed as an effective exposure, `initial_folded` behaves like a
same-shaped plan carrying 528–529 rather than its nominal 527.38 — a penalty
worth roughly **10–24 extra transport rounds**. So the middle point *does* land
where it was predicted to land — between ours and the paper's, in the right order
and within 1.5% of the right magnitude — but the exposure-**only** model is
**rejected at 5σ**: where the transport rounds sit in the cycle matters, not just
how many there are. That 1.5% is the right scale for a plan-shape systematic on
the headline number, and it is the reason caveat 2 below exists.

#### The claim at the operating point

The measured ratio is exposure^η with an exposure ratio of
536.06/523.46 = 1.024071, so the extrapolation to p = 1e-4 is *entirely* a choice
of η; the statistics contribute nothing (they are already at 16σ).

| η | where it comes from | LER reduction |
|---|---|---|
| 7.4 → 6.5 | **measured**, p = 3e-3 → 5e-3 | 16.2% → 14.3% |
| 5.00 | ⌈d_circ/2⌉ — the p→0 value *if* transport faults appear in minimal failing sets in proportion to their share of the exposure | **11.2%** |
| 4.68 | local exponent of the paper's own published ansatz at p = 1e-4 | 10.5% |

Two things block a real extrapolation, and both are stated rather than papered
over. First, η is measured to *rise* as p falls (6.50 at 5e-3 → 7.43 at 3e-3),
while theory says it must converge to a p-independent combinatorial constant as
p → 0; the two disagree about direction, so the trend cannot be run down to
1e-4. Second, the ratio η/ν against the LER-vs-p exponent ν — which would have
been a clean bridge, since ν is known at 1e-4 from the paper's own ansatz — is
**not constant** (1.02 at p = 3e-3 falling monotonically to 0.62 at 5e-3,
χ²/dof = 34 against a constant), so that bridge is abandoned too.

> **The defensible statement.** Ours is **14–16% below the paper-equivalent
> 424-round plan at every p we can measure**, and at the p = 1e-4 operating point
> the reduction is bracketed at **10.5%–16%**, with **~11%** the value to quote if
> a single number is needed — because η = 5 is what both the paper's own
> prefactor and this task's score already assume, and it is the conservative end
> of what we measured.

The structural reason the effect is bounded is unchanged and still worth
internalising: a transport round costs `p/2000` on each qubit while a two-qubit
gate layer costs `p` on 70 pairs, so the whole plan-dependent share of the error
budget is ~3.5% of exposure and the frozen circuit dominates. Even **free**
transport would buy only 25% (at η = 5) to 32% (at the measured η) against the
paper. What changed is the conclusion drawn from that: a bounded effect is not
the same as no effect, and the 11–15% actually on offer is worth measuring
rather than dismissing.

#### Fits, and what not to do with them

Fitting the paper's own form `LER/SEC = p^5 exp(αp² + βp + ζ)` to the five
X-observable points gives, per arm:

| arm | α | β | ζ | χ²/dof | → p = 1e-3 | → p = 1e-4 |
|---|---|---|---|---|---|---|
| ours (244) | 8.67e4 | +245 | 17.33 | 2.6/2 | 4.70e-8 | 3.46e-13 |
| paper-equiv (424) | 9.63e4 | +155 | 17.70 | 2.6/2 | 6.26e-8 | 4.95e-13 |
| lever 1000 | 1.30e5 | −165 | 18.92 | 1.9/2 | 1.59e-7 | 1.62e-12 |
| **IonQ published** | **1.07e6** | **−3410** | **23.0** | — | **9.39e-7** | **7.00e-11** |

A pure power law `LER ∝ p^ν` gives ν = 9.01 ± 0.30 with χ²/dof = 5.6/3 — a poor
fit, so there is real curvature, but the three-parameter form is only barely
determined by five near-threshold points (the arms with four points fit to an α
of the *wrong sign*). **These extrapolations are not defensible as absolute
predictions** and are shown only for completeness: they sit ~200× below IonQ's
published curve at 1e-4, which is what happens when a near-threshold slope is
run out 1.5 decades.

In particular, **do not divide the two extrapolated curves**: paper-fit/ours-fit
at 1e-4 comes out at 1.43, implying η ≈ 15, which is nonsense. The two arms are
measured on the same shots and their per-shot failure statistics are correlated
at **0.994** (measured, p = 5e-3); fitting them independently throws that
correlation away and then amplifies the residue over 1.5 decades. The ratio must
come from the paired estimator, which is what every number above does.

#### Caveats, in full

1. **Different decoder from the paper.** Theirs is beam search, ours is relay-BP.
   Absolute LERs are **not** comparable. Our measured curve happens to sit *below*
   their published one across the whole plotted range (~20× at p = 1e-3, ~200× at
   p = 1e-4 by our fit) — **this is not a fidelity claim**. It is a different
   decoder, a different observable normalisation, and an ansatz of theirs whose
   `exp(αp²)` term with α = 1.07e6 was fitted far below our measurement window
   and rises very steeply inside it. Only the ratio between our two arms, under
   one decoder, is interpretable. The published curve is on the figure for
   context and is labelled as such.
2. **The baseline is a noise-profile equivalent**, not IonQ's plan. It inherits
   *our* per-gap shape, and the falsifiability test above shows plan shape is
   worth ~1.5% in LER — so a ±1.5%-scale systematic sits on the headline number
   from this alone. It is the largest systematic identified, an order of
   magnitude above the decoder-prior one.
3. **η at the operating point is not measured and cannot be**, which is where all
   the remaining uncertainty lives.
4. **X-observable only** except at p = 5e-3. "Failure" means any of the 6 X
   logicals misdecoded over 9 SECs; the paper's normalisation may differ, which
   is another reason absolutes do not transfer.
5. **Sampling stopped on an error budget**, which biases a single LER estimate
   upward by O(1/k) ≈ 1%. It cancels in the paired ratios.
6. **9 SECs, not a full memory experiment**; the per-SEC conversion assumes the
   SECs fail independently, which is the paper's convention.
7. **424 is IonQ's published figure for a hand design**, not a floor for their
   strategy. Against the ~355-round mechanical reconstruction the exposure gap
   shrinks to 1.48% and the reduction to ~7% (η = 5) / ~9% (measured η).

*Artifacts: `ler_headtohead.png` (log-log LER/SEC vs p with the fits and IonQ's
published curve, plus the exposure response law), `h2h_report.txt` (the full
numeric report), `ler_campaign.py` (runner), `paper_equiv_plan.py` (baseline
builder), `analyze_h2h.py` (fits/plot), and the `campaign_*.json` checkpoints —
all in the working scratchpad, outside the repo. ~224,000 shots decoded (each
scoring all seven arms by reweighting), ~5.5 h wall clock on 24 cores.*

---

## Part 3 — What we actually found

1. **The paper's 424 was never the obstacle.** Both layouts we had by run 2 have
   distance floors well under 424 (329/333 as measured then; 226/230 on the
   corrected chip). A perfect router on the geometry already in hand would have
   beaten the published number.
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
   1.7–2.1× floor to 1.27–1.34× (as the floor was then measured): 566 → 421 and
   700 → 446 rounds. That is the whole difference between "34% worse than the
   paper" and "level with it".
5. **The comparison itself was mis-stated**, five times now, each time corrected:
   the footprint bar compared vertices against trap sections (a 3.7× error); an
   LER win was first *implied* on no evidence, then *denied* on no evidence; the
   chip over-charged vertical motion by 67%; and the cycle boundary demanded a
   walk home the paper's own algorithm does not make. The chip and boundary fixes
   are the 2026-07-29 corrections in Part 2, worth 421 → 358 rounds on the best
   seed with **no change to any plan's actual movement** — they only stopped
   charging for things the hardware does not charge for. The LER question was
   finally settled by measuring it rather than arguing from the exposure
   arithmetic: there *is* a win, 14–16% at every p we can sample, ~11% at the
   operating point under the paper's own exponent. **The lesson is that both the
   optimistic and the pessimistic version of that claim were asserted, for
   months, without a single logical error being sampled in anger.**
6. **The floor is a Stage-1 property and is now clearly the deepest lever.**
   Since l = 7 and m = 5 are coprime, the ring torus is isomorphic to Z₃₅, so
   every realignment collapses to **one** 1-D rotation instead of two per-axis
   passes — analytically ~194 rounds of rotation versus the current 226- and
   230-round floors.
7. **Stage 2's one identified defect has now been fixed, and it was worth what
   it looked like.** The chip correction had cut the floor further than it cut
   the plans, so routing slack *rose* from ~1.28× to 1.58–1.63×. The cause was
   local: the routers inside SECTION 2 were still walking the old five-edge
   graph — they never used the junction-crossing steps — so they paid 5
   primitive steps per row where the evaluator's floor charged 3. They were
   planning on a strictly harsher chip than the one they were priced on.
   At v6.1 `is_edge`/`neighbors` gained the three missing edge families and
   `site_dist` was re-derived from scratch on the widened graph (a clique-grid
   argument; it is now *exact*, verified by brute-force BFS against the seeds'
   own `neighbors()` over every ordered site pair on eight different grid
   shapes). **No layout constant was touched.** Result: 375 → 300 and
   358 → 310 rounds, slack 1.63× → **1.30×** and 1.58× → **1.37×**, footprint
   287 → 283 and 301 → 277, scores +2.2392 → +2.6565 and +2.2542 → +2.6300.
   It also flipped the ranking: the folded seed is now the better plan.
   What is left above the floor is genuine packing loss, and collapsing it
   entirely is worth only +0.45 / +0.54 — so **finding 6 is now the main event**.

### Attribution, stated plainly

No result in the current seeds is purely evolution's. Run 2's evolved layout is
in there, but its headline change (the pitch compression) was a regression caused
by the broken metric; the pitch repair, the router, both 2026-07-29 model
corrections and the v6.1 router repair were hand-made. Note especially that the
421 → 358 improvement is **not** a better plan — it is the same plans, re-priced
correctly — whereas 358 → 310 and 375 → 300 *are* genuinely shorter plans, just
found by a hand-written fix rather than by search. That is why every candidate
reports `gain_over_seed` (now against +2.6300, which tracks `initial_evolved`
even though `initial_folded` scores 0.0265 higher) — so the next run's own
contribution is unambiguous.

# q70_ring_shuttle — evolve the ring→hardware mapping + shuttle plan for the Q70 memory block

**Fixed code, fixed circuit, evolved transport.** The task takes IonQ's Walking Cat
Q70 = [[70,6,9]] BB memory block (arXiv:2604.19481) with its published Table X
syndrome-extraction schedule FROZEN, and evolves *how the three rings are laid out
and shuttled on a junction-grid QCCD chip*: the static layout (which trap site every
ion occupies) plus a per-cycle timeline of parallel transport rounds, merge/split
rounds, gate layers, and prep/measure batches. Every candidate runs the *identical*
gate-level circuit (same d = 9, same published d_circ = 9); candidates differ only
in where ions sit and how they move — which changes SEC duration (POC), the
noise exposure accumulated per cycle (hence logical error rate at the operating
point), and the trap footprint (zones).

See [HEAD_TO_HEAD.md](HEAD_TO_HEAD.md) for a plain-language walkthrough of the
four stages a solution goes through (geometry → routing → assembly → scoring),
the audited comparison against the paper, and what the two runs found.
[DESIGN.md](DESIGN.md) has the design rationale and background primer.

## Contract files

| File | Role |
|---|---|
| `initial.py` | Seed A (EVOLVE-BLOCK): the *unfolded* realization of the Fig.-60 strategy — block swaps for the long ring, conveyor+rail-wrap medium shifts, Fig.-61 embedded short shifts, vertical data hops for gating, in-place prep/measure on optical rows. Score anchor (0.0), **896 rounds**. Parametric in `(l, m, schedule)`; self-verifies while building. |
| `initial_folded.py` | Seed B (EVOLVE-BLOCK): the *folded* 2D embedding — cells of 3 rows (data-L / ancillas / data-R), family change = ±2-column side flip (no block swaps), per-column vertical conveyors with lane-column wraps, Fig.-61 embedded shifts per row. **300 rounds / +2.6565** at 283 rail sections — the best of the three seeds since the v6.1 router repair. |
| `initial_evolved.py` | Seed C (EVOLVE-BLOCK): run `q70ring_v2`'s best evolved program with its cell pitch repaired 3→4 (evolution had compressed it to win the then-broken zone term, which aliased the X/Z wrap lanes and forced an X-then-Z sequential fallback). 676 → 566 → 421 → 358 → **310 rounds / +2.6300** at 277 rail sections. Since the v6.1 router repair seed B edges ahead, so `SHIPPED_SEED_SCORE` tracks **seed B** (+2.6565) — it must always track the best shipped plan, or a run could report positive `gain_over_seed` just by rediscovering a basin it was already handed. |
| `evaluate.py` | Immutable oracle: exact plan compiler/validator, POC + noise-exposure accounting, stim noiseless-determinism check, deterministic seed-anchored score. Also hosts the certification-only circuit builder + BP-OSD sampler. |
| `routing.py` | **FROZEN REFERENCE ONLY — no seed imports it, and since v6.1 it no longer matches them.** It *was* the shared, non-evolved round packer; the router now lives INSIDE each seed's EVOLVE-BLOCK (SECTION 2), so a mutation can improve the packing algorithm as well as the geometry. It is kept on disk **unchanged**, which means it still walks the pre-correction *five*-family subgraph: it reproduced the inlined copies exactly (419/301 and 455/292) only up to v6, and the v6.1 repair of `neighbors`/`site_dist` in the seeds deliberately left it behind. Treat it as a frozen *pre-repair* baseline, not as the current router. `python routing.py` still runs its self-test. |
| `certify.py` | Out-of-loop head-to-head: re-runs a candidate in a fresh process and measures real Monte-Carlo LER at chosen p (BP-OSD), tabled against the paper's published Q70 numbers. |
| `qecc/q70.json` | Pinned code assets (Hx/Hz supports, symplectic logical pairs, schedule), generated + verified by `make_code_assets.py`. |
| `selfcheck.py` | Dev driver: seed build → compile → determinism → scoring-path → invalid-plan probe battery → optional `--ler`. |

## The plan format (what the candidate returns)

```
{"grid": {"rows": R, "cols": C},              # R<=24, C<=96
 "layout": {"data": [site x70], "x_anc": [site x35], "z_anc": [site x35],
            "beacon": [site x70], "reservoir": [site x10]},
 "timeline": [phase, ...]}                    # ONE cyclic SEC
site  = ["S"|"J"|"U"|"D", row, col]
phase = {"t":"move",    "moves": [[qid, from_site, to_site], ...]}   # 1 parallel
        {"t":"merge",   "pairs": [[mobile_qid, host_qid], ...]}      #   round each
        {"t":"split",   "pairs": [[mobile_qid, target_site], ...]}
        {"t":"gate",    "round": 0..6}
        {"t":"prep",    "ancillas": [...]} / {"t":"measure", "ancillas": [...]}
```

Grid semantics (**corrected 2026-07-29 against Fig. 62's well census — see
"The chip model" below**): every node is a *potential well* and every edge is
one primitive transport step. A horizontal rail section `(r,c)` carries exactly
two wells, `S(r,c)` and `J(r,c)`; a vertical section carries exactly two,
`D(r,c)` and `U(r+1,c)`; **a junction carries no well at all** — it is a
zero-length crossing, so the four wells around junction `(r,c)` — `J(r,c)`,
`S(r,c+1)`, `U(r,c)`, `D(r,c)` — are mutually one step apart. The eight edge
families are therefore

```
S(r,c)–J(r,c)        (inside a horizontal section)
D(r,c)–U(r+1,c)      (inside a vertical section)
J(r,c)–S(r,c+1)   J(r,c)–U(r,c)   J(r,c)–D(r,c)
S(r,c+1)–U(r,c)   S(r,c+1)–D(r,c) U(r,c)–D(r,c)      (across the junction)
```

so **one column costs 2 steps and one row costs 3**, and the rest-site metric is
`d = 2·dr + max(2·dc, 1)` (`2·dc` when `dr = 0`). **Odd rows are optical**
(prep/measure allowed there only). One ion per site; a move phase is one
parallel transport round; merges require one-edge adjacency to a host on an S
site.

Validator highlights (each violation → archived sentinel score −2.0 with the exact
rule named): plans are JSON-sanitized on entry (plain data only); edge-legality +
collision freedom + no head-on swaps per round; **beacons and reservoir ions are
static** (may not move); gate rounds in schedule order with the merged-pair set
exactly equal to the frozen required pairs; merges only for pairs the *next* gate
round requires, ≤1 merge + 1 split per ion per inter-gate interval (merge/split
cost 0 POC per the paper, so this closes free-teleport chains; they DO carry
transport-round noise); prep before gates / measure after all gates, both on
optical rows, each ancilla exactly once; beacons in the same row as their partner
data qubit; **cycle boundary** — every *non-ancilla* ion (data, beacon,
reservoir) must end the SEC on its **exact** layout site, and each **ancilla
species must restore its own occupied SET** (X compared to X, Z compared to Z,
separately), verified by replay.

## Hardware model (paper's moving-qubit model, Table III)

| op | error weight (× p) | time |
|---|---|---|
| 2q gate (merged pair) | 1 | 1 POC |
| prep / measure | 1/10 | 1 POC per batch phase |
| idle during a prep/measure POC | 1/100 | — |
| transport or merge/split round | 1/2000 on ALL 140 simulated qubits | 1/20 POC (ms: 0) |

`T_SEC = transport_rounds/20 + 7 gate layers + prep_phases + measure_phases + 7.05`
(the 7.05 POC is the paper's loss/leakage-check time, charged but not simulated —
their Fig.-18 sensitivity analysis shows loss/measurement are ×1.10/×1.13 levers,
and the head-to-head uses their Pauli-only *reference* curves). Physical qubits are
constant by construction: 70 data + 70 ancilla + 70 beacons + 10 reservoir = 220
(matches Table XI).

## The chip model and the cycle boundary: two corrections, grounded (2026-07-29)

Two things in this task's model were wrong against the paper. Both were
established by **reproducing IonQ's own Table XXVI from their published
schedules with zero free parameters**, and both have now been applied. The
seeds and the score anchors moved as a result; nothing about the code, the
circuit or the noise model changed.

**How the reconstruction was grounded.** Rebuilding the SEC's
primitive-operation counts from the Table X schedules alone reproduces every
*non-transport* row of Table XXVI **exactly, for BOTH Q70 and Q102, with no
fitted parameter**: merge/split 16 / 18, two-qubit layers 8 / 9, readout 3 / 3,
state preparation 1 / 1. Getting the two-qubit row right requires identifying
the extra layer — Q70's schedule has 7 rounds but the table says 8 — as the
**data leakage-detection unit** (the beacon-based data LDU that runs at the
start of the SEC, p.34: a 2q layer plus 1q layers plus a readout, and zero
transport). A model that lands four independent integer rows on two different
codes is a model of the right SEC.

**Correction 1 — the junction holds no well.** Fig. 62's vector well census is
exactly 2 wells per horizontal section, 2 per vertical section, and **none at a
junction**; combined with p.78's "two shuttling steps to increment its column
index" this forces a one-row well-to-well hop to be **3** primitive steps (and a
`dr`-row hop `2·dr + 1`). Our grid charged **5** and `3·dr + 2·max(dc,1)`,
because our `J` node was doing double duty as both the section's second well
*and* the junction. The fix adds the three junction-crossing edges
`D(r,c)–S(r,c+1)`, `U(r,c)–S(r,c+1)`, `U(r,c)–D(r,c)`; BFS over the resulting
graph gives `d = 2·dr + max(2·dc, 1)`, and `_dist_lb` now reproduces it exactly
(verified exact on every interior S–S pair, and a valid lower bound on the
`c = 0` boundary column).

> **Stated plainly: the transport-round total alone does NOT settle this.** It
> is degenerate — a 5-step row hop with the paper's own routing reconstructs
> Table XXVI's 424 for Q70, and so does a 3-step row hop plus ~19% of overhead
> the reconstruction does not model (the same 0.837 ratio appears for Q102, so
> whatever it is, it is code-independent). **The well census is what settles
> it**, because it is a direct structural reading of the figure rather than a
> fit. Do not cite p.85's "v = 10" as corroboration: in context `v` counts the
> ten *vertical sections* an 11-row block spans, not a step cost.

**Correction 2 — ancillas need not return to their exact sites.** IonQ's
Algorithm 1 (p.30) runs only **6 of the 7** shift legs and ends each SEC with
every ancilla displaced by one **uniform group shift**, absorbing the mismatch
with "Relabel the ancilla in software" (Alg. 1 line 2; p.28: "no physical
transport"). That residual was derived **two independent ways** and agrees:
summing Algorithm 1's six executed legs gives `(long 0, medium 3, short 4)`,
and tracking the alignment cell of every ancilla through this repo's own
`required_pairs()` gives the *same* `(0, 3, 4)` for all 35 X and all 35 Z
ancillas (one distinct residual per species, i.e. genuinely a group shift).
Nothing pins an ancilla ion to a position — beacons and cooling partners attach
to **data**, the loss protocol's ancilla is dynamic, and the reservoir swaps
ancilla ions in and out — and `build_circuit` here is entirely position-blind
with detectors keyed on the **check index**, so a relabel is invisible to the
circuit. The rule is now: data / beacon / reservoir ions must end on their
**exact** layout site (permuting data would silently relabel the logical
frame), and each ancilla **species** must restore its own occupied **set**,
compared **separately** for X and Z. Both halves are verified by replay.

Effect on the seeds: **1012 → 896**, **446 → 375**, **421 → 358** transport
rounds, and `rounds_over_floor` *rose* (1.36→1.39, 1.34→1.63, 1.28→1.58)
because the floor fell further than the plans did — the seeds' routers were
still walking the old five-edge subgraph and paying 5 steps for a row.

**v6.1 (2026-07-30) closed that gap.** Both evolved seeds' SECTION-2b
`is_edge`/`neighbors`/`site_dist` were re-derived on the corrected eight-family
graph (`site_dist` is now *exact* on the obstacle-free chip; the derivation
treats the four wells around each junction as a clique and the chip as a grid
of cliques joined by one edge each). No layout constant was touched. Result:
**375 → 300** and **358 → 310** transport rounds, `rounds_over_floor`
**1.63 → 1.30x** and **1.58 → 1.37x**, footprint 287 → 283 and 301 → 277 rail
sections, scores **+2.2392 → +2.6565** and **+2.2542 → +2.6300**. The anchor
`initial.py` shares no router code with them (it is a template-based builder
whose `hop_steps` deliberately walks the 5-step J-centred ladder), so it was
left alone at 896 rounds and the three `SEED_*` anchors did not move.

## Score (v4 — deterministic; v3 zone metric, v4 anchors)

```
exposure  = 490 (gates) + 14 (prep/meas) + idle_slots/100
          + 140·(transport_rounds + merge_rounds)/2000    # fault events per SEC, p factored out
var_exp   = exposure − 504                                # the part a plan can move
T_core    = transport_rounds/20 + 7 + prep_phases + measure_phases
zones     = distinct RAIL SECTIONS (S sites) the plan ever occupies

score = 1.0 * log2(65.10 / var_exp)      # plan-dependent noise exposure
      + 0.5 * log2(53.80 / T_core)       # core SEC time (no frozen 7.05 overhead)
      + 1.0 * log2(428   / zones)        # trap footprint, in the paper's own unit
```

**v4 anchor change:** `SEED_VAR_EXPOSURE` 73.22 → **65.10** and `SEED_T_CORE`
59.60 → **53.80**, because the anchor seed now runs the SEC in 896 rounds
instead of 1012 under the corrected cycle-boundary rule. `SEED_ZONES` is
unmoved at 428 — neither correction touches the footprint. `SHIPPED_SEED_SCORE`
2.2028 → **2.2542**. Weights are unchanged (1.0 / 0.5 / 1.0).

**v6.1:** all three `SEED_*` anchors are **unchanged** (the router repair did
not touch `initial.py`, which still scores exactly 0.0000), and only
`SHIPPED_SEED_SCORE` moved, 2.2542 → **2.6300**, tracking the rebuilt
`initial_evolved.py`.

Anchors = the unfolded seed (scores exactly 0). The operating-point reliability
readout — `ler_shift_log10 = 5·log10(total exposure ratio)`, the paper's
extrapolation-ansatz LER shift — is a **public metric and the certification
claim, deliberately not the fitness** (its frozen part carries no gradient).

**v3 change and why.** v2 counted every S/J/U/D *vertex* an ion touched (anchor
1612) and compared that against the paper's ~288 *trap sections* — a ~3.7x
granularity error that inflated the "paper-grade" bar by ~2 points nobody could
earn, and, worse, paid the search to funnel all traffic through few corridors
(i.e. to **serialize**). Run q70ring_v2 took exactly that gradient: 80% of its
total score gain came from the zone term while transport rounds moved 3.4%, and
its winner had compressed the cell pitch 4→3, aliasing the two ion species'
wrap lanes onto shared columns and forcing an X-then-Z sequential fallback for
half its rounds. v3 counts rail sections only, so transit detours are free in
the footprint term and are priced (correctly) in time and exposure instead.

Why deterministic: the fault structure is frozen, BP-OSD costs ~0.1–4 s/shot
(certification-only), and the score carries zero sampling noise.

### The exposure-for-LER substitution is measured, not assumed

Neither earlier run validated it (run v2 certified a single plan, which only
tested anti-gaming). Direct test — real BP-OSD LER for three plans spanning a
6% exposure spread, p = 2e-3, 9 SECs, X observable, ~420 logical errors each,
identical decoder config (`osd_order=0`), ~1.9 h total:

| plan | exposure | transport rounds | measured LER/shot |
|---|---|---|---|
| unfolded | 577.22 | 1012 | 4.891e-2 ± 2.4e-3 |
| folded | 555.38 | 700 | 4.148e-2 ± 2.1e-3 |
| evolved | 546.00 | 566 | 3.898e-2 ± 1.9e-3 |

- **Ordering: exact agreement.** Ranking by exposure and by measured LER give
  the same order — which is all that selection consumes.
- **Magnitude: right to within one unit of exponent.** unfolded-vs-evolved
  predicted 1.321x, measured 1.255x (3.7σ from unity, so the effect is real);
  folded-vs-evolved predicted 1.089x, measured 1.064x (0.9σ, direction right,
  stats thin). The implied effective exponent is **~3.6–4.1 rather than the
  ansatz's ⌈d_circ/2⌉ = 5**, i.e. the reliability term is mildly optimistic
  about what a given exposure cut buys. Caveat: measured at p = 2e-3 with a
  weak decoder, which is nearer threshold than the p = 1e-4 operating point —
  the exponent should steepen toward 5 further below threshold, so this is a
  lower bound on the true sensitivity, and it errs conservative.

Verdict: the substitution is sound for ranking and roughly calibrated in
magnitude. Reproduce with `scratchpad/metric_validation.py` after any change to
the noise model or the score. *(Those three exposures are the pre-router plans
as they stood when the LER was measured; the seeds have since moved to
569.10 / 527.38 / 528.08. The validation is of the exposure→LER mapping itself,
which neither v4 correction touches — the Table III weights are unchanged — so
it stands as measured and was not re-run.)*

> **PROVISIONAL, revisit after the next run** (orchestrator note — do not
> surface mutable-rules talk to the mutation LLM): weights are 1.0 / 0.5 / 1.0.
> The zone term is bounded (220 sections is the hard floor — 220 ions must
> each rest on their own S site — so it can yield at most log2(428/220) = +0.96
> and cannot be farmed by serializing). Watch next: whether `rounds_over_floor`
> keeps falling — it *rose* to 1.58–1.63x under the v4 corrections because the
> floor moved further than the plans did, and the v6.1 router repair brought it
> back to **1.30–1.37x** without touching a layout constant, so what is left
> above the floor is packing slack rather than a wrong chip map — and whether
> any plan games `low_occ_rounds` by
> padding high-occupancy no-op rounds. Changing weights or anchors mid-run
> invalidates archive ordering; do it between runs only.

## Seed calibration and the honest paper bar (measured under score v4)

| plan | rounds | rail sections | T_SEC | floor | rounds/floor | score |
|---|---|---|---|---|---|---|
| `initial.py` unfolded (anchor) | 896 | 428 | 60.85 | 646 | 1.39x | **0.000** |
| **`initial_folded.py` (inlined router, best)** | **300** | **283** | **31.05** | **230** | **1.30x** | **+2.657** |
| `initial_evolved.py` (inlined router, `SHIPPED_SEED_SCORE`) | 310 | 277 | 31.55 | 226 | 1.37x | **+2.630** |
| **paper Q70 as published (Table XXVI)** | **424** | **~288** | 34.2 micro | — | — | **+2.010** |
| folded layout floor-perfect | 230 | 283 | 27.55 | 230 | 1.00x | +3.110 |
| evolved layout floor-perfect | 226 | 277 | 27.35 | 226 | 1.00x | +3.170 |
| CRT geometry, analytic rotation cost | ~194+gating | — | — | — | — | ≈+3.42 |

The two "floor-perfect" rows hold each plan's own realised footprint fixed and
only collapse its routing slack; they are what SECTION 2 + SECTION 3 alone could
still buy (**+0.45 / +0.54**). Everything past that has to come from SECTION 1.
The v6.1 router repair took the two seeds most of the way from where they were
(+2.239 / +2.254) to those ceilings, and it flipped their order: the folded
layout has the *worse* floor (230 vs 226) and the *bigger* footprint (283 vs
277) but packs tighter, so it now scores higher.

Paper rows are priced through *our* accounting (14 merge/split rounds, one prep
and one measure phase) so that only the transport count and the footprint
differ; that is the same convention the +2.253 figure used before the v4
anchors, so the row moved only because the anchors did.

Pre-correction history for reference: under the pre-v4 model the same three
seeds were 1012 / 446 / 421 rounds and scored 0 / +2.165 / +2.203. Before the
router was inlined the two routed layouts cost 700 and 566 rounds (+1.296 /
+1.711), and run q70ring_v2's best evolved plan was 676 (+1.651). With the
shared `routing.py` they were 455 and 419; the seeds now carry their own
trimmed copy of that router INSIDE the evolve block (see "The router is
evolvable now" below).

Every candidate's public metrics carry **`gain_over_seed` = score − 2.6565**, so
a run's own contribution is always separable from what it was handed. Report it
alongside the absolute score in any write-up. The constant tracks the **best**
shipped plan (`initial_folded.py`), so every seed boots at `gain_over_seed <= 0`
and only genuine progress reads positive — if it tracked a weaker seed instead,
a candidate could show a positive gain merely by rediscovering the better basin
it was already handed.

**The bar is +2.01, and it is now a genuine like-for-like number.** Both of the
asymmetries this section used to carry have been *corrected*, not merely noted:

- *The unit is identical.* Table XXVI's 424 counts parallel rounds exactly as we
  do — their own arithmetic `424/20 + 2 + 8 + 3 = 34.2 POC` (p. 85) and the
  sibling row `Merge/split 16 = 2 x 8 gate layers` both confirm it, and their
  horizontal cost (2 primitive steps per column) matches our `S→J→S` exactly.
- *The vertical cost is now identical too.* We used to charge 5 rounds per
  one-row `S→S` hop against the chip's 3. **Corrected** in the evaluator
  (v6, "The chip model and the cycle boundary" above) and, as of **v6.1**, in
  the seeds' own routers as well — until then they were still *planning* on the
  5-step graph while being *priced* on the 3-step one. Our 424-vs-300
  comparison is now on one graph at both ends.
- *The cycle-boundary convention is now identical too.* We used to return every
  ancilla to its own layout site and then add that tax to the paper's 424 to get
  a "like-for-like 502". **Corrected** — the paper's own Algorithm 1 leaves a
  uniform group shift and relabels in software, and so may we. `wrap_rounds` is
  still reported (5 of 300 on the best seed: the data ions' walk home) but there
  is no tax to add to the paper's number any more, so the 502 row is gone.

**What we do and do NOT beat the paper on — state this carefully.**

- *Transport rounds and SEC time: ahead, by ~29%.* 300 vs 424 rounds, 28.00 vs
  34.20 POC in the paper's own accounting formula (−18.1%), on the same chip
  model and the same cycle-boundary convention. This is a **speed** result — it
  shortens the logical clock cycle, hence algorithm wall-clock. One honest
  qualifier remains: 424 is *their published number for their own hand design*,
  whereas an idealized reconstruction of that same design on the corrected chip
  comes out near 355 — so treat "29% fewer" as the published-number comparison
  it is, not as a claim about the best plan their strategy admits. (The
  footprint caveat is gone: since v6.1 both seeds sit at 283 and 277 rail
  sections, *below* the paper's ~288, where they used to be at 287 and 301.)
- *Logical error rate: still essentially a tie.* Our exposure is 527.38 against
  536.06 for their 424-round plan under identical accounting — **1.6%**, i.e.
  a ~8% LER change at the ansatz exponent (~6% at the measured one). Do not
  claim a meaningful LER improvement. The reason is structural and worth
  internalising: a transport round costs `p/2000` on each qubit while a
  two-qubit gate layer costs `p` on 70 pairs, so the entire plan-dependent
  budget is ~6% of total exposure. Even **free** transport would buy only ~25%
  LER versus the paper. Fidelity is dominated by the frozen circuit; what a plan
  actually buys is time and area.

The open problem is now split cleanly in two, and v6.1 removed the confound
between them. Routing slack is **1.30–1.37x** floor and is *entirely* packing
loss — stalls, shove-asides, replans and SECTION 3's phase boundaries — because
SECTION 2 now walks exactly the graph the floor is computed on. Collapsing it
completely is worth +0.45 / +0.54 (the floor-perfect rows). Everything beyond
that is the floor itself, a property of the layout (see the CRT row).

## The router is evolvable now (was `routing.py`, shared and frozen)

`routing.py` used to be imported by both routed seeds and did the parallel
round packing for every candidate — worth 566 → 419 rounds on the best seed,
but NOT evolvable. It is now a **frozen reference file that nothing imports**,
and a trimmed copy of the packer lives inside each seed's EVOLVE-BLOCK, so a
mutation can improve the packing algorithm as well as the geometry.

Each seed's evolve block is three labelled sections, each with a banner saying
what it owns and what a mutation could try, so one can be rewritten without
reading the others:

| section | owns | key handle |
|---|---|---|
| 1 GEOMETRY | grid dims, every ion's rest site, the per-gate ion→site tables | `CELL_BASE` / `CELL_PITCH`; the layout's distance **floor** is set here and nowhere else |
| 2 ROUTER | `site_dist`, A* with soft site costs + directed-edge congestion pricing, BFS field, the round packer (per-ion stalls, ≥3-cycle rotation in one round, cascading shove-aside, bounded replanning) | `ROUTER_POLICY` + `ROUTER_ATTEMPTS`, one block at the top of the section |
| 3 ASSEMBLY | prep, the 7 merge/gate/split rounds, measure, the data-only walk home, phase emission | the gap>0 "duck out and come back" overlap |

What the inlined copy drops from `routing.py` (~39% of it): the 6-config
attempt portfolio (now 2 configs — measured better, see below), `route()`,
`check_rounds`, `ShuttleRouter`, `to_timeline`, `apply_rounds`' occupancy
bookkeeping, `avoid`/`soft_cost`, `_infer_grid`, the `field_cache` (dead on the
path-planning code path the seeds use), the whole field-plan engine mode with
its `_descend`/`override` machinery, and the self-test. Given the same
`DEFAULT_ATTEMPTS` the trimmed copy reproduced `routing.py`'s schedules
**exactly** (419 rounds / 301 sections and 455 / 292 under the pre-v4 rules), so
the removal is verified lossless; the shipped 2-config portfolio then traded 2
rounds on the evolved seed for 5 fewer on the folded one, 5 fewer rail sections,
and a third of the build time (~4 s per plan). *That equivalence held up to v6
only* — the v6.1 repair below moved the inlined copies off `routing.py`'s graph
on purpose.

**v6.1: the router now walks the chip's FULL edge set.** Until v6.1 SECTION 2
used the five families it had before the v4 chip correction and paid 5 primitive
steps for a one-row `S→S` hop the evaluator's graph (and its floor) charges 3 —
it was planning on a strictly harsher subgraph than it was priced on. `is_edge`
and `neighbors` gained `S(r,c+1)–U(r,c)`, `S(r,c+1)–D(r,c)` and `U(r,c)–D(r,c)`
in both directions, and `site_dist` — which is *both* the A* heuristic and the
per-ion journey bound, so it must never over-estimate — was re-derived from
scratch on the widened graph. The derivation is the clique picture: every well
belongs to exactly one junction clique, the chip is a rectangular grid of those
cliques joined by one edge each, so a route pays `2·(|dr|+|dc|) − 1` plus a
0-or-1 correction at each end depending on which clique member it starts and
finishes on. It is **exact**, not merely admissible, and was checked against
brute-force BFS over the seeds' own `neighbors()` for every ordered pair of
sites (all four kinds, both boundary columns) on grids from 1×4 to 8×3.
Measured effect, with no layout constant touched: 375 → 300 and 358 → 310
rounds. `routing.py` is frozen and was deliberately **not** updated, so it is
now a pre-repair baseline rather than a mirror of the seeds.

## Head-to-head protocol

- Score 0 = anchor-seed parity. **Paper parity = +2.010** (their 424 rounds at
  ~288 rail sections), and since the v4 corrections that is a single
  like-for-like number: same chip graph, same cycle-boundary convention, no tax
  to add either way. Quote `transport_rounds`, `zones` and `rounds_over_floor`
  together — the raw round count alone still says nothing about footprint.
- Certification (`certify.py`, fresh process, evolution never sees it): real
  BP-OSD LER at p ∈ {1e-3, 2e-3, 3e-3}, ≥100 errors/point where affordable, fit
  the paper's ansatz `p^5·exp(αp²+βp+ζ)`, extrapolate to p = 1e-4, table against
  the published Q70 reference row (9.72e-7/SEC @ 1e-3 → 7.27e-11 @ 1e-4). The
  in-loop combined_score must REPRODUCE in the fresh process — any mismatch
  means the candidate tampered with the in-process evaluator (see Anti-gaming).
- Transfer chapter: rerun elite plan *generators* on other three-ring codes
  (e.g. the gross code [[144,12,12]]: w = 6 → a 6-round walking-cat-style
  schedule, expressible in this format; needs a wider grid than the 24×96 cap,
  so the transfer harness relaxes grid limits). Score stays Q70-only; the
  evaluator's text_feedback nudges candidates toward `(l, m, schedule)`-
  parametric strategies so elites stay transfer-ready.

## Anti-gaming (adversarial review log)

- **Closed:** free teleport via 0-POC merge/split chains (required-pair +
  once-per-interval rules; ms rounds also carry transport noise now); movable
  beacons/reservoir as free traffic (static rule); stateful-`__iter__`/list-
  subclass divergence between validated and simulated plans (JSON sanitize);
  malformed-plan crash paths escaping the sentinel (typed catches + shape checks
  before coercion; 10-probe battery in `selfcheck.py`); gate-round overrun
  (IndexError → sentinel); in-process monkeypatch of scoring machinery
  (`sys.modules['__main__']` rebinding — scoring now runs through a pristine
  re-import of evaluate.py from disk; the demonstrated exploit now lands on the
  sentinel).
- **Closed at v4, when the cycle-boundary rule was relaxed** (4 further probes,
  all executed in `selfcheck.py`, all reaching the sentinel on the *named*
  rule): a plan leaving a **DATA** ion displaced (→ the non-ancilla exact-site
  rule); an ancilla parked on a site **outside** its species' layout set (→ the
  per-species set rule); an **X↔Z end-position swap**, which is constructed to
  preserve the *combined* ancilla site set exactly and is caught only because X
  and Z are compared **separately**; and two ancillas ending on **one** site,
  which the move-collision rule refuses before the boundary check is even
  reached — that occupancy invariant is what makes the multiset comparison
  impossible to fool with duplicates. The relaxation grants a candidate exactly
  one thing: it may choose *which* ancilla of a species ends on *which* of that
  species' own sites. That is the paper's own software relabel, and the circuit
  cannot see it (position-blind builder, detectors keyed on the check index,
  every ancilla reset at prep and measured before the boundary).
- **Residual, documented:** a maximally determined in-process attacker can still
  subvert any in-process defense (this repo's eval architecture runs candidate
  code in the evaluator process); the backstop is certification — every elite is
  re-scored in a fresh process and archives keep full code, so tampering is
  non-reproducible and visible. Sanctioned merge→gate→split flow still grants a
  mobile ion ≤2 noise-charged-but-POC-free edges per interval (bounded by
  geometry; paper-faithful). The optical-row rule prices no readout contention
  (one optical zone per section is the only constraint) — do not read results as
  optimizing measurement routing.

## Verification log (task setup)

- `make_code_assets.py`: CSS commutation; k = 6 (rank 32/32); check weight 7;
  6 symplectic logical pairs (Lx·Lzᵀ = I, commute with stabilizers); randomized
  distance probe finds weight-9 logicals and nothing below (paper: d = 9 exact);
  schedule = exact Table X permutation; alignment closure — the 7 scheduled
  rounds reproduce every check's Tanner support exactly.
- `selfcheck.py`: all three seeds legal under the full validator; cycle boundary
  satisfied; noiseless-deterministic on both observable circuits (validates
  detector/record arithmetic end-to-end); anchor seed scores +0.0000; **14**
  invalid-plan probes → sentinel with named rules (10 malformed/illegal + the 4
  v4 cycle-boundary probes above); full scoring path 0.7–0.9 s.
- v4 chip-model correction: BFS over the graph built *directly from*
  `_is_edge` confirms 2 wells per horizontal section, 2 per vertical section and
  a K4 clique of wells around each junction; `d(S,S) = 2·dr + max(2·dc,1)` exact
  on every interior pair for `dr,dc ∈ 0..6` in all four sign combinations;
  `_dist_lb` a valid lower bound on all 41 472 ordered S/J/U/D pairs tested and
  **exact** on the 1104 interior S–S pairs (the only kind `compile_plan` ever
  feeds it — verified by replay: every gate-time snapshot of all three seeds
  contains S sites only).
- v6.1 seed-router repair: the seeds' own `is_edge` agrees with the evaluator's
  `_is_edge` on all 10 000 ordered site pairs of a 5×5 window; their
  `neighbors()` equals the evaluator's adjacency restricted to the grid, site by
  site; and `site_dist` is **admissible and exact** against brute-force BFS over
  that same `neighbors()` on every ordered pair of sites — all 16 site-kind
  combinations — for grids 6×6, 5×7, 4×4, 3×9, 2×2, 8×3, 1×4 and 7×5 (85 424
  pairs). On the seeds' own 15×28 grid `site_dist ≥ _dist_lb` for all 176 400
  S–S pairs, with equality on 176 190 of them (the 210 exceptions all touch the
  dead-end column 0, where `_dist_lb` stays a strict lower bound by design).
- Determinism: each seed built twice per process, byte-identical JSON.
- 4-agent adversarial review (validator, stim/decoder, exploit hunt, seed
  robustness) with live probe execution; all critical/major findings fixed (see
  Anti-gaming) or documented. Reviewer verified detector lookback arithmetic
  independently (all 700 detectors), CX/CZ ↔ Hx/Hz conventions, and seed
  wrap/conveyor collision-safety margins for all Q70 schedule deltas.
- Monkeypatch exploit replication post-hardening → sentinel (−2.0).
- Official smoke test `python evaluate.py --program_path initial.py
  --results_dir ...` → combined_score 0.0, correct = true, ~1 s.

## THE OBJECTIVE, IN ONE LINE

**The shipped seeds beat the published design on speed; the largest remaining
win is GEOMETRY, with packing slack a close second.** `initial_folded.py` runs
the SEC in **300 transport rounds** vs IonQ's published 424, at 283 rail
sections vs their ~288, on the same chip graph and the same cycle-boundary
convention — but at **1.30x its own 230-round distance floor** (the pitch-4
seed: 310 rounds, 1.37x over 226). Since v6.1 the router walks exactly the graph
the floor is computed on, so that 1.30–1.37x is pure packing loss — stalls,
shove-asides, replans, phase boundaries — worth **+0.45 / +0.54** if collapsed
entirely. Past that the floor itself is a property of the embedding: a
CRT/sheared-torus geometry (l=7, m=5 coprime ⇒ the ring torus is Z₃₅, so every
realignment becomes ONE 1-D rotation instead of two per-axis passes) has an
analytic rotation cost near **194 rounds**, i.e. ≈+3.42. Both levers are inside
the evolve block.

## Run playbook (binding lessons from runs q70ring_v1 $30 and q70ring_v2 $50)

The next orchestrator should treat this section as the default run recipe —
each item traces to a measured failure or win of run v1 (postmortem archived in
`orchestrator/run_archive/q70ring_v1__*/RUN_SUMMARY.md`; do NOT read that
archive mid-run — everything actionable is already folded in here and into the
seeds/score).

1. **Config**: copy `configs/orchestrator_run.default.json`;
   `task.eval_program_path` = this task's `evaluate.py`;
   **`task.init_program_paths` = [`initial.py`, `initial_folded.py`,
   `initial_evolved.py`]** (NOT the single-seed key — v1 ran single-seed and
   burned 5 of 7 windows re-deriving the folded seed); `task.language: "python"`;
   **`eval_time: 00:30:00`** (see "Builders may search" below);
   `db_config.num_islands: 4-6`;
   **`db_config.migration_rate: 0.05`** with a finite `migration_interval` — v1
   ran migration 0 and two islands starved. But **exempt a newly spawned island
   from migration for a few generations**: in v2 a grounded CRT family got
   immigrants within one window, whose scores then masked the family's real
   state until it died with one child.
1b. **Patch mix — changed by the router inlining.** The two strong seeds are now
   ~49 KB / ~1160 lines each (the router lives in the evolve block). A `full`
   rewrite therefore means emitting 1100+ lines, which is slow, expensive and
   error-prone. Set `evo.patch_type_probs` diff-heavy — e.g.
   `["diff","full","cross","fix"]` at `[0.72, 0.13, 0.10, 0.05]` (v2 used 0.55
   diff / 0.30 full on 300-line seeds). The three SECTION banners exist so a
   diff can rewrite one section without touching the others; say so in
   `task_sys_msg`.

2. **Model arms**: v1's bandit worked with
   `["azure-gpt-5.4-mini@low", "azure-gpt-5.3-codex@medium", "azure-gpt-5.5@medium"]`
   after the warmup demoted mini from @medium (23.7 min/$0.37 for zero gain at
   @medium; at @low it delivered a +0.100 candidate for $0.067). Start there.
3. **Discovery round: EARLY, and budget it honestly.** v1 fired its (excellent)
   DR at 71% of budget — three sound grounded families got ~2 windows to live.
   If stagnation appears, spend DR by ~window 2-3, and reserve **1.5× the
   estimate** (the real o3-DR call billed $7.51 vs the $5.0 default estimate).
4. **Guidance scoping**: v1's routing-discipline `extra_guidance` halved
   self-check failures (0.40 → 0.22) but measurably made grounded
   implementations timid (all three fell back to conservative mechanics).
   Scope legality reminders to fix/repair flows; keep fresh-mutation prompts
   bold.
5. **`task_sys_msg` authoring** (both runs' texts produced ZERO
   contract-misunderstanding invalids in warmup — reuse their shape): goal =
   drive `rounds_over_floor` toward 1.0 for the frozen Q70 SEC; hard constraints
   = quote the validator highlights above (all 9 rules, **the EIGHT edge
   families**, odd-rows-optical, and the **two-part cycle boundary**: exact
   sites for data/beacon/reservoir, per-species set for X and Z ancillas);
   building blocks = **geometry first now that v6.1 fixed the router's chip
   map** (the CRT/sheared-torus embedding; a cell pitch that varies with the
   column; interleaving the two blocks in one row band; min-cost matching for
   the per-round ancilla→site assignment), then packing (fuse a gap's
   `(delta_i, delta_j)` into ONE stall-tolerant pass instead of sequential
   per-axis passes; interleave the X and Z species into shared rounds; exploit
   that a closed-loop rotation advances every ion in the same round; insert
   per-ion stalls rather than aborting a pass; overlap SECTION 3's phases
   harder); point at the slack map and the parallelism line as the two things to
   move; runtime = see "Builders may search" below.
   **Do NOT quote "beat 424" as the target** — both shipped seeds already do,
   and both are already below the paper's footprint. Name each plan's own floor
   (226 / 230) and the CRT geometry hypothesis as the target.

5b. **BUILDERS MAY SEARCH (changed after run v2).** Earlier runs told candidates
   to build the plan fast and "avoid brute-force search", and evaluation took
   ~1–2 s — so every candidate was a one-shot constructive heuristic. The budget
   is now **30 minutes per evaluation** (the routed seeds build in ~4 s since
   the v6.1 repair — shorter routes, fewer A* expansions — so there is ~400x
   headroom). Say so explicitly in `task_sys_msg`: a builder MAY
   run a real optimizer — search over layout parameters, anneal the ion→site
   assignment, try several geometries and keep the cheapest, tune the router's
   knobs per gap, or re-plan a gap several ways and pick the best. It must stay
   deterministic (no wall-clock or RNG-seed dependence on run order) and must
   finish inside the budget. This is the single biggest widening of the design
   space available, and it is unexplored.
6. **Two known unexploited assets** (the third, the junction-crossing edges,
   was **spent at v6.1** — `is_edge`/`neighbors`/`site_dist` now walk the full
   eight-family graph in both seeds, worth 375→300 and 358→310 rounds; do not
   re-suggest it). (a) The **CRT sheared-torus** layout, from v2's archive —
   since l=7 and m=5 are coprime the ring torus is Z_35, so every realignment
   collapses to ONE 1-D rotation instead of two per-axis passes (its grounded
   implementation cut gap r1's floor 24→12 but routed at 35x floor;
   analytically the seven rotations total ~194 rounds if done in lockstep).
   (b) The **farthest-first single-phase router** that produced v2's best
   lineage. **(a) and (b) were never combined** — and both now sit on a router
   that finally prices vertical travel correctly, so that combination is the
   obvious move.
7. **Budget**: v1 $30 → 7 windows/68 programs; v2 $50 → 10 windows/107
   programs, but 6 of its 10 windows produced no new best ($21 of $49). Expect
   ~$3–5 per window. The certification tier (`certify.py`) runs on elites
   between windows or at run end — in-loop scores must reproduce there.
8. **Cadence**: v2 hit stagnation-forced control returns every window from w5
   on, because `stagnation_rel_frac: 0.05` scales the bar with a score that is
   a sum of log2 ratios (at best=1.65 it demanded a ~6% single-window jump).
   Lower it to ~0.02 or raise `consecutive_required` to 3–4; keep
   `early_phase_windows: 5`.
9. **Bandit**: set `evo.llm_dynamic_selection_kwargs.exploration_coef: 0.3`
   (default 1.0 is ~10x the arm-reward spread on this task and degenerates into
   "pick the least-pulled arm" — v2 gave 80% weight to an arm that finished
   5/20 correct while starving the 29/32 arm). `cost_aware_coef: 0.10`.

```bash
python tasks/q70_ring_shuttle/selfcheck.py
```

```bash
python tasks/q70_ring_shuttle/certify.py --program_path tasks/q70_ring_shuttle/initial_folded.py --p 2e-3 --target_errors 50 --max_shots 20000
```

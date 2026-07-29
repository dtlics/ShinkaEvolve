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

See [DESIGN.md](DESIGN.md) for the design rationale and background primer.

## Contract files

| File | Role |
|---|---|
| `initial.py` | Seed A (EVOLVE-BLOCK): the *unfolded* realization of the Fig.-60 strategy — block swaps for the long ring, conveyor+rail-wrap medium shifts, Fig.-61 embedded short shifts, vertical data hops for gating, in-place prep/measure on optical rows. Score anchor (0.0). Parametric in `(l, m, schedule)`; self-verifies while building. |
| `initial_folded.py` | Seed B (EVOLVE-BLOCK): the *folded* 2D embedding — cells of 3 rows (data-L / ancillas / data-R), family change = ±2-column side flip (no block swaps), per-column vertical conveyors with lane-column wraps, Fig.-61 embedded shifts per row. **446 rounds / +2.1649** at 287 rail sections. |
| `initial_evolved.py` | Seed C (EVOLVE-BLOCK): run `q70ring_v2`'s best evolved program with its cell pitch repaired 3→4 (evolution had compressed it to win the then-broken zone term, which aliased the X/Z wrap lanes and forced an X-then-Z sequential fallback). 676 → 566 → **421 rounds / +2.2028**. |
| `evaluate.py` | Immutable oracle: exact plan compiler/validator, POC + noise-exposure accounting, stim noiseless-determinism check, deterministic seed-anchored score. Also hosts the certification-only circuit builder + BP-OSD sampler. |
| `routing.py` | **FROZEN REFERENCE ONLY — no seed imports it.** It *was* the shared, non-evolved round packer; the router now lives INSIDE each seed's EVOLVE-BLOCK (SECTION 2), so a mutation can improve the packing algorithm as well as the geometry. Kept on disk unchanged as a regression baseline: given the same `DEFAULT_ATTEMPTS`, the inlined copy reproduces its schedules exactly (419/301 and 455/292). `python routing.py` still runs its self-test. |
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

Grid semantics: `S(r,c)` rail sections joined through junction nodes `J(r,c)`
(between `S(r,c)` and `S(r,c+1)`) with leg stubs `U/D(r,c)`; vertical passage
`D(r,c)–U(r+1,c)`. Each listed edge is one primitive transport step. **Odd rows are
optical** (prep/measure allowed there only). One ion per site; a move phase is one
parallel transport round; merges require one-edge adjacency to a host on an S site.

Validator highlights (each violation → archived sentinel score −2.0 with the exact
rule named): plans are JSON-sanitized on entry (plain data only); edge-legality +
collision freedom + no head-on swaps per round; **beacons and reservoir ions are
static** (may not move); gate rounds in schedule order with the merged-pair set
exactly equal to the frozen required pairs; merges only for pairs the *next* gate
round requires, ≤1 merge + 1 split per ion per inter-gate interval (merge/split
cost 0 POC per the paper, so this closes free-teleport chains; they DO carry
transport-round noise); prep before gates / measure after all gates, both on
optical rows, each ancilla exactly once; beacons in the same row as their partner
data qubit; **cyclicity** — every ion must end the SEC on its layout site so
cycles tile.

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

## Score (v3 — deterministic; zone metric corrected after run q70ring_v2)

```
exposure  = 490 (gates) + 14 (prep/meas) + idle_slots/100
          + 140·(transport_rounds + merge_rounds)/2000    # fault events per SEC, p factored out
var_exp   = exposure − 504                                # the part a plan can move
T_core    = transport_rounds/20 + 7 + prep_phases + measure_phases
zones     = distinct RAIL SECTIONS (S sites) the plan ever occupies

score = 1.0 * log2(73.22 / var_exp)      # plan-dependent noise exposure
      + 0.5 * log2(59.60 / T_core)       # core SEC time (no frozen 7.05 overhead)
      + 1.0 * log2(428   / zones)        # trap footprint, in the paper's own unit
```

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
the noise model or the score.

> **PROVISIONAL v3, revisit after the next run** (orchestrator note — do not
> surface mutable-rules talk to the mutation LLM): weights are 1.0 / 0.5 / 1.0.
> The zone term is now bounded (220 sections is the hard floor — 220 ions must
> each rest on their own S site — so it can yield at most log2(428/220) = +0.96
> and cannot be farmed by serializing). Watch next: whether `rounds_over_floor`
> actually falls, and whether any plan games `low_occ_rounds` by padding
> high-occupancy no-op rounds. Changing weights or anchors mid-run invalidates
> archive ordering; do it between runs only.

## Seed calibration and the honest paper bar (measured under score v3)

| plan | rounds | rail sections | T_SEC | rounds/floor | score |
|---|---|---|---|---|---|
| `initial.py` unfolded (anchor) | 1012 | 428 | 66.65 | 1.36x | **0.000** |
| `initial_folded.py` (inlined router) | 446 | 287 | 38.35 | 1.34x | **+2.165** |
| **`initial_evolved.py` (inlined router, best)** | **421** | **301** | **37.10** | **1.28x** | **+2.203** |
| paper Q70 + our cyclicity tax | 502 | ~288 | — | — | +1.939 |
| paper Q70 as published (Table XXVI) | 424 | ~288 | 34.2 micro | — | +2.253 |
| evolved layout floor-perfect | 329 | 301 | — | 1.00x | +2.65 |
| CRT geometry, analytic rotation cost | ~194+gating | — | — | — | — |

Pre-router history for reference: the same two layouts cost 700 and 566 rounds
when each seed emitted its own movement rounds (+1.296 / +1.711), and run
q70ring_v2's best evolved plan was 676 (+1.651). With the shared `routing.py`
they were 455 and 419; the seeds now carry their own trimmed copy of that
router INSIDE the evolve block (see "The router is evolvable now" below).

Every candidate's public metrics carry **`gain_over_seed` = score − 2.2028**, so
a run's own contribution is always separable from what it was handed. Report it
alongside the absolute score in any write-up.

**Read the bar as +1.94, not +2.25.** Two documented asymmetries, both audited
against the paper (Sec. XIX–XX, Tables XI/XXVI):

- *The unit is identical.* Table XXVI's 424 counts parallel rounds exactly as we
  do — their own arithmetic `424/20 + 2 + 8 + 3 = 34.2 POC` (p. 85) and the
  sibling row `Merge/split 16 = 2 x 8 gate layers` both confirm it, and their
  horizontal cost (2 primitive steps per column) matches our `S→J→S` exactly.
- *We are stricter on cyclicity.* Our rule returns every ancilla to its layout
  site so one ancilla batch tiles; the paper instead pipelines a **second**
  ancilla batch, which "would remove all contribution of ancilla measurement
  and reset to the logical clock cycle" (p. 81). That wrap-back is reported per
  plan as `wrap_rounds` (evolved seed: 78 of 566). Adding it to the paper's 424
  gives the like-for-like **502 rounds / +1.939**. We keep the strict rule
  because our 220-qubit budget matches their Table XI count, which does *not*
  include a second batch — but every comparison must state which convention it
  uses.
- *We may also be stricter vertically*: we charge 5 rounds per one-row `S→S`
  hop; the paper's only numeric vertical statement (p. 85) implies ~3. Untested,
  so not corrected for — it makes our bar, if anything, more conservative.

**What we do and do NOT beat the paper on — state this carefully.**

- *Transport rounds and SEC time: parity, edging ahead.* 421 vs 424
  as-published; 354 vs 424 like-for-like (removing our wrap-back tax). This is
  a **speed** result — it shortens the logical clock cycle, hence algorithm
  wall-clock.
- *Logical error rate: a tie, not a win.* Our exposure is 535.85 against the
  paper's ~536.20 for its own 424-round plan — a **0.1%** difference, i.e. a
  ~0.4% LER change at the measured exponent. Do not claim an LER improvement.
  The reason is structural and worth internalising: a transport round costs
  `p/2000` on each qubit while a two-qubit gate layer costs `p` on 70 pairs, so
  the entire plan-dependent budget is only ~6% of total exposure. Even
  **free** transport would buy just ~21% LER versus the paper. Fidelity is
  dominated by the frozen circuit; what a plan actually buys is time and area.

The open problem has moved: routing slack is down to 1.28x floor, so the next
gains must come from a layout whose *floor* is lower (see the CRT row) — or
from a better packer, which is now also inside the evolve block.

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
| 3 ASSEMBLY | prep, the 7 merge/gate/split rounds, measure, wrap-back, phase emission | the gap>0 "duck out and come back" overlap |

What the inlined copy drops from `routing.py` (~39% of it): the 6-config
attempt portfolio (now 2 configs — measured better, see below), `route()`,
`check_rounds`, `ShuttleRouter`, `to_timeline`, `apply_rounds`' occupancy
bookkeeping, `avoid`/`soft_cost`, `_infer_grid`, the `field_cache` (dead on the
path-planning code path the seeds use), the whole field-plan engine mode with
its `_descend`/`override` machinery, and the self-test. Given the same
`DEFAULT_ATTEMPTS` the trimmed copy reproduces `routing.py`'s schedules
**exactly** (419 rounds / 301 sections and 455 / 292), so the removal is
verified lossless; the shipped 2-config portfolio then trades 2 rounds on the
evolved seed for 5 fewer on the folded one, 5 fewer rail sections, and a third
of the build time (~6 s per plan).

## Head-to-head protocol

- Score 0 = anchor-seed parity. **Paper parity = +1.94** like-for-like (their 424
  rounds + our cyclicity wrap-back, at 288 rail sections); +2.25 if the wrap-back
  is ignored. Quote `transport_rounds`, `wrap_rounds` and `zones` together, and
  say which cyclicity convention the comparison uses — the raw round count alone
  is not comparable.
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
- `selfcheck.py`: seed legal under the full validator; cyclic; noiseless-
  deterministic on both observable circuits (validates detector/record
  arithmetic end-to-end); seed scores +0.0000; 10 invalid-plan probes → sentinel
  with named rules; full scoring path 0.4 s.
- 4-agent adversarial review (validator, stim/decoder, exploit hunt, seed
  robustness) with live probe execution; all critical/major findings fixed (see
  Anti-gaming) or documented. Reviewer verified detector lookback arithmetic
  independently (all 700 detectors), CX/CZ ↔ Hx/Hz conventions, and seed
  wrap/conveyor collision-safety margins for all Q70 schedule deltas.
- Monkeypatch exploit replication post-hardening → sentinel (−2.0).
- Official smoke test `python evaluate.py --program_path initial.py
  --results_dir ...` → combined_score 0.0, correct = true, ~1 s.

## THE OBJECTIVE, IN ONE LINE

**The shipped seed already beats the published design — now push past it by
changing the GEOMETRY.** `initial_evolved.py` runs the SEC in **421 transport
rounds** vs IonQ's 424 (354 vs 424 like-for-like, see below), at 1.28x its own
distance floor. The primary open problem is the *layout*: the floor itself is a
property of the embedding, and a CRT/sheared-torus geometry (l=7, m=5 coprime ⇒
the ring torus is Z₃₅, so every realignment becomes ONE 1-D rotation instead of
two per-axis passes) has an analytic rotation cost near **194 rounds**. Move
the floor, and the router will follow it down. Secondary, and now in scope for
the first time: the ~92 rounds of slack ABOVE that floor, because the round
packer lives inside the evolve block too (SECTION 2) rather than in a shared
frozen module.

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
   = quote the validator highlights above (all 9 rules, the 5 edge types,
   odd-rows-optical, cyclicity); building blocks = **router architecture first**
   (fuse a gap's `(delta_i, delta_j)` into ONE stall-tolerant pass instead of
   sequential per-axis passes; interleave the X and Z species into shared
   rounds; exploit that a closed-loop rotation advances every ion in the same
   round; insert per-ion stalls rather than aborting a pass), then layout;
   point at the slack map and the parallelism line as the two things to move;
   runtime = see "Builders may search" below.
   **Do NOT quote "beat 424" as the target** — the shipped seed already does.
   Name the plan's own floor, and the CRT geometry hypothesis, as the target.

5b. **BUILDERS MAY SEARCH (changed after run v2).** Earlier runs told candidates
   to build the plan fast and "avoid brute-force search", and evaluation took
   ~1–2 s — so every candidate was a one-shot constructive heuristic. The budget
   is now **30 minutes per evaluation** (the routed seeds build in 15–20 s, so
   there are ~100x headroom). Say so explicitly in `task_sys_msg`: a builder MAY
   run a real optimizer — search over layout parameters, anneal the ion→site
   assignment, try several geometries and keep the cheapest, tune the router's
   knobs per gap, or re-plan a gap several ways and pick the best. It must stay
   deterministic (no wall-clock or RNG-seed dependence on run order) and must
   finish inside the budget. This is the single biggest widening of the design
   space available, and it is unexplored.
6. **Two known unexploited assets**, both from v2's archive: (a) the **CRT
   sheared-torus** layout — since l=7 and m=5 are coprime the ring torus is
   Z_35, so every realignment collapses to ONE 1-D rotation instead of two
   per-axis passes (its grounded implementation cut gap r1's floor 24→12 but
   routed at 35x floor; analytically the seven rotations total ~194 rounds if
   done in lockstep, which would beat the paper outright); (b) the
   **farthest-first single-phase router** that produced v2's best lineage. **The
   two were never combined** — that combination is the obvious first move.
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

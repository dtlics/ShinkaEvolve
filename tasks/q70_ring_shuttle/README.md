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
| `initial_folded.py` | Seed B (EVOLVE-BLOCK): the *folded* 2D embedding — the layout family run `q70ring_v1`'s discovery round found, given the parallel router that candidate lacked. Cells of 3 rows (data-L / ancillas / data-R), family change = ±2-column side flip (no block swaps), per-column vertical conveyors with lane-column wraps, Fig.-61 embedded shifts per row. Boots at **+1.32**. Run BOTH seeds via `task.init_program_paths`. |
| `evaluate.py` | Immutable oracle: exact plan compiler/validator, POC + noise-exposure accounting, stim noiseless-determinism check, deterministic seed-anchored score. Also hosts the certification-only circuit builder + BP-OSD sampler. |
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

## Score (v2 — deterministic, plan-dependent parts only; re-shaped after run q70ring_v1)

```
exposure  = 490 (gates) + 14 (prep/meas) + idle_slots/100
          + 140·(transport_rounds + merge_rounds)/2000    # fault events per SEC, p factored out
var_exp   = exposure − 504                                # the part a plan can move
T_core    = transport_rounds/20 + 7 + prep_phases + measure_phases

score = 1.0 * log2(73.22 / var_exp)      # plan-dependent noise exposure
      + 0.5 * log2(59.60 / T_core)       # core SEC time (no frozen 7.05 overhead)
      + 1.0 * log2(1612  / zones)        # trap footprint
```

Anchors = the unfolded seed (scores exactly 0). The honest operating-point
reliability readout — `ler_shift_log10 = 5·log10(total exposure ratio)`, the
paper's extrapolation-ansatz LER shift — is a **public metric and the
certification claim, deliberately not the fitness**: run `q70ring_v1` measured
the v1 headline term (5·log10 of *total* exposure) 85%-saturated by the frozen
490-point gate cost (it contributed +0.045 of the best +0.1445 while the time
term gave +0.098), and the v1 zone weight (0.25) let a genuine −57% footprint
discovery score −4.9, killing the stepping stone toward the paper's folded
design. v2 scores only what a plan can move, and prices footprint at parity.

Why deterministic (unchanged from v1, validated by the run): the fault structure
is frozen, BP-OSD costs ~0.1–4 s/shot (certification-only), eval ≈ 1–2 s with
zero score noise. Score ladder measured under v2: **unfolded seed 0 → folded
seed +1.32 → paper-grade (~424 rounds, ~288 zones) ≈ +4.2**. Beyond +4.2 beats
IonQ's hand design.

> **PROVISIONAL v2, revisit after the next run** (orchestrator note — do not
> surface mutable-rules talk to the mutation LLM): weights (1.0 / 0.5 / 1.0)
> and certification budgets are second-pass choices informed by one run. Watch
> next: zone-term farming via layouts that shrink footprint by serializing
> transport (the exposure+time terms must stay strong enough to veto it), and
> whether the folded island simply dominates (fine) or cross-breeds with
> unfolded routing tricks (better). Changing weights or anchors mid-run
> invalidates archive ordering; do it between runs only.

## Seed calibration (measured under this evaluator, score v2)

| quantity | unfolded seed (anchor, 0.0) | folded seed (+1.32) | best of run v1 | paper Q70 |
|---|---|---|---|---|
| transport rounds / SEC | 1012 | **700** | 842 | ~424 (Table XXVI) |
| T_SEC | 66.65 POC | **51.05** | 58.15 | 27.70 abstract / ~34.2 micro |
| plan-dependent exposure | 73.22 | **51.38** | 61.32 | ~32 at 424 rounds |
| zones | 1612 | **1074** | 1608 | ~288 sections (Fig. 62) |

The unfolded seed keeps the score anchor stable across runs; the folded seed
already beats everything run `q70ring_v1` evolved in 7 windows, and its per-gap
floors (printed in every text_feedback as `used/floor`) show the remaining
router slack — e.g. its gap r2 uses 102 rounds against a 46-round distance
floor. Folding further, tightening wraps, overlapping realignment with gating,
and measurement batching all remain open. The paper's ~424-round embedding
(score ≈ +4.2) is the head-to-head bar.

## Head-to-head protocol

- Score 0 = seed parity (this evaluator, this noise model); public `sec_poc` /
  `transport_rounds` give paper parity directly (27.70/34.2 POC, 424 rounds).
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

## Run playbook (binding lessons from run q70ring_v1, $30, 2026-07)

The next orchestrator should treat this section as the default run recipe —
each item traces to a measured failure or win of run v1 (postmortem archived in
`orchestrator/run_archive/q70ring_v1__*/RUN_SUMMARY.md`; do NOT read that
archive mid-run — everything actionable is already folded in here and into the
seeds/score).

1. **Config**: copy `configs/orchestrator_run.default.json`;
   `task.eval_program_path` = this task's `evaluate.py`;
   **`task.init_program_paths` = [`initial.py`, `initial_folded.py`]** (NOT the
   single-seed key — v1 ran single-seed and needed 5 of 7 windows to re-derive
   what the folded seed now ships); `task.language: "python"`;
   `eval_time: 00:05:00`; `db_config.num_islands: 4-6` (≥ 2, seeds fill
   round-robin); **`db_config.migration_rate: 0.05`** with a finite
   `migration_interval` — v1 ran migration 0 and islands 0/3 starved all run
   (+0.039/+0.064 while leaders hit +0.14).
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
5. **`task_sys_msg` authoring** (the v1 text produced ZERO
   contract-misunderstanding invalids all warmup — reuse its shape): goal =
   jointly minimize plan-dependent exposure, core SEC time, and zones for the
   frozen Q70 SEC; hard constraints = quote the validator highlights above
   (all 9 rules, the 5 edge types, odd-rows-optical, cyclicity); building
   blocks = layout/folding choice, per-ring shift realization (conveyor +
   rail-wrap vs embedded Fig.-61 vs lane-column vertical conveyors vs side
   flips), gating approach paths, measurement batching; point at the per-gap
   `used/floor` feedback as the slack map; runtime caution = deterministic
   pure-Python `run_experiment`, comfortably under 30 s.
6. **Budget**: $30 bought 7 windows / 68 programs and recovered ~25% of the
   seed→paper gap from a cold single-seed start. With both seeds and v2
   scoring, $30 is a meaningful run; $50 is a realistic shot at the paper-grade
   +4.2 ladder rung. The certification tier (`certify.py`) runs on elites
   between windows or at run end — in-loop scores must reproduce there.

```bash
python tasks/q70_ring_shuttle/selfcheck.py
```

```bash
python tasks/q70_ring_shuttle/certify.py --program_path tasks/q70_ring_shuttle/initial_folded.py --p 2e-3 --target_errors 50 --max_shots 20000
```

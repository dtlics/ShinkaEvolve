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
| `initial.py` | Seed plan generator (EVOLVE-BLOCK). Paper-faithful but deliberately *unfolded* realization of the Fig.-60 strategy: block swaps for the long ring, conveyor+rail-wrap medium shifts, Fig.-61 embedded short shifts, vertical data hops for gating, in-place prep/measure on optical rows. Written parametrically in `(l, m, schedule)`. Self-verifies with the evaluator's own move rules while building. |
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

## Score (deterministic — no sampling in the loop)

```
exposure = 490 (gates) + 14 (prep/meas) + idle_slots/100
         + 140·(transport_rounds + merge_rounds)/2000     # fault events per SEC, p factored out

score = 5   * log10(SEED_EXPOSURE / exposure)     # reliability at the operating point
      + 0.5 * log2(SEED_T_POC / T_SEC_POC)        # SEC wall time
      + 0.25* log2(SEED_ZONES / zones)            # trap footprint
```

Why deterministic: with the schedule frozen, the circuit's fault *structure* is
identical for every candidate — only noise strengths move — so at the p = 1e-4
operating point the paper's own extrapolation ansatz gives
`log10(LER) ≈ ⌈d_circ/2⌉·log10(exposure) + const`, i.e. the exposure term IS the
LER term to leading order. Measured facts forced this (adversarial review, live
timing): BP-OSD costs ~0.1–4 s/shot on this 700-detector/31k-mechanism DEM, and at
any p where LER is sampleable in minutes the circuit sits near threshold where the
frozen gate noise drowns the plan signal 7:1. Real LER lives in `certify.py`,
where hours are acceptable. Consequences: eval ≈ 1–2 s, zero score noise (no
winner's curse), and `eval_time: 00:05:00` is ample in the run config.

> **PROVISIONAL, revisit after the first evolution run** (note for the
> orchestrator — do not surface mutable-rules talk to the mutation LLM): the
> weights (5 / 0.5 / 0.25) and the certification budgets (p list, target errors,
> shot caps, osd_order) are first-pass choices. Watch for: the zone term
> dominating via degenerate compact layouts; time-term saturation; exposure and
> time being near-collinear (they differ only through idle slots and the /20 vs
> /2000 denominators — if candidates stop differentiating, re-weigh). Changing
> weights or anchors mid-run invalidates archive ordering; do it between runs.

## Seed calibration (measured under this evaluator; seed scores 0.0 exactly)

| quantity | seed value | paper Q70 reference |
|---|---|---|
| transport rounds / SEC | 1012 | ~424 (Table XXVI, folded Fig.-62 embedding) |
| T_SEC | 66.65 POC | 27.70 POC abstract (Table XI) / ~34.2 micro-counted |
| noise exposure | 577.22 (504 frozen + 73.22 plan-dependent) | ~534 at 424 rounds |
| zones | 1612 sites | ~288 sections (Fig. 62, ~12×24) |

The seed is *intentionally* the simple unfolded realization: the paper's folded
embedding (~424 rounds) is discoverable by evolution — folding, smarter wrap
routing, overlapping realignment with gating, and measurement batching are all
inside the design space. Beating ~34 POC = beating the paper's hand design; the
score reachable-box analysis (review) caps the time term at ~+0.75 and zone term
at ~+0.33, with the exposure term worth ~+0.29 for a paper-grade plan — so a
score near +1.0 ≈ paper-level, and beyond that is novel territory.

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

## Running

```bash
python tasks/q70_ring_shuttle/selfcheck.py
```

```bash
python tasks/q70_ring_shuttle/certify.py --program_path tasks/q70_ring_shuttle/initial.py --p 2e-3 --target_errors 50 --max_shots 20000
```

Evolution: copy `configs/orchestrator_run.default.json`, point
`task.eval_program_path` / `task.init_program_path` at this task, set
`task.language: "python"`, `eval_time: 00:05:00`. Orchestrator notes for
`task_sys_msg` authoring: goal = minimize noise exposure, SEC time, and trap
footprint jointly for the frozen Q70 SEC; hard constraints = the plan-legality
rules above (quote the validator highlights); building blocks = layout choice,
per-ring shift realization (conveyor/rail-wrap vs embedded vs block swap vs
folded layouts), gating approach paths, measurement batching; runtime caution =
run_experiment must stay deterministic, pure-Python, and comfortably under 30 s.

# tasks/

Each subdirectory here is a self-contained ShinkaEvolve task. Use the `shinka-setup` or `shinka-convert` skills to scaffold new ones — don't write the boilerplate by hand.

## Expected layout per task

```
tasks/<task_name>/
├── initial.<ext>          # seed solution; mark the optimizable region with EVOLVE-BLOCK markers
│                          #   (multi-seed tasks: several initial_<k>.<ext> files, identical
│                          #   markers + I/O contract, listed in task.init_program_paths —
│                          #   each seed roots its own island at boot; boot-only, opt-in)
├── evaluate.py            # scoring harness; returns the metrics dict for shinka
├── results/               # gitignored — per-task run artifacts (programs.sqlite, journal/, logs)
└── (run config)           # an orchestrator_run.json lives next to the run, not checked in
```

Only `initial.<ext>` + `evaluate.py` are the task contract. There is no per-task
`run_evo.py` / `shinka.yaml` anymore — the run is configured by an
`orchestrator_run.json` (copy the `configs/orchestrator_run.default.json` starter).

## Running a task (you are the orchestrator)

From this repo root, with the `shinka` conda env activated, point a run config at
the task's `evaluate.py` + `initial.<ext>` and drive windows — see
[../.claude/skills/shinka-orchestrator/SKILL.md](../.claude/skills/shinka-orchestrator/SKILL.md):

```bash
conda activate shinka
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

The inner loop runs windows autonomously and returns to you on stagnation or a
window cap; the budget is hard-capped in code via `budget_usd`. Per-task
`results/` lives inside the task directory (gitignored).

## Existing tasks

* [cnot_grid_synth/](cnot_grid_synth/) — CNOT-equivalent linear-function synthesis on a 2D L×L grid (Clifford circuits, n-weighted average-case CX-depth-per-qubit saved vs a snake-KMS baseline). Active user task.
* [bb_syndrome_sched/](bb_syndrome_sched/) — syndrome-extraction circuit scheduling for a BB (bivariate-bicycle) code (AlphaSyndrome-style), scored by an error-budget evaluator.
* [pbb_code_discovery/](pbb_code_discovery/) — discovery of non-CSS perturbed bivariate-bicycle (PBB) codes (Campaign-5 port of arXiv:2606.02418), scored by trust-adjusted FOM = k·d²/n.
* [gross_code_gauging/](gross_code_gauging/) — end-to-end gauging-gadget design on the [[144,12,12]] gross code (Williamson & Yoder, arXiv:2410.02213): smallest cycle-bearing gadget whose tail-priced measured protocol LER stays within 1.1× of the paper reference (probe-found fault sets priced into the effective LER, not gated); includes a GeneCS (arXiv:2605.21746) reimplementation for apple-to-apple baselines + config reverse-engineering.
* [gross_code_spectral_synth/](gross_code_spectral_synth/) — minimum-edge spectral certification on the same instance (v2): smallest simple graph with λ₂ ≥ 2 (the WY Cheeger ≥ 1 bar — stricter than GeneCS's real λ₂ ≥ 2β gate, honestly framed as context not head-to-head); deterministic sub-second eval; lexicographic score (certification first, then edges; leaf penalty) after the v1 star-loophole postmortem; seeded with the verified certified-E=20 record; open jackpot: certified E ≤ 19.

## Reference example

See [`../examples/circle_packing/`](../examples/circle_packing/) — the small
reference task that drives the orchestrator smoke test (same calling conventions,
smaller scope).

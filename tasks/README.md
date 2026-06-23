# tasks/

Each subdirectory here is a self-contained ShinkaEvolve task. Use the `shinka-setup` or `shinka-convert` skills to scaffold new ones — don't write the boilerplate by hand.

## Expected layout per task

```
tasks/<task_name>/
├── initial.<ext>          # seed solution; mark the optimizable region with EVOLVE-BLOCK markers
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
* [gross_code_gauging/](gross_code_gauging/) — gauging-measurement edge search on the [[144,12,12]] gross code (Williamson & Yoder, arXiv:2410.02213): minimize the ancilla qubits (graph edges) that keep the deformed-code distance at 12.

## Reference example

See [`../examples/circle_packing/`](../examples/circle_packing/) — the small
reference task that drives the orchestrator smoke test (same calling conventions,
smaller scope).

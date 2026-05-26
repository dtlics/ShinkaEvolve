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
`orchestrator_run.json` (the `shinka-setup`/`shinka-convert` skills emit a starter).

## Running a task (you are the orchestrator)

From this repo root, with the `shinka` conda env activated, point a run config at
the task's `evaluate.py` + `initial.<ext>` and drive windows — see
[../orchestrator/SKILL.md](../orchestrator/SKILL.md):

```bash
conda activate shinka
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

The inner loop runs windows autonomously and returns to you on stagnation or a
window cap; the budget is hard-capped in code via `budget_usd`. Per-task
`results/` lives inside the task directory (gitignored).

## Existing tasks

* [cnot_grid_synth/](cnot_grid_synth/) — CNOT-equivalent linear-function synthesis on a 2D L×L grid (Clifford circuits, score = baseline_slope − candidate_slope). Active user task.

## Reference example

See [`../examples/circle_packing/`](../examples/circle_packing/) — the small
reference task that drives the orchestrator smoke test (same calling conventions,
smaller scope).

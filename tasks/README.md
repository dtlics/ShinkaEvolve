# tasks/

Each subdirectory here is a self-contained ShinkaEvolve task. Use the `shinka-setup` or `shinka-convert` skills to scaffold new ones — don't write the boilerplate by hand.

## Expected layout per task

```
tasks/<task_name>/
├── initial.<ext>        # seed solution; mark the optimizable region with EVOLVE-BLOCK markers
├── evaluate.py          # scoring harness; returns metrics dict for shinka
├── run_evo.py           # optional async runner (Python entry point)
├── shinka_config.yaml   # optional per-task overrides (model list, generations, budget)
└── results/             # gitignored — per-task run artifacts (evolution_db.sqlite, logs, plots)
```

## Running a task

From this repo root, with the `shinka` conda env activated:

```bash
conda activate shinka                  # cwd: /Users/dantongli/GIthub/Shinka/shinkaevolve
shinka_run --task-dir tasks/<task_name> --results-dir tasks/<task_name>/results ...
```

Per-task `results/` lives inside the task directory (gitignored). The flag composition for the agentic + research-grounding features is in [../CLAUDE.md](../CLAUDE.md).

## Existing tasks

* [cnot_grid_synth/](cnot_grid_synth/) — CNOT-equivalent linear-function synthesis on a 2D L×L grid (Clifford circuits, score = baseline_slope − candidate_slope). Active user task.

## Upstream references

See [`../examples/`](../examples/) for upstream working references (circle_packing, game_2048, julia_prime_counting, novelty_generator) — same calling conventions, smaller scope.

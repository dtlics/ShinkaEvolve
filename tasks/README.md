# tasks/

Each subdirectory here is a self-contained ShinkaEvolve task. Use the `shinka-setup` or `shinka-convert` skills to scaffold new ones — don't write the boilerplate by hand.

## Expected layout per task

```
tasks/<task_name>/
├── initial.<ext>        # seed solution; mark the optimizable region with EVOLVE-BLOCK markers
├── evaluate.py          # scoring harness; returns metrics dict for shinka
├── run_evo.py           # optional async runner (Python entry point)
└── shinka_config.yaml   # optional per-task overrides (model list, generations, budget)
```

## Running a task

From the repo root, with the `shinka` conda env activated:

```bash
conda activate shinka
shinka_run --task-dir tasks/<task_name> --results-dir results/<task_name> ...
```

See `shinkaevolve/examples/` for working references (circle_packing, game_2048, julia_prime_counting, novelty_generator).

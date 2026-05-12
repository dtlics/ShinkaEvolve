# configs/

Optional workspace-level Hydra config overrides that you want to reuse across tasks. Per-task config belongs next to the task itself in `examples/<task_name>/shinka_config.yaml`.

Reference the bundled configs that ship with shinka:

```
shinka/configs/
├── cluster/        # gcp, local, remote
├── database/       # island_large/medium/small
├── evolution/      # large_budget, medium_budget, small_budget
├── task/           # task-specific overrides (extend with your own)
└── variant/        # composed variants (default, circle_packing_example, ...)
```

To use this directory as a custom config dir: `shinka_launch --config-dir $PWD/configs ...` (run from the repo root).

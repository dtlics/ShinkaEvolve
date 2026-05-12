# configs/

Optional Hydra config overrides for shinka runs that you want to reuse across tasks. Per-task config belongs next to the task itself in `tasks/<task_name>/shinka_config.yaml`.

Reference the bundled configs that ship with shinka:

```
shinkaevolve/shinka/configs/
├── cluster/        # gcp, local, remote
├── database/       # island_large/medium/small
├── evolution/      # large_budget, medium_budget, small_budget
├── task/           # task-specific overrides (extend with your own)
└── variant/        # composed variants (default, circle_packing_example, ...)
```

To use a custom config dir: `shinka_launch --config-dir /Users/dantongli/GIthub/Shinka/configs ...`

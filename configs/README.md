# configs/

Optional Hydra config overrides for shinka runs that you want to reuse across tasks. Per-task config belongs next to the task itself in `tasks/<task_name>/shinka_config.yaml`.

Reference the bundled configs that ship with shinka (one level up under the package):

```
shinka/configs/
├── cluster/        # gcp, local, remote
├── database/       # island_large/medium/small
├── evolution/      # large_budget, medium_budget, small_budget
├── task/           # task-specific overrides (extend with your own)
└── variant/        # composed variants (default, circle_packing_example, ...)
```

This `configs/` directory at the repo root holds user-curated overrides:

* [azure_default.yaml](azure_default.yaml) — Azure model portfolio (the four `azure-gpt-5.*` chat deployments + the `azure-text-embedding-3-small` embedding deployment) with sensible UCB bandit + reasoning_effort settings. Use as a base for per-task `evo.*` overrides.

To use this dir from `shinka_launch`:

```bash
shinka_launch --config-dir /Users/dantongli/GIthub/Shinka/shinkaevolve/configs variant=...
```

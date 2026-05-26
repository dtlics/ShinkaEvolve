# configs/

> **Legacy.** These are Hydra config fragments from the pre-orchestrator launch
> path. The orchestrator does **not** read them — a run is configured by an
> `orchestrator_run.json` (see [orchestrator/SKILL.md](../orchestrator/SKILL.md)).
> `azure_default.yaml` is kept as a handy reference for the Azure model portfolio
> you'd copy into a run config's `evo.*` block.

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

The orchestrator run config (`orchestrator_run.json`) supersedes the old
`shinka_launch` CLI; copy the model list from `azure_default.yaml` into your run
config's `evo.*` block rather than launching from here.

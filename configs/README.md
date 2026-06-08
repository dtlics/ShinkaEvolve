# configs/

> This directory holds the **canonical run-config starter** the orchestrator copies
> to launch a job — `orchestrator_run.default.json` — plus a reference model
> portfolio. A run is configured by an `orchestrator_run.json` (see
> [.claude/skills/shinka-orchestrator/SKILL.md](../.claude/skills/shinka-orchestrator/SKILL.md)). `azure_default.yaml` is a handy
> Azure model-list reference you'd copy into a run config's `evo.*` block — a leftover
> Hydra fragment the orchestrator does **not** read directly.

Reference the bundled configs that ship with shinka (one level up under the package):

```
shinka/configs/
├── cluster/        # gcp, local, remote
├── database/       # island_large/medium/small
├── evolution/      # large_budget, medium_budget, small_budget
├── task/           # task-specific overrides (extend with your own)
└── variant/        # composed variants (default, circle_packing_example, ...)
```

This `configs/` directory at the repo root holds:

* [orchestrator_run.default.json](orchestrator_run.default.json) — the canonical run-config starter the `shinka-setup` / `shinka-convert` skills point you to. Copy it next to your run, set `task.*` / `budget_usd` / a no-spoil `task_sys_msg`, and drive `orchestrator/harness/run_window.py`. It ships `task_sys_msg` as the `__UNSET_AUTHOR_AT_BOOT__` sentinel, so the run refuses to start until you author the goal.
* [azure_default.yaml](azure_default.yaml) — Azure model portfolio (the four `azure-gpt-5.*` chat deployments + the `azure-text-embedding-3-small` embedding deployment) with sensible UCB bandit + reasoning_effort settings. Use as a base for per-task `evo.*` overrides.

The orchestrator run config (`orchestrator_run.json`) supersedes the old
`shinka_launch` CLI; copy the model list from `azure_default.yaml` into your run
config's `evo.*` block rather than launching from here.

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

* [orchestrator_run.default.json](orchestrator_run.default.json) — the canonical run-config starter the `shinka-setup` / `shinka-convert` skills point you to. Copy it next to your run, set `task.*` / `budget_usd` / a `task_sys_msg` (the goal; leak-proofing is the evaluator's job — held-out numbers under `private` metrics), and drive `orchestrator/harness/run_window.py`. It ships `task_sys_msg` as the `__UNSET_AUTHOR_AT_BOOT__` sentinel, so the run refuses to start until you author the goal.
* [azure_default.yaml](azure_default.yaml) — a REFERENCE for the run.json `evo.llm_models` + `evo.embedding_model` + `evo.llm_dynamic_selection_kwargs` (the only `evo.*` keys whose VALUES this `azure_default.yaml` reference supplies — `run_window` itself reads ~20 more, e.g. `window_size`, `patch_types`, `reasoning_effort`, `meta_model`/`meta_reasoning_effort`, `novelty_*`, `stagnation_*`, `reward_mode`; see [orchestrator_run.default.json](orchestrator_run.default.json) for the full `evo` block). Copy these three into your run config; do NOT copy the pruned `meta_llm_models`/`novelty_llm_models`/`llm_kwargs`/`max_api_costs` keys — they were read by nothing (L84).

The orchestrator run config (`orchestrator_run.json`) supersedes the old
`shinka_launch` CLI; copy ONLY the readable keys (`llm_models`, `embedding_model`,
`llm_dynamic_selection_kwargs`) from `azure_default.yaml` into your run config's
`evo.*` block rather than launching from here.

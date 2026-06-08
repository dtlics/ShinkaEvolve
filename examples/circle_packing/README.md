# Circle Packing (reference task)

Compact Shinka task: pack `n=26` circles in a unit square, maximize the sum of
radii. Kept as the small **reference task that drives the orchestrator smoke
test** (`orchestrator/tests/smoke_test.py`) — minimal and fast.

## Ingredients

- `initial.py` — seed solution; exposes `run_packing()` inside an `EVOLVE-BLOCK`.
- `evaluate.py` — validator + scorer; runs `run_packing`, checks the geometry
  constraints, and emits the standard metrics / correctness outputs.

> The old launch artifacts (`run_evo.py`, `shinka_{small,medium,large}.yaml`,
> the `load_results`/`viz_circles` notebooks) were removed in the Azure-only
> prune. Evolution is now driven by the orchestrator, not a per-example runner.

## Single-program evaluation (no evolution)

```bash
conda activate shinka
python examples/circle_packing/evaluate.py \
    --program_path examples/circle_packing/initial.py \
    --results_dir /tmp/circle_eval
```

## Run evolution (as the orchestrator)

Point a run config at this task and drive windows — see
[.claude/skills/shinka-orchestrator/SKILL.md](../../.claude/skills/shinka-orchestrator/SKILL.md):

```bash
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

where the config's `task.eval_program_path` / `task.init_program_path` point at
this directory's `evaluate.py` / `initial.py`. Copy the
`configs/orchestrator_run.default.json` starter and edit it.

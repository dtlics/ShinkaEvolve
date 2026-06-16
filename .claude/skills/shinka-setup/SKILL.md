---
name: shinka-setup
description: Create ShinkaEvolve task scaffolds from a target directory and task description, producing `evaluate.py` and `initial.<ext>` (multi-language). Use when asked to set up new ShinkaEvolve tasks, evaluation harnesses, or baseline programs for ShinkaEvolve.
---

# Shinka Task Setup Skill
Create a setup scaffold consisting of an evaluation script and initial solution for an optimization problem given a user's task description. Both ingredients will be used within ShinkaEvolve, a framework combining LLMs with evolutionary algorithms to drive code optimization.

> **This repo is orchestrator-driven (Azure-only).** Your deliverables are
> `evaluate.py` (calling `shinka.core.run_shinka_eval`) and `initial.<ext>` (with
> an `EVOLVE-BLOCK`). Do **not** generate `run_evo.py` / `shinka.yaml` or use
> `shinka_run` — that launch path was removed. To RUN evolution, the
> **shinka-orchestrator** outer loop (`.claude/skills/shinka-orchestrator/SKILL.md`) points a run
> config at this task's `evaluate.py` + `initial.<ext>` and drives
> `orchestrator/harness/run_window.py`. Use `configs/orchestrator_run.default.json` as
> the run-config starter.

# When to Use
Invoke this skill when the user:
- Wants to optimize code with LLM-driven code evolution (Shinka/ShinkaEvolve)
- No `evaluate.py` and `initial.<ext>` exist in the working directory

## User Inputs
- Task description + success criteria
- Target language for `initial.<ext>` (if omitted, default to Python)
- What parts of the script to optimize
- Evaluation metric(s) and score direction
- Number of eval runs / seeds
- Required assets or data files
- Dependencies or constraints (runtime, memory)

## Workflow
1. Check if all user inputs are provided and ask the user follow-up questions if not inferrable.
2. Inspect working directory. Detect chosen language + extension. Avoid overwriting existing `evaluate.py` or `initial.<ext>` without consent.
3. Write `initial.<ext>` with a clear evolve region (`EVOLVE-BLOCK` markers or language-equivalent comments) and stable I/O contract.
4. Write `evaluate.py`:
   - Python `initial.py`: call `run_shinka_eval` with `experiment_fn_name`, `get_experiment_kwargs`, `aggregate_metrics_fn`, `num_runs`, and optional `validate_fn`.
   - Non-Python `initial.<ext>`: run candidate program directly (usually via `subprocess`) and write `metrics.json` + `correct.json`.
5. Ensure candidate output schema matches evaluator expectations (tuple/dict for Python module eval, or file/CLI contract for non-Python). **Make the evaluator leak-proof:** put held-out / gate-defining numbers under a `private` metrics dict — only `public` metrics reach the inner loop (`perf_str` renders only `public`), and `text_feedback` describes failures without revealing a target. Then any candidate that passes and improves the metric is a good candidate, and the inner loop is always fed the evaluator's feedback.
6. Validate draft `evaluate.py` before handoff:
   - Run a smoke test:
     - `python evaluate.py --program_path initial.<ext> --results_dir /tmp/shinka_eval_smoke`
   - Confirm evaluator runs without exceptions.
   - Confirm a metrics `dict` is produced (either from `aggregate_fn` or `metrics.json`) with at least:
     - `combined_score` (numeric),
     - `public` (`dict`),
     - `private` (`dict`),
     - `extra_data` (`dict`),
     - `text_feedback` (string, can be empty).
   - Confirm `correct.json` exists with `correct` (bool) and `error` (string) fields.
7. Hand off to the **shinka-orchestrator** outer loop to run evolution: copy
   `configs/orchestrator_run.default.json`, set `task.eval_program_path` /
   `task.init_program_path` to this task's `evaluate.py` / `initial.<ext>`, a
   `task.language`, a `budget_usd`, and a precise `task_sys_msg`, then drive
   `python orchestrator/harness/run_window.py --config <run>/run.json --until-decision`.
   The starter ships `task_sys_msg` as the sentinel `__UNSET_AUTHOR_AT_BOOT__` — the
   harness REFUSES to start until the orchestrator authors a real goal (the goal + hard
   constraints). Leak-proofing is the EVALUATOR's job: held-out / gate-defining numbers go
   under a `private` metrics dict (only `public` metrics reach the inner loop), and
   `text_feedback` describes a failure without revealing a target — so any candidate that
   passes and improves the metric is by construction good. Running this task means BEING the
   orchestrator/outer-loop under that run loop (warmup → wake-per-taper cluster →
   automatic per-window meta → framework-audit + DR checks → end-of-run archive).

## What is ShinkaEvolve?
A framework developed by SakanaAI that combines LLMs with evolutionary algorithms to propose program mutations, that are then evaluated and archived. The goal is to optimize for performance and discover novel scientific insights. 

Repo and documentation: https://github.com/SakanaAI/ShinkaEvolve
Paper: https://arxiv.org/abs/2509.19349

### Evolution Flow
1. Select parent(s) from archive/population
2. LLM proposes patch (diff, full rewrite, or crossover)
3. Evaluate candidate → `combined_score`
4. If valid, insert into island archive (higher score = better)
5. Periodically migrate top solutions between islands
6. Repeat for N generations

### Core Files To Generate
| File | Purpose |
|------|---------|
| `initial.<ext>` | Starting solution in the chosen language with an evolve region that LLMs mutate |
| `evaluate.py` | Scores candidates and emits metrics/correctness outputs that guide selection |

(Only these two are the task contract. The run itself is configured by an
`orchestrator_run.json` — see step 7 — not a per-task `run_evo.py`/`shinka.yaml`.)

## Shinka availability
In this repo `shinka` is the in-tree framework source — no install needed; the
orchestrator forces the repo root onto `sys.path`. From the repo root,
`python -c "import shinka"` resolves to this tree.

## Language Support (`initial.<ext>`)
Shinka supports multiple candidate-program languages. Choose one, then keep extension/config/evaluator aligned.

| `task.language` (in run.json) | `initial.<ext>` |
|---|---|
| `python` | `initial.py` |
| `julia` | `initial.jl` |
| `cpp` | `initial.cpp` |
| `cuda` | `initial.cu` |
| `rust` | `initial.rs` |
| `swift` | `initial.swift` |
| `json` / `json5` | `initial.json` |

Rules:
- `evaluate.py` stays the evaluator entrypoint.
- Python candidates: prefer `run_shinka_eval` + `experiment_fn_name`.
- Non-Python candidates: evaluate via `subprocess` and write `metrics.json` + `correct.json`.
- Always set both `task.language` and a matching `task.init_program_path` in the run config (L4: the pruned `evo_config.*` keys are read by nothing; the live keys are `task.*` — see the shinka-orchestrator run.json schema).

## Template: `initial.<ext>` (Python example)
```py
import random

# EVOLVE-BLOCK-START
def advanced_algo():
    # Implement the evolving algorithm here.
    return 0.0, ""
# EVOLVE-BLOCK-END

def solve_problem(params):
    return advanced_algo()

def run_experiment(random_seed: int | None = None, **kwargs):
    """Main entrypoint called by evaluator."""
    if random_seed is not None:
        random.seed(random_seed)

    score, text = solve_problem(kwargs)
    return float(score), text
```

For non-Python `initial.<ext>`, keep the same idea: small evolve region + deterministic program interface consumed by `evaluate.py`.

## Template: `evaluate.py` (Python `run_shinka_eval` path)
```py
import argparse
import numpy as np

from shinka.core import run_shinka_eval  # required for results storage


def get_kwargs(run_idx: int) -> dict:
    return {"random_seed": int(np.random.randint(0, 1_000_000_000))}


def aggregate_fn(results: list) -> dict:
    scores = [r[0] for r in results]
    texts = [r[1] for r in results if len(r) > 1]
    combined_score = float(np.mean(scores))
    text = texts[0] if texts else ""
    return {
        "combined_score": combined_score,
        "public": {},
        "private": {},
        "extra_data": {},
        "text_feedback": text,
    }


def validate_fn(result):
    # Return (True, None) or (False, "reason")
    return True, None


def main(program_path: str, results_dir: str):
    metrics, correct, err = run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="run_experiment",
        num_runs=3,
        get_experiment_kwargs=get_kwargs,
        aggregate_metrics_fn=aggregate_fn,
        validate_fn=validate_fn,  # Optional
    )
    if not correct:
        raise RuntimeError(err or "Evaluation failed")


if __name__ == "__main__":
    # argparse program path & dir
    parser = argparse.ArgumentParser()
    parser.add_argument("--program_path", required=True)
    parser.add_argument("--results_dir", required=True)
    args = parser.parse_args()
    main(program_path=args.program_path, results_dir=args.results_dir)
```

## Template: `evaluate.py` (non-Python `initial.<ext>` path)
```py
import argparse
import json
import os
from pathlib import Path


def main(program_path: str, results_dir: str):
    os.makedirs(results_dir, exist_ok=True)

    # 1) Execute candidate program_path (subprocess / runtime-specific call)
    # 2) Compute task metrics + correctness
    metrics = {
        "combined_score": 0.0,
        "public": {},
        "private": {},
        "extra_data": {},
        "text_feedback": "",
    }
    correct = False
    error = ""

    (Path(results_dir) / "metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    (Path(results_dir) / "correct.json").write_text(
        json.dumps({"correct": correct, "error": error}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--program_path", required=True)
    parser.add_argument("--results_dir", required=True)
    args = parser.parse_args()
    main(program_path=args.program_path, results_dir=args.results_dir)
```

## Run config: `orchestrator_run.json`

See `configs/orchestrator_run.default.json` for the run-config
starter. Copy it next to your run, point `task.*` at this task's `evaluate.py` +
`initial.<ext>`, set `budget_usd` + the Azure `evo.llm_models`, and drive it with
the **shinka-orchestrator** outer loop (`.claude/skills/shinka-orchestrator/SKILL.md`).

## Notes
- Keep evolve markers tight; only code inside the region should evolve.
- Keep evaluator schema stable (`combined_score`, `public`, `private`, `extra_data`, `text_feedback`).
- Python module path: ensure `experiment_fn_name` matches function name in `initial.py`.
- Non-Python path: ensure evaluator/runtime contract matches `initial.<ext>` CLI/I/O.
- Higher `combined_score` values indicate better performance.

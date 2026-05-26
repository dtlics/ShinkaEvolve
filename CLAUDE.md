# CLAUDE.md — Project Memory

Read first. This file is loaded into every Claude Code session at this repo root.

## What this repo is

Personal working repo for evolutionary code optimization with [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve), running on Azure OpenAI. Started as a fork to fix Azure compat, then a sequence of branches added agentic features and research grounding (see [CHANGELOG.md](CHANGELOG.md) for the fork lineage), which were later **replaced** by the Claude-as-orchestrator rewrite + Azure-only prune. Everything lives here now — framework, tasks, configs, credentials, skills.

The outer `Shinka` repo at `/Users/dantongli/GIthub/Shinka/` is a thin shim that initializes this submodule. **Daily work happens here, in `shinkaevolve/`.**

## Your standing role: the evolutionary orchestrator

When asked to run, optimize, evolve, or improve a program in this repo, **you are
the orchestrator / outer loop** of the evolutionary system in [`orchestrator/`](orchestrator/).
Read [`orchestrator/SKILL.md`](orchestrator/SKILL.md) — it is your operating
playbook — before acting. In short:

- **You drive windows, not mutations.** The inner loop (`orchestrator/harness/run_window.py`)
  runs W iterations per call and returns a diagnostics JSON. You read it between
  windows and decide whether to intervene.
- **Inner-loop LLM calls go to Azure, never to you.** Mutations/fixes/judges are
  made by `orchestrator/scripts/*` calling Azure in background-poll mode. Never
  simulate a mutation in your own context — that breaks the 100× cost asymmetry.
  Your tokens are for window-level reasoning only.
- **Your power is rewriting strategy CODE.** When the search stagnates, you
  rewrite the mutable policy files in `orchestrator/scripts/` (sampling, novelty,
  bandit, reward, prompt, stagnation, island, fix, record policies) — as whole
  *concerns* (generation + consumption together), via the validate → deploy →
  measure → rollback protocol. You must NOT touch the **foundation** (sqlite
  schema, the JSON contract, the evaluator, the user's `evaluate.py`/`initial.*`).
  Defer foundation ideas to the end-of-run `RUN_SUMMARY.md`.
- **Do not stop until a termination criterion is met.** A run is one long
  consecutive process; keep invoking windows. The healthiest run is fifty
  windows read with no intervention.
- **The budget is hard-capped in code.** Set `budget_usd` in the run config; the
  harness keeps a cost ledger (`journal/run.json` → `total_cost`) summing every
  LLM call (mutation/meta/deep-research/embeddings) + your logged interventions,
  and `run_window` hard-stops at the cap (`return_reason="budget_exhausted"`).
  Before any discretionary `meta_summarize`/`deep_research` call, check
  `journal.budget_remaining(...)` and log its cost.
- **This repo's shinka is the only one used.** `run_window` asserts `shinka`
  resolves to this worktree at startup; the orchestrator scripts force it onto
  `sys.path` first and the eval subprocess inherits a repo-root `PYTHONPATH`, so
  the editable install is not required and an original checkout can't leak in.

The Azure/deployment/env details below are your toolbox for live runs.

## Environment

- **Conda env**: `shinka` (Python 3.11). Never let pip install into `base` or any other env on this machine — others must stay clean (`coc`, `couple_therapy`, `efficient_cs`, `pl_ht`, `supercollider`).
  - Activate: `conda activate shinka`
  - Direct binaries when activation isn't available: `/opt/anaconda3/envs/shinka/bin/{python, pip}` (the `shinka_run`/`shinka_launch`/`shinka_models`/`shinka_visualize` console scripts were removed in the Azure-only prune).
- **Install**: not required. The orchestrator forces this repo root onto `sys.path` and the eval subprocess inherits a repo-root `PYTHONPATH`, so `import shinka` always resolves to *this* tree (`run_window` asserts it at startup). `pip install -e .` is optional — only needed for `import shinka` from a cwd outside the repo. Edits to `shinka/...` take effect immediately.
- **Pytest**: `testpaths = ["orchestrator/tests"]` in pyproject — the offline parity/smoke/improvement suite; keeps `tasks/*/evaluate.py` out of test discovery.

## Two Azure resources, parallel structure

The user runs **two separate Azure resources**: a main chat/reasoning endpoint and a deep-research endpoint. Both use the umbrella URL form (`https://<resource>.openai.azure.com`); each has its own key, project, and deployment set. The framework keeps them separable via distinct env-var pairs.

| | Main | Deep research |
|---|---|---|
| Resource | `dtlics2000shinka` | `dtlics2000-4351-resource` |
| Region | East US 2 | (different region) |
| Endpoint env | `AZURE_API_ENDPOINT` | `AZURE_DR_ENDPOINT` |
| Key env | `AZURE_OPENAI_API_KEY` | `AZURE_DR_API_KEY` |
| API version | `AZURE_API_VERSION=preview` | `AZURE_DR_API_VERSION=preview` |
| Client factory | `shinka.llm.client.get_async_client_llm` | `shinka.llm.agent.dr_client.get_dr_async_client` |
| Used by | mutate / meta_summarize / fix / novelty embeddings | `orchestrator/scripts/deep_research.py` (DR Stage-C prompt) |
| Cost separation | `purpose=mutate / meta / fix` | `purpose=deep_research` |

Both endpoints' base_url is built by appending `/openai/v1` to the bare resource URL — same logic, two parallel functions (`_build_azure_base_url` and `_build_dr_base_url`).

### Main resource deployments

| Shinka model id | Deployment name | Underlying model | Notes |
|---|---|---|---|
| `azure-gpt-5.4-pro` | `gpt-5.4-pro` | gpt-5.4-pro v2026-03-05 | $30/$180 per 1M. **Requires reasoning effort ≥ medium** (low rejected). |
| `azure-gpt-5.5` | `gpt-5.5` | gpt-5.5 v2026-04-24 | $5/$30 per 1M. |
| `azure-gpt-5.3-codex` | `gpt-5.3-codex` | gpt-5.3-codex v2026-02-24 | Coding-tuned, $1.75/$14 per 1M. |
| `azure-gpt-5.4-mini` | `gpt-5.4-mini` | gpt-5.4-mini v2026-03-17 | Cheap workhorse, $0.75/$4.50 per 1M. |
| `azure-text-embedding-3-small` | `text-embedding-3-small` | — | $0.02 per 1M tokens. Default for all tasks. |
| `azure-text-embedding-3-large` | `text-embedding-3-large` | — | $0.13 per 1M tokens. Only when dedup looks lossy. |

**Critical**: the bare name `text-embedding-3-small` (no `azure-` prefix) routes to the OpenAI provider and demands `OPENAI_API_KEY`. Always use `azure-text-embedding-3-small`. Verify deployments with `python scripts/test_azure.py`.

### DR resource deployment

- `o3-deep-research` deployment, underlying model version `2025-06-26`. Used by `orchestrator/scripts/deep_research.py` (Stage-C DR prompt) via the dedicated `dr_client`. Override the deployment name in that script if you rename it.

### Reasoning-effort gotcha

Setting `reasoning_effort: low` errors out for `azure-gpt-5.4-pro` (it rejects `low`). Use `medium` (or `high`) for any pool containing `gpt-5.4-pro`. The cheaper models support all three. For the meta / novelty helper calls (typically `azure-gpt-5.4-mini`), `low` is fine and saves cost.

### Smoke tests

```bash
conda activate shinka
cd /Users/dantongli/GIthub/ShinkaEvolve
python scripts/test_azure.py     # hits each main-resource deployment
```

## Running a task (you are the orchestrator)

Read [`orchestrator/SKILL.md`](orchestrator/SKILL.md) — the full playbook. In
short: author a run config (`orchestrator/SKILL.md` documents the schema; the
`shinka-setup`/`shinka-convert` skills emit a `scripts/orchestrator_run.json`
starter) pointing at the task's `evaluate.py` + `initial.<ext>`, then drive
windows:

```bash
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

The inner loop runs windows autonomously and returns to you only on stagnation
(or a window cap). You read the diagnostics, optionally rewrite a mutable
strategy file via the validate → deploy → measure → rollback protocol, and
continue until a termination criterion is met. Per-run artifacts (the archive
`programs.sqlite`, `journal/`, per-strategy snapshots in
`orchestrator/strategy_history/`) live under the run's `results_dir`
(gitignored). The old `shinka_run` CLI was removed in the Azure-only prune.

### Active user task

[`tasks/cnot_grid_synth/`](tasks/cnot_grid_synth/) — CNOT-equivalent linear-function synthesis on a 2D L×L grid. EVOLVE-BLOCK in [initial.py](tasks/cnot_grid_synth/initial.py); scoring + adjacency/Clifford gates in [evaluate.py](tasks/cnot_grid_synth/evaluate.py). Read [tasks/cnot_grid_synth/README.md](tasks/cnot_grid_synth/README.md) for the problem statement and score targets. [`examples/circle_packing/`](examples/circle_packing/) is a smaller reference task (its `evaluate.py`/`initial.py` drive the orchestrator smoke test).

> The pre-prune `shinka_run` research-grounding flags (`use_agentic_proposer`,
> `enable_deep_research`, `enable_literature_grounded`) and the `AgentLLMClient`
> per-generation agentic architecture were **removed** in the orchestrator
> rewrite + Azure-only prune. The inner-loop mutation is now the stateless
> Azure background-poll call in `orchestrator/scripts/mutate.py`; deep research
> is `orchestrator/scripts/deep_research.py` (called deliberately by the
> orchestrator, not on a fixed cadence). Truncation still applies: `error_traceback`
> ~8KB and `stderr_log` ~16KB (head+tail). See `orchestrator/SKILL.md`.

## Working in this repo

### Adding a new task
Use the `shinka-setup` skill (scaffold from a description) or `shinka-convert` skill (turn an existing repo into a Shinka task). Don't hand-write `evaluate.py` / `initial.<ext>` — the skills know the calling conventions.

### Inspecting results
Use the `shinka-inspect` skill — it loads top programs into agent context as a markdown bundle.

### Patching the framework
Edit `shinka/...` directly (no install needed — imported from this tree). Commit on the current branch. To push:

```bash
git push -u origin <branch>        # origin = dtlics/ShinkaEvolve.git
```

## Things future agents should NOT do

- Do not `pip install` into anything other than the `shinka` conda env.
- Do not commit `.env`, `tasks/*/results/`, or `evolution_db.sqlite` (gitignored).
- Do not install the shinka skills into `~/.claude/skills/` (global). They live at `.claude/skills/` in this repo and track this branch.
- Do not edit `dr_client.py` to share env vars with the main endpoint — they're separate resources by design.
- Do not re-add non-Azure providers or the old `shinka_run` / agentic-proposer code — this fork is Azure-only and orchestrator-driven.
- Do not touch the FOUNDATION mid-run (sqlite schema, the scripts' JSON contract, `evaluate.py`, the user's `evaluate.py`/`initial.*`). Defer foundation ideas to `RUN_SUMMARY.md`.

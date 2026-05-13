# CLAUDE.md — Project Memory

Read first. This file is loaded into every Claude Code session at this repo root.

## What this repo is

Personal working repo for evolutionary code optimization with [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve), running on Azure OpenAI. Started as a fork to fix Azure compat, then a sequence of branches added agentic features and research grounding (see [README.md](README.md) for the branch table). Now collapsed so everything lives here — framework, tasks, configs, credentials, skills.

The outer `Shinka` repo at `/Users/dantongli/GIthub/Shinka/` is a thin shim that initializes this submodule. **Daily work happens here, in `shinkaevolve/`.**

## Environment

- **Conda env**: `shinka` (Python 3.11). Never let pip install into `base` or any other env on this machine — others must stay clean (`coc`, `couple_therapy`, `efficient_cs`, `pl_ht`, `supercollider`).
  - Activate: `conda activate shinka`
  - Direct binaries when activation isn't available: `/opt/anaconda3/envs/shinka/bin/{python, pip, shinka_run, shinka_launch, shinka_models, shinka_visualize}`
- **Install**: this package is `pip install -e .` from this directory. Edits to `shinka/...` take effect immediately.
- **Pytest**: `testpaths = ["tests"]` in pyproject — keeps `scripts/` smoke runners and `tasks/*/evaluate.py` out of test discovery.

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
| Used by | proposer / meta / novelty / fix | `DeepResearchSummarizer` Stage C only |
| Cost separation | `purpose=proposer / meta / fix` | `purpose=dr_stage_a / dr_stage_b / dr_stage_c / dr_stage_d` |

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

**Critical**: the bare name `text-embedding-3-small` (no `azure-` prefix) routes to the OpenAI provider and demands `OPENAI_API_KEY`. Always use `azure-text-embedding-3-small`. Verify deployments with `shinka_models --verbose`.

### DR resource deployment

- `o3-deep-research` deployment, underlying model version `2025-06-26`. Used by `DeepResearchSummarizer` Stage C via the dedicated `dr_client`. The framework default `evo.dr_model="o3-deep-research"` matches; override per task if you rename the deployment.

### Reasoning-effort gotcha

Sampling `reasoning_efforts: [low, medium, high]` errors out for `azure-gpt-5.4-pro` (it rejects `low`). Use `[medium, high]` (or just `[medium]`) for any pool containing `gpt-5.4-pro`. The cheaper models support all three. For `meta_llm_models` and `novelty_llm_models` (typically `azure-gpt-5.4-mini`), `[low]` is fine and saves cost.

### Smoke tests

```bash
conda activate shinka
cd /Users/dantongli/GIthub/Shinka/shinkaevolve
python scripts/test_azure.py     # hits each main-resource deployment
python scripts/test_agentic.py   # exercises the agentic proposer end-to-end
```

## Running a task

```bash
cd /Users/dantongli/GIthub/Shinka/shinkaevolve
shinka_run --task-dir tasks/<name> --results-dir tasks/<name>/results ...
```

Per-task results live inside `tasks/<name>/results/` (gitignored). Cross-task `results/` at the repo root also gitignored.

### Enabling the research-grounding features

Each is opt-in via Hydra `--set`. The agent loop benefits from `max_patch_attempts=2`+ so it has room to apply→evaluate→fix:

```bash
shinka_run --task-dir tasks/<name> --results-dir tasks/<name>/results \
  --num_generations 30 \
  --set evo.use_agentic_proposer=true \
  --set evo.max_patch_attempts=2 \
  --set evo.enable_deep_research=true \
  --set evo.enable_literature_grounded=true
```

What each flag turns on:

- **`use_agentic_proposer=true`** — `_run_agent_proposal` instead of `_run_patch_async`. The agent loops apply_patch → evaluate → reflect with continuous context across attempts (one `Runner.run` per generation, `max_turns = max_patch_attempts × 3`).
- **`enable_deep_research=true`** — every `dr_meta_interval` (default 20) programs, run Stage A (drift judge) → Stage B (novelty cache lookup) → Stage C (o3-deep-research call) → Stage D (per-item web_search grounding). Per-island briefs; the freeform meta cycle keeps running at its own cadence (`meta_rec_interval`, default 10).
- **`enable_literature_grounded=true`** — adds a fourth mutation type that picks one BriefItem from the parent's island brief and asks the agent to apply it, with `web_search` enabled for that one call. Suppressed for islands with no eligible brief item.

The schema column `error_traceback` (truncated stderr/traceback when correct=False) and the in-loop `Program.metadata.fix_telemetry` dict (`{apply_attempts, eval_attempts, had_failure_then_success, final_correct}`) are written on every agentic run — they don't need flags. Query them after a run for fix-skill diagnostics.

## Agentic architecture (one screen)

`AgentLLMClient` (in `shinka/llm/agent/client.py`) wraps the `openai-agents` SDK. Each generation:

1. `_run_agent_proposal` builds a `ShinkaToolContext` with the parent's code, the patch dir, the wired evaluator closure, and the per-island brief.
2. It picks tools from `evo_config.agentic_tools` (default `["apply_patch", "evaluate"]`); for the `literature_grounded` arm it forces in `web_search` for that one call.
3. `AgentLLMClient.run_agent` constructs a fresh `Agent` + `BackgroundOpenAIResponsesModel` + `AsyncAzureOpenAI` client per call, calls `Runner.run`, parses the `PatchProposalOutput` Pydantic structured output (name + description).
4. The agent's tool calls mutate `tool_ctx`: `last_successful_patch_text`, `last_successful_num_applied`, `last_eval_result`, `tool_call_trace`. The orchestrator reads these after the run and either short-circuits to the cached eval result (Phase E cache-and-skip) or submits the patched code through the scheduler.
5. Bandit update happens once on the program's final `combined_score`; `abort_reason="insufficient_reference"` (literature_grounded clean abort) skips the update to preserve the "aborts ≠ low-score" invariant.

Six agent tools (registered in `shinka/llm/agent/tools/`): `apply_patch`, `evaluate`, `web_search` (opt-in, server-side via `agents.WebSearchTool`), `query_evolution_db`, `read_host_file`. Web search is enabled per-call only — never set as a global default (each call costs $0.01–0.03 plus content tokens).

## Working in this repo

### Adding a new task
Use the `shinka-setup` skill (scaffold from a description) or `shinka-convert` skill (turn an existing repo into a Shinka task). Don't hand-write `evaluate.py` / `initial.<ext>` — the skills know the calling conventions.

### Inspecting results
Use the `shinka-inspect` skill — it loads top programs into agent context as a markdown bundle.

### Patching the framework
Edit `shinka/...` directly (editable install). Commit on the current branch. To push:

```bash
git push -u fork <branch>          # fork = dtlics/ShinkaEvolve.git
# Outer-repo: git add shinkaevolve && git commit -m "Bump shinkaevolve: ..."
```

## Things future agents should NOT do

- Do not `pip install` into anything other than the `shinka` conda env.
- Do not commit `.env`, `tasks/*/results/`, or `evolution_db.sqlite` (gitignored).
- Do not install the shinka skills into `~/.claude/skills/` (global). They live at `.claude/skills/` in this repo and track this branch.
- Do not add `web_search` to the global `agentic_tools` list — it's per-call only.
- Do not edit `dr_client.py` to share env vars with the main endpoint — they're separate resources by design.

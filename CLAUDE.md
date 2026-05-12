# CLAUDE.md — Project Memory for Shinka (collapsed-agent layout)

This file is loaded into every Claude Code session at this repo root. Read it first.

## What this repo is

A working environment for **ShinkaEvolve** (Sakana AI's LLM-driven evolutionary code optimization) with an Azure OpenAI backend, plus the user's own task code and the **research-grounding** features added on top of the agentic rewrite (deep research, literature_grounded mutation arm, fix telemetry, Azure-aware cost kwargs).

After the `collapsed-agent` reorganization, **everything meaningful lives in this repo**:
- Framework source (`shinka/`), examples, tests.
- User tasks (`tasks/<name>/`).
- Shared Hydra config overrides (`configs/`).
- Smoke-test scripts (`scripts/`).
- Credentials (`.env`, gitignored).
- Project-scoped Claude Code skills (`.claude/skills/`).

The outer repo at `/Users/dantongli/GIthub/Shinka/` is a thin shim that initializes this submodule and gets out of the way. **All edits happen here.**

## Layout

```
shinkaevolve/
├── shinka/             # framework source — pip install -e .
├── examples/           # upstream example tasks (circle_packing, game_2048, ...)
├── tasks/              # user-defined tasks (initial.<ext>, evaluate.py, ...)
├── configs/            # shared Hydra config overrides
├── scripts/            # smoke tests (test_azure.py, test_agentic.py)
├── skills/             # Claude Code / Codex skills, exposed via .claude/skills/*
├── tests/              # pytest suite (537 tests as of collapse)
├── docs/, mkdocs.yml   # documentation
├── .env                # Azure + LLM credentials (gitignored — never commit)
├── .env.example        # template
├── .claude/skills/     # symlinks into skills/ — what Claude Code reads
├── CLAUDE.md           # this file
└── pyproject.toml      # editable install metadata
```

## Environment

- **Python env**: conda env named `shinka` with Python 3.11. Always use this — never let pip install into `base` or any other env.
  - Activate: `conda activate shinka`
  - Direct binary path (for non-interactive shells): `/opt/anaconda3/envs/shinka/bin/python`, `/opt/anaconda3/envs/shinka/bin/pip`.
  - Shinka CLI: `/opt/anaconda3/envs/shinka/bin/shinka_launch`, `shinka_run`, `shinka_models`, `shinka_visualize`.
- **ShinkaEvolve install**: editable (`pip install -e .` from this directory). Edits to `shinka/...` take effect immediately — no reinstall needed.
- **Branches**: this repo is `dtlics/ShinkaEvolve.git`. Active branches:
  - `agentic-rewrite` — agents-SDK rewrite + 6 agent tools (apply_patch / evaluate / web_search / query_evolution_db / read_host_file).
  - `agent-research-grounding` — on top of agentic-rewrite: error_traceback persistence, Azure-aware call kwargs, fix telemetry, DR meta cycle, literature_grounded mutation arm.
  - `collapsed-agent` — on top of agent-research-grounding: this layout (tasks/configs/scripts/.env collapsed in).

## LLM backends (two separate Azure resources)

### Main chat/reasoning endpoint
Foundry resource: **dtlics2000shinka** in **East US 2**, project name **Shinka**. All deployments are **Global Standard**. Credentials in `.env`:

| Var | Value |
|---|---|
| `AZURE_OPENAI_API_KEY` | (in `.env`) |
| `AZURE_API_ENDPOINT` | `https://dtlics2000shinka.openai.azure.com` (bare; shinka appends `/openai/v1`) |
| `AZURE_API_VERSION` | `preview` |

Chat/reasoning deployments (used for `evo.llm_models`, `evo.meta_llm_models`, `evo.novelty_llm_models`):

| Shinka model id | Deployment name | Underlying model | Notes |
|---|---|---|---|
| `azure-gpt-5.4-pro` | `gpt-5.4-pro` | gpt-5.4-pro v2026-03-05 | Strong reasoning, $30/$180 per 1M. **Requires reasoning effort ≥ medium** (low rejected). |
| `azure-gpt-5.5` | `gpt-5.5` | gpt-5.5 v2026-04-24 | $5/$30 per 1M. |
| `azure-gpt-5.3-codex` | `gpt-5.3-codex` | gpt-5.3-codex v2026-02-24 | Coding-tuned, $1.75/$14 per 1M. |
| `azure-gpt-5.4-mini` | `gpt-5.4-mini` | gpt-5.4-mini v2026-03-17 | Cheap workhorse, $0.75/$4.50 per 1M. |

Embedding deployments (used for `evo.embedding_model` — separate from chat: vector embeddings for code-similarity dedup and dashboard PCA, NOT proposal/novelty/meta):

| Shinka model id | Deployment name | Notes |
|---|---|---|
| `azure-text-embedding-3-small` | `text-embedding-3-small` | $0.02 per 1M tokens. Default for all tasks. |
| `azure-text-embedding-3-large` | `text-embedding-3-large` | $0.13 per 1M tokens. Only when dedup looks lossy. |

**Critical**: the bare `text-embedding-3-small` (without the `azure-` prefix) routes to the OpenAI provider and demands `OPENAI_API_KEY`. Always use the `azure-text-embedding-3-small` form. Verify availability with `shinka_models --verbose`.

### Deep-research endpoint (phase 2 of research-grounding)
**Separate** Foundry resource: **dtlics2000-4351-resource**, project **dtlics2000-4351**. Hosts the `o3-deep-research` deployment (model version `2025-06-26`). Powers `DeepResearchSummarizer` Stage C — distinct from chat reasoning. Credentials in `.env`:

| Var | Value |
|---|---|
| `AZURE_DR_API_KEY` | (in `.env`) |
| `AZURE_DR_ENDPOINT` | `https://dtlics2000-4351-resource.services.ai.azure.com/api/projects/dtlics2000-4351` (bare form; the dr_client normalizes both bare and `/openai/v1` and `/openai/v1/responses` variants) |
| `AZURE_DR_API_VERSION` | `preview` |
| `evo.dr_model` config knob | `o3-deep-research` (the deployment name; override if you renamed your deployment) |

### Why this is a fork (`dtlics/ShinkaEvolve`) and not upstream

Upstream `SakanaAI/ShinkaEvolve` builds the Azure client with `azure_endpoint=`, which makes the OpenAI SDK inject `/openai/deployments/{model}/...` into every URL. Shinka calls the **responses API**, and on Azure that endpoint only lives at `/openai/v1/responses` — not the deployment-based path. Result on upstream: 404 on every Azure call.

The fork's Azure-compat patches:
1. **LLM client base_url fix** — `base_url=` pointing at `/openai/v1` so the responses API works.
2. **Embedding api-version split** — `/responses` requires `preview`, but embeddings need a stable date-based version (`AZURE_EMBEDDING_API_VERSION` env var, default `2024-10-21`).
3. **LLM timeout 1200 → 3600 (60 min)** — `gpt-5.4-pro` at `reasoning_effort=high` regularly takes more than 20 min.
4. **Disable inner @backoff retry** — the legacy `@backoff` decorator retried with the same poisoned httpx pool for hours on transient HTTP errors; fixed by relying on the outer client-rebuild retry (now further refined by the agentic-rewrite background-mode transport that avoids long idle TCP altogether).

If upstream merges equivalent fixes, repoint the submodule back to `SakanaAI/ShinkaEvolve` and drop these patches.

### Reasoning effort

Shinka samples a reasoning effort per call from the YAML's `reasoning_efforts` list. **Use `[medium, high]` (or just `[medium]`) for any pool that includes `azure-gpt-5.4-pro`** — `low` errors out for that model. The cheaper models support all three (`low`/`medium`/`high`).

For `meta_llm_models` and `novelty_llm_models` (which typically use `azure-gpt-5.4-mini`), `[low]` is fine and saves cost.

### Smoke tests

```bash
conda activate shinka
cd /Users/dantongli/GIthub/Shinka/shinkaevolve
python scripts/test_azure.py     # hits each chat/embed deployment
python scripts/test_agentic.py   # exercises the agentic proposer end-to-end
```

Run after any credential change or major patch. Either prints latency + per-call cost.

## Working in this repo

### Adding a new task
Use the `shinka-setup` skill (scaffold from a description) or `shinka-convert` skill (turn an existing codebase into a shinka task). Don't write `evaluate.py`/`initial.<ext>` by hand — the skills know the calling conventions.

### Running an existing task
Use the `shinka-run` skill, or directly:

```bash
conda activate shinka
cd /Users/dantongli/GIthub/Shinka/shinkaevolve
shinka_run --task-dir tasks/<name> --results-dir tasks/<name>/results ...
```

Per-task results stay inside `tasks/<name>/results/` (gitignored). Cross-task `results/` at the repo root is also fine and gitignored.

### Enabling the research-grounding features

Each feature is opt-in via Hydra config. Compose them in your task's run config:

```bash
shinka_run \
  --task-dir tasks/<name> \
  --results-dir tasks/<name>/results \
  --num_generations 30 \
  --set evo.use_agentic_proposer=true \
  --set evo.max_patch_attempts=2 \
  --set evo.enable_deep_research=true \
  --set evo.enable_literature_grounded=true
```

- `use_agentic_proposer=true` activates the agent loop (apply_patch + evaluate + reflect, continuous context across fix attempts).
- `enable_deep_research=true` activates Stage A→D every `dr_meta_interval` (default 20) programs.
- `enable_literature_grounded=true` adds the new mutation arm; suppressed for islands with no eligible brief item.

### Inspecting results
Use the `shinka-inspect` skill — it loads top programs into agent context as a markdown bundle.

### Patching shinka itself
Edit files under `shinka/` directly (editable install). To commit those changes:
1. `git checkout -b <branch>` from the current working branch.
2. Commit, then either push to fork and update the outer-repo gitlink, or keep changes local.

## Things future agents should NOT do

- Do **not** `pip install` into anything other than the `shinka` conda env. Other envs exist on this machine (`base`, `coc`, `couple_therapy`, `efficient_cs`, `pl_ht`, `supercollider`) and must stay clean.
- Do **not** commit `.env`, `tasks/*/results/`, or `evolution_db.sqlite` files. They're gitignored — don't `git add -f` them.
- Do **not** install the shinka skills into `~/.claude/skills/` (global). They're already symlinked into `.claude/skills/` so they track this repo's version.
- Do **not** ship the `.env` content (real keys) outside this machine; rotate keys if accidentally exposed.

## Quick references

- Shinka source: [shinka/](shinka/)
- Working examples: [examples/](examples/) — circle_packing, game_2048, julia_prime_counting, novelty_generator
- User tasks: [tasks/](tasks/) — cnot_grid_synth (your existing task)
- Azure client wiring (main endpoint): [shinka/llm/client.py](shinka/llm/client.py)
- DR client wiring: [shinka/llm/agent/dr_client.py](shinka/llm/agent/dr_client.py)
- Model name resolver (handles `azure-`, `openrouter/`, `local/` prefixes): [shinka/llm/providers/model_resolver.py](shinka/llm/providers/model_resolver.py)
- Bundled Hydra configs: [shinka/configs/](shinka/configs/) (default portfolios)
- Shared user overrides: [configs/](configs/) (e.g. `azure_default.yaml`)
- Research-grounding plan + state: [AGENTIC_REWRITE.md](AGENTIC_REWRITE.md), Phase 1-3 commits on the `agent-research-grounding` branch.

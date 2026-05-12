# CLAUDE.md — Project Memory for Shinka

This file is loaded into every Claude Code session in this repo. Read it first.

## What this repo is

A working environment for running **ShinkaEvolve** (Sakana AI's LLM-driven evolutionary code optimization framework) against an Azure OpenAI backend. The owner runs experiments here — defining tasks, kicking off evolution runs, and inspecting results.

This is the `collapsed` branch of [`dtlics/ShinkaEvolve`](https://github.com/dtlics/ShinkaEvolve): the workspace state (Azure config, project-specific tasks, the smoke-test script) lives directly in this repo on top of the upstream code, rather than in a thin wrapper repo that consumes ShinkaEvolve as a submodule.

## Layout

```
.
├── shinka/              # ShinkaEvolve source (the framework itself)
├── examples/            # tasks (one subdir each: initial.<ext>, evaluate.py, run_evo.py, ...)
│                        #   workspace task: cnot_grid_synth/
│                        #   bundled upstream: circle_packing, game_2048, julia_prime_counting,
│                        #                     novelty_generator, fortran_heat_diffusion
├── configs/             # optional shared Hydra config overrides (workspace defaults)
│   └── azure_default.yaml
├── scripts/             # workspace utilities
│   └── test_azure.py    # Azure smoke test
├── skills/              # Claude Code skills (shinka-setup / shinka-convert / shinka-run / shinka-inspect)
├── tests/               # framework unit tests
├── docs/                # mkdocs site (upstream)
├── .env                 # Azure + LLM credentials (gitignored — never commit)
├── .env.example         # template for the above
├── CLAUDE.md            # this file
└── README.md            # upstream ShinkaEvolve overview
```

## Environment

- **Python env**: conda env named `shinka` with Python 3.11. Always use this — never let pip install into `base` or any other env.
  - Activate: `conda activate shinka`
  - Direct binary path (when conda activate isn't available, e.g. inside a non-interactive shell): `/opt/anaconda3/envs/shinka/bin/python`, `/opt/anaconda3/envs/shinka/bin/pip`, etc.
  - The shinka CLI scripts live at `/opt/anaconda3/envs/shinka/bin/shinka_launch`, `shinka_run`, `shinka_models`, `shinka_visualize`.
- **Install**: editable (`pip install -e .` from this repo root). Edits to `shinka/...` take effect immediately — no reinstall needed.

## LLM backend (Azure OpenAI)

Foundry resource: **dtlics2000shinka** in **East US 2**, project name **Shinka**. All deployments are **Global Standard**.

Required env vars in [.env](.env) (gitignored; loaded automatically from CWD by `shinka.env.load_shinka_dotenv`):

| Var | Value |
|---|---|
| `AZURE_OPENAI_API_KEY` | (in `.env`) |
| `AZURE_API_ENDPOINT` | `https://dtlics2000shinka.openai.azure.com` (bare; shinka appends `/openai/v1`) |
| `AZURE_API_VERSION` | `preview` |
| `AZURE_EMBEDDING_API_VERSION` | `2024-10-21` (optional; defaults to `2024-10-21` if unset — needed because `preview` does not serve `/embeddings`) |

### Active deployments

**Chat/reasoning models** (used for `evo.llm_models`, `evo.meta_llm_models`, `evo.novelty_llm_models`):

| Shinka model id | Deployment name | Underlying model | Notes |
|---|---|---|---|
| `azure-gpt-5.4-pro` | `gpt-5.4-pro` | gpt-5.4-pro v2026-03-05 | Strong reasoning, $30/$180 per 1M. **Requires reasoning effort ≥ medium** (low rejected). |
| `azure-gpt-5.5` | `gpt-5.5` | gpt-5.5 v2026-04-24 | $5/$30 per 1M. |
| `azure-gpt-5.3-codex` | `gpt-5.3-codex` | gpt-5.3-codex v2026-02-24 | Coding-tuned, $1.75/$14 per 1M. |
| `azure-gpt-5.4-mini` | `gpt-5.4-mini` | gpt-5.4-mini v2026-03-17 | Cheap workhorse, $0.75/$4.50 per 1M. |

**Embedding models** (used for `evo.embedding_model` — a separate concept from chat: they're vector-embedding endpoints, called by shinka for code-similarity dedup and dashboard PCA, NOT for proposal/novelty/meta reasoning):

| Shinka model id | Deployment name | Notes |
|---|---|---|
| `azure-text-embedding-3-small` | `text-embedding-3-small` | $0.02 per 1M tokens. The default for all our tasks. |
| `azure-text-embedding-3-large` | `text-embedding-3-large` | $0.13 per 1M tokens. Higher-dim — only use if dedup looks too lossy. |

These were auto-provisioned by the Azure Foundry "Shinka" project alongside the chat models. **Critical**: the bare name `text-embedding-3-small` (without the `azure-` prefix) routes to the OpenAI provider and demands `OPENAI_API_KEY` — always use the `azure-text-embedding-3-small` form. Verify availability with `shinka_models --verbose`.

### Azure compatibility patches on this branch

Upstream `SakanaAI/ShinkaEvolve` builds the Azure client with `azure_endpoint=`, which makes the OpenAI SDK inject `/openai/deployments/{model}/...` into every URL. But shinka calls the **responses API**, and on Azure that endpoint only lives at `/openai/v1/responses` — not at the deployment-based path. Result: 404 on every Azure call.

The `collapsed` branch carries the following Azure-compatibility patches on top of the upstream code + the research-grounding (phase 0–6) work:

1. **LLM client `base_url=` fix** — switches both sync and async clients to `base_url=…/openai/v1` so the responses API works ([shinka/llm/client.py](shinka/llm/client.py)).
2. **Embedding client api-version split** — `AZURE_API_VERSION=preview` (needed for `/responses`) does *not* serve the embeddings endpoint. The embed client uses `AZURE_EMBEDDING_API_VERSION` if set, else defaults to a stable `2024-10-21` ([shinka/embed/client.py](shinka/embed/client.py)).

Three further patches that lived on the older `shinka-azure-v1-fix` branch are no longer needed here:
- The 1200 → 3600 timeout bump: superseded by the bg+poll architecture (Phase 2 of research-grounding) — long waits now happen in `responses.retrieve` polling loops bounded by `POLL_TIMEOUT_DEFAULT/DR/SHELL_FIX`, not in single long-running HTTP calls.
- `BACKOFF_MAX_TRIES=1`: superseded — `@backoff.on_exception` is no longer used anywhere in `shinka/`.
- Robust `/v1/responses` parser fixup: superseded by the Phase 2b rewrite of [shinka/llm/providers/openai.py](shinka/llm/providers/openai.py).

### Reasoning effort

Shinka samples a reasoning effort per call from the YAML's `reasoning_efforts` list. **Use `[medium, high]` (or just `[medium]`) for any pool that includes `azure-gpt-5.4-pro`** — `low` errors out for that model. The cheaper models support all three (`low`/`medium`/`high`) so they're not the limiting factor.

For `meta_llm_models` and `novelty_llm_models` (which only use `azure-gpt-5.4-mini`), `[low]` is fine and saves cost.

### Smoke test

```bash
conda activate shinka
python scripts/test_azure.py
```

Hits each deployment with a tiny prompt and prints latency + per-call cost. Run after any credential or patch change.

## Working in this repo

### Adding a new task
Use the `shinka-setup` skill (scaffold from a description) or `shinka-convert` skill (turn an existing codebase into a shinka task). Don't write `evaluate.py`/`initial.<ext>` by hand — the skills know the calling conventions. New tasks live under `examples/<name>/`.

### Running an existing task
Use the `shinka-run` skill, or directly:

```bash
conda activate shinka
shinka_run --task-dir examples/<name> --results-dir results/<name> ...
```

### Inspecting results
Use the `shinka-inspect` skill — it loads top programs into agent context as a markdown bundle.

### Patching shinka itself
Edit files under [shinka/](shinka/) directly (editable install). Commit on this `collapsed` branch (or a new branch off it).

## Things future agents should NOT do

- Do **not** `pip install` into anything other than the `shinka` conda env. Other envs exist on this machine (`base`, `coc`, `couple_therapy`, `efficient_cs`, `pl_ht`, `supercollider`) and must stay clean.
- Do **not** commit `.env`, `results/`, or `evolution_db.sqlite` files. They're gitignored — don't `git add -f` them.
- Do **not** install the shinka skills into `~/.claude/skills/` (global) — the `skills/` dir in this repo is project-scoped and tracks the in-repo version.

## Quick references

- Shinka source: [shinka/](shinka/)
- Working examples: [examples/](examples/) — circle_packing, game_2048, julia_prime_counting, novelty_generator, fortran_heat_diffusion, cnot_grid_synth
- Azure client wiring: [shinka/llm/client.py](shinka/llm/client.py)
- Embedding client wiring: [shinka/embed/client.py](shinka/embed/client.py)
- Model name resolver (handles `azure-`, `openai/` prefixes): [shinka/llm/providers/model_resolver.py](shinka/llm/providers/model_resolver.py)
- Bundled Hydra configs: [shinka/configs/](shinka/configs/)
- Workspace overrides: [configs/azure_default.yaml](configs/azure_default.yaml)

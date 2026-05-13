# Shinka — personal working repo

Personal fork of [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve) (Sakana AI's LLM-driven evolutionary code optimization), repurposed as a single self-contained working repo. The framework, user tasks, configs, credentials, smoke tests, and Claude Code skills all live here. The outer Shinka shell is a thin pointer.

## What's in this fork beyond upstream

| Layer | What changed | Branch lineage |
|---|---|---|
| **Azure compat** | base_url to /openai/v1 for the Responses API, split embedding api-version, raise LLM timeout to 60 min, disable inner @backoff retry storm | `shinka-azure-v1-fix` |
| **Agentic rewrite** | LLM call layer on the `openai-agents` SDK with `background=True` + polling transport; six agent-callable tools (apply_patch, evaluate, web_search, query_evolution_db, read_host_file, plus the SDK's web_search built-in); `_run_agent_proposal` orchestrator path | `agentic-rewrite` |
| **Research grounding** | error_traceback persistence + fix telemetry from the tool trace; Azure-aware call kwargs (prompt_cache_key, metadata, store, safety_identifier); per-island DR meta cycle calling `o3-deep-research` (Stage A drift → Stage B novelty cache → Stage C DR → Stage D code grounding); `literature_grounded` mutation arm that grounds in a DR brief item with bounded `web_search` | `agent-research-grounding` |
| **Collapse + doom-remediation** | outer Shinka repo merged into this submodule (tasks/, configs/, scripts/, .env, CLAUDE.md, .claude/skills/); plus five fixes from the always-on workflow walkthrough — `apply_patch` auto-evaluates so the LLM never has to call evaluate (default `agentic_tools=["apply_patch"]`); DR cost summed into `total_api_cost`; placeholder DR briefs don't poison the prompt; `stderr_log` head+tail truncated at load; `BriefItem.confirmed` flag filters unverified lit_grounded references | `collapsed-agent` (current) |

If upstream Sakana merges equivalent Azure-compat fixes, those branches can be deleted; the agentic-rewrite and research-grounding work would still live as a stand-alone fork.

## Quick start

```bash
# Conda env (one-time)
conda activate shinka                             # python 3.11
pip install -e .                                  # editable install of this package

# Credentials (one-time)
cp .env.example .env                              # fill in keys; never commit
python scripts/test_azure.py                      # smoke-test main endpoint

# Run a task
shinka_run --task-dir tasks/cnot_grid_synth \
  --results-dir tasks/cnot_grid_synth/results \
  --num_generations 30 \
  --set evo.use_agentic_proposer=true \
  --set evo.enable_deep_research=true \
  --set evo.enable_literature_grounded=true

# Inspect — use the shinka-inspect skill in Claude Code
```

Full operating guide for AI agents (and humans) is in [CLAUDE.md](CLAUDE.md). Architecture for the agentic rewrite is in [AGENTIC_REWRITE.md](AGENTIC_REWRITE.md).

## Layout

```
shinkaevolve/
├── shinka/             # framework source — pip install -e .
├── examples/           # upstream example tasks (circle_packing, game_2048, ...)
├── tasks/              # user tasks (initial.<ext>, evaluate.py, ...)
├── configs/            # shared Hydra overrides
├── scripts/            # smoke tests (test_azure.py, test_agentic.py)
├── skills/             # Claude Code / Codex skills, exposed via .claude/skills/
├── tests/              # pytest suite — 538 tests
├── docs/               # mkdocs reference site (framework features)
├── .env                # credentials — gitignored
├── .env.example        # template with both Azure resources documented
└── pyproject.toml
```

## Reference

- Framework source: [shinka/](shinka/)
- Working examples: [examples/](examples/) — circle_packing, game_2048, julia_prime_counting, novelty_generator
- User tasks: [tasks/](tasks/)
- Main Azure client: [shinka/llm/client.py](shinka/llm/client.py) (`_build_azure_base_url`)
- DR client: [shinka/llm/agent/dr_client.py](shinka/llm/agent/dr_client.py)
- Agentic proposer: [shinka/core/async_runner.py](shinka/core/async_runner.py) (`_run_agent_proposal`)
- DR summarizer: [shinka/core/deep_research_summarizer.py](shinka/core/deep_research_summarizer.py)
- Bundled Hydra config defaults: [shinka/configs/](shinka/configs/)
- Shared user overrides: [configs/azure_default.yaml](configs/azure_default.yaml)

## Citation (upstream)

```
@article{lange2025shinka,
  title={ShinkaEvolve: Towards Open-Ended And Sample-Efficient Program Evolution},
  author={Lange, Robert Tjarko and Imajuku, Yuki and Cetin, Edoardo},
  journal={arXiv preprint arXiv:2509.19349},
  year={2025}
}
```

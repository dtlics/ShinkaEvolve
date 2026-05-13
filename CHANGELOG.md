# Changelog

Personal fork lineage on top of upstream `SakanaAI/ShinkaEvolve`. For upstream
release history (v0.0.x and the framework's own changelog), see [upstream
CHANGELOG](https://github.com/SakanaAI/ShinkaEvolve/blob/main/CHANGELOG.md).

## Fork branches (most recent first)

### `collapsed-agent`
Collapsed the outer `Shinka` repo into this submodule. `tasks/`, `configs/`,
`scripts/`, `.env`, `CLAUDE.md`, and `.claude/skills/` symlinks all live
here now. Outer repo becomes a thin pointer; daily work happens here.
`.gitignore` extended for `.env`, `tasks/*/results/`, etc. Pytest scoped to
`tests/` so smoke runners and per-task `evaluate.py` aren't discovered.

### `agent-research-grounding`
Three features on top of the agentic rewrite, 8 commits, 538 tests:

- **Phase 1a**: `Program.error_traceback` column + migration; Azure-aware
  call kwargs (`prompt_cache_key`, `metadata`, `store`, `safety_identifier`)
  threaded through `AgentLLMClient._query_via_agents` ModelSettings.
- **Phase 1b**: `fix_telemetry` derived from the agent's tool trace —
  `{apply_attempts, eval_attempts, had_failure_then_success, final_correct}`
  per program, surfaces the in-loop fix-skill signal the bandit reward
  can't see.
- **Phase 2a–2c**: `DeepResearchSummarizer` Stage A (drift judge) → B
  (novelty cache) → C (Azure `o3-deep-research`) → D (per-item `web_search`
  grounding). New tables `meta_briefs` + `dr_brief_cache`. Per-island
  accumulator on `MetaSummarizer`. Sampler `island_brief` injection takes
  priority over freeform meta_recommendations. Separate Azure resource via
  `AZURE_DR_*` env vars.
- **Phase 3a–3b**: `literature_grounded` mutation arm. Suppressed when no
  brief item has a non-empty `reference_snippet`. When picked, the
  orchestrator forces `agentic_tools=["apply_patch", "evaluate", "web_search"]`
  for that one call, bumps `max_turns` to `literature_grounded_max_turns`
  (default 6), uses `web_search_context_size="high"`. Clean abort
  (no apply_patch) marks `meta_patch_data["abort_reason"] = "insufficient_reference"`;
  the post-eval bandit update skips rows with `abort_reason` set.

### `agentic-rewrite`
LLM call layer on `openai-agents` SDK with `background=True` + polling
transport. Six agent-callable tools (`apply_patch`, `evaluate`,
`query_evolution_db`, `read_host_file`, `web_search` opt-in). `Phase D`
orchestrator wired `_run_agent_proposal`. `Phase E` wired the evaluator
into the agent loop with cache-and-skip on the downstream eval pipeline.
`PatchProposalOutput` Pydantic structured output. `ShinkaAgentHooks` for
tool telemetry. Cleanup F.1 removed `RobustRunner` (the OpenAI SDK's
built-in retry is sufficient once background mode addresses the long-idle
TCP failure mode). Full plan: [AGENTIC_REWRITE.md](AGENTIC_REWRITE.md).

### `shinka-azure-v1-fix`
Made Azure OpenAI work with the responses API:

1. `base_url=` instead of `azure_endpoint=` so the SDK doesn't inject
   `/openai/deployments/{model}/...` (the responses API only lives at
   `/openai/v1/responses` on Azure).
2. Embedding api-version split — `/responses` requires `preview`, but
   embeddings need a GA date (`AZURE_EMBEDDING_API_VERSION`).
3. LLM timeout 1200 → 3600 s — `gpt-5.4-pro` at reasoning_effort=high
   routinely thinks more than 20 min.
4. Disabled the inner `@backoff` retry storm (it was retrying on the same
   poisoned httpx pool for hours when an Azure LB silently killed a
   socket). Now transport errors escalate to the outer client-rebuild
   retry. The agentic-rewrite later made this entirely moot by switching
   to background mode polling.

If upstream merges equivalent fixes, `shinka-azure-v1-fix` becomes
deletable and the rebase target can move to upstream `main`.

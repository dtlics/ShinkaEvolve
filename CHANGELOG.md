# Changelog

Personal fork lineage on top of upstream `SakanaAI/ShinkaEvolve`. For upstream
release history (v0.0.x and the framework's own changelog), see [upstream
CHANGELOG](https://github.com/SakanaAI/ShinkaEvolve/blob/main/CHANGELOG.md).

## Fork branches (most recent first)

### `main` — Claude-as-orchestrator rewrite + Azure-only prune
Recast the system so **Claude Code is the outer-loop orchestrator**: the inner
loop (`orchestrator/harness/run_window.py`) runs W-iteration windows and returns a
diagnostics JSON; the agent reads it between windows and rewrites mutable strategy
CODE (`orchestrator/scripts/*`) on stagnation via validate → deploy → measure →
rollback. EvoX-faithful stagnation (trigger Δ<τ; J kept for rollback). Inner-loop
LLM calls run on Azure in background-poll mode (never the agent's own tokens); a
single cost ledger (`journal`) hard-stops at `budget_usd`.

**Azure-only prune** — removed `async_runner`, the agentic-proposer layer,
`summarizer`/`novelty_judge`/`prompt_evolver`/`deep_research_summarizer`, the async
DB + prompt DB, all non-Azure providers, `plots/`/`webui/`/`docs/`/`cli/`, the
`shinka_run` launch path, and the legacy `tests/` suite. Kept the authoring skills
(setup/convert/inspect) + the cnot task so the repo stays reusable. `pip install -e .`
is no longer required — `import shinka` is path-forced to this tree. See
[orchestrator/SKILL.md](orchestrator/SKILL.md) + [orchestrator/NOTES.md](orchestrator/NOTES.md);
design map in [AUDIT.md](AUDIT.md) + [taxonomy.md](taxonomy.md).

### `collapsed-agent`
Collapsed the outer `Shinka` repo into this submodule. `tasks/`, `configs/`,
`scripts/`, `.env`, `CLAUDE.md`, and `.claude/skills/` symlinks all live
here now. Outer repo becomes a thin pointer; daily work happens here.
`.gitignore` extended for `.env`, `tasks/*/results/`, etc. Pytest scoped to
`tests/` so smoke runners and per-task `evaluate.py` aren't discovered.

**Doom-loophole remediation** (five fixes from the always-on workflow
walkthrough, 5 commits, 560 tests):

- **Fix 1 (auto-eval inside `apply_patch`)** — `apply_patch` now
  deterministically runs the evaluator after every successful apply
  and appends the eval result to its tool return string. The LLM no
  longer chooses when to evaluate; the framework guarantees every
  code change is scored. Default `agentic_tools` drops from
  `["apply_patch", "evaluate"]` to `["apply_patch"]` (manual
  `evaluate` is still registered, opt-in for edge cases like
  re-evaluating with different seeds). Eliminates the cache-and-skip
  staleness mode at the source: `tool_ctx.last_eval_result` is now
  structurally always paired with the latest applied code. Reduces
  per-cycle LLM round count by ~30% on the agent path.
- **Fix 2 (DR pipeline cost into `total_api_cost`)** — Stage A judge
  + Stage C `o3-deep-research` + Stage D agent runs all sum into
  `self.total_api_cost` at the DR firing site. `DRBrief` gains a
  `total_cost` property; `StageAOutput` gains a `cost` field so the
  judge call's spend stops being discarded. With
  `dr_max_calls_per_run=30` this previously allowed $150-300+ to
  bypass `max_api_costs`.
- **Fix 3 (placeholder briefs don't poison the prompt)** —
  `_latest_island_briefs[island_idx]` is only updated when the new
  brief is real (`source != "placeholder"` AND `items` non-empty). A
  Stage C failure no longer suppresses the freeform meta_recommendations
  fallback for 20 generations with a "DR pipeline did not produce
  items..." string. Placeholder briefs still persist into the
  `meta_briefs` SQLite table for diagnostic visibility.
- **Fix 4 (`stderr_log` head+tail truncation at load site)** —
  `_load_results` in `shinka.utils.general` caps `stderr_log` at
  ~16KB with both head and tail preserved (the tail is where the
  actual raise site lives). Chatty evaluators (jax compile noise,
  numpy deprecation warnings, qiskit transpiler chatter) can no
  longer flood the agent's intra-loop fix context with megabytes.
- **Fix 5 (`BriefItem.confirmed` + lit_grounded filter)** —
  `BriefItem` gains a `confirmed: bool` field (default True for
  backward compat). Stage D sets `confirmed=False` when its
  per-item web_search verification couldn't validate the reference
  (crash, empty response, or model returned `confirmed: false`). The
  lit_grounded eligibility filter excludes unconfirmed items so the
  agent doesn't burn a generation re-discovering Stage D's negative
  verdict.

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
TCP failure mode). Full plan: `AGENTIC_REWRITE.md` (removed in the orchestrator rewrite).

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

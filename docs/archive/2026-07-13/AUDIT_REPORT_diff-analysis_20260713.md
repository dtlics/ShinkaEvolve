> **STATUS: APPLIED (2026-07-13).** Historical record of the three-track audit
> (upstream parity / SimpleTES / concurrency) and its implementation batch.
> Live guidance is CLAUDE.md + .claude/skills/shinka-orchestrator/SKILL.md.

# shinka/ semantic diff: upstream SakanaAI/ShinkaEvolve → Azure-only orchestrator fork

Net: **1,707 insertions, 42,726 deletions across 96 files** (`scratchpad/audit/shinka_diff_stat.txt:97`). The fork strips shinka down to a passive library (prompt assembly, archive DB, eval wrapper, Azure LLM plumbing); the outer loop moved to `orchestrator/`.

## 1. REMOVED SUBSYSTEMS (whole files/dirs deleted)

- `shinka/core/async_runner.py` (5,851 lines) — upstream's `ShinkaEvolveRunner`, the fully-async pipelined evolution main loop (60 `async def`s) with concurrent proposal/eval jobs.
- `shinka/core/summarizer.py` + `async_summarizer.py` — `MetaSummarizer`: 3-step meta pipeline (per-program summaries → global-insights **scratchpad** (`meta_scratch_pad`, upstream `summarizer.py:41`) → recommendations).
- `shinka/core/novelty_judge.py` + `async_novelty_judge.py` — LLM-as-judge "meaningfully different?" novelty gate.
- `shinka/core/prompt_evolver.py` + `shinka/database/prompt_dbase.py` + `shinka/prompts/prompts_prompt_evo.py` — meta-prompt evolution (`SystemPromptEvolver`, `SystemPromptDatabase`).
- `shinka/core/pipeline_timing.py`, `runtime_slots.py` — async-runner timing instrumentation and job-slot bookkeeping.
- `shinka/database/async_dbase.py`, `shinka/edit/async_apply.py` — async wrappers for DB and patch application.
- `shinka/edit/marker_validation.py` — post-patch EVOLVE-BLOCK marker validator (calls also removed from `apply_diff.py`/`apply_full.py`).
- `shinka/cli/` (run.py, launch.py, models.py, run_config.py, `shinka_launch`/`shinka_run`/`shinka_visualize` entry points) — CLI runner.
- `shinka/webui/` (index.html, viz_tree.html 18k lines, visualization.py) and `shinka/plots/` (costs/evals/lineage/pareto/throughput/similarity/anim) — all visualization.
- `shinka/llm/providers/{anthropic,deepseek,gemini,headless,local_openai}.py`, `shinka/google_genai.py`, `shinka/local_openai_config.py` — all non-OpenAI/Azure providers; `headless.py` was a subprocess driver for coding-agent CLIs (Claude/codex agents, upstream `headless.py:181-232`).
- `shinka/model_availability.py`, `release_check.py`, `logo.py`, `favicon.png` — model preflight probing, PyPI release check, ASCII logo.
- `shinka/launch_hydra.py`, `eval_hydra.py` — Hydra launch/eval entry points (`shinka/utils/utils_hydra.py` and `shinka/configs/` are retained, `shinka/configs/__init__.py:1-4`).
- `shinka/utils/wolfram.py`, `shinka/prompts/prompts_meta.py` — Wolfram-language eval helper; the 3-step meta prompts.
- `shinka/utils/languages.py` — trimmed (150 lines), not deleted: Go, Fortran, Wolfram/Mathematica, and several fence aliases removed; file survives with Python/C++/CUDA/Rust etc.

New files (fork-only): `shinka/llm/agent/dr_client.py` (+334) — Azure `o3-deep-research` client on separate `AZURE_DR_*` env vars; `shinka/prompts/prompts_deep_research.py` (+56) — `DR_SYS_MSG`/`DR_USER_MSG`.

## 2. SEMANTIC CHANGES IN KEPT FILES

**`core/config.py`** — `EvolutionConfig` trimmed: removed `use_text_feedback` (default False), the controlled-oversubscription group (`enable_controlled_oversubscription`, `proposal_target_*`, `proposal_buffer_max`), and all 11 `evolve_prompts`/`prompt_*` fields. Kept fields' defaults unchanged. The orchestrator doesn't read this dataclass (module docstring, `shinka/core/config.py:1-10`).

**`core/sampler.py`** — the biggest behavioral file:
- `use_text_feedback` default **False → True** (`sampler.py:37`).
- New `sample()` params: `island_brief` (overrides global `meta_recommendations`), `failure_note`, `objective_brief`, `forced_patch_type` (run_window samples the patch mode first and forces it; internal sampling is now a legacy/test path that never draws `"fix"`).
- Meta-recommendation framing changed from optional "# Potential Recommendations" to a **directive** "# Direction for this attempt … not an optional suggestion"; when no direction exists, appends new `EXPERT_CREATIVE_PREAMBLE` (`prompts_base.py:15-20`).
- Persistent `failure_note` ("# Known failure modes to avoid") is injected into every gen including cross and fix.
- Metric slot becomes `objective_section(objective_brief) + perf_str(...)`; `sample_fix()` gains `failure_note`/`objective_brief` rendered into the fix system message.
- `get_cross_component` now receives `parent`.

**`database/dbase.py`** —
- `DatabaseConfig`: removed `max_stdout_log_chars` (upstream stdout-truncation knob); added `archive_floor_per_island=3` (`dbase.py:61`), `max_islands=0`, `island_evict_strategy="worst_best_fitness"` (`dbase.py:90-93`).
- `Program` gains `error_traceback` column + schema migration 5 (`dbase.py:188,456,630`).
- New `meta_briefs` table (migration 6) + `record_meta_brief`/`get_latest_meta_brief` — the live per-island direction store.
- New `append_program_error` (accumulates failed-repair tracebacks, re-truncated to ~8KB) and `tombstone_program` (de-archives, preserves row/lineage; reasons `"repair"` vs `"novelty_evict"`).
- Archive eviction rewritten: upstream evicted the globally worst (`_update_archive_fitness`) or the most-similar (`_update_archive_crowding`); fork routes both through new `_pick_archive_victim` — protects the global best, each island's elite, and any *other* island at/below the floor; tombstoned programs are reclaimed first; a below-floor island gets guaranteed admission even if not globally better (hunks `@@ -2231` / `@@ -2312`).
- New `spawn_island_from_program()` and `apply_island_actions()` (executes migrate/spawn/retire decided by `orchestrator/scripts/island_policy.py`).

**`database/islands.py`** — final-fallback island assignment changed **random → least-populated** (both assignment strategies, hunks `@@ -102`, `@@ -182`). `ElitistMigrationStrategy` now migrates over the *actual* `DISTINCT island_idx` set instead of `range(num_islands)`, so dynamically spawned islands can send/receive migrants (hunk `@@ -225`). `CombinedIslandManager` gains `retire_island` (islands.py:1054, non-destructive, protects island 0 + global-best island), `allocate_island_index_for_spawn` (:1074, honors `max_islands` cap with eviction), `spawn_island_from_program` (:1097).

**`database/parents.py`, `inspirations.py`, `island_sampler.py`, `display.py`, `complexity.py`** — parents/inspirations/island_sampler/display are **byte-identical** to upstream (empty diff). `complexity.py` only drops `go`/`golang` from the C-like language list (complexity.py:263).

**`llm/client.py`** — only `openai`/`azure_openai` providers survive; new `_azure_creds_for()` routes `azure-gpt-5.4-pro` to `AZURE_EASTUS2_*` when set; Azure clients now use `base_url=<resource>/openai/v1` (Responses API) instead of `azure_endpoint`.

**`llm/query.py`** — dispatch table collapsed to `query_openai`/`query_openai_async`; any other provider raises.

**`llm/constants.py`** — `TIMEOUT` **1200 → 3600** (constants.py:3); backoff constants (`BACKOFF_MAX_TRIES=20` etc.) deleted; new `PER_REQUEST_TIMEOUT` (60s, env-overridable, constants.py:12).

**`llm/providers/openai.py`** — **all `@backoff.on_exception` retry decorators removed** from `query_openai`/`query_openai_async` (retries now live in orchestrator's bg-poll layer); response parsing rewritten: upstream's `_extract_response_text` (uses `output_text`, aggregates all reasoning summaries) replaced by `_extract_message_text`/`_extract_thought_text` (first message item / first reasoning summary only, with a diagnostic error for reasoning-only responses).

**`llm/kwargs.py`** — `NO_TEMPERATURE_MODELS` deleted and the openai/azure-reasoning-model temperature exclusion dropped: upstream *omitted* `temperature` for OpenAI/Azure reasoning models; the fork now **always sends temperature** (1.0 for fixed-temp models, else sampled) (kwargs.py hunk `@@ -106`). DeepSeek thinking branch removed.

**`llm/prioritization.py`** (bandit kept) — `save_state` now atomic (tmp + fsync + `os.replace`); cost-blend variable renamed `k`→`kc` to avoid clobbering the virtual-pull loop counter; local/openrouter arm-name formatting removed.

**`llm/llm.py`** — headless plumbing (`headless_work_dir`, `_attach_headless_work_dir`) removed throughout; `extract_between` docstring fixed.

**`prompts/prompts_base.py`** — `perf_str` reformatted from `;`-joined line to one `- key: value` per line; new `objective_section()`; `construct_eval_history_msg` gains `for_cross` and reframes inspirations as "EVAL HISTORY … NOT inspirations to copy or combine" (soft background framing on cross).

**`prompts/prompts_cross.py`** — crossover partner selection changed from `random.choice` to `_most_distant` (lowest cosine similarity to parent via embeddings — resolves upstream's `TODO(RobertTLange)`); system/user prompts rewritten to demand a deliberate merge; EVOLVE-BLOCK guard line added. `prompts_diff.py`/`prompts_full.py` task text now defers to the system-message direction; EVOLVE-BLOCK constraints added. `prompts_fix.py`, `prompts_novelty.py` unchanged.

**`core/wrap_eval.py`** — captures `traceback.format_exc()` on eval exception, truncated head+tail to 8KB (`_ERROR_TRACEBACK_MAX_BYTES`, wrap_eval.py:14) into `correct.json`; honors an evaluator-returned `metrics["correct"] is False` as a domain failure; `verbose`/`SHINKA_EVAL_VERBOSE` parameter removed (banners always print).

**`launch/local.py` + `scheduler.py` + `slurm.py`** — new `kill_process_tree()` (psutil, kills the conda-shim *grandchild*; local.py:74) replaces `process.kill()` in monitor and `cancel_job`; subprocess pipes and log files pinned to UTF-8/`errors="replace"` (Windows cp1252 fix). Removed from base `JobConfig`: `eval_verbose` and `numeric_threads_per_job` (the latter survives on `LocalJobConfig` only — SLURM jobs lost numeric-thread capping and `_build_eval_env` export/`-e` injection).

**`edit/apply_diff.py`/`apply_full.py`** — EVOLVE marker regexes narrowed to `#`/`//` comments (Fortran `!`, HTML, Wolfram `(*` forms dropped); `validate_evolve_markers` post-checks removed from both patch paths.

**`utils/general.py`** — upstream's config-driven `truncate_log_tail` (stdout, tail-biased, `max_stdout_log_chars`) replaced by fixed `_truncate_log` on **stderr** only: 16KB head+tail (`_STDERR_LOG_MAX_BYTES`, general.py:17); `stdout_log` is now loaded untruncated (general.py:65-68).

**`env.py`** — `.env` discovery now walks *upward* (`_nearest_dotenv`) so git worktrees inherit the main repo's `.env`.

**`embed/client.py`/`embedding.py`** — openrouter/gemini/local embedding backends removed; Azure/OpenAI only; `_get_google_embeddings_and_cost` kept but explicitly dead (its docstring marks it a dead path — `resolve_embedding_backend` never returns `provider="google"`).

**`shinka/__init__.py`** — `__version__` regressed `"0.0.7"` → `"0.0.5"` (fork predates upstream's bump).

## 3. LOST-VS-REPLACED

| Upstream capability | Status |
|---|---|
| Async parallel evolution runner (`async_runner.py`, `runtime_slots.py`, oversubscription `proposal_target_*`) | **(b) Reimplemented, deliberately sequential** — `orchestrator/harness/run_window.py` ("driver is sequential (one candidate at a time)", run_window.py:15). Parallel eval concurrency is genuinely dropped. |
| `MetaSummarizer` 3-step meta + scratchpad | **(b)** `orchestrator/scripts/meta_summarize.py` + `island_brief.py` + the `meta_briefs` table — single call writing per-island directions. The rolling global scratchpad has no equivalent (orchestrator/ has no meta-scratchpad mechanism; its only "scratchpad" grep hit is `orchestrator/NOTES.md:3` describing NOTES.md itself). |
| LLM novelty judge (`novelty_judge.py`) | **(b/c)** embedding-cosine `orchestrator/scripts/novelty_check.py` (threshold 0.99, keep-the-better eviction; novelty_check.py:1-35). `prompts_novelty.py` messages are kept but nothing invokes them — LLM-as-judge is dropped. |
| Prompt evolution (`prompt_evolver.py`, `prompt_dbase.py`) | **(c) genuinely dropped** — no orchestrator equivalent (the orchestrator's "framework-audit" rewrites strategy files by hand instead). |
| CLI (`shinka/cli/`) / Hydra launch | **(b)** `orchestrator/harness/run_window.py --config …` |
| WebUI + plots | **(c) dropped** — only `shinka.utils.load_df` + the `shinka-inspect` skill remain for inspection. |
| Non-Azure providers (anthropic/bedrock/deepseek/gemini/openrouter/local/headless coding agents) | **(c) dropped by design** (CLAUDE.md: Azure-only). `pricing.csv` rows retained for cost lookup only (kwargs.py comment). |
| Bandit model selection (`AsymmetricUCB`) | **(a) still present** in `shinka/llm/prioritization.py`, wrapped by `orchestrator/scripts/select_llm.py` (select_llm.py:7-14). |
| Crossover patch type | **(a) kept, enhanced** (most-distant partner, prompts_cross.py:65-95). |
| Parent selection strategies / inspirations / island sampler | **(a) unchanged** (`database/parents.py` etc., byte-identical). |
| Complexity metrics | **(a) kept** (`database/complexity.py`), minus Go. |
| Query-level backoff/retry (`backoff` decorators, retry-after handling) | **(b)** moved to `orchestrator/scripts/_azure.py` background-poll loop (per constants.py:8-11); the sync `shinka.llm.query` path itself now has only `LLMClient`'s 3 retries (llm.py `MAX_RETRIES = 3`). |
| Marker validation, Wolfram/Fortran/Go language support, `eval_verbose`, SLURM numeric-thread caps, `max_stdout_log_chars` | **(c) genuinely dropped** (grep of orchestrator/ finds no equivalents; `max_stdout_log_chars` has zero hits repo-wide). |
| Model availability preflight (`model_availability.py`) | **(c) dropped**; nearest analog is `tests/smoke/check_azure.py` (manual smoke test). |

## 4. DEFAULT DRIFT (mechanism exists in both)

| Knob | Upstream | Fork | Where |
|---|---|---|---|
| `PromptSampler.use_text_feedback` | `False` | `True` | `shinka/core/sampler.py:37` |
| `default_patch_types()` | `["diff","full","cross"]` | `["diff","full","cross","fix"]` | `shinka/defaults.py:17` |
| `default_patch_type_probs()` | `[0.6, 0.3, 0.1]` | `[0.55, 0.3, 0.1, 0.05]` | `shinka/defaults.py:21` |
| LLM `TIMEOUT` | `1200` s | `3600` s | `shinka/llm/constants.py:3` |
| Temperature for Azure/OpenAI reasoning models | omitted from kwargs | always sent (`1.0` fixed-temp, else sampled) | `shinka/llm/kwargs.py` hunk `@@ -106` |
| Azure client addressing | `azure_endpoint` + trailing `/openai/v1/` | `base_url=…/openai/v1` (no trailing slash) | `shinka/llm/client.py:_build_azure_base_url` |
| Archive eviction | globally-worst / most-similar | island-aware victim, floor=3 per island (`archive_floor_per_island: int = 3` — new knob, non-zero default changes behavior out of the box) | `shinka/database/dbase.py:61` |
| Island fallback assignment | random island | least-populated island | `shinka/database/islands.py` hunks `@@ -102/-182` |
| Migration domain | `range(num_islands)` | actual `DISTINCT island_idx` set | `shinka/database/islands.py` hunk `@@ -225` |
| Eval verbosity | `eval_verbose=True`, env-gated | unconditional printing (knob removed) | `shinka/core/wrap_eval.py` |
| `__version__` | `0.0.7` | `0.0.5` | `shinka/__init__.py:5` |

Expected-but-absent notes: no `parents.py`/`inspirations.py`/`island_sampler.py` changes exist despite being named audit targets (they are identical to upstream); `EvolutionConfig` kept-field defaults show no drift — all drift there is field *removal*.

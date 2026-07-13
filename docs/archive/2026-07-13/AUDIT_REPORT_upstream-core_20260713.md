> **STATUS: APPLIED (2026-07-13).** Historical record of the three-track audit
> (upstream parity / SimpleTES / concurrency) and its implementation batch.
> Live guidance is CLAUDE.md + .claude/skills/shinka-orchestrator/SKILL.md.

# Upstream SakanaAI/ShinkaEvolve — Design Read (snapshot audit)

All paths relative to the upstream snapshot root (`scratchpad/upstream/`).

## 1. Run loop: fully async, multiple jobs in flight

**There is no `shinka/core/runner.py` / sync `EvolutionRunner` in this snapshot** — the runner is `ShinkaEvolveRunner` in `shinka/core/async_runner.py:223` ("Fully async evolution runner with concurrent proposal generation"), exported from `shinka/core/__init__.py:2`. It is **not** sequential one-proposal-one-eval.

`run_async()` (`async_runner.py:973`) spawns up to three concurrent asyncio tasks: `_job_monitor_task` (`:1973`), `_proposal_coordinator_task` (`:2352`), and — only when the meta summarizer is enabled (it is off by default, see §2) — `_meta_summarizer_task` (`:5358`, a 30s watchdog/logger only) (`:987-1000`), then waits on a `finalization_complete` event (`:1003`).

**Concurrency knobs are runner constructor args, not EvolutionConfig fields** (`async_runner.py:233-235`): `max_evaluation_jobs=4`, `max_proposal_jobs=6`, `max_db_workers=2`, backed by three `LogicalSlotPool`s (sampling/evaluation/postprocess, `:534-538`). The shipped hydra example `shinka/configs/evolution/small_budget.yaml:1-3` sets 1/2/2.

**Rolling-window semantics, no generational barrier.** Each loop iteration (≤5s cadence, `:2451`) the coordinator computes `pipeline_capacity = len(running_jobs) + len(active_proposal_tasks)` and fills to `pipeline_target` via `proposals_needed = min(target − capacity, remaining_work, generation_slots, max_proposal_jobs − active)` (`:2420-2430`). `pipeline_target` defaults to `max_evaluation_jobs`; with `enable_controlled_oversubscription=True` (default **False**, `config.py:50`) it becomes adaptive: `ceil(base × min(sampling_ewma/evaluation_ewma, ratio_cap=2.0))`, clamped by `proposal_buffer_max=2` and `max_proposal_jobs` (`:875-934`), EWMAs with `alpha=0.3` (`:845-873`).

**Generation numbers are submission indices**: `_start_proposals` assigns `generation = next_generation_to_submit` and increments atomically, hard-capped at `num_generations` (`:2490-2502`). **Parent selection uses a stale archive by design**: each proposal task calls `async_db.sample_with_fix_mode_async(...)` at proposal-creation time (`:2683`), which reads only programs already persisted to SQLite — results of in-flight evaluations are invisible, so up to `max_proposal_jobs` concurrent proposals sample parents from the same archive snapshot (asynchronous steady-state EA).

The job monitor batch-polls eval jobs (`scheduler.batch_check_status_async`, `:1991`), cancels hung jobs (`_is_job_hung`, `:5072`; cancellation at `:2044-2060`), and processes completions under a `processing_lock` (`:2089`). Budget enforcement is proactive: `committed_cost = actual + avg_proposal_cost × in-flight`; proposing stops when it exceeds `max_api_costs` (`:820-842`, `:2395-2415`). Stuck detection: 60s without progress, up to 3 recoveries (`:564-565`). Bandit state saved/restored (`:572-601`).

## 2. Defaults

**EvolutionConfig** (`shinka/core/config.py:20-71`, `shinka/defaults.py`):

| Field | Default |
|---|---|
| `task_sys_msg` | generic "expert optimization…" string (`defaults.py:7-10`) |
| `patch_types` / `patch_type_probs` | `["diff","full","cross"]` / `[0.6,0.3,0.1]` (`defaults.py:13-18`) |
| `num_generations` | 50 |
| `max_patch_resamples` / `max_patch_attempts` | 3 / 1 |
| `job_type` / `language` | `"local"` / `"python"` |
| `llm_models` | `["gpt-5-mini","gemini-3-flash-preview","gemini-3.1-pro-preview","gpt-5.4"]` (`defaults.py:21-27`) |
| `llm_dynamic_selection` | `"ucb"` → `AsymmetricUCB` (`async_runner.py:374-380`) |
| `llm_dynamic_selection_kwargs` | `{"cost_aware_coef": 0.5}` (`defaults.py:30-31`) |
| `llm_kwargs` | `{"temperatures":[0.0,0.5,1.0], "max_tokens":16384}` (`defaults.py:34-38`); one model (bandit-weighted when probs given, else uniform), temperature, and max-tokens value sampled per query (`llm/kwargs.py:90-140`) |
| `meta_rec_interval` | 10; but `meta_llm_models=None`, and the summarizer is built only if **both** are set (`async_runner.py:427`) → meta off by default |
| `meta_max_recommendations` / `sample_single_meta_rec` | 5 / True |
| `embedding_model` | `"text-embedding-3-small"` |
| `max_novelty_attempts` / `code_embed_sim_threshold` | 3 / 0.99 |
| `novelty_llm_models` | None → **entire novelty judge off** (`async_runner.py:453-472` sets `novelty_judge = None`); by default proposals are accepted with **no embedding-similarity gate either** (`async_runner.py:2840-2843`); embeddings are still computed and stored |
| `use_text_feedback` | **False** |
| `max_api_costs` | None (if `num_generations=None` it must be set; runs "indefinitely" `int(1e6)` gens, `async_runner.py:339-346`) |
| `inspiration_sort_order` | `"ascending"` (best-last for recency bias, `inspirations.py:288-300`) |
| oversubscription block | `enable_controlled_oversubscription=False`, `proposal_target_mode="adaptive"`, `min_samples=5`, `ratio_cap=2.0`, `buffer_max=2`, `hard_cap=None`, `ewma_alpha=0.3` |
| prompt evolution | `evolve_prompts=False`, prompt patch types `["diff","full"]` `[0.7,0.3]`, `prompt_archive_size=10`, `prompt_ucb_exploration_constant=1.0`, `prompt_epsilon=0.1`, `prompt_evo_top_k_programs=3`, `prompt_percentile_recompute_interval=20` |

**DatabaseConfig** (`shinka/database/dbase.py:52-104`): `num_islands=2`, `archive_size=40`, `max_stdout_log_chars=None`, `elite_selection_ratio=0.3`, `num_archive_inspirations=1`, `num_top_k_inspirations=1`, `migration_interval=10`, **`migration_rate=0.0`**, `island_elitism=True`, `enforce_island_separation=True`, `island_selection_strategy="uniform"`, `enable_dynamic_islands=False`, `stagnation_threshold=100`, `island_spawn_strategy="initial"`, `island_spawn_subtree_size=1`, `parent_selection_strategy="weighted"`, `exploitation_alpha=1.0`, `exploitation_ratio=0.2`, `parent_selection_lambda=10.0`, `num_beams=5`, `archive_selection_strategy="fitness"`, `archive_criteria={"combined_score":1.0}` (`defaults.py:49-50`).

**Job configs** (`shinka/launch/scheduler.py:57-118`): `JobConfig(eval_program_path="evaluate.py", extra_cmd_args={}, eval_verbose=True, numeric_threads_per_job=None)`; `LocalJobConfig` adds `time/conda_env/activate_script/python_executable` (all None); `SlurmDockerJobConfig(image="ubuntu:latest", partition="gpu", time="01:00:00", cpus=1, gpus=1, mem="8G")`; `SlurmCondaJobConfig` analogous.

**AsymmetricUCB defaults** (`shinka/llm/prioritization.py:297-314`): `exploration_coef=1.0`, `epsilon=0.2`, `auto_decay=0.95`, `shift_by_baseline=True`, `shift_by_parent=True`, `adaptive_scale=True`, `asymmetric_scaling=True`, `exponential_base=1.0`, `cost_aware_coef=0.0` (overridden to 0.5 by default evo kwargs), `cost_exploration_coef=0.1`, `cost_power=1.0`.

**Run-level early stopping does not exist** — termination is only `num_generations` or `max_api_costs`. `shinka/utils/eval_stop.py` is a different thing: per-evaluation trial-level early-stop statistics (`NoEarlyStop`, `BayesianEarlyStop`, `ConfidenceIntervalEarlyStop`, `HybridEarlyStop`, `eval_stop.py:65-364`).

## 3. Mechanisms

**Parent sampling** (`dbase.py:1192-1304`): sample island first — `island_sampler.sample_island(initialized_islands)` (`:1236`), uniform by default (`island_sampler.py:92-98`; "equal"/"proportional"/"weighted" variants exist). Then per-island strategy:
- **weighted (default)** (`parents.py:240-433`): over correct archive members of the island; `alpha_0 = median(scores)`, `scale = max(MAD, 1e-6)`, `s_i = sigmoid(λ·(alpha_i − alpha_0)/scale)` with λ=10 (`:380-391`), novelty bonus `h_i = 1/(1+children_count)` (`:394`), `w_i = s_i·h_i` normalized to probabilities (`:397-407`).
- **power_law** (`parents.py:105-195`): archive sorted by score desc, `P(rank i) ∝ (i+1)^−α`, α=`exploitation_alpha`=1.0 (`:35-42`). Note: `exploitation_ratio=0.2` ("chance to pick from archive", `dbase.py:90`) is only referenced in a `hasattr` guard (`parents.py:106`) — **no probabilistic archive-vs-population coin flip is implemented**; the archive is always tried first.
- **beam_search** (`parents.py:439+`), `num_beams=5`, with async state sync (`async_runner.py:2691-2700`).
- **Fix mode**: if an island has no correct programs, `sample_parent_with_fix_mode` returns an incorrect program + ancestors and the runner issues a fix patch instead (`parents.py:694-772`, `async_runner.py:2703-2719`).

**Migration** (`islands.py:213-427`): `ElitistMigrationStrategy` — every `migration_interval` gens, each island moves `max(1, ⌊size×migration_rate⌋)` randomly chosen **correct, generation>0** programs to a random other island; the single top-scoring program is protected when `island_elitism=True`; no-op if `num_islands<2` or `migration_rate≤0` (`:225`) — **so migration is dead with the shipped `migration_rate=0.0`**. Optional stagnation-based island spawning (`spawn_new_island`, `islands.py:945`) seeds from `"initial"`/`"best"`/`"archive_random"`.

**Inspirations** (`inspirations.py:36-171`): archive inspirations = best-overall program (if correct; same-island when `enforce_island_separation=True`) → `int(n×elite_selection_ratio)` top-score island archive members → random island archive members → global random only if separation off. Top-k = highest-scoring archive programs excluding parent + archive inspirations (`:143-171`). Combined and sorted ascending for the prompt (`sampler.py:56-60`). Crossover ("cross" patch) is filtered out when no inspirations exist (`sampler.py:91-110`).

**Novelty rejection** (`novelty_judge.py:26-172`, async variant `async_novelty_judge.py:62-186`; wired at `async_runner.py:2765-2843`): the judge is constructed **only when `novelty_llm_models` is set** (`async_runner.py:453-472`) — with the shipped default (None) every proposal is accepted without any similarity check (`:2840-2843`). When configured: embed the patched code; skip the check until all islands are initialized; compute cosine similarities against the parent's island; accept if `max_sim ≤ 0.99`; else consult the novelty LLM against the most-similar program (LLM error → straight reject) and resample parent+inspirations, up to 3 novelty attempts × 3 patch resamples in the runner's outer loops; final exhaustion rejects the proposal.

**Meta-recommendation** (`summarizer.py:22-227`): trigger = **count of newly persisted programs ≥ meta_rec_interval** (not generation modulo; `summarizer.py:76-92`), invoked from the post-persist side-effect path (`async_runner.py:4384-4396`). Three LLM steps: (1) per-program summaries, (2) a global-insights **scratchpad**, (3) ≤5 numbered recommendations (`:120-146`); summaries accumulate, recs archived to history. At mutation time one random recommendation is sampled when `sample_single_meta_rec=True` (`summarizer.py:456`, `async_runner.py:2579`) and injected into the system prompt before format instructions (`sampler.py:117-126`; skipped for "cross" patches). Final meta summary at shutdown (`async_runner.py:1067-1090`).

**LLM bandit** (`prioritization.py`): `AsymmetricUCB` (also `FixedSampler`, `ThompsonSampler`; `async_runner.py:365-387`). Select: one arm choice **per generation** (`:2668-2672`); posterior = normalized means + `c·√(2·ln t / n_i)`; unseen arms get uniform priority; ε=0.2 mass spread over non-winners; cost blend `scores = (1−k)·scores + k·(cost_ratio_norm)^cost_power` with optimistic cost LCB (`prioritization.py:601-653, 528-560`). Update on persist: `reward = child.combined_score` (None if incorrect → worst-imputed), `baseline = max(parent.combined_score, global_baseline)`; `r = max(reward − baseline, 0)` (asymmetric — only improvements score), accumulated in log-space via `log(exp(exponential_base·r) − 1)` additions (exponential scaling, base 1.0) with 0.95 decay (`:470-509`; caller `async_runner.py:4416-4430`). `update_submitted` counts in-flight pulls into `n = max(n_submitted, n_completed)` (`:383, 462-468`); `update_cost` books per-arm spend (`:511-526`). Baseline seeded from gen-0 score (0.0 if the initial program is incorrect; `async_runner.py:1753-1756`).

**Complexity/embeddings**: radon-based cyclomatic/Halstead/nesting metrics (`database/complexity.py:1-105`) fill `Program.complexity`; embeddings + PCA 2d/3d + KMeans cluster ids stored per program (`dbase.py:180-184`), recomputed periodically and force-recomputed at shutdown with 120s timeout (`dbase.py:2743`, `async_runner.py:1036-1038`).

## 4. Upstream surface an Azure-only single-machine fork could drop

- **CLI entry points** `shinka_launch` / `shinka_run` / `shinka_visualize` (`shinka/cli/`).
- **WebUI** genealogy-tree visualization (`shinka/webui/index.html`, `visualization.py`).
- **Hydra stack**: `shinka/launch_hydra.py`, `shinka/eval_hydra.py`, config tree `shinka/configs/{cluster,database,evolution,task,variant}`.
- **SLURM launchers** incl. Docker image submission (`shinka/launch/slurm.py`, `SlurmDockerJobConfig`).
- **Multi-provider LLM**: anthropic, bedrock, openai, azure_openai, deepseek, gemini, local_openai, headless CLI-agent backends (`shinka/llm/providers/`, `client.py:42-74`), plus top-level `shinka/google_genai.py`, `shinka/model_availability.py`, `shinka/local_openai_config.py`.
- **Meta-prompt evolution subsystem** (`evolve_prompts`, `core/prompt_evolver.py`, `database/prompt_dbase.py`, `prompts/prompts_prompt_evo.py`).
- **Trial-level eval early-stop stats** (`shinka/utils/eval_stop.py`).
- **Plots/utility extras**: `shinka/plots/`, `utils/wolfram.py`, `database/display.py` rich tables, `async_dbase.py` thread-pool DB wrapper.

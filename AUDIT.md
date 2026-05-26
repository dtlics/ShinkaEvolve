# AUDIT.md — ShinkaEvolve Structure (pre-refactor snapshot)

> **HISTORICAL — describes the codebase *before* the orchestrator rewrite +
> Azure-only prune.** Many files cited below (`async_runner.py`, `novelty_judge.py`,
> `deep_research_summarizer.py`, `prompt_evolver.py`, the agentic proposer,
> `shinka_run`, `webui/`, `plots/`, non-Azure providers) have since been
> **removed**. Kept as the design rationale behind the rewrite; for the current
> system see [orchestrator/SKILL.md](orchestrator/SKILL.md) +
> [orchestrator/NOTES.md](orchestrator/NOTES.md).

> Produced as step 1 of the Claude-Code-orchestrated rewrite (see
> `agent_rewrite_brief.md`). This document maps the *existing* codebase: every
> decision point, prompt-construction site, external LLM call, and database
> operation, with inputs/outputs and dependencies. `taxonomy.md` classifies
> these into the four mutability cells; this file is the raw map.
>
> Method: the orchestration control flow (`async_runner.py`), `config.py`,
> `defaults.py`, the entry-point API, and the task contract were read directly.
> The four large subsystems (database, llm/bandit, novelty/meta/DR/prompts,
> edit/eval/sampler/tools) were mapped by parallel read-only exploration agents
> and cross-checked against direct greps. Line numbers are accurate as of the
> `claude/jovial-einstein-407911` branch and may drift.

---

## 0. TL;DR — the three findings that shape the refactor

1. **The inner loop is an async producer/consumer pipeline, not a `for` loop.**
   `ShinkaEvolveRunner.run_async` ([async_runner.py:1150](shinka/core/async_runner.py)) runs three concurrent
   `asyncio` tasks — a proposal coordinator, a job monitor, and an optional meta
   summarizer — that talk through slot pools and an SQLite archive. "Run W
   iterations" therefore means "drive this pipeline until `completed_generations`
   advances by W," not "call a function W times."

2. **The canonical per-candidate order already exists and is single-sourced**
   inside `_generate_evolved_proposal` ([async_runner.py:3150](shinka/core/async_runner.py)):
   `select_llm → sample_parent(+inspirations) → construct_prompt → mutate →
   apply → embed → novelty_check → evaluate → archive_record → bandit_update`.
   The refactor's `harness/run_window.py` must reproduce this exact sequence.

3. **The EvoX meta-loop does not exist yet.** There is no window, no J-score, no
   normalized-improvement-rate, no `stagnation_detector`, no strategy database,
   no strategy rewrite/rollback. The closest analogs are (a) Shinka's per-island
   `is_stagnant()` → dynamic island spawn (generations since global best
   improved, not a window J-score), and (b) the existing **prompt-evolution**
   subsystem, which *is* a real meta-evolution mechanism (UCB over a prompt
   archive) but evolves only the task system prompt, not strategy code. These
   are seeds, not the thing itself.

---

## 1. Repository map

```
shinka/
  core/            evolution loop + meta + eval wrapper
    async_runner.py        (6962)  THE orchestrator: ShinkaEvolveRunner, all control flow
    config.py              (187)   EvolutionConfig — the evo knobs
    sampler.py             (335)   PromptSampler — mutation-prompt assembler
    runtime_slots.py       (50)    LogicalSlotPool — bounded async concurrency
    pipeline_timing.py     (137)   per-program timing telemetry
    wrap_eval.py           (525)   run_shinka_eval — the evaluation contract
    novelty_judge.py       (225)   embedding+LLM novelty rejection (sync)
    async_novelty_judge.py (287)   async variant
    summarizer.py          (796)   MetaSummarizer — 3-step meta recommendations
    async_summarizer.py    (453)   async variant
    deep_research_summarizer.py (766) 4-stage DR brief pipeline (A drift→B cache→C DR→D ground)
    prompt_evolver.py      (668)   SystemPromptEvolver — evolves the task system prompt
  database/         the archive
    dbase.py               (3110)  ProgramDatabase, DatabaseConfig, Program dataclass, schema, sample()
    async_dbase.py         (1284)  AsyncProgramDatabase — threadpool wrapper
    parents.py             (863)   parent-selection strategies (power_law/weighted/beam/...)
    islands.py             (1022)  island assignment / migration / dynamic spawn
    island_sampler.py      (253)   which island to sample (uniform/equal/proportional/weighted)
    inspirations.py        (382)   archive + top-k inspiration selection, context ordering
    complexity.py          (268)   per-program complexity metrics (radon/regex)
    prompt_dbase.py        (1332)  SystemPromptDatabase — prompt archive + UCB sampling
    display.py             (679)   Rich console tables (no logic)
  llm/              model calls + selection
    prioritization.py      (1470)  BanditBase, AsymmetricUCB, ThompsonSampler, FixedSampler
    llm.py                 (786)   LLMClient / AsyncLLMClient
    client.py              (184)   provider client factories (Azure base_url, etc.)
    query.py               (95)    provider dispatch
    kwargs.py              (175)   per-call kwargs sampling (reasoning effort, temp, max_tokens)
    constants.py           (1)     TIMEOUT=3600
    providers/             anthropic/openai/gemini/deepseek/local_openai + model_resolver + pricing + result
    agent/           the already-built agentic layer (openai-agents SDK)
      client.py            (864)   AgentLLMClient.run_agent
      background_model.py  (245)   BackgroundOpenAIResponsesModel (Azure long-call resilience)
      dr_client.py         (271)   separate Azure DR endpoint client
      hooks.py             (139)   ShinkaAgentHooks — tool telemetry
      tools/               apply_patch / evaluate / query_db / read_file / web_search + context + registry
  edit/             patch application
    apply_diff.py          (774)   SEARCH/REPLACE engine (EVOLVE-BLOCK aware)
    apply_full.py          (304)   full-file rewrite
    async_apply.py         (315)   async dispatch + syntax validation
    summary.py             (42)    diff stats
  embed/            embeddings for novelty/dedup/plots
  prompts/          all prompt templates (diff/full/cross/fix/meta/novelty/deep_research/literature/init/prompt_evo)
  launch/           job scheduling: local.py, scheduler.py (JobScheduler), slurm.py
  cli/              run.py (shinka_run Hydra entry), launch.py, models.py
  configs/          Hydra dataclass configs (evolution/database/cluster/task/variant)
  utils/            eval_stop.py, general.py (_load_results + 16KB truncation), load_df.py, languages.py
  plots/, webui/    visualization (out of scope)
examples/           circle_packing, game_2048, julia_prime_counting, novelty_generator
tasks/cnot_grid_synth/  the user's active task
```

Entry points: `shinka_run` → `cli/run.py` (Hydra) → `ShinkaEvolveRunner.run()` →
`run_async()`. Example tasks call the runner directly (`examples/*/run_evo.py`).

---

## 2. The three-layer model (current code → brief's layers)

The brief frames the target as three concentric layers. The current code already
contains all three, tangled together inside `async_runner.py`:

| Brief layer | What it is | Where it lives today |
|---|---|---|
| **Evaluation primitive** | run a candidate, return score + artifacts, no LLM | `run_shinka_eval` ([wrap_eval.py:128](shinka/core/wrap_eval.py)) executed as a subprocess by `JobScheduler` ([launch/scheduler.py:96](shinka/launch/scheduler.py)) |
| **Inner loop** | sample→mutate→eval→record under a fixed strategy, no agent | `_generate_evolved_proposal` + `_run_patch_async` + `_job_monitor_task` + `_persist_completed_job` (all in `async_runner.py`) |
| **Orchestrator** | window-level decisions, strategy rewrites, termination | **does not exist** — `run_async` only does fixed-budget + cost-limit + liveness recovery |

The refactor's job is to *cut these apart*: extract the inner-loop sequence into
`harness/run_window.py`, extract each decision policy into a `scripts/*.py`
subroutine, and put the orchestrator layer in the SKILL.md + Claude Code.

---

## 3. The canonical inner-loop control flow (most important section)

This is the exact sequence the harness must reproduce. From
`_generate_evolved_proposal` ([async_runner.py:3150](shinka/core/async_runner.py)) and its callees.

**Per generation (one candidate):**

1. **Select LLM (once).** `self.llm_selection.select_llm()` →
   `(model_sample_probs, model_posterior)`. ([async_runner.py:3185](shinka/core/async_runner.py)) The bandit
   (`AsymmetricUCB`) picks the arm; the choice is fixed for this candidate's
   retries.

2. **Novelty + resample loop** (`max_novelty_attempts`=3 outer ×
   `max_patch_resamples`=3 inner):

   a. **Sample parent + inspirations.**
   `async_db.sample_with_fix_mode_async(...)` →
   `(parent, archive_programs, top_k_programs, needs_fix)`.
   ([async_runner.py:3196](shinka/core/async_runner.py)) This single call does island selection →
   parent selection → archive-inspiration selection → top-k selection, and sets
   `needs_fix=True` when no correct programs exist on the island.

   b. **Mutate.** If `needs_fix`: `_run_fix_patch_async`. Else the proposer —
   `_run_agent_proposal` (if `use_agentic_proposer`) or `_run_patch_async`
   (default). ([async_runner.py:3241](shinka/core/async_runner.py)) The legacy proposer
   ([async_runner.py:4498](shinka/core/async_runner.py)):
      - `_get_current_system_prompt()` → possibly-evolved system prompt.
      - `prompt_sampler.sample(parent, archive_inspirations, top_k_inspirations, meta_recommendations)`
        → `(patch_sys, patch_msg, patch_type)`. **construct_mutation_prompt.** Patch
        type (diff/full/cross/literature_grounded) is sampled *inside* the sampler.
      - `self.llm.get_kwargs(model_sample_probs)` → resolves `model_name`.
      - `self.llm_selection.update_submitted(model_name)` — bandit "arm pulled."
      - loop `max_patch_attempts`: `self.llm.query(...)` → response; extract
        `<NAME>`/`<DESCRIPTION>`; `apply_patch_async(...)`. `update_cost(arm, cost)`.
      Returns `(code_diff, meta_patch_data, success)`.

   c. If patch failed → next resample. If it succeeded → break inner loop.

   d. **Embed.** `_get_code_embedding_async(exec_fname)` → `code_embedding`.
   ([async_runner.py:3294](shinka/core/async_runner.py))

   e. **Novelty check.** `novelty_judge.should_check_novelty_async(...)` then
   `assess_novelty_with_rejection_sampling_async(...)` →
   `(should_accept, novelty_metadata)`. ([async_runner.py:3308](shinka/core/async_runner.py)) If rejected
   → next novelty attempt (resample a different parent/patch). If accepted →
   break outer loop.

3. **Submit for evaluation.** `_submit_evaluation_job_with_slot(...)` (or
   `_inject_cached_evaluation_with_slot` when the agentic path already evaluated
   inline). ([async_runner.py:3404](shinka/core/async_runner.py)) Creates an `AsyncRunningJob`.

4. **(async) Evaluate.** `_job_monitor_task` ([async_runner.py:2466](shinka/core/async_runner.py)) polls
   the `JobScheduler`; the evaluator runs `evaluate.py` as a subprocess writing
   `metrics.json` + `correct.json`.

5. **Archive record + side effects.** `_persist_completed_job`
   ([async_runner.py:4967](shinka/core/async_runner.py)) reads results, builds a `Program`, calls
   `async_db.add_program_async(...)` (which assigns island, computes complexity,
   updates archive, schedules migration, may spawn island).

6. **Bandit update.** `self.llm_selection.update(arm, reward, baseline)` at
   [async_runner.py:5510](shinka/core/async_runner.py) (inside the persisted-program side effects),
   where `reward = combined_score` and `baseline = parent.combined_score`.

**Coordinator** (`_proposal_coordinator_task`, [async_runner.py:2865](shinka/core/async_runner.py)):
`while not should_stop`: compute pipeline target → start proposals to keep the
eval queue full → enforce `max_api_costs` via committed-cost estimate → stop
when `completed_generations >= num_generations`. Liveness recovery via
`_is_system_stuck`/`_handle_stuck_system` ([async_runner.py:6346](shinka/core/async_runner.py)) —
**pipeline stall detection, not search stagnation.**

---

## 4. Decision-point inventory

### 4.1 Orchestration / control (`async_runner.py`)

| Unit | line | Decision | In → Out |
|---|---|---|---|
| `_compute_proposal_pipeline_target` | 1052 | how many proposals to keep in flight (adaptive EWMA) | runtime state → int |
| `_proposal_coordinator_task` | 2865 | when to start proposals / stop on budget/cost | — |
| `_generate_proposal_async` | 3039 | mutation-type dispatch, meta-rec fetch | gen → job |
| `_generate_evolved_proposal` | 3150 | the full per-candidate sequence (§3) | gen → job |
| `_is_system_stuck`/`_handle_stuck_system` | 6346 | pipeline-liveness recovery | — → bool |
| `_meta_summarizer_task` | 6469 | meta-rec cadence (`meta_rec_interval`) | — |
| bandit glue | 3185, 3927, 4032, 4204, 4342, 4551, 4670, 5510 | `select_llm`/`update_submitted`/`update_cost`/`update` call sites | — |

These are orchestration glue. Most are **immutable plumbing** (slot pools,
budget math, liveness). The per-candidate *sequence* in `_generate_evolved_proposal`
is what gets re-homed into the harness.

### 4.2 Parent / island / inspiration sampling (`database/`)

Selected via `DatabaseConfig` strings; each is a swappable strategy class.

| Strategy family | classes | param | file |
|---|---|---|---|
| **Parent** (`parent_selection_strategy` ∈ weighted/power_law/beam_search/best_of_n/sequential) | `PowerLawSamplingStrategy`, `WeightedSamplingStrategy`, `BeamSearchSamplingStrategy`, `BestOfNSamplingStrategy`, `SequentialSamplingStrategy`, dispatched by `CombinedParentSelector` | `exploitation_alpha`, `parent_selection_lambda`, `num_beams`, `exploitation_ratio` | [parents.py:69–863](shinka/database/parents.py) |
| **Island select** (`island_selection_strategy` ∈ uniform/equal/proportional/weighted) | `UniformIslandSampler`, `EqualIslandSampler`, `ProportionalIslandSampler`, `WeightedIslandSampler` via `create_island_sampler` | temperature / fitness_weight / count_weight | [island_sampler.py:93–253](shinka/database/island_sampler.py) |
| **Island assign/migrate/spawn** | `DefaultIslandAssignmentStrategy`, `CopyInitialProgramIslandStrategy`, `ElitistMigrationStrategy`, `CombinedIslandManager` | `migration_interval`, `migration_rate`, `island_elitism`, `enable_dynamic_islands`, `stagnation_threshold`, `island_spawn_strategy` | [islands.py:51–1022](shinka/database/islands.py) |
| **Inspiration select** | `ArchiveInspirationSelector`, `TopKInspirationSelector` via `CombinedContextSelector`; ordering by `InspirationContextBuilder` | `elite_selection_ratio`, `num_archive_inspirations`, `num_top_k_inspirations`, `enforce_island_separation`, `inspiration_sort_order` | [inspirations.py:36–382](shinka/database/inspirations.py) |

Entry: `ProgramDatabase.sample` / `sample_with_fix_mode` ([dbase.py:1257/1372](shinka/database/dbase.py))
compose island→parent→inspiration in order. **All policy; all evolvable.**

### 4.3 Archive management (`dbase.py`)

| Unit | line | Decision |
|---|---|---|
| `_update_archive` | 2256 | which strategy (fitness vs crowding) when archive full (`archive_selection_strategy`) |
| `_update_archive_fitness` | 2300 | replace worst by `archive_criteria` weighted score |
| `_update_archive_crowding` | 2354 | replace most-similar (embedding cosine) if better — diversity niching |
| `_update_best_program` | 2401 | track global best; feeds stagnation counter |
| `is_stagnant` / `check_and_spawn_island_if_stagnant` | 2499/2516 | **gens since best improved ≥ `stagnation_threshold` → spawn island** (Shinka's only "stagnation") |
| `analyze_code_metrics` | [complexity.py:230](shinka/database/complexity.py) | complexity score (feeds `archive_criteria` if weighted) |

### 4.4 LLM-selection bandit (`prioritization.py`)

The "select_llm" target. `AsymmetricUCB` is the default (`llm_dynamic_selection="ucb"`).

| Unit | line | Role |
|---|---|---|
| `BanditBase` | 70 | arm indexing, pickle state, posterior contract — **plumbing** |
| `select_llm` | 170 | sample one arm from `posterior()` → one-hot + probs — **policy** |
| `AsymmetricUCB.posterior` | 601 | UCB1 (mean + `c·√(2ln t / n)`) + optional cost blending + ε-greedy — **policy** |
| `AsymmetricUCB.update` | 470 | reward = `combined_score − baseline` (baseline = parent score), clamp asymmetric, accumulate, optional decay — **policy** (reward def) |
| `update_submitted` / `update_cost` | 462 / 511 | submission + cost bookkeeping — **plumbing** |
| `_normalized_means` / `_normalized_cost_ratio` | 445 / 528 | scaling math — **plumbing** |
| `get_state`/`set_state`/`save_state`/`load_state` | 914 | pickle persistence across runs — **plumbing** |
| `ThompsonSampler`, `FixedSampler` | 1106 / 950 | alternative algorithms — **policy** |

Reward signal is *implicit and hardcoded* in `async_runner` (combined_score vs
parent). Extracting it is a refactor opportunity but changes behavior, so leave
as-is unless asked.

### 4.5 Mutation-prompt construction (`sampler.py` + `prompts/`)

| Unit | line | Role |
|---|---|---|
| `PromptSampler.sample` | [sampler.py:79](shinka/core/sampler.py) | assemble `(sys, user, patch_type)` from parent+inspirations+meta+brief; **samples patch type**; suppresses cross when no inspirations, literature_grounded when no confirmed snippet — **policy, the construct_mutation_prompt target** |
| `PromptSampler.sample_fix` | 261 | fix-mode prompt from incorrect program + ancestors — **policy** |
| `PromptSampler.initial_program_prompt` | 64 | gen-0 prompt — plumbing |

Templates (all editable text = policy): `prompts_diff` (SEARCH/REPLACE),
`prompts_full`, `prompts_cross`, `prompts_fix`, `prompts_literature`,
`prompts_init`, `prompts_base` (shared `perf_str`, `construct_eval_history_msg`).

### 4.6 Mutation operator: LLM call + patch application

| Unit | line | Role |
|---|---|---|
| `LLMClient.query` / `AsyncLLMClient.query` | [llm.py:~294/566](shinka/llm/llm.py) | one mutation LLM round-trip → `QueryResult` — **plumbing (wraps the call)** |
| `sample_model_kwargs` | [kwargs.py:63](shinka/llm/kwargs.py) | per-provider reasoning/temp/max_tokens shaping — plumbing |
| `query` / `query_async` dispatch | [query.py:22/60](shinka/llm/query.py) | model→provider routing — plumbing |
| `apply_search_replace` / `apply_diff_patch` | [apply_diff.py:585/687](shinka/edit/apply_diff.py) | SEARCH/REPLACE within EVOLVE-BLOCKs → `(code, num_applied)` — **plumbing** |
| `apply_full_patch` | [apply_full.py:11](shinka/edit/apply_full.py) | full rewrite w/ marker heuristics — plumbing |
| `apply_patch_async` | [async_apply.py:46](shinka/edit/async_apply.py) | dispatch by patch_type — plumbing |
| `AgentLLMClient.run_agent` | [agent/client.py](shinka/llm/agent/client.py) | agentic proposer loop (apply_patch auto-evals) — **plumbing (the call); prompt = policy** |

### 4.7 Novelty rejection (`novelty_judge.py` + `embed/`)

| Unit | line | Role |
|---|---|---|
| `assess_novelty_with_rejection_sampling` | [novelty_judge.py:60](shinka/core/novelty_judge.py) | embedding cosine vs island; if `≥ code_embed_sim_threshold` (0.99) optional LLM judge; reject → resample — **policy + embedded LLM** |
| `check_llm_novelty` | 174 | LLM-as-judge NOVEL/NOT_NOVEL (`prompts_novelty`) — **embedded LLM, prompt=policy** |
| `EmbeddingClient.get_embedding` | [embed/embedding.py:119](shinka/embed/embedding.py) | embed code → vector + cost — **plumbing** |

Config: `max_novelty_attempts=3`, `code_embed_sim_threshold=0.99`,
`novelty_llm_models` (LLM judge only fires if set).

### 4.8 Meta recommendations (`summarizer.py`)

3-step LLM cycle on cadence `meta_rec_interval` (10 programs). All **embedded
LLM, prompts = policy**, the orchestration = plumbing.

| Step | line | LLM call | prompt |
|---|---|---|---|
| 1 individual summaries | [summarizer.py:267](shinka/core/summarizer.py) | `meta_llm_client.batch_kwargs_query` | `META_STEP1_*` |
| 2 global scratchpad | 341 | `meta_llm_client.query` | `META_STEP2_*` |
| 3 recommendations (≤`meta_max_recommendations`=5) | 379 | `meta_llm_client.query` | `META_STEP3_*` |

Output injected into mutation prompts via the sampler's recommendations slot;
`get_sampled_recommendation()` picks one per mutation.

### 4.9 Deep research (`deep_research_summarizer.py`) — 4 stages

Per-island, cadence `dr_meta_interval` (20). Entry: `update_async(generation, island_indices)`
→ `{island_idx: DRBrief}`. **Wraps a paid external service (immutable).**

| Stage | line | Does | LLM call |
|---|---|---|---|
| A drift judge | 657 | score population drift 0–1, emit candidate_question; gates C/D at `dr_drift_threshold`=0.5 | cheap model (`dr_stage_a_llm_model`) |
| B novelty cache | 525 | embed question, cosine vs `dr_brief_cache` ≥ `dr_brief_cache_threshold`=0.95 → reuse | none |
| C deep research | 701 | `o3-deep-research` via separate Azure DR endpoint → BriefItems | `dr_model` via `dr_client` |
| D code grounding | 721 | per-item `web_search` confirm → set `BriefItem.confirmed` | agent + web_search |

`BriefItem{idea, rationale, reference_source, reference_snippet, gotchas, confirmed}`;
`DRBrief{island_idx, items, candidate_question, drift_score, source, cost, stage_a_cost}`.
Cost summed into `total_api_cost` (doom Fix 2). Tables: `meta_briefs`, `dr_brief_cache`.

### 4.10 Prompt evolution (`prompt_evolver.py` + `prompt_dbase.py`) — existing meta-evolution

Opt-in (`evolve_prompts`). **This is the closest existing thing to the brief's
strategy evolution** — but it evolves the *task system prompt*, not strategy code.

| Unit | line | Role |
|---|---|---|
| `SystemPromptEvolver.evolve` | [prompt_evolver.py:213](shinka/core/prompt_evolver.py) | sample diff/full → LLM mutate the system prompt → store in `SystemPromptDatabase` — embedded LLM |
| `SystemPromptSampler` (UCB+ε) | 100 | choose which prompt to use next, fitness = avg percentile of its programs | policy |
| `SystemPromptDatabase.sample` | [prompt_dbase.py:690](shinka/database/prompt_dbase.py) | UCB1 + ε-greedy over prompt archive | policy |
| `SystemPromptDatabase.update_fitness` | 813 | percentile-based fitness from program scores | policy |

The orchestrator's strategy-rewrite protocol is conceptually a generalization of
this (UCB-sampled archive of evolved artifacts with fitness from downstream
programs), which is encouraging for feasibility.

### 4.11 Evaluation primitive (`wrap_eval.py` + `launch/scheduler.py` + `eval_stop.py`)

The innermost layer. **Immutable plumbing.**

| Unit | line | Contract |
|---|---|---|
| `run_shinka_eval` | [wrap_eval.py:128](shinka/core/wrap_eval.py) | `(program_path, results_dir, experiment_fn_name, num_runs, get_experiment_kwargs, aggregate_metrics_fn, validate_fn, plotting_fn, run_workers, early_stop_*)` → `(metrics, correct, first_error)`; writes `metrics.json` + `correct.json` |
| `save_json_results` | 104 | `correct.json={correct,error,error_traceback(8KB)}`, `metrics.json=full` |
| `JobScheduler.run`/`submit_async`/`get_job_results` | [scheduler.py:204/266/366](shinka/launch/scheduler.py) | run `evaluate.py` as subprocess, collect results dir |
| `LocalJobConfig` | [scheduler.py:47](shinka/launch/scheduler.py) | `eval_program_path`, `time` |
| early-stop strategies | [eval_stop.py:82–404](shinka/utils/eval_stop.py) | Bayesian / CI / Hybrid (sequential only) |
| `_load_results` + `_truncate_log` (16KB head+tail) | [utils/general.py:52/21](shinka/utils/general.py) | load artifacts; cap stderr |

`metrics` must contain `combined_score: float` (primary fitness) plus
`public`/`private` dicts; `correct: bool`; `first_error: Optional[str]`.

---

## 5. External LLM call sites (every one)

| # | Site | Client | Prompt | Purpose | Mutability of prompt |
|---|---|---|---|---|---|
| 1 | mutation proposer | `self.llm.query` / `AgentLLMClient.run_agent` | diff/full/cross/lit templates via `PromptSampler` | generate candidate | **mutable** |
| 2 | fix proposer | `_run_fix_patch_async` | `prompts_fix` | repair broken candidate | mutable |
| 3 | novelty judge | `novelty_llm_client.query` | `prompts_novelty` | NOVEL/NOT_NOVEL | mutable |
| 4 | meta step 1/2/3 | `meta_llm_client` | `prompts_meta` | recommendations | mutable |
| 5 | DR stage A | `stage_a_judge` | `DR_STAGE_A_*` | drift score | mutable |
| 6 | DR stage C | `dr_client` (`o3-deep-research`) | `DR_STAGE_C_*` | external research | **immutable (paid service)** |
| 7 | DR stage D | agent + web_search | `DR_STAGE_D_*` | ground references | immutable-ish |
| 8 | prompt evolve | `llm_client.query` | `prompts_prompt_evo` | evolve system prompt | mutable |
| 9 | initial program (if no seed) | `self.llm.query` | `prompts_init` | bootstrap | plumbing |
| 10 | embeddings | `EmbeddingClient` | — | novelty/dedup | plumbing |

Two Azure resources: main endpoint (1–5,8,9) and DR endpoint (6, partly 7). See
CLAUDE.md.

---

## 6. Database operations + schema

**Writes:** `add` / `add_program_async` ([dbase.py:845](shinka/database/dbase.py)) inserts a `programs`
row, bumps parent `children_count`, then `run_post_add_maintenance` (archive
update, best-program update, island copy, migration schedule, dynamic spawn,
embedding recompute). `_update_archive*` mutate the `archive` table.
`persist_brief`/`cache_brief` write `meta_briefs`/`dr_brief_cache`.
`SystemPromptDatabase.add` writes `system_prompts`/`prompt_archive`.

**Reads:** `get(id)`, `get_ancestry`, `get_best_program`, `get_all_programs`,
`sample`/`sample_with_fix_mode`, `lookup_cached_brief`. The agent's
`query_evolution_db` tool ([tools/query_db.py](shinka/llm/agent/tools/query_db.py)) exposes read-only
`top_n_by_score`/`recent_failures`/`lineage_of`/`by_generation`.

**Tables:** `programs` (23 cols), `archive`, `metadata_store` (best_program_id,
best_score_ever, last_iteration, beam_search_parent_id), `generation_event_log`,
`attempt_log`, `meta_briefs`, `dr_brief_cache`; separate prompts DB:
`system_prompts`, `prompt_archive`, `prompt_metadata_store`.

All DB ops are **immutable plumbing** (correctness-critical). `AsyncProgramDatabase`
is a threadpool wrapper over a single sync connection.

---

## 7. Data model

**`Program`** ([dbase.py:145](shinka/database/dbase.py)): `id, code, language, parent_id,
archive_inspiration_ids, top_k_inspiration_ids, island_idx, generation,
timestamp, code_diff, combined_score, public_metrics, private_metrics,
text_feedback, correct, error_traceback, children_count, complexity, embedding,
embedding_pca_2d/3d, embedding_cluster_id, migration_history, metadata,
in_archive, system_prompt_id`.

**`DatabaseConfig`** ([dbase.py:53](shinka/database/dbase.py)): `num_islands=2, archive_size=40,
elite_selection_ratio=0.3, num_archive_inspirations=1, num_top_k_inspirations=1,
migration_interval=10, migration_rate=0.0, island_elitism, enforce_island_separation,
island_selection_strategy="uniform", enable_dynamic_islands=False,
stagnation_threshold=100, island_spawn_strategy, parent_selection_strategy,
exploitation_alpha=1.0, exploitation_ratio=0.2, parent_selection_lambda=10.0,
num_beams=5, archive_selection_strategy="fitness", archive_criteria={combined_score:1.0}`.

**`EvolutionConfig`** ([config.py:19](shinka/core/config.py)) key knobs: `patch_types=[diff,full,cross]`
`patch_type_probs=[0.6,0.3,0.1]`, `num_generations=50`, `max_patch_resamples=3`,
`max_patch_attempts=1`, `llm_models`, `llm_dynamic_selection="ucb"`,
`llm_dynamic_selection_kwargs={cost_aware_coef:0.5}`, `meta_rec_interval=10`,
`max_novelty_attempts=3`, `code_embed_sim_threshold=0.99`,
`use_agentic_proposer=False`, `agentic_tools=[apply_patch]`,
`enable_deep_research=False`, `dr_meta_interval=20`, `enable_literature_grounded`,
`evolve_prompts=False`, `max_api_costs`.

---

## 8. Entry points & invocation API

```python
from shinka.core import ShinkaEvolveRunner, EvolutionConfig, run_shinka_eval
from shinka.database import DatabaseConfig
from shinka.launch import LocalJobConfig

runner = ShinkaEvolveRunner(
    evo_config=EvolutionConfig(**cfg["evo_config"]),
    job_config=LocalJobConfig(eval_program_path="evaluate.py", time="00:05:00"),
    db_config=DatabaseConfig(**cfg["db_config"]),
    max_evaluation_jobs=..., max_proposal_jobs=..., max_db_workers=...,
    verbose=True,
)
runner.run()                       # → run_async()
```

Archive lands at `{results_dir}/programs.sqlite`; per-gen dirs `gen_N/` hold
`main.<ext>` + `results/{metrics.json,correct.json}`. Resume is automatic when
`programs.sqlite` exists with `last_iteration > 0`. Task contract: an
`evaluate.py` calling `run_shinka_eval(...)` and an `initial.<ext>` with an
`EVOLVE-BLOCK-START/END` region (see `examples/circle_packing`).

---

## 9. Gap analysis — EvoX meta-loop vs what exists

| EvoX/brief concept | Exists? | Closest current code | Refactor action |
|---|---|---|---|
| Window of W iterations | No | `num_generations` budget | harness runs the pipeline for W gens, returns diagnostics |
| J-score (normalized improvement rate) | No | — | new `stagnation_detector.py` computes it from archive |
| Stagnation flag (J < τ for 2 windows) | No | `is_stagnant` = gens-since-best ≥ thr (per-island spawn) | new; can borrow the gens-since-best idea |
| Strategy = swappable code file | Partial | strategy *classes* selected by config string | expose as `scripts/*.py` with stable entry points |
| Strategy database/history | Partial | prompt archive (`SystemPromptDatabase`) | new `strategy_history/` (hash dirs + index.json) |
| Strategy rewrite by LLM | Partial | `SystemPromptEvolver` (prompts only) | orchestrator (Claude) writes a new `scripts/*.py` |
| Validate(S′) before deploy | No | — | new `harness/validate_strategy.py` (parse + smoke) |
| Rollback on J regression | No | — | orchestrator restores prior hash from `strategy_history/` |
| Per-window diagnostics JSON | Partial | `display.py` tables, `pipeline_timing` | new `diagnostics.py` emits the brief's JSON shape |
| Deep research at onset + stuck | Yes (cadence-based) | `DeepResearchSummarizer` | re-expose as `scripts/deep_research.py`; orchestrator calls it demand-driven |
| Agent in the per-candidate loop | Yes (the wrong place per brief) | `_run_agent_proposal` | the brief wants agency at the *window* level, not per candidate — keep `_run_patch_async` as harness default |

**Important**: the brief says the agent must NOT be in the per-candidate path
because of the ~100× cost asymmetry. The current `use_agentic_proposer=True`
path puts an agent inside each generation. For the new harness, the **legacy
`_run_patch_async` path is the right default** (stateless API mutation per
candidate); the agentic proposer becomes an *optional* mutation operator, not the
heartbeat.

---

## 10. Bridge to `taxonomy.md`

Provisional four-cell placement (justified in `taxonomy.md`):

- **pure-code, mutable (strategy targets):** parent/island/inspiration sampling
  (database strategy classes), archive selection, novelty thresholding logic,
  bandit `select_llm`/`posterior`/reward, stagnation/J-score (new), island policy.
- **pure-code, immutable (plumbing):** archive DB ops + schema, async wrappers,
  slot pools, patch application engines, embedding client, cost math, the
  evaluator subprocess + `run_shinka_eval`, diagnostics emission, scheduler.
- **LLM-embedded, mutable (prompt/template editable):** `PromptSampler` /
  mutation templates, novelty-judge prompt, meta prompts, DR stage A prompt,
  prompt-evolver prompts → the `mutate.py` + `construct_mutation_prompt.py` pair.
- **LLM-embedded, immutable (paid service wrapper):** `deep_research.py` (DR
  stage C via `dr_client` / `o3-deep-research`).

The single biggest structural fact: **everything the harness needs already runs
end-to-end inside `_generate_evolved_proposal`**. The refactor is about *cutting*
that monolith into composable, separately-callable subroutines with JSON
contracts — not rewriting any algorithm.

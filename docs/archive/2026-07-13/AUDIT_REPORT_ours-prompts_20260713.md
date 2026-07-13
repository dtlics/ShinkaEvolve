> **STATUS: APPLIED (2026-07-13).** Historical record of the three-track audit
> (upstream parity / SimpleTES / concurrency) and its implementation batch.
> Live guidance is CLAUDE.md + .claude/skills/shinka-orchestrator/SKILL.md.

# Mutation-prompt construction audit — ShinkaEvolve fork (orchestrator inner loop)

## 1. Prompt anatomy

The prompt is built by `orchestrator/scripts/construct_mutation_prompt.py`, which delegates template-filling to the fork's `shinka.core.PromptSampler` (`construct_mutation_prompt.py:82-89` → `shinka/core/sampler.py:30`). One call = one single-turn (system, user) pair; `mutate.py` sends it via `_azure.bg_query(model_name, patch_sys, patch_msg, ...)` with no message history (`orchestrator/scripts/mutate.py:242-245`).

**System message, in order** (`sampler.py:90-176`, then appendices in `construct_mutation_prompt.py:127-176`):

1. **`task_sys_msg`** verbatim (orchestrator-authored at boot; `BASE_SYSTEM_MSG` fallback, `sampler.py:90-93`, `prompts_base.py:5-10`). Flows from run config `task.task_sys_msg` (`run_window.py:739`); boot refuses the `__UNSET_AUTHOR_AT_BOOT__` sentinel (`run_window.py:78, 1258-1271`).
2. **`# Direction for this attempt`** — directive framing ("the assigned goal … not an optional suggestion", `sampler.py:136-147`). Source precedence: (a) a per-gen direction sampled from the island's *structured* meta brief (`sample_parent.py:333-347`, threaded at `run_window.py:703-705`); (b) the island's headline brief content (`run_window.py:706-715`); (c) the constant no-brief placeholder from `_compose_meta_for_gen` ("No explicit direction yet — follow the rest of the prompt…", `run_window.py:371-383`). `island_brief` *replaces* the global direction by default; `brief_compose_mode:"augment"` prepends it instead (`construct_mutation_prompt.py:106-115`, `sampler.py:100-101`). Cross gens get **no** direction header (`skip_meta_rec_for={"cross"}`, `sampler.py:134`). Note: because the harness always passes the non-None placeholder, `EXPERT_CREATIVE_PREAMBLE` (`prompts_base.py:15-20`, gated at `sampler.py:148-149`) is **dead code on the harness path** — a direction header is always present for diff/full.
3. **`# Known failure modes to avoid`** + `failure_note` — always rendered when set, independent of patch type/brief (`sampler.py:155-161`); comes from `evo.meta_failure_note`, persisted from meta rounds (`run_window.py:735, 1571-1573`).
4. **Patch-format instructions**: `DIFF_SYS_FORMAT` (SEARCH/REPLACE blocks, EVOLVE-BLOCK-only edits, `prompts_diff.py:1-44`); or one of **5 full-rewrite variants sampled uniformly at random** (`sampler.py:166-170`, `FULL_SYS_FORMATS` = default / different-algorithm / context-motivated / structural / parametric, `prompts_full.py:147-153`); or `CROSS_SYS_FORMAT` (`prompts_cross.py:8-36`). All demand `<NAME>`/`<DESCRIPTION>` + `<DIFF>` or `<CODE>` (parsed at `mutate.py:265-266`).
5. **`# Additional guidance`** — `evo.extra_guidance`, an orchestrator rewrite lever appended verbatim (`construct_mutation_prompt.py:127-131`).
6. **`# Runtime budget`** caution — conditional: rendered only if the parent, any inspiration, or (immediate-fix) the just-failed candidate `timed_out` or had `runtime_sec ≥ slow_caution_frac (0.8) × eval_budget_sec` (`construct_mutation_prompt.py:140-176`).

**User message** = `eval_history_msg + "\n" + iter_msg` (`sampler.py:237-240`):

1. **Inspiration/eval-history block** (only if inspirations exist): header "prior programs … EVAL HISTORY for quick reference only — NOT inspirations to copy or combine" (`prompts_base.py:90-110`); on cross gens softened to plain background (`for_cross`, `prompts_base.py:79-85`). Per program: **full code** in a fence, `perf_str` (combined score + one line per public metric, `prompts_base.py:23-34`), and its **text feedback** (always on: `use_text_feedback=True` hardcoded, `construct_mutation_prompt.py:87`). Programs are deduped and sorted **ascending by score (worst first, best last)** — default `inspiration_sort_order="ascending"` (`construct_mutation_prompt.py:88`, `InspirationContextBuilder`, `shinka/database/inspirations.py:288-382`).
2. **`# Current program`** — parent's full code, then the metric slot = `objective_section(objective_brief)` (`# What we are optimizing` header, `prompts_base.py:37-44`) + `perf_str(parent)` + parent `text_feedback` (`sampler.py:199-231`; `DIFF_ITER_MSG`/`FULL_ITER_MSG`/`CROSS_ITER_MSG` at `prompts_diff.py:47-72`, `prompts_full.py:164-183`, `prompts_cross.py:39-62`).
3. **`# Instructions` / `# Task`** — mode-specific closing instructions inside the ITER templates.
4. **Cross only**: `# Crossover partner program` appended — ONE partner chosen from all inspirations as the **embedding-most-distant from the parent** (lowest cosine), fallback uniform random (`get_cross_component`/`_most_distant`, `prompts_cross.py:65-118`); embeddings are fetched only for cross gens (`run_window.py:633, 657`).

**Patch-mode choice**: `run_window` samples the mode *before* the parent via `_sample_patch_mode` (`run_window.py:353-368, 592`) over `evo.patch_types`/`patch_type_probs`, defaults `["diff","full","cross","fix"]` / `[0.55, 0.3, 0.1, 0.05]` (`configs/orchestrator_run.default.json:16-17`, `shinka/defaults.py:13-21`). A "fix" draw pairs with an errored parent; if none exists it re-draws excluding fix (`run_window.py:615-623`). The mode is forced into the sampler (`forced_patch_type`, `run_window.py:741`; `sampler.py:107-113`), with cross→full downgrade when there are zero inspirations.

## 2. Context selection

- **Parent**: `sample_parent.py` ports `WeightedSamplingStrategy`: uniform island draw (default `island_selection_strategy="uniform"`; "proportional"/"weighted" available, `sample_parent.py:118-148`), same-island pool (`enforce_island_separation` default True, `sample_parent.py:292-298`), weight = `sigmoid(λ·(score−median)/MAD) × 1/(1+children_count)` with `parent_selection_lambda=10.0` (`sample_parent.py:101-115, 309`; `orchestrator_run.default.json:13`).
- **Inspirations** (`sample_parent.py:327-367`): if the island has a structured meta brief, ONE direction is weight-sampled and its `assigned_program_ids` become the exemplars — first `num_top_k_inspirations` as top-k, next `num_archive_inspirations` as archive inspirations. Pre-brief default `"top"`: score-ranked top-k excluding parent, then the next-ranked "elites" as archive inspirations; `"random"` draws uniformly instead (`prebrief_inspiration_mode`, `run_window.py:587`).
- **Counts**: `DatabaseConfig` defaults `num_archive_inspirations=1`, `num_top_k_inspirations=1` (`shinka/database/dbase.py:65-66`; not overridden in `orchestrator_run.default.json:13`) → **at most 2 inspiration programs per prompt** (plus the cross partner drawn from that same set).
- **Absences**: the shinka-native selectors (`ArchiveInspirationSelector` with best-program slot + `elite_selection_ratio=0.3` + random fill, `TopKInspirationSelector`; `shinka/database/inspirations.py:36-242`) are **not** on the orchestrator path — `sample_parent.py` implements its own policy in-script for rewritability. There is **no explicit global-best or cross-island-best slot** in the mutation prompt; with island separation on, context never leaves the parent's island. No embedding/novelty numbers ever appear in the prompt (novelty is a post-hoc gate, `run_window.py:893-912`).

## 3. Truncation / budget

There is **no prompt-size budgeter**: parent code, inspiration code, text_feedback, briefs, and objective_brief go in **untruncated** (no caps anywhere in `sampler.py`/`prompts_base.py`/`construct_mutation_prompt.py`). Bounded items are error text only, capped at the source before ever reaching a prompt:

- `error_traceback`: **8 KB head+tail** at eval time (`_ERROR_TRACEBACK_MAX_BYTES = 8*1024`, `shinka/core/wrap_eval.py:14, 88-101, 116`).
- `stderr_log`: **16 KB head+tail** at `load_results` (`shinka/utils/general.py:17-31`).
- eval's synthesized timeout/crash message embeds only a **1000-char stderr tail** (`orchestrator/scripts/evaluate.py:160-165`).
- Fix-attempt error-history entries: **2 KB head+tail each** (`_head_tail_trunc`, `run_window.py:386-394`); the combined history is re-capped at **8192 chars by dropping oldest entries** at archive time (`run_window.py:1172-1182`); repair records re-truncate the combined traceback to **~8000 chars** keeping first+latest (`shinka/database/dbase.py:852-855`).

Output side: per-call `max_output_tokens` defaults to **200,000** with per-model overrides (`orchestrator/scripts/_azure.py:47-54`) — the ~$10/call cap. Apply-retries are bounded at `max_attempts=3` (`mutate.py:215`, `run_window.py:832`).

## 4. Fix prompt

Both fix paths route through the `needs_fix` branch (`construct_mutation_prompt.py:91-101`) → `sampler.sample_fix` (`sampler.py:243-332`); reply is **full code** (`FIX_SYS_FORMAT`, `prompts_fix.py:4-28`) routed to the full-patch applier (`mutate.py:95-99`).

**System**: `task_sys_msg` + `FIX_SYS_FORMAT` ("make it correct first, performance secondary") + objective_brief section (fix's ITER template has no metric slot, so it moves to system, `sampler.py:274-277`) + failure_note (`sampler.py:281-284`) + the runtime caution (applies to both branches, `construct_mutation_prompt.py:139-176`). **No** direction header, island brief, or patch-format-diff.

**User**: optional ancestor block, then `FIX_ITER_MSG` — the incorrect code, its text_feedback, and `format_error_output_section` (stdout/stderr fences from `metadata.stdout_log`/`stderr_log`, `prompts_fix.py:30-75`, `sampler.py:303-311`).

Differences by path:
- **Sampled repair gen** (5% fix mode / errfrac latch): parent = most-recent errored program under `repair_attempt_cap=2` (`sample_parent.py:207-226`); **no inspirations at all** — `ancestor_inspirations` forced `[]` (`run_window.py:729`); its `stderr_log` is backfilled from `error_traceback`/`text_feedback` (`run_window.py:679-686`).
- **Immediate in-slot fix** (`fix_retry_budget` default 1, `run_window.py:927-944`): the just-failed candidate is wrapped as the incorrect program with `stderr_log = error_traceback || text_feedback || stderr_log` and live `parent_runtime_sec`/`parent_timed_out` (`run_window.py:461-490`); `ancestor_inspirations=[sampled parent]` (`run_window.py:477`). **Framing bug worth noting**: `sample_fix` renders ancestors with `correct=False` framing — "error outputs of … incorrect programs" / "The program is incorrect" (`sampler.py:286-292`, `prompts_base.py:97-101, 119-121`) — even though the passed ancestor is the *correct* sampled parent.
- Web search is OFF for ordinary fixes, ON only for discovery-grounding repairs (`run_window.py:434-436, 506-507`).
- Separately, `mutate.py`'s **apply-failure** retry (not eval failure) re-sends the identical prompt with "Your previous attempt failed to apply: {err}" appended to the user message (`mutate.py:277-282`).

## 5. Cache shape (prefix-caching view)

Each call is a fresh single-turn Responses-API background call (`_azure.bg_query(model, patch_sys, patch_msg, ...)`, `mutate.py:242-245`) — no conversation history to cache.

- **Stable across all calls in a run**: only the leading `task_sys_msg` (plus `BASE`/nothing). Typically short.
- **Varies immediately after it, per call**: the direction block (per-island, and per-gen when a structured brief supplies sampled directions); the patch-format block (per-call mode draw; full mode additionally picks 1-of-5 variants at random, `sampler.py:166-170`); the runtime caution (data-dependent).
- **Stable within a window but not per-call-position**: `failure_note` (updated at each per-window meta round) and `extra_guidance` — but they sit *after* the varying direction block, so they don't extend the cacheable prefix.
- **User message**: starts with the inspiration block, which changes whenever the parent/island draw changes (every slot) — effectively zero stable user prefix.

Net: the cacheable prefix is essentially `task_sys_msg` alone; the design (direction-before-format, random full-variant, ascending-sorted per-gen inspirations first in the user turn) is prefix-cache-hostile. The one strong intra-call cache case is `mutate.py`'s apply-retry loop, which resends the identical `patch_sys` + `patch_msg` with only an appended error line (`mutate.py:278-282`). No prompt-caching parameter or cache-aware ordering exists anywhere in `_azure.py` or `mutate.py` (absence verified by grep). Full prompts are journaled per call to `journal/llm_content/` (`run_window.py:850-863`).

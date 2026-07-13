> **STATUS: APPLIED (2026-07-13).** Historical record of the three-track audit
> (upstream parity / SimpleTES / concurrency) and its implementation batch.
> Live guidance is CLAUDE.md + .claude/skills/shinka-orchestrator/SKILL.md.

# CHANGE SPEC — post-audit improvement batch (user-approved 2026-07-13)

Fusion of: 3-track audit (upstream parity / SimpleTES / concurrency), 8 verified track reports
(`audit/reports/*.md`, all line-checked against this worktree), and the user's clarifications.

## User directives that bind every batch

1. **Cohesion is mandatory.** Any change with dependencies elsewhere (new meta fields, new knobs,
   changed loop semantics) must land WITH: the prompt text that produces/consumes it written
   clearly, the code that carries it, the docs that teach it (SKILL.md, CLAUDE.md when touched),
   docstrings, the default config, and tests — in the SAME commit. No knob that lies, no doc that
   trails the code.
2. **Auditability is a design requirement for concurrency** (user, re Batch H): the slot machine
   must come with journal-level slot lifecycle logging so the audit trail is as readable as the
   sequential driver's. Landing-order commits, drain-at-boundary, `.stop` = drain-never-kill.
3. **No rushing**: each batch = read target files firsthand → edit → test → commit. One commit per
   batch, message explains behavior change.

Approved decisions (from AskUserQuestion rounds):
panel 2+2 [A] · RPUCG-lite adopt (measure-cycle deployable) · meta scratchpad default-ON ·
power_law add-now default-weighted · concurrency Stage 1 build now (K=2/E=1) · siblings opt-in
default-off · all four small-fix bundles (prompt hygiene, bug fixes, marker validation, caching
riders) · implement now, batches, commit; archive audit docs at the end.

Execution context: git clean, branch `claude/xenodochial-pike-f6055c`, no live run, so
between-runs foundation edits (run_window.py, mutate.py, journal.py, shinka/*) are sanctioned.
FOUNDATION that stays untouched regardless: sqlite schema (no new columns), evaluator harness +
`tasks/*/evaluate.py`/`initial.*`, `cadence_policy.py` + termination logic.

---

## Batch A — config defaults + knob-honesty docs  [commit 1]

- `configs/orchestrator_run.default.json` db_config: add `"num_top_k_inspirations": 2,
  "num_archive_inspirations": 2` (departure from upstream 1/1, SimpleTES Table-18 sweet spot 3–5).
- SKILL.md config-lever table: add rows for the already-live pass-through db_config levers:
  `num_top_k_inspirations`, `num_archive_inspirations` (with the new defaults + when to
  raise/lower), `island_selection_strategy`, `migration_rate` + `migration_interval` (guidance:
  0.1/10 at a control-return = cheapest diversity injection short of a discovery round),
  `island_elitism`. Note `elite_selection_ratio` is dead in the fork if SKILL mentions it.
- Do NOT touch the four `policy_*` rows here — Batch B wires them and fixes the rows in the same
  commit (docs and code move together).

## Batch B — bug fixes  [commit 2]

1. **Fix-prompt ancestor framing** (`shinka/core/sampler.py:286-292`, templates
   `shinka/prompts/prompts_base.py:97-101,119-121` + `prompts_fix.py`): `sample_fix` renders
   ancestor inspirations with incorrect-program framing even when the ancestor is the CORRECT
   sampled parent (immediate in-slot fix passes `[sampled parent]`, run_window.py:477). Fix:
   branch framing on each ancestor's actual `correct` flag — correct ancestor gets a "last
   known-correct ancestor this attempt derived from — behavioral reference" header; incorrect
   ancestors keep today's text. Read both template call sites first; keep template texts in
   prompts_base/prompts_fix (not inline).
2. **Revive EXPERT_CREATIVE_PREAMBLE**: `run_window.py:371-383` `_compose_meta_for_gen` returns a
   constant placeholder for direction-less gens, making sampler.py:148-149's preamble dead. Fix in
   run_window (between-runs edit): return None when there is no brief/direction; verify
   construct_mutation_prompt passes None through untouched and sampler then renders the preamble.
   Check nothing else keys on the placeholder string (grep).
3. **Wire the four policy levers**: `run_window.py:1414-1423` forwards only
   `policy_spawn_cooldown`+`last_policy_spawn_generation` into the island_policy payload; forward
   `policy_migrate_enabled`, `policy_migrate_interval`, `policy_spawn_enabled`,
   `policy_spawn_stagnation` from evo config when present (island_policy.py:179-182 already reads
   payload-first with db_config-derived defaults). SKILL.md line ~1018 rows updated in the same
   commit to say wired + payload precedence.

## Batch C — prompt hygiene  [commit 3]

1. **Stdout/stderr cap at prompt build** (`construct_mutation_prompt.py`, MUTABLE): before
   delegating to `sampler.sample_fix`, head+tail-truncate the incorrect program's
   `metadata.stdout_log`/`stderr_log` to 16 KB, reusing the same helper/constants as
   `shinka/utils/general.py:17-31` (import it, don't copy). Archive stays lossless (no DB-side
   knob). Docstring notes the cap; SKILL.md fix-prompt note.
2. **Anti-inbreeding exemplar exclusion** (`sample_parent.py:327-367`): when composing
   direction-assigned AND pre-brief top-k/archive inspirations, exclude the parent's own
   `parent_id` and the parent's children (derive from pool `parent_id` edges). If the filtered
   candidate set can't fill the counts, fall back to unfiltered ranking (tiny-island safety).
3. **Deterministic failure histogram** (`meta_summarize.py`, MUTABLE): pure-Python aggregation of
   recent errored rows (recency window ≈ last 2×window_size rows; normalize error signature =
   first non-empty line of error text / exception class; count per island). Append a compact
   block — `Top error signatures (deterministic count, last ~2 windows): ...` — top-5, ≤400 chars,
   to the returned `failure_note` AFTER the LLM call (LLM cannot drop/dilute it). The meta prompt
   is told the histogram is appended automatically so it must not duplicate counts (prompt-clarity
   directive). Recomputed fresh each round ⇒ self-clearing. Verify at implementation where errored
   rows carry error text (metadata error_history per run_window.py:1172-1182 / text_feedback) and
   window_size source in meta payload.

## Batch D — sampler strategies + parity tests  [commit 4]

Dispatch on `db_config.parent_selection_strategy` in `sample_parent.py` (currently ignored):
- `"weighted"` — existing parity-faithful port, byte-identical behavior (parity tests keep
  passing unchanged).
- `"power_law"` — port upstream `parents.py:105-195`: same-island correct pool sorted by
  combined_score desc, P(rank i) ∝ (i+1)^-α, α = `db_config.exploitation_alpha` (default 1.0).
  (Deviation from upstream documented: pool = island pool, as for weighted.)
- `"lineage_weighted"` — RPUCG-lite: children map from pool `parent_id` edges;
  U_i = max(s_i, γ·max_child U_j) computed bottom-up (process by generation desc; γ default 0.8,
  payload-overridable as `lineage_gamma` if the payload plumbing already carries evo keys — else
  module constant); then existing weight formula with U_i replacing s_i:
  sigmoid(λ·(U−median(U))/MAD(U)) × 1/(1+children_count). Invariant: pool with zero children ⇒
  U≡s ⇒ identical to "weighted" (tested).
- Default flips to `"lineage_weighted"` in `configs/orchestrator_run.default.json` (visible,
  one-line revert to "weighted" at any control-return = the measure-cycle escape).
- Tests (`orchestrator/tests/`): power_law distribution matches the closed form on a fixed pool;
  lineage_weighted == weighted on a childless pool; lineage_weighted upweights a fertile mediocre
  parent on a hand-built DAG. SKILL.md: strategy values + when to flip (power_law on score
  compression: picks ~uniform while top lineages clearly better; back to weighted if hot-lineage
  concentration shows in islands-health sensors).

## Batch E — rolling meta scratchpad (global_insights)  [commit 5]

- `meta_summarize.py`: input payload gains `global_insights_prev` (string, may be empty). Meta
  prompt gets a clearly-delimited section: previous insights verbatim + instruction — update,
  don't regrow; merge duplicates; DROP entries that no longer match the archive; ≤12 lines, each
  line prefixed with the window index it was last confirmed; return under a `global_insights` key
  in the same structured output the call already returns (verify exact response contract at
  implementation and extend it consistently). Code enforces the cap deterministically (truncate to
  12 lines / ~1500 chars) after parse.
- `run_window.py`: thread prev blob into the meta call; persist returned blob in-process next to
  `meta_failure_note` (:1571-1573 pattern) and re-hydrate at boot/resume from the last logged meta
  call exactly as failure_note does (:1305-1308 pattern — mirror the mechanism found there).
- Scope decision (cohesion): the scratchpad feeds the META ROUND ONLY (its next-round prompt), not
  the per-mutation prompts — briefs/failure_note remain the only meta→mutation channels. SKILL.md
  states this explicitly + outer-loop guidance: read the blob as a control-return diagnostic; if
  it collapses into repetition, rewrite the pruning instruction in meta_summarize.py ([B] rewrite),
  never hand-edit blob content.

## Batch F — caching riders  [commit 6]

1. **Ledger cached-token awareness**: `_azure.py:69-96` `_usage_cost` + foreground
   `shinka/llm/providers/openai.py:62-101` read `usage.input_tokens_details.cached_tokens`
   (guard-absent → 0); `pricing.csv` gains `cached_input_price` column;
   `pricing.py calculate_cost` prices input as (input−cached)·in + cached·cached_price with
   fallback cached_price=input_price when the column is empty ⇒ empty column reproduces today's
   (conservative, never-undercount) ledger bit-for-bit. Log cached_tokens in the call record for
   observability. Try to verify Azure Standard cached rates on the web during implementation; only
   fill the column with numbers actually verified (else leave empty + SKILL.md note: verify from a
   live response/bill, then fill).
2. **prompt_cache_key plumbing**: optional param on `bg_query` (`_azure.py:187`) →
   `responses.create(prompt_cache_key=...)` (param verified present in installed SDK 2.41.0);
   `mutate.py` passes `f"{run_id}:{island_id}"` — island id must ride the existing mutate payload
   (check payload keys; add optional key, backward-compatible: absent ⇒ param omitted).
3. **Pin one FULL-format variant per window**: run_window threads a window-stable variant index
   (e.g. seeded on window index) through the construct_mutation_prompt payload; sampler.py:166-170
   uses the pinned index when provided, else today's per-call random (back-compat default).
4. **Docs**: SKILL.md boot guidance — author `task_sys_msg`+`objective_brief` comfortably >1,024
   tokens (the Azure cache cliff); note caching is automatic, ~15 RPM/prefix irrelevant at our
   rate; note the one-time live verification of `cached_tokens` on a background call.

## Batch G — EVOLVE-marker post-apply validation  [commit 7]

- In `mutate.py` apply path, after a successful apply: count `EVOLVE-BLOCK-START` /
  `EVOLVE-BLOCK-END` substrings in new code vs parent code; any mismatch ⇒ treat as apply failure
  (`applied:false` path) with an explanatory error string appended to the retry prompt — the
  existing `max_patch_attempts` retry loop handles it. Applies to full/cross/fix rewrites AND diff
  (cheap; diff should never trip it). Language-agnostic substring count, exact-count equality
  only. Test: full rewrite that eats markers → rejected, retried, error text mentions markers.

## Batch H — concurrency Stage 1: slot state machine  [commit 8]

**Knob semantics (simplified from track-3's K/E to avoid a degenerate-case trap):**
- `evo.parallel_slots` (default **2**) = max slots in flight (a slot = one candidate's whole
  lifecycle). `parallel_slots=1` ⇒ *strictly sequential*, the parity anchor: one slot exists at a
  time, so ordering is exactly today's. LLM-call concurrency is implicitly ≤ parallel_slots.
- `evo.parallel_eval_slots` (default **1**) = max concurrent evaluations (≤ parallel_slots).
  Defaults (2,1) = the user's assembly line: mutate N+1 overlaps eval N; never 2 evals.

**Restructure `_one_window` (`run_window.py:1383-1396` loop):** per-slot state machine
PREPARE → MUTATE → EMBED → EVAL → (FIX: re-enter MUTATE/EVAL) → COMMIT, run on per-slot threads
(window_size max), gated by: an admission semaphore (parallel_slots), an eval semaphore
(parallel_eval_slots), and ONE commit mutex (`threading.Lock` suffices — `.run.lock` guarantees
one process).
- Under the commit mutex: PREPARE (sample_parent + prompt build + select_llm select — pkl RMW),
  and COMMIT (novelty resolve → repair/tombstone bookkeeping → reward → record → archive_record →
  bandit update → counters/journal add) in landing order.
- Gen numbers pre-assigned `next_gen+i` exactly as today (:1336,1388).
- **No-duplicate-parent guard**: PREPARE excludes parents currently pinned by in-flight slots
  (in-flight-parents set); **eviction pinning**: keep-the-better tombstone path skips (keeps both,
  logs) when the incumbent is a pinned in-flight parent.
- **Repair slot solo**: repair (slot 0 when latched) admits alone — drain before, run single,
  then open admission.
- **Budget admission check**: before admitting a slot: prior_total + counters.cost +
  n_inflight×avg_recent_slot_cost ≥ budget_usd ⇒ stop admitting, drain, end window early
  (today's railguard, widened honestly).
- **`.stop`**: checked at admission — stop admitting, DRAIN in-flight (never kill; consistent
  with the never-kill-Azure rule), then boundary as today.
- **Window boundary**: drain ALL slots before island_policy/diagnostics/append_window/meta —
  quiesced archive exactly as today; meta/stagnation/cadence untouched.
- **Journal slot lifecycle logging (user's auditability directive)**: new append-only
  `journal/slots.jsonl` — events slot_prepared/slot_llm_done/slot_eval_done/slot_committed with
  gen, ts, in-flight counts, arm, parent — written via the same fsync'd append helper
  (journal.py:73-91). windows.jsonl/run.json event shapes unchanged (Monitor keeps working).
- **Tests** (`orchestrator/tests/`): fake mutate/evaluate/embed via monkeypatch with deterministic
  latencies. Assert: (1) parallel_slots=1 reproduces sequential ordering exactly (commit order ==
  gen order == prepare order; bandit pull sequence identical); (2) slots=2: all slots commit,
  bandit pulls == slots (no lost pkl updates), counters/cost exact, novelty rows complete, commits
  in landing order; (3) repair-solo honored; (4) `.stop` drains without dropping in-flight slots;
  (5) budget admission stops new slots. Mirror the harness style of the existing
  smoke/improvement tests.
- **Docs**: SKILL.md — new knobs, drain semantics, crash exposure ≤ parallel_slots unlogged-billed
  calls (keep ≤3), eval-contention warning (raise parallel_eval_slots to 2 only after measuring
  one eval's CPU footprint; `numeric_threads_per_job` exists to pin threads). CLAUDE.md — grep for
  sequential-loop phrasing and update any sentence the change falsifies (keep minimal).

## Batch I — opt-in sibling fan-out  [commit 9]

- `evo.sibling_samples` (default 1 = off; code-enforced cap 2). In PREPARE, when >1 and mode is a
  normal mutation (not repair): emit N slots sharing parent+prompt+arm (distinct gens, each a
  normal window slot), stagger sibling ≥2 dispatch by ~30 s (prefix-cache warm; from Batch F they
  share prompt_cache_key). Bandit: `update_submitted` per sibling (upstream-consistent in-flight
  counting). Children flow the normal novelty/archive/bandit path — NO best-of-K discard.
- SKILL.md: when to enable (strong parent + exploitation phase), why keep-all beats best-of-K
  here, cost note (~90% cached input on siblings 2..K, ~15% of call cost).

## Final  [commit 10]

- Full `pytest` (orchestrator/tests) green; smoke tests not run (paid) unless needed.
- Multi-agent adversarial review workflow over the entire diff; fix confirmed findings.
- Copy the 8 verified reports + 3 track proposals + this spec into `docs/archive/` with an
  APPLIED banner (repo convention); commit.

## Cross-batch cohesion checklist (apply at every commit)

- [ ] Default config, code fallback default, SKILL.md table, and docstring all state the SAME
      default value.
- [ ] Any new payload key: producer (run_window) and consumer (script) land together;
      absent-key behavior == old behavior (back-compat).
- [ ] Any new prompt text: purpose stated in the prompt itself, no contradiction with other
      sections (failure_note vs histogram; scratchpad vs briefs).
- [ ] Tests updated/added in the same commit.
- [ ] No sqlite schema change anywhere. No cadence/termination change anywhere.

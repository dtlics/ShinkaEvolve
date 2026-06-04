# Audit — ShinkaEvolve Orchestrator Logic & Workflow (2026-06-03)

## Scope & method
This is a read-only logic + workflow + documentation audit of the Azure-only, "Claude-as-orchestrator" ShinkaEvolve framework. It covers 15 functional dimensions (boot/no-spoil setup; inner-loop mutation + truthful failure recording; parent + inspiration sampling; novelty/keep-the-better; the automatic per-window meta round; island-brief→inspiration coupling; island policy/cap/spawn/migration; deep-research + 3-scenario triage; outer-loop control-return + work-score taper; the framework-audit rewrite cycle; budget hard-cap + crash-durable ledger; termination + end-of-run archive; model selection/bandit; diagnostics + stagnation; warmup oversight) plus 5 cross-cutting sweeps (code↔doc consistency + stale-reference/phantom-lever hunt; orchestrator teachability; first-principles evolutionary-search critique; edge-case/failure-path hunt; completeness/CI/test-integrity). Every finding below was checked against the actual source (Read/Grep/Bash); the highest-impact ones (the `restore_state` code-rewind contract, the brief→inspiration coupling, the negative-score bandit collapse, the termination/interventions plumbing, the reverted latency-aware selection, the unconditional novelty increment, the `deploy()` bundle-guard, `island_brief` mutability, and the immediate-fix spoil leak) were re-confirmed by direct line reading during synthesis. Cross-cutting claims were de-duplicated against the per-dimension findings and weak speculation was dropped.

## Executive summary

**Counts by severity:** Critical 1 · High 12 · Medium 22 · Low 33 · Nit 9 — **77 findings kept**, 0 refuted.

**The 10 most important issues:**
1. **(C1, critical)** The documented revert `restore_state` does NOT rewind the strategy `.py` file — only the DB + bandit + ledger. A regressing rewrite the agent "reverts" per SKILL.md step 6 stays LIVE for the rest of the run, poisoning every subsequent window. Repeated as a "FULL rewind of code" in SKILL.md, CLAUDE.md, AND the NOTES P9-T0 contract row.
2. **(H1, high)** Brief→inspiration coupling is a text-only system-prompt swap, not the "direction-oriented" inspiration switch the design requires; islands share identical score-ranked exemplar code, so "islands differentiate BY DEFAULT" is wired only halfway.
3. **(H2, high)** The island model cannot hold distinct basins on the default route: global archive eviction + zero default migration + the empty-pool separation-fallback compound into single-lineage convergence while `island_health` still reports islands as "differentiated".
4. **(H3, high)** Negative-score tasks in the DEFAULT `absolute` reward mode collapse every correct candidate to the same bandit contribution as a failure (baseline clamped to ≥0); the "negative-score tasks fully supported" claim is false. `reward_mode:relative` sidesteps it but is undocumented as the fix.
5. **(H4, high)** Latency-aware model selection — asserted as a live, load-bearing safeguard by the user's auto-memory (injected every session) — was REVERTED out of the code (`0edea84`). A fresh orchestrator believes slow arms auto-demote and runs `gpt-5.5@medium` (25–40 min/mutation) with no safeguard.
6. **(H5, high)** The novelty gate drops the newcomer UNEVALUATED before scoring — it can never "keep the better" near-duplicate (the user's explicit requirement); a strictly-better variant near its parent is permanently lost.
7. **(H6, high)** The termination rule's "≥1 DR of 5, read from interventions.jsonl" is uncomputable as written: DR self-logs only to calls.jsonl, and the doc never teaches the `work_dr>0` coupling — risking a stop with no DR, or never auto-terminating.
8. **(H7, high)** The docs instruct TWO disjoint interventions.jsonl entry shapes (rewrite-cycle vs work-score), so a control-return yields 0/1/2 rows; the five-consecutive count is non-deterministic and a rewrite-only row is invisible to the taper.
9. **(H8, high)** "Five consecutive control-returns each involving an intervention" is ambiguous: a no-op return still writes a `work_score=0` row, and what RESETS the streak is unspecified — directly risking a mis-timed stop.
10. **(H9, high)** `use_text_feedback:false` is documented as a COMPLETE spoil mitigation but the immediate-fix path leaks the sampled ancestor's evaluator `text_feedback` (and the meta round leaks `error_traceback`), so the one-flag guarantee is broken on a spoil-risky task.

**Health verdict by subsystem.** The deterministic *plumbing* is healthy: the crash-durable ledger (atomic write + recompute-from-streams), the fail-closed rollback judge, the stagnation J-formula, division-by-zero guards, the budget railguards, and the serial harness are all sound, with only narrow edge cases. The *correctness-critical* gaps cluster in two places: (1) the **framework-audit revert** is torn — the code-rewind half is unwired and triple-mis-documented (C1); (2) the **island/diversity model** is structurally collapse-prone on the default route (H1/H2/H5 + the meta-blindness and reward credit-assignment findings). The **orchestrator-teaching docs** carry several load-bearing ambiguities (termination H6/H7/H8, the spoil mitigation H9, latency H4) that would make a competent fresh agent make a wrong call. Phantom levers are rare — the lever tables are tightly coupled to code — but a handful of advertised knobs (`island_selection_strategy="equal"`, `inspiration_sort_order`, the `azure_default.yaml` evo block, `island_policy` retire) are inert.

---

## Findings — Critical

### C1 — `restore_state` does NOT rewind the strategy code; a "reverted" regressing rewrite stays live for the rest of the run
**concern + kind:** code-doc-mismatch (the rewrite-cycle safety net). **Sources:** rewrite-cycle dimension + 3 cross-cutting sweeps (consistency, teachability, edge-case hunt).
**Locations:** `orchestrator/harness/strategy_store.py:253-291`, `orchestrator/harness/strategy_store.py:270-279`, `orchestrator/harness/strategy_store.py:194-210` (the real code-revert), `orchestrator/SKILL.md:399-401`, `CLAUDE.md:45`, `orchestrator/NOTES.md:35` (P9-T0 contract row), `orchestrator/tests/smoke_test.py:197-201`.
**Intended:** SKILL.md step 6: "If regressed: `restore_state(results_dir, snap_id)` — a FULL rewind of code + archive DB + bandit to the snapshot, except the cost ledger." CLAUDE.md:45 and the P9-T0 contract row repeat "a full rewind of code + archive DB + bandit." The agent is taught `restore_state` is the ONE revert call.
**Actual:** `restore_state` (the loop at lines 271-279) restores ONLY `programs.sqlite`, `bandit_state.pkl`, and `journal/run.json` — and it CANNOT rewind code because `snapshot_state` (242-245) never copies the strategy `.py`. The code revert is a SEPARATE API, `rollback(target, prior_hash)` / `rollback_bundle(...)` (194-210, 454-471), which step 6 never names. The implementers' own canonical revert proves the gap: `smoke_test.py:197-201` calls `ss.rollback(target, dep["prior_hash"], …)` + `record_outcome(...)` and never calls `restore_state`. The test docstring (`test_improvements.py:626`) likewise scopes `restore_state` as "a FULL rewind of archive + bandit." Only the agent-facing teaching docs claim it rewinds code.
**Impact:** Following the SKILL playbook literally on a regression rewinds the archive + bandit but leaves the poisoned strategy file LIVE in `scripts/`. Since `run_window` imports `scripts/` fresh every subprocess, EVERY subsequent window runs the bad policy against a rewound DB — a torn, inconsistent revert that defeats the entire safety net. The fingerprint-reset (run_window.py:957-963) even grants the bad strategy a fresh fair-trial streak. Because the false claim is also in the P9-T0 contract table (the doc-lint's own source of truth), it is self-certified as consistent while being false. **Confidence: high.**

---

## Findings — High

### H1 — Brief→inspiration coupling is a text-only swap, not the direction-oriented inspiration switch the design requires
**concern + kind:** design-gap. **Sources:** brief-coupling dimension + consistency sweep.
**Locations:** `shinka/core/sampler.py:96-97`, `shinka/core/sampler.py:134-141`, `orchestrator/scripts/sample_parent.py:247-254`, `orchestrator/harness/run_window.py:546-557`, `orchestrator/SKILL.md:567-569`, `orchestrator/scripts/island_brief.py:9-15`.
**Intended:** Before a brief exists, inspirations are score-sampled code; after a brief exists, inspiration sampling becomes DIRECTION-ORIENTED — each island pulls DIFFERENT exemplar code aligned with its direction. The brief should change WHICH code is shown.
**Actual:** Inspirations are chosen in `sample_parent.py:248-254` purely by `combined_score` (top-k + elites); the file reads no brief (grep-confirmed). The entire effect of a brief is `sampler.py:96-97` `if island_brief: meta_recommendations = island_brief` — a substitution of the system-prompt text. `build_context` builds the same archive+top_k inspirations regardless, and on a `cross` patch the whole rec block (including the brief) is suppressed (`sampler.py:134-135`). `sampler.py:91-95` still carries stale "Phase 2 of research-grounding" language from the removed agentic machine.
**Impact:** Islands sharing the global archive receive identical score-ranked exemplars; only guidance text differs. The intended per-island differentiation pressure never occurs, so islands converge on the same exemplars and "islands differentiate BY DEFAULT" is materially weaker than designed — the explicit half-wired case this audit was asked to catch. A fresh orchestrator over-trusts island differentiation. **Confidence: high.**

### H2 — The island model cannot hold distinct basins on the default route (global eviction + zero migration + separation-fallback compound)
**concern + kind:** design-gap. **Source:** first-principles evolutionary-search critique.
**Locations:** `shinka/database/dbase.py:2420-2491`, `shinka/database/dbase.py:2399-2418`, `shinka/database/dbase.py:65`, `orchestrator/scripts/sample_parent.py:224-230`, `shinka/database/islands.py:249-250`.
**Intended:** Islands explore genuinely DIFFERENT families/basins in parallel so good-but-different lineages mature before being out-competed (the FunSearch/AlphaEvolve rationale; "islands differentiate BY DEFAULT").
**Actual:** Three default-route mechanisms each break separation and compound: (1) the archive is ONE GLOBAL pool of 40; `_update_archive_fitness` evicts the GLOBALLY-worst correct program regardless of island (2468-2491), so a strong island starves weaker islands out of the archive. (2) `migration_rate` defaults 0.0 (dbase.py:65), so `perform_migration` returns False immediately (islands.py:249-250) — no cross-pollination ever runs. (3) When a weak island's archived-correct pool empties, `sample_parent` falls back to the FULL archive (`pool = archived_correct`, sample_parent.py:227-228), sampling the dominant island's programs.
**Impact:** Once one lineage pulls ahead it monopolizes the archive, the other islands' distinct material is evicted, and every island ends up mutating descendants of the same lineage — premature convergence, the exact failure islands exist to prevent. `island_health` shows islands as "differentiated" (distinct brief strings) while the genetic material has collapsed. **Confidence: high.** *(Note: parent SAMPLING is island-scoped via the programs table — see M-findings — so the collapse is concentrated in the shared inspiration/novelty pool and the empty-pool fallback, not the parent draw.)*

### H3 — Negative-score tasks in the default `absolute` reward mode collapse every correct candidate to a failure's bandit value
**concern + kind:** code-bug. **Source:** model-selection/bandit dimension.
**Locations:** `shinka/llm/prioritization.py:463-477`, `orchestrator/scripts/compute_reward.py:66-87`, `orchestrator/harness/run_window.py:813-820`.
**Intended:** `compute_reward.py:66-67` promises "NEGATIVE FINITE scores pass through to the floor logic … negative-score tasks are fully supported," with the floor keeping a correct candidate STRICTLY above a failed one.
**Actual:** `AsymmetricUCB.update` sets `baseline = max(passed_baseline, self._baseline)` with `self._baseline` fixed at 0.0 (`set_baseline_score` is never called — the K10 comment at compute_reward.py:79-80 admits this). For a negative parent it uses 0.0, so `r = r_raw - 0.0 < 0`, and the asymmetric clamp `r = max(r,0.0)` → 0.0 → `_logexpm1(0) = -inf` (nothing). Reproduced: a correct arm (−5→−3) and a failure arm both end at `s=-inf`, identical posterior; with the SAME deltas but positive scores the correct arm is correctly favored. The `mode == "relative"` path (line 85) returns `baseline=0.0` with the floored delta directly and sidesteps the bug.
**Impact:** On any negative-score task (loss/regret minimization) in the DEFAULT mode, every correct candidate is indistinguishable from a failure to the bandit; selection learns nothing. The "fully supported" guarantee is false. Inert on cnot (scores ≥0); a latent trap for the next task. `reward_mode:relative` is the workaround but is never documented as such. **Confidence: high.**

### H4 — Latency-aware selection is asserted live by the user's auto-memory but was reverted out of the code
**concern + kind:** code-doc-mismatch (code vs auto-memory). **Source:** model-selection/bandit dimension.
**Locations:** `orchestrator/scripts/select_llm.py:52-144`, `orchestrator/harness/run_window.py:874-886`, `orchestrator/scripts/mutate.py:123-209`, `shinka/llm/prioritization.py:458`, `/Users/dantongli/.claude/projects/-Users-dantongli-GIthub-ShinkaEvolve/memory/MEMORY.md:7`, the detailed note `shinka_realrun_cnot_20260527.md`.
**Intended:** The user's persistent auto-memory (injected into every session as authoritative teaching) states selection is latency-aware (inverse-latency prior + live EWMA fed by per-mutation wallclock), auto-routing ~96% to fast models so "all models + medium" is SAFE.
**Actual:** `select_llm.py` contains ZERO latency logic; `run_window` never measures or passes `latency_sec`; `mutate.main` returns no latency; `AsymmetricUCB.update` has no latency parameter. Git confirms the feature was added in `8d7d809` then explicitly REVERTED in `0edea84` ("revert latency-aware bandit"). The live in-repo docs were correctly scrubbed; the false claim survives only in the auto-memory.
**Impact:** A fresh orchestrator trusting the auto-memory believes slow arms auto-demote and that medium effort across the full pool is safe. With the shipped pool (`azure-gpt-5.4-mini` + `azure-gpt-5.5`) at `medium`, `gpt-5.5` runs 25–40 min/mutation with NO mechanism to route around it, so a real run can stall for hours while the agent thinks it is protected. **Fix target is the memory, not the repo docs. Confidence: high.**

### H5 — Novelty gate drops the newcomer unconditionally before evaluation — never "keeps the better" near-duplicate
**concern + kind:** design-gap. **Source:** novelty dimension.
**Locations:** `orchestrator/harness/run_window.py:707-728`, `orchestrator/harness/run_window.py:730-733`, `orchestrator/scripts/novelty_check.py:51-93`.
**Intended:** User's explicit contract: when max cosine ≥ `code_embed_sim_threshold`, compare the two programs' EVALUATOR scores and KEEP THE BETTER — "not merely drop the new one."
**Actual:** `novelty_check.main` returns no score field (88-93). On `not nov.get("accept")` the slot is dropped with a bare `return` at run_window.py:728 — BEFORE `_evaluate_candidate` at 730. The newcomer is never scored, so it cannot be compared; the existing (possibly worse) program is always kept. `most_similar_id` is returned but never consumed for any score comparison. `archive_selection_strategy` defaults to `fitness` and is never set to `crowding`, so the surviving member is later pruned by global-worst fitness, not similarity-niching.
**Impact:** A genuinely improved variant textually near its parent (large programs routinely cluster 0.96–0.99) is discarded UNEVALUATED and permanently lost, even when it would have scored strictly higher. Directly violates the stated requirement; the archive can be pinned to an inferior member. On the real-run path (the active task sets `enable_novelty:true`). **Confidence: high.**

### H6 — Termination's "≥1 DR of 5" is uncomputable from interventions.jsonl as instructed
**concern + kind:** doc-unclear. **Sources:** termination dimension + teachability sweep.
**Locations:** `orchestrator/SKILL.md:441-446`, `orchestrator/scripts/deep_research.py:168-191`, `orchestrator/harness/journal.py:213-223`, `orchestrator/harness/journal.py:280`.
**Intended:** Stop after five consecutive control-returns each involving an intervention, ≥1 a DR, "read from interventions.jsonl."
**Actual:** DR self-logs ONLY via `log_external_call(..., "dr", ...)` (deep_research.py:168, 183) → `journal.log_call` → `journal/calls.jsonl`, NOT interventions.jsonl. Tree-wide grep confirms `append_intervention` is NEVER auto-called by any harness/script code — the only interventions.jsonl writer is the agent's manual work-score entry. So the only DR trace in interventions.jsonl is the agent manually writing `work_dr>0`, which the termination section never instructs.
**Impact:** The agent could terminate without any DR having occurred (violating ≥1-DR), or look in calls.jsonl (a count decoupled from the per-return rows the rule depends on), or assume DR is auto-recorded and never log it. The load-bearing stop rests on an undocumented `work_dr` coupling. **Confidence: high.**

### H7 — The docs instruct TWO disjoint interventions.jsonl entry shapes, making the five-count non-deterministic and a rewrite-only row invisible to the taper
**concern + kind:** inconsistency. **Source:** termination dimension.
**Locations:** `orchestrator/SKILL.md:388` (rewrite-cycle "log the rewrite"), `orchestrator/SKILL.md:111-124` (work-score entry), `orchestrator/harness/journal.py:358-360`, `orchestrator/tests/test_improvements.py:76`, `orchestrator/tests/test_improvements.py:195-204`.
**Intended:** Each intervention-return should leave one well-defined countable row that the termination rule counts AND the taper reads as a work magnitude.
**Actual:** Two unreconciled write instructions: (1) step 4 — `{type,target,reason,outcome}`, NO `work_score` (fixture line 76); (2) work-score — `{work_audit,work_dr,work_score}`, NO `type/target` (line 195). The doc never states whether they are one row or two. So a rewrite+work-score return writes TWO rows, a no-op writes ONE (work_score=0), a DR-only return may write ONE — row-count ≠ intervention-return count. And `_work_scores` (358-360) keeps only rows with a numeric `work_score`, so a step-4-only rewrite row is INVISIBLE to `recent_work_score`/`work_low_streak` — the taper sees "no work" and mis-paces.
**Impact:** Neither the row-count termination heuristic nor `_work_scores` interprets the literal-instruction stream unambiguously — risking a mis-timed stop AND a mis-paced taper. Root cause of the cosmetic `build_run_summary` rendering bug (L-finding). **Confidence: high.**

### H8 — "Five consecutive control-returns each involving an intervention" is ambiguous (no-op rows count; no reset rule defined)
**concern + kind:** doc-unclear. **Source:** termination dimension.
**Locations:** `orchestrator/SKILL.md:441-446`, `orchestrator/SKILL.md:111-124`, `orchestrator/harness/journal.py:358-403`.
**Intended:** Count five CONSECUTIVE returns that each actually INVOLVED an intervention; a no-intervention return must reset the streak.
**Actual:** The work-score section tells the agent to append a row "after every control-return" with "no change 0" / "not run 0," so a no-intervention return STILL writes a `work_score=0` row (`test_improvements.py:196`). The termination section says only "read from interventions.jsonl" and never tells the agent to filter to `work_score>0` nor that a `work_score==0` return RESETS the five-streak. Compounding: `work_low_streak` counts the OPPOSITE (consecutive LOW work) for the taper and shares the magnitude 5 with `cadence` base_low.
**Impact:** The agent could terminate too early (five no-op rows counted as five interventions) or never reset and over-run — the one place "do not stop until a criterion is met" is operationalized, so the ambiguity directly risks a wrong stop. **Confidence: high.**

### H9 — `use_text_feedback:false` is documented COMPLETE but the immediate-fix path leaks the sampled ancestor's evaluator text
**concern + kind:** code-doc-mismatch. **Source:** boot/no-spoil dimension.
**Locations:** `orchestrator/harness/run_window.py:382-395`, `orchestrator/harness/run_window.py:377-379`, `orchestrator/scripts/construct_mutation_prompt.py:78`, `shinka/core/sampler.py:277-283`, `shinka/prompts/prompts_base.py:73-79`, `orchestrator/SKILL.md:238-240`, `orchestrator/SKILL.md:560`, `CLAUDE.md:30-33`.
**Intended:** Disabling text feedback removes ALL evaluator text from the fix/repair prompt — "a COMPLETE suppression" (SKILL.md:238-239), "the complete mitigation" (CLAUDE.md:33). One flag makes a spoil-risky evaluator safe.
**Actual:** The immediate-fix loop builds its repair prompt at run_window.py:382-395 with NO `use_text_feedback` key, so `construct_mutation_prompt.py:78` defaults it True and `PromptSampler` runs with `use_text_feedback=True`. It passes `ancestor_inspirations=[learn_from]` (the sampled parent) → `construct_eval_history_msg(..., include_text_feedback=True, correct=False)` (sampler.py:277-283), and `prompts_base.py:73-79` renders that ancestor's `text_feedback` even on the `correct=False` branch. The harness blanks the just-failed CANDIDATE's `stdout_log/stderr_log` (377-379, gated by `_utf`) but never sets `use_text_feedback=False` on this construct call. `text_feedback` is persisted for every program (run_window.py:859), so the sampled parent carries it. The normal-mutation path passes the flag at :583; only the immediate-fix path is unguarded.
**Impact:** On a task protected with `use_text_feedback:false`, every fix-retry still embeds the sampled parent's evaluator `text_feedback`. On a spoil-risky task that text IS exactly what gets persisted, so a mutation can read held-out detail and game the metric — the failure the flag is documented to prevent. The ancestor can be a correct OR an errored parent, so the leak is broader than "the correct parent." High, not critical: only materializes if the evaluator writes privileged detail into `text_feedback` and only on a fix-retry, but it silently breaks a guarantee the docs call COMPLETE. **Confidence: high.**

### H10 — `restore_state` silently rewinds the cost ledger when the live `run.json` is corrupt at revert time
**concern + kind:** code-bug. **Source:** rewrite-cycle dimension.
**Locations:** `orchestrator/harness/strategy_store.py:264-291`, `orchestrator/harness/journal.py:317-333`, `orchestrator/harness/journal.py:109-122`.
**Intended:** `restore_state` "NEVER rewinds the COST LEDGER … so spend stays counted and a revert-and-retry can never exceed the budget" (docstring 254-258). The durable design recomputes `total_cost` from the JSONL streams when run.json is corrupt.
**Actual:** `live_total` is read in a try/except; if run.json is corrupt the except sets `live_total=None` (269). The restore loop (271-279) then copies the SNAPSHOT's run.json (older, lower `total_cost`) over the live one, and the re-stamp (281) is SKIPPED because it is gated on `live_total is not None`. The restored run.json is now a VALID dict WITH a `total_cost` key, so `read_run`'s recompute-from-streams safety net (journal.py:329) does NOT engage — it returns the snapshot's lower value, never consulting the durable streams. Reproduced in a temp dir: snapshot 1.0, true spend 5.0, corrupt live → `total_cost_preserved=None`, on-disk `{"total_cost":1.0}`, `_recompute_total_cost` would report 5.0.
**Impact:** At the exact conjunction the durable ledger is meant to defend (a revert AND a corrupt live run.json), the budget railguard under-counts spend by (live − snapshot), so a revert CAN effectively exceed budget. Narrow but a real ledger-integrity hole. The correct behavior is to recompute from streams when `live_total` is unreadable. **Confidence: high.**

### H11 — Meta never receives source code on the automatic per-window path (best program omitted, recents gathered code-less)
**concern + kind:** design-gap. **Source:** meta-round dimension.
**Locations:** `orchestrator/harness/run_window.py:1092-1105`, `orchestrator/scripts/meta_summarize.py:144-173`, `orchestrator/scripts/_common.py:196-200`.
**Intended:** `meta_summarize` renders the current best program's full CODE (capped 4000 chars) plus recent attempts so the strategist grounds directions in what the best implementation actually does.
**Actual:** The auto-meta payload (run_window.py:1092-1105) never passes `best_program`, so the "# Current best ```code```" block is never rendered. `_gather_recent` calls `archive_query` with only `include_metadata:True` — no `include_code`/`code_preview_chars` — and `program_summary` adds no code field without them. Grep confirms the only callers passing `include_code:True` are the inner-loop sampler, never meta.
**Impact:** Every automatic per-window meta round reasons purely over score trends, patch names, and ~160-char error snippets — blind to the algorithmic content. Directions and per-island briefs are proposed without seeing the code they steer, materially weakening relevance and differentiation on a real run. **Confidence: high.**

### H12 — No live doc warns the shipped example pool (`gpt-5.5@medium`) contains a slow arm now that the latency safeguard is gone
**concern + kind:** doc-unclear. **Source:** model-selection/bandit dimension.
**Locations:** `orchestrator/SKILL.md:516-518`, `orchestrator/SKILL.md:543-565`, `orchestrator/SKILL.md:528-533`.
**Intended:** The selection section should let a fresh orchestrator choose a pool + reasoning effort that completes in reasonable wallclock, since the bandit cannot auto-route around slow arms.
**Actual:** The shipped example uses `reasoning_effort:medium` with `azure-gpt-5.5`; the levers table and pool note never mention per-model latency or that `gpt-5.5`/pro at medium can take 25–40 min/mutation (recorded only in auto-memory, which wrongly says it is auto-handled). No live doc tells the agent how to avoid slow arms.
**Impact:** A new orchestrator following the example verbatim can launch a pool whose slow arm stalls the run, with neither a doc warning nor a code safeguard. Distinct teachability gap from H4 (same root, different surface). **Confidence: medium.**

---

## Findings — Medium

### M1 — `restore_state`/deploy/snapshot calls are shown as bare Python but `strategy_store` has no CLI; the import-only invocation is never taught
**kind:** doc-unclear. **Sources:** rewrite-cycle dimension + teachability sweep.
**Locations:** `orchestrator/SKILL.md:381-403`, `orchestrator/harness/strategy_store.py:1-496`, `orchestrator/harness/validate_strategy.py:373-382`, `orchestrator/harness/journal.py:559-580`.
**Intended/Actual:** `strategy_store.py` has no `__main__`/`run_main`/argparse (grep-confirmed import-only), yet SKILL steps 4/6 show `strategy_store.deploy(...)`, `restore_state(...)`, `record_outcome(...)` (and `journal.append_intervention(...)`) as bare calls. `validate_strategy.py` and `journal.py` DO ship stdin CLIs, so the pattern is inconsistent. The doc never says this safety-critical cluster must run as in-process `import strategy_store` Python.
**Impact:** The agent must infer that this one cluster is import-only while every other helper is a stdin-JSON subprocess; an improvised `restore_state` still omits `rollback()` (compounds C1). **Confidence: high.**

### M2 — `deploy()` rejected-hash guard misses BUNDLE-rejected hashes; a bundle-rejected candidate re-deploys as a single file without `force`
**kind:** code-bug. **Source:** rewrite-cycle dimension.
**Locations:** `orchestrator/harness/strategy_store.py:156-167`, `orchestrator/harness/strategy_store.py:399-411`, `orchestrator/SKILL.md:371-372`.
**Intended:** SKILL.md:372: "both deploy and deploy_bundle refuse it unless you pass force=True."
**Actual:** `deploy()`'s guard matches only single-deploy entries (`new_hash == cand_hash`); bundle entries store hashes under `new_hashes` (plural) with no `new_hash`, so `deploy()` never inspects them. `deploy_bundle()` correctly checks BOTH (`_rej_single` via `new_hash` AND `_rej_bundle` via `new_hashes[target]`). Asymmetry: single→bundle re-deploy is caught; bundle→single is NOT (reproduced: deploy after a rejected bundle was ALLOWED).
**Impact:** A bundle-rejected hash silently re-deploys as a single file, defeating the retread-guard in that direction and contradicting the SKILL promise. The cycle still fail-closes if it regresses again, but the documented net is half-open. **Confidence: high.**

### M3 — `island_brief.py` is marked Mutable=Yes in SKILL but absent from `MUTABLE_TARGETS`, so the cycle refuses to deploy it
**kind:** inconsistency. **Source:** teachability sweep.
**Locations:** `orchestrator/SKILL.md:498`, `orchestrator/harness/strategy_store.py:343-355`, `orchestrator/harness/strategy_store.py:82-92`.
**Intended/Actual:** SKILL.md:498 marks `island_brief.py` Mutable=**Yes**, but `MUTABLE_TARGETS` (343-355) excludes it; `_assert_mutable` (88) raises `PermissionError` for it without the foundation-write override. It is also omitted from `current_fingerprint()`.
**Impact:** Deploying a rewritten `island_brief.py` via the cycle hard-fails — a real doc-vs-code contradiction in the mutability contract. **Confidence: high.**

### M4 — "overshoot ≤ one slot" understates overshoot: the un-gated automatic meta round can add ~$6 (~$9 with pro@high) past the cap
**kind:** code-doc-mismatch. **Sources:** budget dimension + consistency sweep.
**Locations:** `orchestrator/SKILL.md:51`, `orchestrator/harness/run_window.py:1088-1132`, `orchestrator/harness/run_window.py:996`, `orchestrator/scripts/meta_summarize.py:284-292`, `orchestrator/scripts/_azure.py:36`.
**Intended/Actual:** The inner-loop railguard (996) only stops STARTING the next candidate; after `append_window`, the auto-meta block (1088-1132) runs unconditionally (gated only by `evo.auto_meta` default True), even on the budget-hit window. Meta's only budget guard is its self-skip when `remaining < meta_estimated_cost_usd` (default $1.0). A meta call costs up to the gpt-5.5 200k cap ≈ $6, or ~$9 at pro@high.
**Impact:** A tight-budget run can overspend by several dollars before the next boundary check stops the cluster; the cap is never DEFEATED but a fresh orchestrator mis-budgets. The $1 self-skip estimate is ~6× below meta's worst case. **Confidence: high.**

### M5 — Setup/convert skills never teach the `use_text_feedback:false` mitigation, and their starter run.json omits the key
**kind:** doc-unclear. **Source:** boot/no-spoil dimension.
**Locations:** `skills/shinka-setup/SKILL.md:51-60`, `skills/shinka-convert/SKILL.md:76-82`, `skills/shinka-setup/scripts/orchestrator_run.json`, `skills/shinka-convert/scripts/orchestrator_run.json`.
**Intended/Actual:** Both skills teach the sentinel + no-spoil goal but grep confirms neither mentions `use_text_feedback`, and neither starter JSON includes the key (defaults feedback-ON). The mitigation lives only in `orchestrator/SKILL.md`.
**Impact:** A setup-skill user can author a clean `task_sys_msg` yet leave the spoil knob at default; teachability gap at the precise step the no-spoil decision is made. (Even threaded, H9/M6 show the flag is not currently complete.) **Confidence: high.**

### M6 — The meta round surfaces the evaluator's `error_traceback` into briefs, a second eval-text channel `use_text_feedback:false` does NOT suppress
**kind:** design-gap. **Source:** boot/no-spoil dimension.
**Locations:** `orchestrator/scripts/meta_summarize.py:174-199`, `orchestrator/harness/run_window.py:1088-1130`, `orchestrator/harness/run_window.py:573-576`, `shinka/core/sampler.py:135-141`, `orchestrator/SKILL.md:234-240`.
**Intended/Actual:** The auto-meta round (auto_meta default True) bakes each recent program's `error_traceback` exception line into the meta user message (meta_summarize.py:178-198), NOT gated by `use_text_feedback`. The output becomes `meta_directions` + per-island briefs that ride into every subsequent mutation prompt.
**Impact:** Compounds H9: at least TWO eval-text channels escape the flag (immediate-fix ancestor + meta traceback). An orchestrator believing it sealed all leakage is wrong; a held-out value in an exception tail propagates into briefs. Bounded (one-line tail; most evaluators don't write held-out numbers into exceptions). **Confidence: high.**

### M7 — `island_selection_strategy="equal"` is a silent no-op (identical to uniform); DatabaseConfig + legacy sampler advertise "fewest-populated"
**kind:** code-doc-mismatch. **Source:** parent-sampling dimension.
**Locations:** `orchestrator/scripts/sample_parent.py:110-140`, `shinka/database/dbase.py:70`, `shinka/database/island_sampler.py:101-121`.
**Intended/Actual:** `DatabaseConfig.island_selection_strategy` advertises uniform/equal/proportional/weighted with `equal` = sample the fewest-populated island. The live `_select_island` only branches on `proportional`/`weighted`; everything else (including `equal`) falls to `rng.choice(islands)` (uniform). Reproduced: `equal` gave ~50/50 identical to uniform, where legacy `equal` would always pick the minority island.
**Impact:** An orchestrator who sets `equal` to rescue a starving island gets plain uniform with no rebalancing and no diagnostic. **Confidence: high.**

### M8 — SKILL.md "one island dominates → weighted" points the WRONG way (live `weighted` reinforces the dominant island)
**kind:** code-doc-mismatch. **Source:** parent-sampling dimension.
**Locations:** `orchestrator/scripts/sample_parent.py:117-138`, `orchestrator/SKILL.md:563`, `shinka/database/island_sampler.py:124-216`.
**Intended/Actual:** SKILL.md:563's only imbalance remedy is "one island dominates → weighted," implying it spreads effort AWAY from a dominant island (legacy `weighted` = fitness/count). But live `weighted` weights each island purely by BEST `combined_score` (no inverse-count), and `proportional` weights by population. Reproduced: `weighted` concentrated MORE on the highest-fitness island. No available value de-concentrates a dominant island.
**Impact:** A fresh orchestrator following the doc makes a wrong load-bearing call — the documented fix amplifies the imbalance it should cure. **Confidence: high.**

### M9 — shinka's keep-the-better crowding niching is dead code on the default route
**kind:** dead-code. **Source:** novelty dimension.
**Locations:** `shinka/database/dbase.py:2413-2418`, `shinka/database/dbase.py:2493-2554`, `shinka/database/dbase.py:106`, `shinka/database/dbase.py:2248-2287`.
**Intended/Actual:** The framework ships a crowding strategy that "replaces the most similar program if better." But `archive_selection_strategy` defaults `fitness` and is never set to `crowding` anywhere in `orchestrator/` (grep: zero occurrences), so `_update_archive_crowding` + `_find_most_similar_in_archive` are never invoked. The schema doesn't expose the knob, so it can't be turned on without editing foundation config.
**Impact:** The one mechanism that could satisfy keep-the-better is unreachable AND undiscoverable; even enabled it fires post-eval, whereas H5's gate already dropped the better candidate pre-eval. **Confidence: high.**

### M10 — Docs frame novelty as "reject near-duplicates" only — never disclose the better near-duplicate is dropped
**kind:** code-doc-mismatch. **Source:** novelty dimension.
**Locations:** `orchestrator/scripts/novelty_check.py:1`, `orchestrator/scripts/novelty_check.py:9-14`, `orchestrator/SKILL.md:208-210`, `orchestrator/SKILL.md:272`, `orchestrator/SKILL.md:491`, `orchestrator/SKILL.md:554`.
**Intended/Actual:** Every doc surface frames novelty purely as rejection; "better"/"crowding"/"niching"/"keep" never appear in any novelty doc passage (grep-confirmed). None warns that a strictly-better near-duplicate is discarded unevaluated.
**Impact:** A fresh orchestrator believes the implemented behavior already satisfies keep-the-better and won't know raising `code_embed_sim_threshold` is the only mitigation. **Confidence: high.**

### M11 — `novelty_acceptance_rate` reports 1.0 (not null) when novelty is disabled, defeating the null-vs-0 discipline that feeds rollback_decision
**kind:** code-doc-mismatch (merges two reports: the diagnostics-dimension finding and the novelty-dimension contract-violation finding).
**Locations:** `orchestrator/harness/run_window.py:777`, `orchestrator/harness/run_window.py:694`, `orchestrator/scripts/diagnostics.py:109-115`, `orchestrator/harness/rollback_decision.py:64-69`, `orchestrator/SKILL.md:150`, `orchestrator/SKILL.md:272`.
**Intended:** `diagnostics.py:112-115` (O10/K15) returns `novelty_acceptance_rate=None` "when NO novelty events occurred (UNKNOWN), so rollback_decision doesn't mistake 'no data' for 'perfectly diverse'."
**Actual:** `counters["novelty_accepts"] += 1` at run_window.py:777 is UNCONDITIONAL — it sits after the apply-exhausted (687) and novelty-reject (728) returns but is NOT guarded by `enable_novelty` (the only guards are at 694/234/756). `enable_novelty` is opt-in (no default). So on an `enable_novelty:false` run every evaluated slot bumps accepts while rejects stays 0 → `accepts/(accepts+0)=1.0`, not None. The None branch is reached only when ZERO slots evaluate — not the novelty-off case the comments claim to handle. diagnostics receives only the two raw counts, never the flag.
**Impact:** On a novelty-off run, the agent's diversity read and `rollback_decision`'s diversity-collapse arm are fed a fabricated "perfectly diverse" 1.0 instead of being skipped — the exact failure O10/K15 tried to prevent, in the configuration where there is no data. **Confidence: high.**

### M12 — `novelty_acceptance_rate=1.0` even with novelty ON when every candidate hit `n_compared=0` (no real comparison)
**kind:** edge-case. **Source:** novelty dimension.
**Locations:** `orchestrator/harness/run_window.py:777`, `orchestrator/harness/run_window.py:694-707`, `orchestrator/scripts/diagnostics.py:109-115`.
**Intended/Actual:** When `enable_novelty` is on but every candidate hit `n_compared=0` (an island with no other embedded programs yet, or the seed embedding returned []), `novelty_check` returns `accept=True` and the slot still bumps accepts at 777. With zero rejects the rate is 1.0 ("perfectly diverse") though NO real comparison happened. The distinguishing per-program `novelty_n_compared` exists only as metadata for accepted candidates, never as a window aggregate.
**Impact:** The orchestrator (or the rollback diversity arm) could conclude diversity is healthy when the gate is inert. Mitigated by the seed embed and queryable per-program metadata, but the window aggregate alone misleads. Distinct mechanism from M11 (novelty-on vs novelty-off). **Confidence: high.**

### M13 — "Assign already-working directions to existing program entries" does not exist; the meta prompt steers AWAY from tried directions
**kind:** design-gap. **Source:** meta-round dimension.
**Locations:** `orchestrator/scripts/meta_summarize.py:101-122`, `orchestrator/harness/run_window.py:1118-1130`, `shinka/database/dbase.py:795-826`.
**Intended/Actual:** Meta should ASSIGN directions that already correspond to working code onto existing program entries. But `island_directions` are recorded only as per-island briefs via `record_meta_brief` (signature `island_idx/generation/content/stage` — NO program linkage), and the meta system prompt (line 118) explicitly says "prioritize directions NOT already tried."
**Impact:** The user-intended "label proven directions onto their entries" capability is intent-only; meta only injects forward-looking directions. **Confidence: high.**

### M14 — Per-island differentiation is LLM-prompt-only — no code-side count/validity/distinctness enforcement
**kind:** design-gap. **Source:** meta-round dimension.
**Locations:** `orchestrator/scripts/meta_summarize.py:113-122`, `orchestrator/scripts/meta_summarize.py:246-254`, `orchestrator/harness/run_window.py:1118-1130`, `orchestrator/SKILL.md:214-216`.
**Intended/Actual:** The "one differentiated direction per live island" guarantee rests entirely on the LLM obeying the prompt. `_parse_meta` (246-254) accepts whatever it returns, dropping only non-int `island_idx`; it never checks count==live-islands, liveness, full coverage, or text distinctness. An omitted island keeps its STALE prior brief or falls back to the global direction. SKILL.md:214-216 makes catching "all briefs reading the same" the agent's MANUAL job — acknowledging there is no code guarantee.
**Impact:** With a less-compliant model or many islands, some islands silently keep an old brief while others update (or a brief lands on a non-live island), partially collapsing the H1-fix differentiation, with no diag signal. (The brief→prompt coupling itself DOES work.) **Confidence: high.**

### M15 — Per-island brief is one fixed text injected every gen, and `replace` mode discards the per-gen weighted global direction (default-path exploration collapse)
**kind:** design-gap (merges two meta/coupling findings: "one fixed text per island" + "replace silently discards the weighted global direction").
**Locations:** `orchestrator/harness/run_window.py:546-557`, `orchestrator/harness/run_window.py:562-577`, `orchestrator/harness/run_window.py:288-306`, `shinka/core/sampler.py:96-97`, `shinka/database/dbase.py:891-920`.
**Intended/Actual:** The design positions global meta directions as a per-gen WEIGHTED sample (one of several per mutation) so intra-island exploration stays varied; a brief adds a per-island steer. But meta emits EXACTLY ONE direction per island; `get_latest_meta_brief` returns the single latest content; run_window passes it verbatim into every generation. In default `replace` mode `sampler.py:96-97` overwrites `meta_recommendations` with the brief, discarding the freshly weighted per-gen global sample (562). Briefs are auto-written for EVERY live island each window, so this is the DEFAULT state.
**Impact:** On any brief island (i.e. all of them, by default) every mutation in the window sees identical system-prompt guidance — the weighted-sample variety the docs advertise is thrown away, narrowing per-island exploration. **Confidence: high.**

### M16 — Recycled island index inherits the evicted island's stale brief (wrong-direction injection)
**kind:** edge-case. **Source:** brief-coupling dimension.
**Locations:** `shinka/database/islands.py:1040-1061`, `shinka/database/islands.py:1010-1038`, `shinka/database/dbase.py:891-908`, `orchestrator/harness/run_window.py:546-557`.
**Intended/Actual:** With `max_islands>0`, `allocate_island_index_for_spawn` evicts the worst island and RETURNS ITS INDEX for reuse. `_evict_island` de-archives the old rows but does NOT touch `meta_briefs`, and `get_latest_meta_brief` has no eviction/recency filter, so the recycled index reads the OLD island's brief until the next meta overwrite.
**Impact:** On the first window after a spawn-with-eviction, the new island is steered by a direction authored for the retired population — wrong-direction injection. Self-heals next meta round; only on the non-default `max_islands>0` path. **Confidence: high.**

### M17 — Docs frame the brief as a per-island "direction" coupling stronger than the text-only swap
**kind:** code-doc-mismatch. **Sources:** brief-coupling dimension + consistency sweep.
**Locations:** `orchestrator/SKILL.md:567-569`, `orchestrator/scripts/island_brief.py:9-15`, `shinka/database/dbase.py:805-809`, `shinka/core/sampler.py:91-97`.
**Intended/Actual:** SKILL.md, `island_brief.py`, and a dbase comment all frame the brief as THE differentiation mechanism without noting it is limited to system-prompt TEXT (the exemplar code is identical across islands because `sample_parent` ignores briefs). `sampler.py:91-95` carries stale "Phase 2 of research-grounding" language.
**Impact:** A fresh orchestrator believes briefs steer both guidance AND which code each island evolves from, and may over-trust island differentiation or not realize a `sample_parent` rewrite is needed — a load-bearing mental-model gap. **Confidence: high.**

### M18 — No per-island archive capacity exists — the archive is a single GLOBAL size cap; docs claim "bounded per-island membership"
**kind:** code-doc-mismatch. **Source:** island-policy dimension.
**Locations:** `shinka/database/dbase.py:2399`, `shinka/database/dbase.py:2230-2246`, `shinka/database/dbase.py:2420-2491`, `orchestrator/SKILL.md:583`, `orchestrator/NOTES.md:160`.
**Intended/Actual:** SKILL.md:583 + NOTES.md:160 assert "bounded per-island membership," but `_update_archive` counts `SELECT COUNT(*) FROM archive` with NO island filter, `_get_archive_programs` does a global JOIN, and eviction picks the GLOBALLY worst program. One prolific island can occupy most of the 40 slots.
**Impact:** The scalability rationale is false. Parent selection IS island-scoped (so starved islands keep their own parents), so the skew is concentrated in the shared elite/inspiration pool and the novelty crowding reference set — weakening cross-island inspiration diversity. **Confidence: high.**

### M19 — `island_policy.main()` spawn/migrate decisions never run on the default route (`island_policy_driven` defaults false)
**kind:** dead-code. **Source:** island-policy dimension.
**Locations:** `orchestrator/harness/run_window.py:1008-1017`, `orchestrator/scripts/island_policy.py:132-210`, `orchestrator/SKILL.md:561`, `orchestrator/SKILL.md:278`.
**Intended/Actual:** SKILL.md presents `island_policy.py` as the live Island-structure control surface, but `main()` is invoked only inside `if evo.get("island_policy_driven"):` (default false). On the default route spawning/migration is handled by db_config thresholds (also off), so the whole spawn/migrate logic is dead. Only `island_health()` from this file is on the default path.
**Impact:** A fresh orchestrator could believe rewriting `island_policy.py` affects the running search; by default it is inert. **Confidence: high.**

### M20 — Re-running `--warmup` does not reset the throwaway workspace → 2nd+ run is NOT fresh and steps.jsonl concatenates
**kind:** code-doc-mismatch (merges the warmup-dimension finding and the edge-case-hunt finding).
**Locations:** `orchestrator/harness/run_window.py:1316-1330`, `orchestrator/harness/run_window.py:198-208`, `orchestrator/harness/run_window.py:967`, `orchestrator/harness/journal.py:286-293`, `orchestrator/SKILL.md:76`, `orchestrator/SKILL.md:187-188`.
**Intended/Actual:** SKILL.md tells the agent to "RESTART warmup until the window is meaningful" and promises warmup confirms the inner loop "on a FRESH archive." But `--warmup` only assigns paths; it NEVER deletes the existing warmup workspace. On a 2nd `--warmup` without a manual `--cleanup-warmup`: `_bootstrap_initial` early-returns on `count.total>0`, so the archive holds the prior run's gens (parent sampling + novelty run against a POPULATED archive); `next_gen` keeps climbing; steps.jsonl concatenates. No SKILL text instructs cleanup BETWEEN reruns.
**Impact:** The standard "fix → rerun --warmup → re-read steps.jsonl" loop silently drifts off the fresh-archive contract from the 2nd iteration; the agent can mistake a stale trace for the corrected policy or declare the loop "fixed" on populated-archive evidence. **Confidence: high.**

### M21 — COMBINE triage branch cannot target the "closest existing program" — the grounding run has no parent/island-targeting knob
**kind:** design-gap. **Source:** deep-research dimension.
**Locations:** `orchestrator/SKILL.md:346-347`, `orchestrator/SKILL.md:350-357`, `orchestrator/harness/run_window.py:462-472`, `orchestrator/scripts/sample_parent.py:217-219`.
**Intended/Actual:** SKILL Triage tells the agent to "combine it into the closest existing program with the grounding run." But the grounding run is a normal window — `_sp_payload` carries no `island_idx`/program-targeting field, and no `seed_program_id`/`force_parent`/`combine_into` knob exists (grep-confirmed). `sample_parent` auto-selects the island and weighted-samples a parent. The COMBINE intent IS achievable as "grounding run, no `spawn_island`" (the injected `meta_directions` biases the LLM), but the "closest program" precision has no mechanism.
**Impact:** A fresh orchestrator cannot direct the technique at the "closest" program; it folds into an arbitrary high-scorer. The COMBINE rationale is half-served, not blocking. **Confidence: high.**

### M22 — SKILL teaches hand-editing `window_state` as the default continuation; `--resume` is documented only as kill-recovery
**kind:** doc-unclear. **Source:** outer-loop/cadence dimension.
**Locations:** `orchestrator/SKILL.md:83-91`, `orchestrator/SKILL.md:126-143`, `orchestrator/SKILL.md:177-179`, `orchestrator/SKILL.md:524-525`, `orchestrator/harness/run_window.py:943-963`, `orchestrator/harness/run_window.py:1346-1358`, `orchestrator/harness/journal.py:177-181`.
**Intended/Actual:** The loop is event-driven; `--resume` carries `window_index` + `prior_low_streak` across each expected cluster boundary by reading the journal. But all three `--resume` mentions frame it as kill-recovery; the canonical launch shows bare `--until-decision` with a static `window_state {0,0}`. `main()` reads the streaks only from `cfg.window_state`; nothing writes the config back; only `--resume` backfills from the journal.
**Impact:** A literal-minded agent relaunches with the static config. Forgetting `prior_low_streak` is the material half: with an unchanged fingerprint the fair-trial reset does NOT fire, so the streak comes from the static 0, silently wiping a cross-boundary stagnation streak and delaying detection. Forgetting `window_index` restarts at 0, and `append_window` (no dedup) writes DUPLICATE rows, corrupting `j_trajectory` and a later `--resume`. Exactly the footgun `--resume` was built to remove, on the hottest path. **Confidence: high.**

### M23 — SKILL self-contradicts on DR cadence and warmup teaches only failure-signals
**kind:** doc-unclear. **Source:** teachability sweep.
**Locations:** `orchestrator/SKILL.md:15-31`, `orchestrator/SKILL.md:99-103`, `orchestrator/SKILL.md:316-319`, `orchestrator/SKILL.md:181-221`.
**Intended/Actual:** Lines 20-22 say DR has no cadence under the ORCHESTRATOR hat; 30-31/99-101/316-319 put DR on the framework-audit taper every control-return. Warmup (181-221) lists only nine failure-signals, no HEALTHY `steps.jsonl` shape.
**Impact:** The agent cannot tell if DR is per-return or ad hoc (feeds the termination ambiguity H6/H8), and cannot positively confirm warmup soundness (assumes absence=health). **Confidence: high.**

### M24 — SKILL points the agent at `evo.meta_directions` for DR-novelty judgment, but auto-meta/DR directions never persist there
**kind:** code-doc-mismatch (merges deep-research dimension + teachability sweep).
**Locations:** `orchestrator/SKILL.md:325-326`, `orchestrator/harness/run_window.py:1114-1117`, `orchestrator/scripts/meta_summarize.py:24-25`, `orchestrator/harness/journal.py:406-411`.
**Intended/Actual:** SKILL:325-326 tells the agent to consult `evo.meta_directions` to judge whether a DR idea is already known. But meta writes directions into the LIVE in-memory `evo` dict (1115), never to disk; each control-return is a fresh subprocess re-loading `evo` from run.json, so the field reverts to the configured (empty) value when the agent is awake. The durable record is `calls.jsonl` (kind meta/dr) + DB briefs — reachable via `journal.read_calls(kind=...)`, which IS named in the same sentence.
**Impact:** A fresh orchestrator may inspect only `evo.meta_directions` (stale/empty) and under-count what's been tried, mis-judging a technique as NOVEL → wasted spawn_island + grounding run. Teachability gap, not missing data. **Confidence: high.**

### M25 — CI Ruff/Mypy steps and the pre-push hook target a non-existent `tests/` directory — every push is red
**kind:** doc-stale. **Source:** completeness sweep.
**Locations:** `.github/workflows/ci.yml:29`, `.github/workflows/ci.yml:32`, `.githooks/pre-push:8`, `.githooks/pre-push:11`, `pyproject.toml [tool.pytest.ini_options] testpaths`.
**Intended/Actual:** After the Azure-only prune the only tests live under `orchestrator/tests/` (pyproject testpaths). ci.yml and the pre-push hook still run `ruff check tests` and `mypy … tests/test_*.py tests/conftest.py`. Verified `tests/` does not exist (0 git-tracked `tests/` files, no `conftest.py`), so ruff and mypy error on the missing path before the (correct) pytest step.
**Impact:** Every push/PR shows red CI (two of three steps hard-fail) and `git push` via the hook aborts at ruff. Doesn't affect a live run, but masks real lint/type signal and trains the user to ignore red CI. **Confidence: high.**

### M26 — No test exercises the live stagnation knobs (`stagnation_rel_frac`/`stagnation_abs_floor`); a regression in the scale-free trigger ships green
**kind:** design-gap. **Source:** completeness sweep.
**Locations:** `orchestrator/tests/smoke_test.py:113`, `orchestrator/tests/test_improvements.py:228`, `orchestrator/harness/validate_strategy.py:113-114`, `orchestrator/scripts/stagnation_detector.py:84-98`.
**Intended/Actual:** The stagnation trigger is `Δ ≤ max(abs_floor, rel_frac·max(s_start,0))`; the `rel_frac` scale-free term is the whole F12 fix. Every test sets only the DEPRECATED `tau` alias; grep finds zero references to `stagnation_abs_floor`/`stagnation_rel_frac`. The relative branch and abs_floor-vs-tau precedence are never asserted.
**Impact:** The most consequential trigger in the loop has its core scale-free behavior untested; a future `stagnation_detector` rewrite can silently regress to over-triggering on small-score tasks (exactly cnot, ~0.01 gains) and the suite stays green. **Confidence: high.**

---

## Findings — Low / Nits

> Grouped by area; each is bounded, non-default-path, or cosmetic. Severity in brackets.

**Boot / spoil**
- **L1 [low] doc-unclear** — No worked "surface the eval CONSTRAINT, hide the held-out NUMBERS" example. `orchestrator/SKILL.md:232-240`, `:507-513`, `tasks/cnot_grid_synth/README.md:63-66`. The rule is abstract; the only concrete spoiler discussion is task-local and never fed to the LLM. A borderline constraint is left to judgment with no exemplar.
- **L2 [low] doc-unclear** — Setup skill frames evaluator output as a 5-field schema rather than foregrounding "a single combined NUMBER + errors as a rejection signal." `skills/shinka-setup/SKILL.md:44-50`, `:144-161`, `:235`. Contract is present; framing is diffuse.
- **L3 [medium→test] edge-case** — The P6-T3 "complete suppression" test never exercises the leaking ancestor-history channel. `orchestrator/tests/test_improvements.py:605-622`. The test omits `ancestor_inspirations`, so H9's leak has no failing test and the green test certifies a guarantee the code doesn't provide. *(Rated medium for its false-confidence-by-assertion; listed here with the boot cluster.)*

**Inner loop / recording**
- **L4 [low] code-bug** — Missing `<NAME>/<DESCRIPTION>` persists the literal string `"none"` into `patch_name` + `code_diff`. `orchestrator/scripts/mutate.py:186-187`, `shinka/llm/llm.py:786`, `orchestrator/scripts/record_policy.py:57-58`, `orchestrator/harness/run_window.py:851`. `extract_between` returns `"none"` (truthy) so the None-filter doesn't drop it; pollutes shinka-inspect's change summary.
- **L5 [low] edge-case** — A no-op rewrite byte-identical to its parent (`num_applied≥1`) is recorded `applied:true` and archived as a parent-identical child; the 60% diff path hits it too (not just full/cross). `shinka/edit/apply_full.py:266`, `shinka/edit/apply_diff.py:647-648`, `orchestrator/harness/run_window.py:672`. Gated by default-on novelty (dropped at sim≈1.0); with novelty off, a wasted Azure call yields a duplicate row + floored reward.
- **L6 [low] edge-case** — An apply-exhaustion INSIDE the immediate-fix loop is invisible to `apply_exhausted_count` (folded into `eval_failures`) and overwrites the original mut's metadata. `orchestrator/harness/run_window.py:416-418`, `:759-776`, `:843-861`, `orchestrator/scripts/diagnostics.py:163-167`. Diagnostic attribution skew on a fix-heavy task.
- **L7 [low] edge-case** — `max_output_tokens` cap-hit ("incomplete") partial output is parsed/applied as a normal mutation with no truncation signal. `orchestrator/scripts/_azure.py:140-146`, `orchestrator/scripts/mutate.py:163-196`. Mostly self-correcting (truncated patch usually fails to apply or is a no-op).
- **L8 [nit] edge-case** — Fix-retry Azure calls bypass select-mode, so `n_submitted` (the model_collapse signal) undercounts physical calls for fix-heavy arms. `orchestrator/harness/run_window.py:412-416`, `orchestrator/scripts/select_llm.py:129/137`, `shinka/llm/prioritization.py:450-456`. Cost is still captured; only the per-arm pull tally undercounts.
- **L9 [nit] doc-stale** — `mutate.py` docstring/INPUT document `max_attempts` while the live route uses `evo.max_patch_attempts` (value 3==3 matches; source label stale). `orchestrator/scripts/mutate.py:17/38`, `orchestrator/harness/run_window.py:405/642`.

**Parent sampling**
- **L10 [low] edge-case** — Cross-island mode: the brief is fetched for `_select_island`'s island but the child is placed on the PARENT's island, so a brief can steer a child on the wrong island. `orchestrator/scripts/sample_parent.py:217-258`, `shinka/database/islands.py:91-103`. Non-default (`enforce_island_separation:false`) only.
- **L11 [low] edge-case** — An unknown/typo'd `island_selection_strategy` is silently swallowed as uniform (no error), unlike the legacy `create_island_sampler` which raises. `orchestrator/scripts/sample_parent.py:116-140`, `shinka/database/island_sampler.py:249-253`. A fat-fingered lever flip is invisible.
- **L12 [nit] doc-stale** — `sample_parent` docstring lists `num_islands` as a policy knob, never read by the policy (island set derived from data). `orchestrator/scripts/sample_parent.py:16-17`.
- **L13 [nit] doc-unclear** — `validity_floor` does not rescue a fully-flat all-equal archive (the symptom the doc names), only a spread one. `orchestrator/scripts/sample_parent.py:232-242`, `orchestrator/SKILL.md:556`. Functional on spread pools; wording oversells the flat case.
- **L14 [nit] inconsistency** — `enforce_island_separation` default disagrees: live sampler/config True, dead legacy `inspirations.py` False. `shinka/database/inspirations.py:55/156`, `orchestrator/scripts/sample_parent.py:224`, `shinka/database/dbase.py:67`. Latent rewrite hazard only.

**Novelty**
- **L15 [low] code-bug** — `max_similarity` initialized to 0.0 mis-reports `most_similar_id` as None when the only neighbors have negative cosine. `orchestrator/scripts/novelty_check.py:70/83-86`. Shinka uses −1.0/−inf. Observability only; accept decision unaffected.

**Meta round**
- **L16 [low] edge-case** — Global `meta_directions`/`meta_failure_note` are in-memory only and reset to run.json at each control-return. `orchestrator/harness/run_window.py:1114-1117/1308-1309`, `orchestrator/scripts/_common.py:263-282`. The first window of each cluster runs with no global direction until its own window-1 meta finishes.
- **L17 [low] edge-case** — A set `failure_note` is never cleared when a later window has no failures (monotonic within a cluster). `orchestrator/harness/run_window.py:1116-1117`, `orchestrator/scripts/meta_summarize.py:112/245`. An outdated caution rides on; resets next cluster.
- **L18 [low] doc-unclear** — Meta output (directions/failure_note/island_directions) is not surfaced into the control-return diag. `orchestrator/harness/run_window.py:1109-1137`, `orchestrator/scripts/diagnostics.py:176-209`, `orchestrator/SKILL.md:148-160/214-216`. Checking differentiation requires opening a separate journal stream.
- **L19 [low] edge-case** — Budget-skipped meta is invisible (no journal trace, no diag flag) and the default $1.0 estimate over-skips. `orchestrator/scripts/meta_summarize.py:284-292`, `orchestrator/harness/run_window.py:1099/1110`. Near end-of-run, islands silently stop receiving briefs.
- **L20 [low] dead-code** — `shinka/prompts/prompts_meta.py` (META_STEP1/2/3) is dead with no live consumer (re-exported only). `shinka/prompts/prompts_meta.py:1-128`, `shinka/prompts/__init__.py:25-32/58-63`, `shinka/core/__init__.py:3`.
- **L21 [low] doc-unclear** — DR grounding-run doc pins `evo.meta_directions` but omits `auto_meta:false`, so the per-window meta overwrites it after window 1. `orchestrator/SKILL.md:350-357`, `orchestrator/harness/run_window.py:1088/1114-1115`. A multi-window grounding run drifts off the pin.
- **L22 [nit] inconsistency** — `num_islands` is passed to meta + declared in its input contract but never consumed. `orchestrator/harness/run_window.py:1104`, `orchestrator/scripts/meta_summarize.py:58`.

**Brief coupling**
- **L23 [low] design-gap** — No direction→program correspondence mapping exists anywhere; the "insert programs that correspond to the sampled direction" half is structurally impossible. `shinka/database/dbase.py:650-660/795-826`, `orchestrator/scripts/sample_parent.py:247-254`. *(Rated high as a pure design-gap by its source; the operational harm overlaps H1, so it is consolidated here as the data-model root cause.)*
- **L24 [low] dead-code** — `structured_json` per-island payload is written/returned but never consumed into any prompt. `orchestrator/scripts/island_brief.py:24/58`, `shinka/database/dbase.py:916`, `orchestrator/harness/run_window.py:555` (reads only `.content`). A grounded DR `structured_json` is silently ignored.
- **L25 [low] code-doc-mismatch** — `brief_compose_mode=replace` works but only as a system-message text substitution, and the brief is invisible on `cross` gens. `orchestrator/scripts/construct_mutation_prompt.py:97-104`, `shinka/core/sampler.py:96-97/134-141`, `orchestrator/SKILL.md:562`.

**Island policy**
- **L26 [low] design-gap** — `island_policy` "retire" is never executed (`retire_island` hardcoded None); a non-destructive retire DOES exist but only via the spawn-cap allocator. `orchestrator/scripts/island_policy.py:191/198`, `shinka/database/dbase.py:2737-2741`, `shinka/database/islands.py:1010-1038`, `orchestrator/NOTES.md:129-131`. Rewriting `island_policy` to return a retire index has no effect.
- **L27 [low] dead-code** — `_get_programs_for_island` is a body-less, never-called stub at file end. `shinka/database/dbase.py:3301-3304`. A future caller would silently get None.
- **L28 [low] code-bug** — Scheduled migration stamps the run's MAX generation, not the triggering one. `shinka/database/dbase.py:2648-2653/1117-1119`, `shinka/database/islands.py:572-580`. Dormant (migration_rate 0.0); cosmetic field error.
- **L29 [low] edge-case** — Cap counts only correct-bearing islands; an incorrect-only island is invisible to the cap and never evictable. `shinka/database/islands.py:1040-1061/996-1008/36-48`. Benign in practice (spawn seeds correct programs).
- **L30 [low] edge-case** — No guard that `max_islands >= num_islands`; a sub-`num_islands` cap silently evicts an original island on the first spawn. `shinka/database/islands.py:1040-1061/627-724`, `shinka/database/dbase.py:79-88`. Off-nominal config only.
- **L31 [nit] doc-stale** — SKILL.md:242 says "num_islands (4 default)"; code default is 2. `orchestrator/SKILL.md:242/513`, `shinka/database/dbase.py:55`. The example config sets 4 explicitly; an omitted key yields 2. *(Merges the island-dimension and consistency-sweep reports.)*

**Deep research**
- **L32 [low] dead-code** — `DeepResearchModel` class is never imported/exported. `shinka/llm/agent/dr_client.py:288-317`, `shinka/llm/agent/__init__.py:15-18`. The active path is `run_dr_call`.
- **L33 [low] doc-stale** — `DeepResearchModel` docstring references a non-existent `DeepResearchSummarizer` and a wrong timeout (30 min vs actual 60 min). `shinka/llm/agent/dr_client.py:294-298/58/307`.
- **L34 [low] edge-case** — On a token-free DR failure that ran a `web_search`, the search surcharge is dropped (gated on `_billed>0`). `orchestrator/scripts/deep_research.py:165-167`. At most ~$0.30 uncounted; only the zero-token-failure corner; untested.
- **L35 [low] doc-unclear** — SKILL points the agent at `evo.meta_directions` for DR-novelty (see M24); the durable source is `read_calls(kind='meta'/'dr')`, named in the same sentence but not foregrounded. *(Consolidated into M24.)*
- **L36 [nit] doc-stale** — `deep_research.py` INPUT docstring omits the `background` knob it reads. `orchestrator/scripts/deep_research.py:22-34/152`.

**Outer-loop / cadence**
- **L37 [low] doc-unclear** — `return_reason`'s stagnation/taper/windows_done values are never mapped to act-vs-relaunch (only `budget_exhausted` is taught). `orchestrator/SKILL.md:51/99-103/160`, `orchestrator/scripts/cadence_policy.py:54/75`, `orchestrator/harness/run_window.py:1196-1207`. Step 4's both-checks-every-return instruction makes the field partly redundant.
- **L38 [low] code-doc-mismatch** — `windows_run` is documented always-attached but is set only on the `--until-decision` path, never on the bounded `--windows N` measure window. `orchestrator/harness/run_window.py:1198-1207`, `orchestrator/scripts/diagnostics.py:35`, `orchestrator/SKILL.md:160`. `total_cost`/`budget_remaining` are on both paths; `windows_run` is the precise mismatch.
- **L39 [low] design-gap** — "Do not stop until a termination criterion is met" is doc-only (no enforcement); the only relaunch-vs-terminate signal is the `return_reason` string the agent must interpret. `orchestrator/SKILL.md:40-43/598`, `orchestrator/harness/run_window.py:1207-1220` (finalize gated strictly on `budget_exhausted` at 1213; `ok=True` set unconditionally at 1219). Faithful agent-only design; a non-budget exit looks superficially complete.
- **L40 [nit] dead-code** — `recent_work_axes`/`work_audit`/`work_dr` two-axis reader is implemented + tested but wired into no cadence decision (honest forward hook). `orchestrator/harness/journal.py:382-388`, `orchestrator/harness/run_window.py:1182-1193`, `orchestrator/scripts/cadence_policy.py:48-76`.
- **L41 [nit] inconsistency** — `cadence_policy` receives two same-named streaks — `work_low_streak` (the real driver) and an inert informational `low_streak` — a naming-collision trap for a future cadence rewriter. `orchestrator/scripts/cadence_policy.py:26/30/59/67`, `orchestrator/harness/run_window.py:1187/1191`.
- **L42 [nit] edge-case** — Cadence/work-score knobs use `value or default`, so an explicit `low_threshold`/`base_low` of 0 is silently coerced; the threshold is computed independently in two files (desync risk). `orchestrator/harness/run_window.py:1149-1150`, `orchestrator/scripts/cadence_policy.py:57-58`, `orchestrator/harness/journal.py:391-403`.

**Budget / ledger**
- **L43 [low] code-bug** — Corrupt-recovery interleaving double-counts the just-appended cost in `append_window`/`append_intervention`/`log_call` (append-then-read where `read_run` repairs from streams that already include the appended line, then `add_cost` re-adds). `orchestrator/harness/journal.py:198-233/280-282/317-333`. Fail-SAFE (over-counts → trips cap early); the window path is safe (run_window.py:986 reads `total_cost` before the append). Reproduced.
- **L44 [low] edge-case** — Per-call "~$10" cap is enforced on OUTPUT tokens only; a large-input pro call can exceed $10 (~$3 input + ~$9 output). `orchestrator/scripts/_azure.py:32-45/109-113`, `orchestrator/SKILL.md:57-60`, `construct_mutation_prompt.py` (no input truncation). Negligible for small EVOLVE-BLOCK tasks.
- **L45 [low] edge-case** — `init_run` does not repair/refresh a corrupt run.json (early return on `os.path.exists`). `orchestrator/harness/journal.py:154-158`, `orchestrator/harness/run_window.py:920/940-941/947`. Railguard reads budget from cfg, not run.json; corrupt file repaired by the first `read_run`.
- **L46 [low] dead-code** — Legacy non-Azure mutate path: `client.query` exception is uncaught (would crash the window + drop billed cost). `orchestrator/scripts/mutate.py:174-184`, `shinka/llm/providers/model_resolver.py:22-47`. Unreachable in this Azure-only fork (`use_bg` always True).
- **L47 [low] code-bug** — `_write_json_atomic` does not fsync the parent directory after `os.replace`; the rename may not survive a hard crash. `orchestrator/harness/journal.py:88-98`. Self-healing via recompute-from-streams; weakens only run.json freshness. (Fixed tmp name also collides under concurrency, which the serial design avoids.)

**Model selection / bandit**
- **L48 [low] code-bug** — The per-gen seed does not control bandit posterior sampling — model selection is not seed-reproducible. `orchestrator/scripts/select_llm.py:64-75/124-144`, `shinka/llm/prioritization.py:68/158-174`. `np.random.seed(seed)` ≠ the bandit's `default_rng(None)`. Reproduced: same seed → different picks.
- **L49 [low] edge-case** — `reward_validity_floor` set to 0 (or None) collapses the correct-vs-failed separation (`0 or 0.0` → 0.0); undocumented foot-gun. `orchestrator/scripts/compute_reward.py:81-87`, `orchestrator/SKILL.md:557`. Non-default path.
- **L50 [low] dead-code** — `_posterior_batch` is dead on every default route and contains a loop-counter-shadowing bug (`k = self.cost_aware_coefficient` clobbers the `while k>0` counter). `shinka/llm/prioritization.py:645-723/679/701`. Unreachable (no caller passes `samples>1`).
- **L51 [low] doc-unclear** — `force_explore` has no auto-reset and the docs never instruct flipping it back; left on, selection stays permanently uniform. `orchestrator/SKILL.md:303-306/559`, `orchestrator/harness/run_window.py:605-611`, `orchestrator/scripts/select_llm.py:124-132`.
- **L52 [low] edge-case** — Cost-blend default (`cost_aware_coef=0.25`) keeps a genuinely-better-but-pricier arm pinned at the epsilon floor even after strong evidence. `shinka/llm/prioritization.py:617-628`, `orchestrator/SKILL.md:305-308/552`. Permanent lock-out is correctly AVOIDED (arm stays reachable at epsilon; collapse uses faithful counts-share); the cheap arm can still dominate the exploit. Documented lever exists.

**Diagnostics / stagnation**
- **L53 [low] edge-case** — Orchestrator-layer NaN handling relies entirely on the foundation `run_shinka_eval` guard; a custom evaluator bypassing it (NaN+correct=True) could NaN-poison `best_score` and silently disable stagnation (`float(x or 0.0)` keeps NaN; `max()` with NaN is order-dependent; `nan<=x` is False). `orchestrator/scripts/evaluate.py:147`, `orchestrator/scripts/diagnostics.py:92`, `orchestrator/scripts/archive_query.py:126-128`, `orchestrator/scripts/stagnation_detector.py:98`, `orchestrator/harness/journal.py:68`. On the default path (`run_shinka_eval`, `wrap_eval.py:454-464`) a NaN never reaches the archive as correct. Off-design defense-in-depth gap.
- **L54 [low] doc-unclear** — `diag['repair_mode_on']` is forward-looking (gates the NEXT window) while same-row `repair_*` counters reflect the PRIOR window's gate; the doc does not disambiguate. `orchestrator/scripts/diagnostics.py:174`, `orchestrator/harness/run_window.py:992-994`, `orchestrator/SKILL.md:421-422`. Off-by-one temporal misread risk only.
- **L55 [nit] verification** — Positive result: `errored_fraction` tombstone-exclusion, `prior_low_streak` threading, the stagnation J-formula, counts-based `model_collapse`, and div-by-zero safety all hold; the diagnostics field set matches the docs (no phantom field). `orchestrator/scripts/diagnostics.py:176-212`, `orchestrator/SKILL.md:148-160`, `orchestrator/harness/run_window.py:1170`. Recorded for completeness.

**Warmup**
- **L56 [low] code-doc-mismatch** — Warmup fires a real paid per-window meta-LLM call whose cost is discarded with the throwaway dir; the doc frames warmup as inner-loop mechanics only and never warns of the spend. `orchestrator/harness/run_window.py:1088/1093/1098/1316-1330/1259-1268`, `orchestrator/SKILL.md:78-82/184-188`. Doubly vacuous (goal `(none)` + tiny archive); untracked against `budget_usd`; mitigable via `evo.auto_meta:false` (undocumented).
- **L57 [low] design-gap** — Flaw-signal "byte-identical child with num_applied==0" is only half-observable from steps.jsonl — no candidate code/hash/diff is traced, so byte-identity is unverifiable. `orchestrator/SKILL.md:217-219`, `orchestrator/harness/run_window.py:660-687/870-872`. `num_applied` + `recorded_correct` ARE joinable via the generation stamp; only byte-identity is unobservable.
- **L58 [low] doc-unclear** — Flaw-signal "per-island briefs all reading the same" is barely surfaceable during warmup — brief text is a bool in steps.jsonl, and a default 1-window/1-iter warmup populates almost no islands. `orchestrator/SKILL.md:214-216`, `orchestrator/harness/run_window.py:589/1327-1330`, `orchestrator/scripts/meta_summarize.py:72`. The "meta call log" pointer is the correct-but-buried path.
- **L59 [low] doc-unclear** — Measure-window steps.jsonl (real-run dir, via `--trace-steps`) is never truncated and accumulates across every framework-audit measure window; the doc says it is "cleaned up after warmup." `orchestrator/harness/journal.py:286-293/432-438`, `orchestrator/harness/run_window.py:1259-1268`, `orchestrator/SKILL.md:389-392/466-467`. Mitigation: `read_steps(generation=...)` can filter.

**First-principles (search mechanics)**
- **L60 [medium] design-gap** — Migration is dead on the default route AND not exposed as a lever — the canonical island cross-pollination never happens. `shinka/database/dbase.py:65`, `shinka/database/islands.py:240-250`, `orchestrator/SKILL.md:513-515/543-565`. `migration_rate` appears in NO lever table; a fresh orchestrator has no documented way to enable it. *(Rated medium; pairs with H2.)*
- **L61 [medium] design-gap** — Novelty gate (cosine ≥0.99 on FULL-FILE embeddings, drop-newcomer) is structurally weak for code diversity: boilerplate-heavy tasks sit at 0.97–0.995 (over-aggressive), others near-inert; a single fixed threshold isn't a reliable cross-task control. `orchestrator/scripts/novelty_check.py:51-93`, `orchestrator/harness/run_window.py:694-728`. *(Rated medium; mechanism overlaps H5/M9.)*
- **L62 [medium] design-gap** — Bandit reward credit-assigns the sampled parent's task-difficulty (headroom) to the (model,effort) arm; no normalization for the parent's achievable delta. `orchestrator/scripts/compute_reward.py:81-87`, `orchestrator/harness/run_window.py:812-820`, `shinka/llm/prioritization.py:458-497`. An arm lucky in its parent draws looks better; decay launders it only slowly. *(Rated medium.)*
- **L63 [medium] design-gap** — The uncapped escalating taper (5,10,20,40,80…, `max_windows_per_call` default None) can leave a FIXABLE-but-sub-stagnation-threshold stall running many windows before the agent is woken; "low work" ≠ "nothing worth intervening on." `orchestrator/scripts/cadence_policy.py:54-76`, `orchestrator/scripts/stagnation_detector.py:97-100`. *(Rated medium.)*
- **L64 [low] design-gap** — Per-candidate evaluation is not seeded — a noisy evaluator makes the bandit reward + parent weights chase noise, and a run/finding can't be exactly reproduced. `orchestrator/harness/run_window.py:149-182`, `orchestrator/scripts/evaluate.py:16-40`. Task-dependent (cnot's evaluator hard-codes per-instance seeds); no framework warning/knob.
- **L65 [low] design-gap** — Parent-selection sharpness (λ=10) is aggressive with no early-phase/all-equal softening beyond the inert `validity_floor`; documented only as "sigmoid sharpness," not a diversity lever. `orchestrator/scripts/sample_parent.py:93-107/232-245`, `shinka/database/dbase.py:100`. Compounds the convergence findings.

**Edge-case / completeness**
- **L66 [low] inconsistency** — Max-islands eviction marks victims `_evicted_island` but not the `repair_tombstoned` key the sensors key on — evicted incorrect rows are miscounted as live-errored (keeping repair mode armed) and could be re-selected by the bootstrap fallback. `shinka/database/islands.py:1024-1033`, `orchestrator/scripts/archive_query.py:142-154`, `orchestrator/scripts/sample_parent.py:68-70`, `orchestrator/scripts/diagnostics.py:152-158`. Only on the non-default dynamic-islands path.
- **L67 [low] edge-case** — A candidate whose embedding silently fails is archived with NO embedding and becomes permanently invisible to the novelty gate. `orchestrator/harness/run_window.py:83-104/691-707/862-863`, `orchestrator/scripts/novelty_check.py:78-81`. The effective comparison set shrinks below archive size; no diagnostic surfaces it.
- **L68 [low] code-bug** — `test_island_policy_stagnation_parity` asserts a tautology (`True and …`), so it never checks the `enable_dynamic_islands` conjunct it claims to. `orchestrator/tests/test_parity.py:163-164`. Weakened regression coverage; benign today.
- **L69 [low] phantom-lever** — `configs/azure_default.yaml` advertises evo.* knobs (`meta_llm_models`, `novelty_llm_models`, `llm_dynamic_selection`, `max_api_costs`) the run.json path never reads; the README says "use as a base for evo.* overrides." `configs/azure_default.yaml:24-49`, `configs/README.md:9-14`, `orchestrator/harness/run_window.py:1093/604`. Copying these blocks yields silently-ignored knobs.
- **L70 [low] phantom-lever** — `inspiration_sort_order` is read by the prompt builder but never set by run_window and never documented as a lever (permanently "ascending"). `orchestrator/scripts/construct_mutation_prompt.py:79`, `orchestrator/harness/run_window.py:563-585`, `shinka/core/config.py:58`. An `evo.inspiration_sort_order` key is silently ignored.
- **L71 [nit] doc-stale** — `evaluate.py` emits `timed_out` (consumed by run_window's timeout/wrong-answer split) but its OUTPUT docstring omits it. `orchestrator/scripts/evaluate.py:154/27-40`, `orchestrator/harness/run_window.py:767`. A future trim of the foundation eval output could drop it.
- **L72 [nit] inconsistency** — The archive `code_diff` column stores the LLM `<DESCRIPTION>` prose, not an actual diff. `orchestrator/harness/run_window.py:851`, `orchestrator/scripts/archive_record.py:27`, `orchestrator/scripts/mutate.py:187`. Misleading for post-hoc lineage tooling.
- **L73 [nit] doc-stale** — `taxonomy.md` (HISTORICAL) cites five deleted files (`novelty_judge.py`, `summarizer.py`, `prompt_evolver.py`, `prompt_dbase.py`, `agent/tools/query_db.py`). `taxonomy.md:40-41/82/138/152/177`. Banner mitigates; links dead-end.
- **L74 [nit] doc-unclear** — `cost_aware_coef` lever note is subtly correct but fragile: SKILL cites engine default 0.0 while `shinka/defaults.py` returns 0.5 — only the orchestrator's empty-dict path makes the doc true. `orchestrator/SKILL.md:552`, `shinka/defaults.py:30-31`, `shinka/llm/prioritization.py:298`. Two coexisting "engine defaults."

**Rewrite-cycle (remaining)**
- **L75 [low] edge-case** — `deploy()` creates a wasted `state_*` snapshot before the foundation-write guard refuses a non-mutable target. `orchestrator/harness/strategy_store.py:170-171`. Fail-closed holds (no foundation write); only an orphan snapshot dir.
- **L76 [low] edge-case** — `decide()` fail-closed absent-check requires ALL three core sensors missing; a partial diag with eval-rate absent reads as perfect correctness. `orchestrator/harness/rollback_decision.py:97-103/60-61`, `orchestrator/scripts/diagnostics.py:176-204`. The real diag emits sensors all-or-nothing, so not currently produced; defensive only.
- **L77 [low] design-gap** — `snapshot_state` "no live window subprocess" precondition is doc-only, not code-enforced (no lockfile/PID guard). `orchestrator/harness/strategy_store.py:227-250`, `orchestrator/harness/run_window.py`. Safe under the sequential foreground flow; a future background window could capture a torn pkl/db. *(The contract's other KEY CHECKS — validate smokes select_llm, `_assert_mutable`+`MUTABLE_TARGETS`+M16, `deploy_bundle` mid-failure atomic restore — were independently verified HEALTHY.)*

---

## Code↔doc consistency (phantom levers, stale refs, contract accuracy)
- **Single most load-bearing mismatch:** the framework-audit revert is overstated in THREE doc surfaces at once — SKILL.md, CLAUDE.md, AND the NOTES P9-T0 contract row — all claiming `restore_state` does a "FULL rewind of code + archive DB + bandit." The code copies only DB+bandit+ledger and never the strategy `.py` (**C1**). This makes the P9-T0 contract row itself untrue.
- **Phantom-lever hunt — largely CLEAN.** Every knob in the run-config block and both lever tables (`auto_meta`, `meta_model`, `repair_*`, `fix_retry_budget`, `mutation/fix_web_search`, `cost_aware_coef`, `epsilon`, `code_embed_sim_threshold`, stagnation floors, `validity_floor`, `reward_validity_floor`, `reward_on_reject`, `force_explore`/`llm_subset`, `use_text_feedback`, `island_policy_driven`, `brief_compose_mode`, `island_selection_strategy`/`enforce_island_separation`, `meta_failures_first_frac`, `extra_guidance`, `migration_interval`, `enable_dynamic_islands`, `max_islands`, `island_evict_strategy`, the `rollback_decision` tuning knobs) is actually READ with the documented default. The genuine inert/phantom surfaces are: `island_selection_strategy="equal"` (no-op, **M7**), `inspiration_sort_order` (**L70**), the `azure_default.yaml` evo.* block (**L69**), and `island_policy` "retire" (**L26**).
- **Stale refs:** `taxonomy.md` cites five deleted files (**L73**); `prompts_meta.py` (**L20**), `DeepResearchModel`/`DeepResearchSummarizer` (**L32/L33**), and `_get_programs_for_island` (**L27**) are dead surfaces; `mutate.py`'s `max_attempts` label (**L9**); `evaluate.py`'s `timed_out` omission (**L71**).
- **Default disagreements:** `num_islands` 4-vs-2 in two doc locations (**L31**); two coexisting `cost_aware_coef` engine defaults 0.0/0.5 (**L74**); `enforce_island_separation` live-True vs legacy-False (**L14**).
- **Contract-table accuracy:** the subroutine-table Mutable/LLM flags and the `archive_query` query-type contract are accurate, EXCEPT `island_brief.py` is marked Mutable=Yes but is not a `MUTABLE_TARGET` (**M3**), and the "overshoot ≤ one slot" railguard claim understates the meta overshoot (**M4**).

## Orchestrator teachability gaps
A fresh orchestrator reading the docs would be misled on these load-bearing calls:
1. **Revert (C1, M1):** following step 6 literally leaves a regressing strategy live; the code-rewind `rollback()` is never named, and the whole import-only invocation mechanism for the cycle is untaught.
2. **Termination (H6, H7, H8):** the five-consecutive-intervention rule is non-deterministic to compute (two entry shapes, no reset rule, DR not in interventions.jsonl), so two careful agents stop at different times or never auto-terminate.
3. **Spoil mitigation (H9, M5, M6):** `use_text_feedback:false` is taught as COMPLETE but leaks via the fix ancestor and the meta traceback, and the setup skills never mention it.
4. **Model latency (H4, H12):** the auto-memory wrongly promises latency-aware auto-routing; the live docs give no warning that `gpt-5.5@medium` is slow and no safeguard exists.
5. **Island differentiation (H1, M17, M14):** the docs frame briefs as a full direction coupling that steers what each island evolves from; in reality it is a one-sentence prompt swap with no code-side enforcement.
6. **Continuation (M22):** the shown default relaunch hand-edits a static `window_state`, silently wiping cross-boundary stagnation streaks and duplicating window rows — the footgun `--resume` exists to remove.
7. **Imbalance remedy (M8, M7):** the only documented fix for "one island dominates" ("→ weighted") amplifies the imbalance, and "equal" is a silent no-op.
8. **DR cadence (M23):** SKILL contradicts itself on whether DR has no cadence or runs every control-return.
9. **DR novelty source (M24):** the agent is pointed at an in-memory-only `evo.meta_directions` that is empty when it is awake.

## Independent design critique (first-principles)
- **Diversity is the systemic weakness.** The island model is structurally unable to maintain distinct basins on the default route (**H2**): global archive eviction starves weak islands, default-zero migration never cross-pollinates (**L60**), the empty-pool fallback collapses separation, briefs differentiate only prompt text for one generation (**H1**), and λ=10 parent selection (**L65**) exploits the local frontier hard. These compound toward single-lineage convergence while `island_health` still reports "differentiated."
- **Reward calibration has two real soft spots:** the negative-score collapse in default `absolute` mode (**H3**), and crediting the sampled parent's headroom to the (model,effort) arm (**L62**).
- **Novelty control is not robust:** a single fixed 0.99 cosine on full-file embeddings is over-aggressive on boilerplate-heavy tasks and near-inert elsewhere, and it drops the better duplicate (**H5/L61/M9**).
- **Cadence:** the uncapped taper can sleep through a fixable sub-threshold stall (**L63**).
- **Reproducibility:** neither the evaluator (**L64**) nor the bandit posterior (**L48**) is seeded, so a noisy evaluator makes both the reward and parent weights chase noise and a finding can't be exactly reproduced.
- **Healthy by construction:** the bandit math, the stagnation J-formula, the fail-closed rollback judge, the atomic+recompute ledger, and the serial harness are sound.

## Coverage map (15 dimensions × files checked-and-cleared)
- **Boot/no-spoil:** `run_window.py` (377-395, 859), `construct_mutation_prompt.py`, `sampler.py`, `prompts_base.py`, `meta_summarize.py`, both setup skills + starters, `cnot/README.md`. Cleared: the harness boot guard refuses an unset/sentinel `task_sys_msg`.
- **Inner loop:** `mutate.py`, `_azure.py`, `apply_full.py`/`apply_diff.py`, `record_policy.py`, `diagnostics.py`, `select_llm.py`. Cleared: truthful apply-False recording (run_window.py:672-687), terminal-call cost folded to the exception.
- **Parent sampling:** `sample_parent.py`, `island_sampler.py` (legacy), `inspirations.py` (legacy), `dbase.py`, `islands.py`. Cleared: island-scoped parent draw, `_weighted_probs`, finite-score coercion.
- **Novelty:** `novelty_check.py`, `run_window.py` (694-733, 862), `dbase.py` crowding. Cleared: `<2`-program accept path, rejected-cost-to-bandit.
- **Meta:** `meta_summarize.py`, `run_window.py` (1088-1132), `_common.py`, `island_brief.py`, `dbase.py` briefs, `prompts_meta.py`. Cleared: brief→prompt coupling wiring, prior-recommendations from disk.
- **Brief coupling:** `sampler.py` (91-141), `sample_parent.py`, `islands.py` eviction. Cleared: augment vs replace branch.
- **Island policy:** `island_policy.py`, `islands.py`, `dbase.py`. Cleared: `island_health()` on the default path, eviction protects global-best.
- **Deep research:** `deep_research.py`, `dr_client.py`, `spawn_island.py`. Cleared: graceful refusal, success surcharge, cost-to-ledger.
- **Outer-loop/cadence:** `cadence_policy.py`, `stagnation_detector.py`, `run_window.py` (943-1220), `journal.py`. Cleared: `--resume` off-by-one-free, work_low_streak threading from the journal.
- **Rewrite cycle:** `strategy_store.py`, `validate_strategy.py`, `rollback_decision.py`, `smoke_test.py`. Cleared: `_assert_mutable`+`MUTABLE_TARGETS`, `deploy_bundle` atomic restore, fail-closed decide.
- **Budget/ledger:** `journal.py` (65-333), `_azure.py`, `run_window.py` (986-996), `mutate.py`. Cleared: railguards read live ledger, recompute-from-streams, per-call output cap.
- **Termination/archive:** `SKILL.md` (438-450), `journal.py` (354-472), `deep_research.py`, `run_window.py` finalize. Cleared: finalize gated on `budget_exhausted`, end-of-run archive intent.
- **Model/bandit:** `select_llm.py`, `prioritization.py`, `compute_reward.py`, `diagnostics.py` collapse. Cleared: epsilon-floor recovery, counts-based collapse.
- **Diagnostics/stagnation:** `diagnostics.py` (full), `stagnation_detector.py`, `run_window.py` counters. Cleared (**L55**): field-set completeness, tombstone + apply-exhausted exclusion, div-by-zero.
- **Warmup:** `run_window.py` (1259-1330), `journal.py` steps. Cleared: throwaway-db routing, trace-step join keys via generation.

## Not audited / follow-ups
- **`tasks/cnot_grid_synth/evaluate.py` internals** (foundation; the user's scoring code) were not re-derived beyond the boot/spoil and seeding angles — the prior design audit's "C1: invalid candidates scored correct=True" is referenced via memory but not re-verified here.
- **`shinka/edit/*` apply internals** were read only for the no-op/num_applied paths (L5), not exhaustively for all patch grammars.
- **Live Azure transport behavior** (real `bg+poll`, real embeddings) was not exercised — only the code paths.
- **Open questions for the human:** (a) should `restore_state` be made to also rewind code, or should step 6 instruct `rollback_bundle()` (decides C1's fix shape); (b) should the termination rule key on `work_dr>0`/`work_audit>0` in a single canonical interventions row (decides H6/H7/H8); (c) should `migration_rate`/island-archive-per-island be exposed as levers (decides H2/L60); (d) whether the negative-score default should switch to `relative` automatically (H3); (e) correcting the user's auto-memory latency claim (H4).

## Appendix: refuted claims
No findings were refuted during verification — every claim that reached this synthesis survived adversarial re-checking against the current code. Two framing corrections were folded in rather than dropped: (1) the first-pass claim that the novelty-drop-the-newcomer behavior was "intended per `test_improvements.py:1744`" was corrected — that test concerns the ARCHIVE-FULL FITNESS PRUNE, not the novelty gate, so it is not evidence of intent (the H5/M10 mismatch stands on the doc surfaces alone); (2) the claim that a NaN+correct=True evaluator output is reachable on the default path was narrowed to an off-design defense-in-depth gap, since `run_shinka_eval` (`wrap_eval.py:454-464`) flips `correct→False` on NaN/inf before any program is recorded (L53).

---

_Generated by the `audit-evolution-logic` workflow (run `wf_73b68e01-79c`): 36 agents — 15 concern dimensions + 5 cross-cutting sweeps, each adversarially verified. Read-only; no code or docs were modified. 2026-06-03._

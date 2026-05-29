# Design-Alignment Audit — ShinkaEvolve Orchestrator
_Generated 2026-05-29 by a 27-agent read-only Workflow audit (13 finders + 13 adversarial verifiers + synthesis)._
_Findings verified against ground-truth code; nothing was modified. Workflow run wf_34af140e-f3c._
_Audit stats: 13 dimensions, 104 findings adjudicated, 25 new findings raised by verifiers._

---

# ShinkaEvolve Orchestrator — Lead Audit Report

**Scope:** Read-only audit of the LLM-driven evolutionary code-optimization framework (orchestrator/ + shinka/), against the owner design and general evolutionary-search soundness. 13 dimensions, all findings verified against ground truth. Nothing was modified.

**Verdict in one line:** The framework's *plumbing* is largely sound, but several **load-bearing signals and levers are inert or lying** — the shipped task evaluator never reports incorrectness, every island gets the same direction, the bandit's quality signal is degenerate, capped Azure calls silently drop their cost, and the orchestrator's own playbook contains direct self-contradictions. A real run would burn budget on garbage candidates while the orchestrator's sensors read "healthy."

---

## 1. Executive Summary

### Severity counts (post-dedup)
| Severity | Count |
|---|---|
| Critical | 1 |
| High | 14 |
| Medium | 19 |
| Low | 18 |
| Nit | 9 |

### The issues that most threaten a real run

1. **C1 — The shipped cnot evaluator reports `correct=True` for invalid/crashing candidates.** The eval+repair pillar is *inert* on the real target: failure paths return `combined_score=0.0` with no exception, `wrap_eval` only downgrades on NaN/inf, so the immediate-fix loop never fires, `evaluation_failure_rate≈0` is a lie, and invalid circuits enter the archive as score-0 "correct" parents. (`tasks/cnot_grid_synth/evaluate.py:336,389`, `shinka/core/wrap_eval.py:207,443-450`)

2. **H1 — Islands are not differentiated and cannot be.** All islands boot as byte-identical copies of one seed, the per-island `island_brief` lever has *no producer* anywhere, and every island gets the same global meta-direction per generation. The owner's "wasted islands" failure is the default state. (`run_window.py:480`, `sampler.py:98-99`, `dbase.py:648-664`)

3. **H2 — Capped/failed Azure calls silently drop real spend from the ledger.** `_azure` raises on `incomplete` (the max-output-tokens outcome) and discards the billed partial output; `mutate.py` and `meta_summarize.py` then record cost 0 (meta crashes outright). The budget hard-stop — a FOUNDATION guarantee — under-counts an unbounded amount. (`_azure.py:121-122`, `mutate.py:158-160`, `meta_summarize.py:235`)

4. **H3 — The bandit's quality signal is degenerate; cost dominates selection.** Asymmetric clamp makes "correct-but-below-parent" bit-for-bit identical to "failed" (both → −inf contribution), and `cost_aware_coef=0.5` then lets the cheapest arm win the argmax even when a pricier model is the only one making progress. This is the owner's "a good model can never recover" worry, realized. (`prioritization.py:476-477,415-417,627-628`)

5. **H4 — The most important safety step (rollback) is untested, blind to the owner's flagship trigger, and the only worked example teaches the deprecated formula.** `rollback_decision.decide()` has zero test coverage; the smoke test still uses `new_J < good_J*0.8`; and the basket is structurally blind to reward/bandit regressions in the flat early phase (rule 3 gated on `p_delta > p_threshold`). (`rollback_decision.py:95`, `smoke_test.py:189`)

6. **H5 — The orchestrator's playbook contradicts itself on two operational essentials:** budget logging (SKILL.md:42 "append_intervention the cost" vs SKILL.md:352 "do NOT") double-counts every ~$5 DR call; and the Main-loop section says `--windows 1` while the cost economics demand `--until-decision`. A cold agent must guess.

---

## 2. Critical & High Findings

### C1 — cnot evaluator marks invalid/crashing candidates `correct=True`; eval+repair pillar is dead on the real task
- **Dimensions:** eval_fix (F1) + new findings (score-collision, contract-level early returns).
- **Where:** `tasks/cnot_grid_synth/evaluate.py:336` (failure → `combined_score:0.0`, no raise), `:255-271` (no-result / non-callable also → 0.0), `:389` (`validate_fn=None`); `shinka/core/wrap_eval.py:207` (`overall_correct_flag=True`), `:443-450` (downgrades only on NaN/inf); propagates via `orchestrator/scripts/evaluate.py:119`; consumed at `run_window.py:579-583` (repair gated on `not ev['correct']`), `sample_parent.py:103`.
- **Why it breaks a run:** Every dominant cnot failure mode (timeout, adjacency, clifford, wrong-type, malformed return) is stored as a correct, score-0 program. The immediate-fix loop never fires; `evaluation_failure_rate` is pinned near 0; the bandit/reward/parent-sampler ingest garbage as legitimate. The orchestrator's primary health sensor is blind on the exact target it ships with.
- **Compounding (H, eval_fix new):** *Score collision* — a valid baseline-tying candidate (`score=max(0,baseline−slope)=0.0`, `:354`) and a fully invalid one both read `0.0`/correct, so reward and parent-selection cannot separate "valid, no gain" from "garbage." This survives even a partial fix that only sets `correct=False`.
- **Pillar:** Inner-loop fixed-retry repair; honest fitness signal; "evaluation_failure_rate is the post-repair rate."
- **Verdict:** confirmed / high confidence (traced end-to-end + re-verified `wrap_eval` here).
- **Fix direction:** Signal incorrectness from *all* failure return sites (NaN, or a `correct` field `run_shinka_eval` honors, or route gates through `validate_fn`) **and** give the valid-score scale a strictly-positive floor so validity is separable.

### H-ref — Reference task (circle_packing) gets correctness right, masking C1
- **Dimensions:** eval_fix (F2).
- **Where:** `examples/circle_packing/evaluate.py:164` (`validate_fn=adapted_validate_packing`), violations → `(False,...)` → `wrap_eval.py:360-362` sets `correct=False`. cnot diverges at `evaluate.py:389`.
- **Why it matters:** The smoke/reference path demonstrates the fix loop *working*, hiding the silent break on cnot, and teaches the wrong template to anyone using shinka-setup/convert.
- **Verdict:** confirmed/high. **Fix:** make cnot mirror the `validate_fn` contract; document one canonical "how to signal incorrect" rule.

### H1 — Islands are not differentiated: identical boot + unwired per-island brief + shared global direction
- **Dimensions:** island (F1, F2) + inner_mutation (F3) + flow_coherence (F5) + meta (new) + island (new: on-demand spawn decays too).
- **Where:**
  - *No brief producer:* `island_brief` consumed at `sampler.py:98-99` (full overwrite of `meta_recommendations`) and forwarded at `construct_mutation_prompt.py:95`, but **grep confirms zero writers in `orchestrator/harness/`**; the `meta_briefs` table (`dbase.py:648-664`) has no INSERT/SELECT anywhere.
  - *Same direction per gen:* `run_window.py:480` passes `_compose_meta_for_gen(evo, generation)` (keyed on `seed+generation`, **not** island) to every island.
  - *Identical boot:* `CopyInitialProgramIslandStrategy` (default, `dbase.py:363-367`) copies the seed verbatim into all islands (`islands.py:603-700`).
  - *On-demand spawn decays (H, island new):* `spawn_island_from_program` (`islands.py:1039-1061`) seeds distinct code but writes no brief, so the DR-grounded island reverts to the global direction on its very next generation.
- **Why it breaks a run:** With the SKILL default (`num_islands=4`, migration off), all islands start identical and explore near-identical trajectories. A near-deterministic cheap model runs N redundant searches — the "wasted islands" failure the owner explicitly names. Even the one path a prior review credited (DR-spawn) loses its distinctness within a generation.
- **Pillar:** "Different directions must live in DIFFERENT islands; islands must stay genuinely differentiated."
- **Partial mitigation (nit, island new):** novelty rejection *is* island-keyed (`run_window.py:553-560`), so within-island duplicates are suppressed — but this gives no *inter*-island differentiation. Record so a fixer doesn't assume novelty solves it.
- **Verdict:** confirmed/high. **Fix:** persist a per-island direction into `meta_briefs` at spawn and have `run_window` fetch + pass it as `island_brief` (keyed by `sp.island_idx`); or have `meta_summarize` emit per-island directions.

### H2 — Capped/failed Azure calls drop real spend (and crash meta); budget guarantee violated
- **Dimensions:** inner_mutation (F2) + budget (F1) + inner_mutation (new: cost=0) + budget (new: meta no try/except).
- **Where:** `_azure.py:27` (`incomplete` is terminal), `:121-122` (`if status != "completed": raise`) — discards the extractable partial output the docstring at `:91-94` describes. Contrast `dr_client.py:224-229` which *tolerates* `incomplete` and bills it. Then:
  - `mutate.py:158-160` catches → `break` *before* `total_cost += cost` (`:171`); the exhausted return (`:194-197`) reports `cost: total_cost = 0.0` while Azure already billed output tokens (re-verified here).
  - `meta_summarize.py:235` calls `bg_query` with **no try/except at all** → the RuntimeError propagates out of `main()`, the whole meta round aborts, *and* the cost log at `:248` is never reached.
- **Why it breaks a run:** A pro call exhausting its 50k output cap is billed up to ~$9 but recorded as $0; repeated capped calls let the hard-stop fire late and overrun `budget_usd` by an unbounded amount — directly contradicting `NOTES.md:54-57` ("budget hard-cap in code") and `SKILL.md:45-50`. The cap bites hardest on long-reasoning models, exactly when a near-complete candidate exists.
- **Pillar:** Budget railguard / "no spend escapes the ledger"; inner loop cheap+stable; meta-loop robustness.
- **Verdict:** confirmed/high. **Fix:** at the `_azure` layer, extract+return text+cost on `incomplete` (mirror `dr_client`) and attach partial cost to the raised exception on true transport failures so callers fold it; both mutate and meta benefit, and meta stops crashing.

### H3 — Bandit reward signal degenerate; cost-aware blend then entrenches the cheapest arm
- **Dimensions:** reward_bandit (F1, F2) + reward_bandit (new: rejected cost invisible) + reward_bandit (F5: novelty-reject visit-count distortion).
- **Where:**
  - *Degenerate quality (F1):* `prioritization.py:476-477` clamps `r=max(r,0)`; with default `exponential_base=1.0` the active path is `_logexpm1`, where correct-but-worse (`r=0→−inf`) and failed (`_impute_worst=−inf`, `:415-417`) both contribute −inf and both bump `divs`/`n_completed` — bit-for-bit identical effect. `compute_reward.py:56-61` sends `score−parent` (re-verified). Most correct children don't beat the parent, so the bandit learns nothing separating a strong explorer from a weak model.
  - *Cost dominance (F2):* `defaults.py:31` ships `cost_aware_coef=0.5`; `prioritization.py:627-628` blends it into a hard-argmax posterior (`:630-639`). Demonstrated argmax flip: cheap-mediocre arm 0.700 vs pricey-best 0.550 → better model drops to ~20% share.
  - *Rejected cost invisible (H, new):* on novelty reject `run_window.py:568` returns before the only `update_cost` feed (`:669` → `select_llm.py:110`), so a duplicate-prone arm's real spend (`counters['cost'] += _mut_cost`, `:541`) never reaches `bandit.total_costs` — making it look *even cheaper*. TESTRUN observed bandit $0.45 vs ledger $0.88.
  - *Visit-count distortion (F5):* `select_llm.py:131` bumps `n_submitted` at selection; novelty reject skips the update, so `n=max(n_submitted,n_completed)` rises and deflates the UCB bonus (`:610-613`); a single reject can even flip an arm from "unseen→uniform" to "seen→exploitation 0."
- **Why it breaks a run:** All four pull the same direction — toward entrenching a cheap, duplicate-prone model regardless of quality, on hard tasks with high reject rates (~49% observed). This is precisely the owner's "reward flaw so a good model can never recover."
- **Pillar:** Reward calibration / exploration-exploitation / "model never selected."
- **Verdict:** confirmed/high. **Fix direction:** expose `asymmetric_scaling=False` (or have `compute_reward` emit a small graded positive for correct-but-worse); document/lower `cost_aware_coef`; forward rejected-slot cost to `update_cost`; defer `update_submitted` until eval.

### H4 — rollback_decision: untested, flat-phase-blind, and the smoke test teaches the deprecated J-guard
- **Dimensions:** rewrite_protocol (F3, F4) + tests (F1) + flow_coherence (F4) + rewrite_protocol (new: novelty-default hole).
- **Where:**
  - *Zero coverage + stale example:* grep finds no test imports `rollback_decision`; `smoke_test.py:189` decides via `new_J < good_J*(1.0−0.2)` (`:39`), the exact anti-pattern F14 was filed to kill. `strategy_store.rollback/record_outcome` take a *pre-computed* decision dict, so they don't exercise `decide()` even indirectly. TESTRUN's "Verified on 3 cases" has no committed test.
  - *Flat-phase blindness (F4):* `rollback_decision.py:95` gates score-regression on `p_delta > p_threshold`; rules 1-2 read only eval-success/novelty, which a reward/bandit rewrite doesn't move within one window. So the owner's flagship outer-loop trigger (a reward flaw) produces *no* rollback signature and is auto-accepted.
  - *Correctness-collapse gap (F5, medium):* `:79` requires `min_eval_success ≤ p_eval`; a prior window already below 0.5 dropping to 0.0 satisfies neither branch (0.2 drop < 0.25) → an all-broken rewrite is accepted in the rough phase.
  - *Novelty-default hole (M, new):* `_nov` and diagnostics default `novelty_acceptance_rate` to 1.0 (`rollback_decision.py:55-58`, `diagnostics.py:82`); a rewrite that records zero novelty events reads as *maximal* diversity 1.0, inverting the F13 flood-detection intent.
- **Why it breaks a run:** "The step that prevents a bad rewrite from poisoning the rest of the run — never skip it" (SKILL.md:302) ships with no automated verification, a blind spot on the exact rewrite class the outer loop exists to catch, and a default-value hole.
- **Pillar:** Outer-loop measure→rollback; reward-hacking detection.
- **Verdict:** confirmed/high. **Fix:** unit-test each basket arm at Δ≈0; add a 4th signal from record_policy (reward_used vs improvement_over_parent) or bandit-weight collapse; add an absolute correctness floor; treat absent novelty as "unknown" (skip), not 1.0; switch smoke to call `decide()`.

### H5a — SKILL contradicts itself on budget logging → guaranteed double-count of every DR/meta call
- **Dimensions:** deep_research (F1) + budget (F3).
- **Where:** `SKILL.md:40-42` ("first check `budget_remaining` and *afterward* log the cost with `append_intervention({type, cost})`") directly contradicts `SKILL.md:352` ("do NOT also `append_intervention` … double-count") and `journal.py:182-184` (same warning in code). `deep_research.py:141` and `meta_summarize.py:248` already self-log via `_common.log_external_call → journal.log_call → add_cost`; `append_intervention` *also* adds cost (`journal.py:140-142`). SKILL.md:224 mandates passing `results_dir` (the self-log path).
- **Why it breaks a run:** DR is the single most expensive action (~$5). Following SKILL.md:42 double-counts it, tripping the budget hard-stop *early* and terminating a run with real budget remaining — right after a costly DR brief is fetched but before the agent can act on it. Same inflation on every meta call.
- **Verdict:** confirmed/high. **Fix:** reword SKILL.md:40-42 to match :352 (pass `results_dir`; never `append_intervention` the LLM cost); optionally de-dup by call-id in `add_cost`.

### H5b — Budget cap is code-enforced only for the inner loop; DR/meta overshoot is unbounded
- **Dimensions:** budget (F2).
- **Where:** only enforced gates are `run_window.py:738` (inter-candidate) and `:351` (pre-fix). `deep_research.py:119` calls `run_dr_call` unconditionally; `meta_summarize.py:235` calls `bg_query` unconditionally — grep shows **zero** `budget_remaining`/`skipped` checks before either model call. The "hard-capped in code … a rewrite can't disable it" claim (`NOTES.md:54-57`) does not cover the largest single spend.
- **Why it matters:** An orchestrator that mis-reads `budget_remaining`, or calls DR when remaining < $5, overruns by the full DR cost. The central budget guarantee is false for discretionary spends.
- **Verdict:** confirmed/high. **Fix:** add a code-level pre-flight in `deep_research.py`/`meta_summarize.py`: when `results_dir`+budget present, return `{ok:false, skipped:"budget"}` if `budget_remaining < estimated_cost`.

### H6 — Constant per-gen seed pins numpy global RNG → operator diversity collapses to one patch type
- **Dimensions:** inner_mutation (F1) + inner_mutation (new: select_llm force_explore also pinned).
- **Where:** `construct_mutation_prompt.py:64-65` does `np.random.seed(seed)` before `PromptSampler.sample()`, whose first global draw is `np.random.choice(valid_types, p=valid_probs)` (`sampler.py:135`). `run_window.py:486` passes bare `evo.seed` with **no `+generation`** (contrast `:289` which *does* offset). Empirically: 5/5 gens returned `diff`. `sample_parent.py:94` re-seeds identically (`run_window.py:429`). `select_llm.py:71-72,121`: the global reseed pins only the `force_explore` uniform draw (the bandit uses its own `default_rng`), so the re-exploration lever degenerates to the same arm every gen — defeating the recovery mechanism the outer loop reaches for.
- **Why it breaks a run:** Any seeded reproducible run (smoke_test sets `seed:0`) collapses the diff/full/cross mixture to one type for the entire run — `full` rewrites and `cross` crossover essentially never fire, silently, with no log. A direct hit to exploration.
- **Pillar:** Operator diversity / exploration floor.
- **Verdict:** confirmed/high. **Fix:** offset the seed by generation at all three call sites (`:486`, `:429`, `:501`), or use local `np.random.Generator`s instead of the global stream.

### H7 — `exhausted_retry_slots` is hardcoded `[]` but is a documented escalation/debug-agent trigger
- **Dimensions:** contract_config (F2) + flow_coherence (F2) + contract_config (new doc) + flow_coherence (new doc) + orch_docs (new: debug-agent "retried three times").
- **Where:** `run_window.py:790` is the *sole* producer, the literal `"exhausted_retry_slots": []`; `diagnostics.py:130` passes it through. The per-window `counters` (`run_window.py:725-728`) has no such key; both exhaustion modes (mutate apply-exhaustion `mutate.py:194` → `applied:False`; fix-budget exhaustion `run_window.py:350`) fold into `eval_failures` (`:601`), never a slot list. Documented as actionable at `SKILL.md:84,136,315-317` and `debug-agent.md:18` (the debug-agent escalation keys on it). Three teaching docs (SKILL, taxonomy.md:90, debug-agent.md) plus the FOUNDATION `diagnostics.py:14-26` docstring all imply it carries data.
- **Why it breaks a run:** The debug-agent escalation rung ("a candidate exhausts its retry budget across two parents") can *never fire from real data* — the failure-escalation path the owner designed is permanently dark.
- **Related (low, orch_docs new):** `debug-agent.md:9-10` tells the subagent the inner loop "already retried it three times," but the default `fix_retry_budget` is 1 (`run_window.py:350`, SKILL.md:411-412) — a false premise that skews the subagent toward a "structural" root cause.
- **Verdict:** confirmed/high. **Fix:** populate `exhausted_retry_slots` with candidate ids on exhaustion (and the per-window counter), then sync SKILL/taxonomy/debug-agent/diagnostics-docstring; fix the "three times" line.

### H8 — `island_policy.main()` spawn/migrate/retire decisions are dead code (inert mutable lever)
- **Dimensions:** island (F3) + flow_coherence (F1).
- **Where:** grep shows `island_policy` used only at `diagnostics.py:103` (`island_health`); **`island_policy.main()` has no caller**. The live spawn/migrate decisions are baked into immutable `dbase.py`: `run_post_add_maintenance_batch` runs `should_schedule_migration` (`:1066`), `check_and_spawn_island_if_stagnant` (`:1069→2526-2543`), `check_scheduled_operations` (`:1093→2502-2506`), all keyed off `db_config`. `archive_record.main` calls `db.add(defer_maintenance=False)`. SKILL ladder rung 2 (`SKILL.md:193-194`) directs the agent to rewrite `island_policy.py`.
- **Why it breaks a run:** An orchestrator following the documented ladder rewrites `island_policy.py` and observes *zero* behavioral change — the spawn/migrate thresholds it edits are overridden by immutable plumbing. Directly defeats the outer-loop framework-mutation duty for the island concern.
- **Note:** docs are *partially* honest (`SKILL.md:140` "executed by archive_record"; `NOTES.md:90-92`), but `taxonomy.md:195` still lists it as the mutable fork/migrate/retire lever and nothing warns the thresholds are db_config-driven.
- **Verdict:** confirmed/high. **Fix:** either call `island_policy.main()` at window boundaries to drive migration/spawn, or relabel it advisory and point rung 2 at the `db_config` knobs / `spawn_island.py` that actually act.

### H9 — shinka-convert starter config ships the F12-condemned `tau:0.05` trigger
- **Dimensions:** contract_config (F1).
- **Where:** `skills/shinka-convert/scripts/orchestrator_run.json:25` has `"tau":0.05` (no `stagnation_abs_floor`/`rel_frac`); `shinka-setup` was migrated to the hybrid trigger (`:25-27`) but convert was not (clean grep split). `stagnation_detector.py:84-87` aliases `tau→abs_floor`, so `abs_floor=0.05` false-flags ~0.001-0.01 gains as stagnant every 2 windows.
- **Why it breaks a run:** convert is a primary scaffolding entry path; it silently reintroduces the exact F12 false-stagnation bug TESTRUN_FINDINGS:425 says was fixed.
- **Verdict:** confirmed/high. **Fix:** replace `tau:0.05` with `stagnation_abs_floor:0.001`, `stagnation_rel_frac:0.05` in the convert starter config.

### H10 — No code guard prevents deploy/rollback from overwriting a FOUNDATION file
- **Dimensions:** rewrite_protocol (F1).
- **Where:** `strategy_store.py:86` (snapshot), `:135-136` (deploy), `:279` (deploy_bundle) write to `scripts_dir()/target` for *any* target string; `MUTABLE_TARGETS` (`:223`) is referenced only by `current_fingerprint` (`:248`), never as a guard. `_common.py`, `evaluate.py`, `archive_record.py` live in the same `scripts/` dir.
- **Why it breaks a run:** An off-by-one in the orchestrator's reasoning silently corrupts the JSON contract or evaluator mid-run, *and the corruption becomes the snapshot restore point* — breaking the consecutive run with no programmatic safeguard. Protection is prose-only (`SKILL.md:109-118`).
- **Verdict:** confirmed/high. **Fix:** in snapshot/deploy/deploy_bundle, raise unless `target in MUTABLE_TARGETS` (with an explicit tooling override).

### H11 — deploy_bundle / rollback_bundle are not atomic; a mid-loop copy failure leaves scripts/ half-applied
- **Dimensions:** rewrite_protocol (F2).
- **Where:** `strategy_store.py:278-281` copies targets one-by-one, `append_index` (`:282`) and the `prior_hashes` return (`:297`) run only after the full loop; a raise on change >0 leaves earlier targets applied, no index row, no returned rollback handle. `rollback_bundle:302-306` has the same shape. Docstring claims "Either all change or none." `test_concern_bundle` exercises only the happy path.
- **Why it breaks a run:** A half-applied scoring bundle (new `select_llm` reading a new reward shape while `sample_parent` still uses old weighting) is the exact incompatible state bundles exist to prevent; with no `prior_hashes` the orchestrator has no rollback handle and proceeds silently corrupted.
- **Verdict:** confirmed/high. **Fix:** copy to temp, `os.replace` each inside try/except that restores from pre-snapshots on any failure before re-raising; write the index entry only on full success.

### H12 — DR Stage-C immutable system prompt demands a verbatim `reference_snippet` — the exact shape SKILL says Azure refuses
- **Dimensions:** orch_docs (F2) + deep_research (F2).
- **Where:** `prompts_deep_research.py:93` hard-codes `"reference_snippet": "<short verbatim quote from the source if you have one>"` inside `DR_STAGE_C_SYS_MSG`; `deep_research.py:106` sets it with no payload override; the agent's query is only the user message. `SKILL.md:229-232` tells the agent *not* to ask for a verbatim snippet because the content filter "refuses it deterministically."
- **Why it matters:** If the refusal claim holds, *every* DR call risks a content-filter refusal regardless of query wording — wasting the ~$5 escalation with no agent-side remedy. If overstated, the SKILL over-constrains the query for nothing. Either way the doc misattributes the risk surface (immutable system prompt) to the agent's authoring.
- **Verdict:** confirmed; **medium confidence** on the causal certainty of the refusal (the field is softened with "if you have one"; the filter may key on the named-paper ask instead — owner should confirm empirically).
- **Fix:** soften the immutable field to "a short paraphrase or locator (section/figure/URL), not a verbatim quote," or correct the SKILL to say the lever is in the immutable system prompt.

### H13 — `low_streak` carry: docs claim a rewrite resets it, code never does, and SKILL tells the agent to carry it forward → one-window re-flag
- **Dimensions:** orch_docs (F1) + stagnation_cadence (F1).
- **Where:** `stagnation_detector.py:99` (`low_streak = prior_low_streak + 1 if low else 0` — resets only on a non-low window, never on a strategy change); `run_window.py` has *no* fingerprint-vs-prior comparison (stamped once at `:719`, only into diagnostics at `:774`); `SKILL.md:104-105` says "Carry `low_streak` → next config's `window_state.prior_low_streak`"; `NOTES.md:11-12` falsely claims "A rewrite resets `low_streak`." `cadence_policy.py:45-46` returns immediately on stagnation.
- **Why it breaks a run:** After deploying a rewrite and running one measure window, carrying `prior_low_streak=2` forward makes a single low window re-trip stagnation (`low_streak=3 ≥ consecutive_required`), returning control immediately — the new strategy is judged stagnant after one window instead of the promised fair trial. **Intervention thrashing, the exact failure NOTES claims is prevented.**
- **Related (medium, stagnation_cadence F1):** without `--resume`, `prior_low_streak` seeds only from `cfg['window_state']` (`:712-713`); one forgotten hand-carry resets the streak and stagnation *never* accumulates — the opposite failure. A journal-derived safe path exists (`read_windows last_n=1`) but is opt-in.
- **Verdict:** confirmed/high. **Fix:** zero `low_streak` on a fingerprint change in `run_window` (and correct NOTES); default to journal-derived resume when `window_state` is absent.

### H14 (tests) — Two canonical-trigger sensors have no exercising test; the bandit parity test only hits the degenerate branch
- **Dimensions:** tests (F2, new) — kept High because they guard the owner's flagship "model never selected" trigger.
- **Where:**
  - *F9 weights path untested:* no test sets `evo.llm_models`, so `run_window.py:750` ("weights" peek, guarded by two bare `except: pass` at `:762` and `select_llm.py:85,98`) never runs; `test_parity.py` only uses update/select. A shape drift in `posterior()`/`get_state()` silently reverts `llm_bandit_weights` to `{}` with CI green.
  - *Parity test degenerate (M, new):* `test_parity.py` updates m1/m2 but never m3, so `posterior()` takes the unseen-arm shortcut (`prioritization.py:604`) and asserts `[0,0,1.0]` — the reward magnitudes (1.0/0.2/0.9) have *zero* effect on the assertion. A broken UCB scoring path would still pass; the test cannot detect a reward-ranking regression — exactly the owner's failure mode.
- **Verdict:** confirmed/high. **Fix:** seed bandit + assert a populated weights/counts dict reaches diagnostics with `evo.llm_models`; give every arm ≥1 update and assert the reward-driven ranking.

---

## 3. Medium Findings

- **M1 — cross/literature_grounded patch types drop BOTH the meta direction and the persistent failure_note.** *(meta F1 + inner_mutation F6)* `sampler.py:146-147` suppresses the entire meta-rec block for `patch_type ∈ {cross, lit}`; `run_window.py:480` funnels the composed failure_note+direction solely through that slot. ~10% of normal gens (cross, default prob 0.1, `defaults.py:18`) silently lose the recurring-failure caution the WS3 design says rides into *every* gen. (lit is unreachable as-wired, see M3.) **Fix:** append failure_note unconditionally, suppress only the generic direction for cross/lit. *(confirmed/high conf)*

- **M2 — failure_note silently dropped whenever `meta_directions` is empty.** *(inner_mutation F5)* `run_window.py:287` opens the only block reading failure_note; when directions are falsy, control falls to `:297` returning the legacy blob, ignoring `meta_failure_note`. So a meta round that finds clear failure modes but no confident direction (common, valuable) drops the caution. Test gap confirmed. **Fix:** compute the failure-note prefix outside the `if meta_directions` branch.

- **M3 — `island_brief` overwrite + `literature_grounded` operator are designed-but-unwired.** *(inner_mutation F3,F4 + meta new)* `island_brief` *overwrites* (not appends) `meta_recommendations` (`sampler.py:98-99`), so any future DR-grounding gen that supplies it would clobber the failure_note — a third silent meta-drop trap. `literature_grounded` is gated on `literature_grounded_item` which `construct_mutation_prompt` never passes and which `default_patch_types()` omits (`defaults.py:13-14`), so the web-grounded operator can never fire via the harness. **Fix:** plumb a brief item from run_window; make island_brief append; or document both as not-yet-wired.

- **M4 — Fix-mode generations receive neither failure_note nor meta direction; `sample_fix` docstring lies about a param it doesn't accept.** *(meta F2 + inner_mutation F7)* `sampler.py:261-265` signature is `(incorrect_program, ancestor_inspirations)`; docstring `:276` claims `meta_recommendations`. Both the sampled-parent fix path and the immediate-fix path (`run_window.py:368-378`) omit meta. Repairs can re-introduce the exact failure class meta warned about. **Fix:** thread failure_note into `sample_fix` or delete the stale docstring.

- **M5 — Sampled-parent fix path feeds NO error text to `sample_fix`.** *(flow_coherence F3)* `run_window.py:432-437` fetches the parent without `include_metadata`, so `_common.program_summary` omits the `metadata` key; `sample_fix` reads only `metadata.stdout_log/stderr_log` (`sampler.py:307-309`) → `prompts_fix.py:73` returns "No error output captured." Bites when the seed is incorrect or correct programs are evicted (`sample_parent.py:107-126`) — the repair prompt is blind. (Distinct from the *immediate*-fix path, which does route `stderr_log`.) **Fix:** inject `error_traceback` into the parent's metadata for the needs_fix prompt.

- **M6 — Rich failure reason (`text_feedback`/`first_failures`) is dropped before the repair prompt.** *(eval_fix F3)* `orchestrator/scripts/evaluate.py:120-121` reads error only from `correct.json`; synthesizes a message only on timeout/crash (`:135`); never reads `metrics['text_feedback']` (`:148-149`). `use_text_feedback` defaults False (`construct_mutation_prompt.py:76`) and is never overridden. The single most actionable cnot repair signal ("adjacency: non-adjacent 2q gate cx(3,7)") is doubly closed. Latent behind C1; active the moment C1 is fixed. **Fix:** synthesize `error_traceback` from `text_feedback`/`first_failures` when correct is False.

- **M7 — Meta failure-note classification keeps only the first traceback line — generic banner on the entire correctness-failure class.** *(meta F5)* `meta_summarize.py:161` does `splitlines()[0][:160]`; `wrap_eval.py:458` builds tracebacks via `format_exc()`, whose first line is always "Traceback (most recent call last):" with the exception type/message on the *last* line. Timeouts classify fine (`EvaluationTerminated` on line 1); every real exception gives meta only the useless banner. **Fix:** prefer the last non-empty traceback line before the 160-char cap. *(severity raised low→medium by verifier; confirmed)*

- **M8 — Per-trial timeout (300s) ≥ documented `eval_time` (5min); the evaluator's 30-min graceful guard is dead code.** *(eval_fix F4)* `SKILL.md:386` sets `eval_time '00:05:00'`; `evaluate.py:51-52` sets `PER_TRIAL_TIMEOUT_S=300`, `EVAL_WALLCLOCK_BUDGET_S=1800`; `local.py:172-177` hard-kills at ~300s, so the 1800s early-abort (`evaluate.py:289`) can never run. One pathological trial can consume the whole 300s cap, starving the other ~269 trials. **Fix:** raise `eval_time` above 1800s, or lower the per-trial/wallclock caps to fit a 5-min budget.

- **M9 — `fix_count` double-counts the needs_fix slot → `fix_success_rate`/`fix_rate` mislead rung 5.** *(eval_fix F5 + stagnation_cadence F4)* Incremented both at `run_window.py:464` (needs_fix parent sampled) and `:354` (per immediate-fix attempt); `fix_success` only at `:406`. A successful needs_fix repair reads as `0/1=0`. Biases rung 5 toward a spurious fix-concern rewrite on failing-seed tasks. **Fix:** separate needs_fix-parent count from immediate-fix attempts.

- **M10 — Silent `cost→0.0` on pricing-lookup failure under-counts spend; the inner-loop path has no warning.** *(budget F4 + budget new)* `_azure.py:67-73` swallows `calculate_cost` exceptions to 0.0 **with no log**, unlike `embedding.py:60` and `dr_client.py:272` which warn. `pricing.py:86-87` raises on an unknown model id. A new/renamed/typo'd Azure deployment keeps spending while the ledger shows $0 for the *highest-volume* path, silently. **Fix:** emit a loud WARNING (or raise) when a billed response yields cost 0.0; surface an `unpriced_calls` counter.

- **M11 — Configured `island_selection_strategy` / `enforce_island_separation` are inert on the orchestrator path.** *(island F6)* `sample_parent.py:131-132` hardcodes uniform island choice; inspirations drawn only from same-island pool (`:147-153`); shinka's `create_island_sampler` / `enforce_island_separation` are reached only via `dbase.sample_random_parent`, which the orchestrator never calls. `AUDIT.md:229-234` presents `island_selection_strategy` as a live evolvable knob. A silently-dead selection-pressure lever. **Fix:** honor the knobs in `sample_parent.py`, or document them inert under the orchestrator.

- **M12 — `island_health.diversity` is a population count, not a spread; `stagnation_count` is always None → rung 2 has no real signal.** *(island F7 + deep_research F4)* `island_policy.py:71-72` (`"diversity": isl.get("count")  # TOY`, `"stagnation_count": None`). Two islands collapsed onto one genome but with many members report HIGH diversity, masking premature convergence. F10 marked "FIXED (made mutable)" but it's still a toy count. **Fix:** compute mean pairwise embedding distance + a genuine per-island gens-since-best count.

- **M13 — Stagnation-spawn clones initial/best/random with no distinct direction; bypasses `max_islands`.** *(island F4, F5)* `spawn_new_island` (`islands.py:1063-1140`) copies a source verbatim with no brief; `strategy='best'` duplicates the current global best. It allocates via `get_next_island_index()` (`:1091`, no cap/eviction) while `spawn_island_from_program` uses the capped path (`:1052`). Dormant under shipped default (`enable_dynamic_islands=False`), but a user enabling dynamic islands + setting `max_islands` gets anti-diversity clones *and* cap overshoot. **Fix:** route both spawn paths through `allocate_island_index_for_spawn()`; pair stagnation-spawn with a fresh direction.

- **M14 — `spawn_island.py` is owner-labeled MUTABLE but code+docs treat it FOUNDATION and exclude it from the strategy fingerprint.** *(contract_config F4)* `MUTABLE_TARGETS` (11-tuple, `strategy_store.py:223-235`) omits `spawn_island.py`; `current_fingerprint` loops only that set, so a spawn_island rewrite is not hashed/snapshot/rollback-tracked. `spawn_island.py:15-16` and `SKILL.md:245` call it foundation; the owner architecture map lists it under MUTABLE STRATEGY. **Design-vs-implementation conflict needing an owner ruling.** *(confirmed/high)*

- **M15 — `mutate.py` is in MUTABLE_TARGETS but has no validate contract → prompt/output-key rewrites pass parse-only.** *(rewrite_protocol F6 + orch_docs F7)* `validate_strategy.py` CONTRACTS has 10 entries, omitting `mutate.py` (the 11th mutable target); the unknown-target branch (`:236-246`) returns `valid:True` "parse-only" if `def main(` exists. A rewrite dropping `candidate_path`/`applied`/`cost` clears validation then breaks every generation. SKILL.md:279 says "Validate each" with no caveat. **Fix:** add a mutate.py smoke contract (mock transport), or fail the unknown-target branch for any name in MUTABLE_TARGETS.

- **M16 — Unknown/typo'd target green-lit by validate; deploy semantics confusing.** *(rewrite_protocol F7, partial)* `validate_strategy.py:236-246` green-lights any `def main(` file regardless of name. `deploy()` of a *new* typo'd name actually raises `FileNotFoundError` at the pre-deploy snapshot (`:134`) — so the "silent dead file" outcome is *mitigated*, but the validate green-light + total absence of a target-name check is the real defect (same root as H10/M15). **Fix:** reject any name not in MUTABLE_TARGETS for the rewrite path. *(confirmed; deploy-mechanism half partial)*

- **M17 — `meta_summarize` validate contract requires only the legacy `recommendations` key, not the consumed `directions`/`failure_note`.** *(tests F5)* `validate_strategy.py:169` `required_keys={"recommendations"}`; `meta_summarize.main` returns directions+failure_note+recommendations; `run_window._compose_meta_for_gen` consumes `meta_directions`. A clean-deploying meta rewrite emitting only the legacy blob passes `Valid(S')` yet silently collapses weighted-direction sampling. **Fix:** require `directions` in the contract.

- **M18 — Budget hard-stop "overshoot ≤ one candidate" is untested for fix-retry/embedding additions and is larger than one mutation.** *(budget F6 + tests F7)* Within an admitted slot, mutation (`run_window.py:540`) and embedding (`:550`) are unconditional, and the first budget-crossing fix runs to completion (`:351,:398`). On a grounding run (`fix_retry_budget=3`, pinned pro + web search) a single near-cap slot can overshoot by a pro mutation + pro fix + embeds (>$15). Tests assert only a lower bound (`total_cost >= budget`). **Fix:** assert `total_cost ≤ budget + max_single_candidate_cost`; re-check the gate before the unconditional mutation/embedding.

- **M19 — Double-count avoidance has no negative test; `_sample_meta_direction`, F7 novelty integration, F16 J-monotonicity, F8 iters_completed, F14 rollback all lack guarding tests.** *(tests F3,F4,F6,F8,F9,F10 + new)* A cluster of "fixed-but-unguarded" items where a future refactor reverts the fix with CI green: `enable_novelty` is False everywhere in tests (`test_improvements.py:192`); smoke scores never cross `s_start=0→ε` (the boundary F16 fixed); `_sample_meta_direction` tested only with degenerate `{1.0,0.0}` (can't distinguish weighted from argmax); `iters_completed` never asserted; `test_concern_bundle` never calls `validate_bundle`. **Fix:** add targeted unit tests per item (each one phrase in §2/§3 fixes).

---

## 4. Low & Nit Findings (the long tail — grouped)

### Cost / ledger accuracy
- **L** — Immediate-fix re-embed cost (and initial novelty-embed cost) folded into `counters['cost']` but never into the bandit arm's `_slot_mut_cost` (`run_window.py:597-598,550-552,669`); arm cost excludes all embedding spend while `rejected_cost` includes it. *(eval_fix F6)*
- **L** — Bandit update silently skipped when `llm_models` is unset (`run_window.py:496,661`); a run pinned via bare `evo.model_name` (undocumented; grep finds it nowhere in docs) leaves per-arm cost/learning empty. *(reward_bandit F7)*
- **L** — DR web-search surcharge is flat $0.30 regardless of `max_tool_calls=20`, assuming $10/1k; over-estimates at the assumed rate but the rate assumption is unverified vs real `web_search_preview` billing (`deep_research.py:131-135`). *(deep_research F5 — owner should confirm the rate; uncertain)*

### Exploration / recovery levers
- **L** — `epsilon` (the real exploration floor, default 0.2, `prioritization.py:636-639`) is in no config and no SKILL knob; reachable only via undocumented `llm_dynamic_selection_kwargs` pass-through (`select_llm.py:54`). Pushes the agent toward a code rewrite when a config flip would do. *(reward_bandit F3)*
- **L** — `force_explore` (the collapsed-bandit cure) is not plumbed into run_window's select payload (`run_window.py:497-503`); reachable only by rewriting `select_llm.py`, though it's a one-line override already present (`:118`). *(reward_bandit F4)*
- **L** — `enable_web_search` silently ignored on the non-Azure/OpenAI legacy provider path (`mutate.py:140,164-165`); a DR-grounding run pinned to a non-Azure model gets no web search and no warning. *(inner_mutation F8)*

### Diagnostics peek / sensor fidelity
- **L** — Diagnostics "weights" peek omits `subset`/`force_explore` (`run_window.py:752-758`), so after a rewrite introducing them, the rung-3 sensor misreports the effective distribution. *(reward_bandit F6)*
- **Nit** — Diagnostics doc lists `window_cost`/`total_cost`/`budget_remaining`/`budget_hit`/`return_reason` under "Diagnostics shape" though `diagnostics.py` post-attaches them in `run_window` (`:793-797,818`); `strategy_fingerprint` IS emitted by diagnostics (`:119`), so the finding slightly overstates. *(orch_docs F8, partial)*

### Edge cases — islands / eviction / migration
- **L** — `max_islands` eviction is a soft cap that silently overshoots when all candidates are protected (`islands.py:1029-1035` returns a fresh index). *(island F8)*
- **L** — Post-eviction, shinka's *native* sampler reverts to the un-inspired initial-program path (`dbase.py:1280-1308`); latent for the orchestrator (uses `sample_parent.py`), bites shinka-native tools/tests. *(island F9, medium conf)*
- **L** — New-program island assignment falls back to a *random* island when the parent's `island_idx` is NULL (`islands.py:186,105-110`) → cross-island contamination for evicted-lineage rows. *(island F10, medium conf)*
- **L** — `rollback()` (single-file) doesn't snapshot the about-to-be-discarded file first (`strategy_store.py:156-171`); recoverability relies on the earlier deploy snapshot. *(rewrite_protocol new, medium conf)*
- **L** — Scheduled migration stamps `self.last_iteration`, not the triggering program's generation (`dbase.py:2502-2507`); migration_history generations can be wrong. Latent (`migration_rate=0.0`). *(island new, medium conf)*
- **L** — `validate_strategy` smoke runs ONE payload per target (`:248-291`); a key dropped only on the incorrect/empty-archive branch passes. *(rewrite_protocol F9)*
- **L** — No code guard against re-deploying a `rejected` hash (`strategy_store.py:119-153` never calls `read_index()`); the "don't retread" invariant is doc-only. *(rewrite_protocol F8)*

### Stagnation / cadence / window edges
- **L** — Bounded mode with `--windows 0` returns an empty diagnostics dict (only `return_reason`/`ok`); downstream reads of `low_streak`/`stagnation_flag` KeyError or read defaults (`run_window.py:809,834-842,905-911`). *(stagnation_cadence new)*
- **L** — At mock cost 0, the budget railguard is vacuous; `window_cap` is the sole termination backstop (`run_window.py:532,738`). Default cap (3) is safe; an enormous cap + a cost-reporting bug could hang. The zero-cost case is untested. *(stagnation_cadence F2, reframed from "never returns" — partial)*

### Mutable-vs-foundation / produced-key documentation drift
- **L** — `evo.extra_guidance` is a live lever (`run_window.py:485` → `construct_mutation_prompt.py:100-102`) but appears nowhere in SKILL.md's schema/levers table. *(contract_config F7)*
- **L** — `diagnostics.py` emits `correct_programs` (`:132`) but SKILL omits it and nothing consumes it. *(contract_config F5)*
- **L** — `trigger_metric` is threaded `run_window:780 → diagnostics` but never read/emitted; default string `'delta'` is stale (live trigger is `'hybrid'`, `stagnation_detector.py:111`). *(stagnation_cadence + contract_config F6)*
- **L** — `novelty_max_similarity` is listed under "spot it in journal" (`SKILL.md:135`) but is per-program *metadata* (`record_policy.py:70`), not a window diagnostic; must be read via `archive_query include_metadata`. *(contract_config)*
- **L** — `archive_query.py` INPUT docstring omits `include_metadata` (`:8-25`) though the code supports it (`:45`) and SKILL instructs using it. *(deep_research new)*
- **Nit** — `windows_run` emitted (`run_window.py:833`) but absent from the documented diagnostics shape. *(contract_config)*
- **Nit** — `diagnostics.py` docstring (`:14-26`) is stale on BOTH sides: documents the dead `exhausted_retry_slots:[id...]` contract and omits ~10 keys the harness actually sends (`strategy_fingerprint`, `stagnation_abs_floor/rel_frac`, `trigger_metric`, `fix_count`, `fix_success`, `novelty_rejected_cost`, `llm_bandit_counts`). The FOUNDATION sensor's own contract is the least accurate description. *(flow_coherence F10 + contract_config new + flow_coherence new)*

### Stale docs / references
- **L** — `taxonomy.md` cites removed files (`async_runner.py`, `summarizer.py`, `deep_research_summarizer.py`, `prompt_evolver.py`) in the four-cell mutability tables with no in-body mapping to live files (banner redirects to SKILL.md). *(orch_docs F6, partial — `prioritization.py` is NOT removed, only its line numbers stale)*
- **L** — `taxonomy.md:42` teaches the superseded `J=Δ·log(1+s_start)/√W` formula and the `J<tau` trigger; live code is `J=Δ/√W` + hybrid trigger. *(contract_config F3)*
- **L** — `configs/azure_default.yaml:2,6` reference removed `shinka_run` CLI and a non-existent `shinkaevolve/…/pricing.csv` path. *(flow_coherence F6)*
- **L** — `run_window.py:12` docstring says "select_llm (deferred to Phase 5)" though it's fully wired, and the order line places select_llm first when it actually runs at step 2b. *(flow_coherence F7 + new)*
- **L** — `NOTES.md:75-92` lists shipped features (WS6 effort-as-bandit-arm; structured weighted meta directions) as "deferred"/legacy, and describes the legacy `evo.meta_recommendations` single-blob path as current. *(meta F4 + flow_coherence F8 + contract_config — multiple dimensions; SKILL.md is correct, so blast radius limited)*
- **L** — README scripts list omits `cadence_policy.py`, `spawn_island.py`, `meta_summarize.py`; `tasks/README.md` says circle_packing "drives the smoke test" but `run_live_smoke()` raises `NotImplementedError` (`smoke_test.py:208-211`). *(flow_coherence F9)*
- **L** — `record_outcome`/`record_bundle_outcome` SKILL examples (`:298-302`) omit the required positional `new_hash`/`J` args and never say which `J` to pass → `TypeError` on a literal copy. *(orch_docs F5)*
- **L** — Three test files use `return True` in pytest-collected functions (17 `PytestReturnNotNoneWarning`); becomes a hard error under future pytest and can mask assert-less tests. *(tests F9)*
- **Nit** — `deep_research.py` mock path (`:111-113`) omits `token_cost`/`search_surcharge` keys the live path returns (`:152-153`). *(deep_research new)*
- **Nit** — `smoke_test.py:8-9,40,130,139` docstring/assertions still use the old "J below tau" stagnation model. *(stagnation_cadence F5)*
- **Nit** — Idempotent `snapshot()` drops meta for re-deployed identical content; `update_meta` overwrites the earlier deploy's `window_index` in the shared `meta.json` (`strategy_store.py:93-107,137-138`); index.json preserves per-deploy rows. *(rewrite_protocol F10)*
- **Nit** — `configs/README.md:24-25` calls the removed CLI `shinka_launch` while `azure_default.yaml` calls it `shinka_run` — two different dead-command names. *(flow_coherence new, medium conf)*
- **Nit** — Immediate-fix re-embeds the repaired code but logs stale pre-fix `novelty_max_similarity` and original `patch_type` alongside the fix's `patch_name` (`run_window.py:553,614-632`, `record_policy.py:55-72`); provenance drift. The patch_type-diagnosis impact rationale is partly ungrounded (SKILL doesn't diagnose via patch_type-per-candidate). *(flow_coherence partial)*
- **Nit** — `_gather_recent` caps recent failures at `n_recent//2` (default 8) before topping up with top performers (`meta_summarize.py:129,136`), so a failure-dominated window under-samples the failure signal WS3 says comes "first." *(meta new, medium conf)*

---

## 5. Orchestrator Doc-Clarity Verdict

**Would a fresh, cold agent know what to do at every decision point? — No. There are direct self-contradictions on operational essentials and a teaching gap on the owner's headline duty.**

### SKILL.md (the playbook, ~471 LOC)
- **Self-contradiction on budget logging (H5a):** `:42` says append_intervention the meta/DR cost, `:352` says do NOT. A cold agent double-counts every ~$5 DR call or guesses.
- **Self-contradiction on the main loop (medium, orch_docs F3):** `:169` says `--windows 1` (debug mode); `:62-66,428-435` say `--until-decision` is "the normal mode." Following the Main-loop section literally pays an agent turn per window, eroding the 100× cost asymmetry rule 1 protects.
- **Headline-duty teaching gap (medium, orch_docs F4):** the owner's canonical "a model is NEVER selected — truly bad, or reward lock-out?" appears nowhere as a standalone investigation. Grep for "never selected"/"locked out"/"never recover"/"zero-count" across all teaching docs returns ZERO. It's surfaced only as stagnation-ladder rung 3 *gated on flat J* (`:195-196`) and rung 4 (`:198`). An agent watching a rising-J run with a silently locked-out arm never investigates. The diagnostic data exists (`llm_bandit_counts`), so it's teachable — just not taught decoupled from the J ladder. **This is the most important doc gap: role (2)'s flagship duty is under-taught.**
- **`low_streak` carry (H13):** `:104-105` actively instructs the protocol that *produces* the thrashing failure NOTES claims is prevented.
- **DR snippet (H12):** `:229-232` tells the agent to control a lever (`reference_snippet`) that lives in the immutable system prompt.
- **Undocumented knobs:** `epsilon`/`force_explore` (the cheap recovery levers, §4) and `extra_guidance` are not in the levers table; the agent reaches for a code rewrite instead.
- **rollback example teaches the deprecated guard** (via smoke_test, H4); `record_outcome` examples `TypeError` on copy (§4).

### CLAUDE.md
- Frames the rewrite power purely as a stagnation reaction (`:25-31`), reinforcing the role-(2) gap. Otherwise accurate on invocation (`:111,130-131` use `--until-decision`, contradicting SKILL's Main-loop outlier — CLAUDE is right here).

### NOTES.md
- Multiple stale claims presented as current: "a rewrite resets low_streak" (false, H13), legacy `evo.meta_recommendations` single-blob meta (M-cluster), WS6/structured-meta listed as "deferred" though shipped. SKILL is the live source, so blast radius is limited — but a future agent consulting NOTES writes to the wrong field or models the wrong behavior. Honest on `island_policy` being advisory (`:90-92`), which is good.

### taxonomy.md
- Carries a HISTORICAL banner redirecting to SKILL, which limits damage — but still teaches the superseded J formula on the meta-loop trigger row (`:42`), lists removed files in the mutability tables (`:115-152`), implies `exhausted_retry_slots` carries data (`:90`), and lists `spawn_island` under the mutable island cell (`:195`) contradicting the code's FOUNDATION treatment (M14). For a doc the owner names as the four-cell mutability teacher, the stagnation-trigger row and the spawn_island cell should be fixed.

### subagents/{debug-agent, archive-analyst}.md
- `debug-agent.md:9-10` states "retried it three times" — false (default `fix_retry_budget=1`); skews the subagent toward a structural root cause (H7-related). The escalation it keys on (`exhausted_retry_slots`) never fires from real data (H7).

### Boot / first-job task_sys_msg boundary
- The "read evaluator, encode hard constraints *without spoiling held-out metrics*" boundary is described, but given C1 (the evaluator never signals incorrectness) and M6 (text_feedback dropped), an agent that correctly reads the evaluator still cannot observe the constraint violations at runtime — the doc boundary is sound but the runtime makes it moot until C1/M6 are fixed.

**Bottom line:** the mechanical "how to invoke / how to read diagnostics" layer is mostly teachable, but (a) two operational contradictions force guessing, and (b) the owner's central role-(2) duty — recognizing a structural reward/framework flaw independent of a stagnation symptom — is essentially untaught.

---

## 6. Design-vs-Implementation Alignment Matrix

| Owner-design pillar | Implemented? | Gap in one phrase |
|---|---|---|
| Role 1: Orchestrator decides cheaply from local logs | partial | Sensors exist, but two are inert/lying (`exhausted_retry_slots`, `evaluation_failure_rate` on cnot) and cheap recovery levers are undocumented |
| Role 2: Outer-loop framework-mutation (recognize+rewrite flawed strategy) | partial/buggy | Headline "model never selected/reward lock-out" duty under-taught; `island_policy.py` rewrite is inert (H8); rollback blind to reward rewrites (H4) |
| Boot / first-job task_sys_msg (read code+eval, encode constraints, no spoiler) | partial | Boundary documented, but evaluator never signals incorrectness (C1) and `text_feedback` is dropped (M6), so constraints aren't observable at runtime |
| Prompt = parent + inspirations + sampled direction + failure warnings | buggy | failure_note dropped on cross/lit (M1), empty-directions (M2), and fix mode (M4); seeded runs collapse operator mix (H6) |
| Inner loop cheap + high-volume + stable | partial | Stable only because failures are mislabeled correct (C1); ~49% spend on novelty rejects; capped calls waste budget (H2) |
| Islands genuinely differentiated | no | Identical boot + unwired `island_brief` + shared global direction (H1); spawn clones (M13) |
| Fixed-retry repair on eval error | buggy | Never fires on cnot (C1); when it would, repair prompt is blind (M5/M6); `fix_count` miscounts (M9) |
| Meta works→weighted directions; fails→failure classification | partial/buggy | Weighted sampling works, but failure_note silently drops in 3 paths (M1/M2/M4); meta classification keeps only the generic traceback banner (M7); no surfacing of active directions (meta F3) |
| Periodic OUTER round rewrites strategy code surgically | partial | Protocol exists but: no FOUNDATION-overwrite guard (H10), non-atomic bundles (H11), mutate has no contract (M15), rollback untested+blind (H4) |
| Deep research + triage against archive | partial | DR call works, but immutable prompt demands forbidden snippet shape (H12), no budget pre-flight (H5b), refusal undetectable (deep_research F3), brief can't reach prompts (M3) |
| Budget hard-capped in code, bounded overshoot | buggy | Cap covers only the inner loop; DR/meta unbounded (H5b); capped/failed Azure calls drop cost (H2); silent cost-0 on pricing miss (M10); double-count via SKILL (H5a) |
| Append-only audit trail / strategy_history | partial | Solid, but no re-deploy guard (rewrite F8), single-file rollback doesn't snapshot discarded bytes (rewrite new), meta.json window_index clobbered on redeploy (rewrite F10) |

---

## 7. First-Principles Observations (evolutionary-search health)

Beyond the named findings, judging the framework as a search:

1. **Premature convergence is the default, not an edge case.** Identical island boot (H1) + shared global direction (H1) + a near-deterministic cheap model + seeded operator collapse (H6) means N islands run nearly the same trajectory. The one structural diversity mechanism that *works* — island-keyed novelty rejection (island new) — only suppresses within-island duplicates; it does nothing for inter-island spread. There is no real diversity *injection* across islands.

2. **The fitness signal cannot distinguish valid-no-gain from invalid (C1 + reward H3).** With cnot, `correct=True/score=0` for garbage *and* for the baseline-tying seed, and the bandit clamp makes correct-but-worse identical to failed. Selection pressure toward genuine improvement is indistinguishable from noise until a strictly-better candidate lands — and a duplicate-prone cheap arm is rewarded by the cost blend for producing nothing useful. **This is reward hacking by omission: the search optimizes "cheap and not-NaN," not "better."**

3. **No usable exploration floor in practice.** `epsilon=0.2` exists but is undocumented and unreachable except via an undocumented kwargs pass-through (§4); `force_explore` requires a code rewrite (H3). When the bandit collapses (the predictable outcome of H3), the cheap recovery is invisible and the expensive one (rewrite + validate + deploy + rollback-watch) is blind to the very collapse it's meant to fix (H4). The recovery loop is structurally broken at both ends.

4. **Selection pressure is mis-weighted toward cost.** `cost_aware_coef=0.5` on a hard-argmax posterior, blended with a flattened quality term, makes cost the dominant differentiator (H3) — and rejected-slot cost being invisible to the bandit (H3) makes the wrong arm look *even better*. On a hard task this entrenches a model that cannot solve it.

5. **The learning (meta) loop leaks exactly when it matters.** failure_note — the mechanism that prevents re-introducing known failure modes — drops on cross gens (~10%), on empty-directions meta rounds (when failures dominate), on every fix/repair, and (latently) on DR-grounding gens (M1/M2/M4/M3). Meta also re-spends budget on near-duplicate directions because `prior_recommendations` is never auto-populated (meta F3), and its failure classification sees only generic traceback banners (M7). The loop that's supposed to make the search *smarter over time* is the leakiest subsystem.

6. **Restart / diversity injection is absent or anti-diversity.** Dynamic-island spawning (when enabled) clones the best or initial program (M13) — the opposite of a restart. There is no random-restart or fresh-direction injection mechanism; the only differentiation source is stochastic mutation drift under one shared direction.

7. **Migration topology is effectively untested and off by default** (`migration_rate=0.0`), with bookkeeping drift if enabled (island new). Not a live risk today, but the island data structure provides no realized benefit (independent niches) while carrying its full complexity cost.

**Net first-principles read:** the framework has the *scaffolding* of a healthy evolutionary search (islands, bandit, novelty, meta, DR, rollback) but the *forces* that make such a search work — a truthful fitness signal, inter-island diversity, a reachable exploration floor, cost-balanced selection, a leak-free learning loop, and a working recovery path — are each individually compromised. The most urgent are C1 (truthful fitness) and H1+H3 (diversity + selection pressure), because without them the rest of the machinery optimizes the wrong objective cheaply and confidently.

---

## 8. Appendix: Checked and Dismissed (refuted findings)

- **`max_windows_per_call=0` returns after every window** *(stagnation_cadence F3 — refuted).* `cadence_policy.py:42` coerces `0 or 3 → 3`, so the comparison is `windows_run >= 3`, not `>= 0`; a 0 behaves exactly like the default. Residual is a purely cosmetic guard inconsistency between `run_window.py:807` (no `or 3`) and the policy (harmless because the unguarded value is consumed only by the guarded policy).

---

### Critical Files for Implementation
- /Users/dantongli/GIthub/ShinkaEvolve/.claude/worktrees/cool-hertz-072f34/orchestrator/harness/run_window.py
- /Users/dantongli/GIthub/ShinkaEvolve/.claude/worktrees/cool-hertz-072f34/orchestrator/scripts/_azure.py
- /Users/dantongli/GIthub/ShinkaEvolve/.claude/worktrees/cool-hertz-072f34/tasks/cnot_grid_synth/evaluate.py
- /Users/dantongli/GIthub/ShinkaEvolve/.claude/worktrees/cool-hertz-072f34/shinka/llm/prioritization.py
- /Users/dantongli/GIthub/ShinkaEvolve/.claude/worktrees/cool-hertz-072f34/orchestrator/SKILL.md

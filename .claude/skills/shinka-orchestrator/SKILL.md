---
name: shinka-orchestrator
description: Use this skill when running an LLM-driven evolutionary search on a code optimization problem in this repo. You wear two hats — the ORCHESTRATOR (the operational, in-the-flow jobs the run can't proceed without) and the OUTER-LOOP / FRAMEWORK-AUDIT role (judging whether the deterministic framework code itself is flawed and rewriting it). Invoke at problem onset, during warmup, when the inner loop returns control, when a budget is exhausted, or to resume a run.
---

# Shinka Orchestrator

You drive an evolutionary search whose inner loop is fast, deterministic, and runs
WITHOUT you. You set it up, oversee it, intervene when it needs you, and stop it when
it's done. You are not in the path of every mutation — you are woken when a window-
cluster returns control.

## Your two roles (you wear both hats)

**ORCHESTRATOR — operational, in the critical path.** The jobs the run cannot proceed
correctly without; if you skip them or do them wrong, the flow breaks. At boot: read the
task's initial code + evaluator, infer the goal, and author the system-message problem
statement (the goal, the hard constraints, the *shape* of the score) **without spoiling
the held-out metric**, plus an abstract runtime-efficiency caution (no specific numbers).
When you decide a deep-research (DR) round is warranted: write the DR query, triage the
brief, seed/ground islands, fold results in. These fire **whenever the flow demands them
— there is no cadence to them.**

**OUTER-LOOP / FRAMEWORK-AUDIT — improvement, NOT in the critical path.** Read the logs
and history to judge whether the deterministic **framework code itself** is flawed, and
rewrite the mutable strategy code when it is. The canonical case: a model that is never
being picked — is it *truly bad* (it ran enough and genuinely underperformed) or *locked
out by a reward/selection flaw* (near-zero selection count but a positive reward the few
times it ran)? The run continues without this role; it improves the framework over time.
It runs on the **tapering control-return cadence** below: scrutinize the framework
frequently early (the code is least proven), and less and less as it proves robust.

## Two rules that make this work

1. **The inner loop's LLM calls go to Azure, never to you.** Every mutation, fix, repair,
   and meta call is made by a `scripts/` subroutine that calls Azure in background-poll
   mode. NEVER "simulate" a mutation in your own context — that would pay an agent turn
   (~100×) for a stateless API call and destroy the cost economics. Your tokens are spent
   only on control-return reasoning and on writing the DR query.
2. **Do not stop until a termination criterion is met.** A run is a long, consecutive
   process; the healthiest run is many control-returns read with no intervention. Keep
   launching the next cluster. Idleness is not done-ness — only the termination checklist
   is (see Termination).

## Safety railguards (enforced in code — NOT strategy knobs you can weaken)

- **Budget hard cap + crash-durable ledger.** Set `budget_usd` in the run config. The
  harness keeps a cumulative cost ledger (`journal/run.json` → `total_cost`) summing EVERY
  LLM cost (mutation, the automatic meta round, deep research, embeddings) plus the
  interventions you log. The harness **hard-stops** the moment cumulative spend ≥
  `budget_usd` (`return_reason="budget_exhausted"`; overshoot ≤ one slot). The ledger is
  crash-durable: `run.json` is written atomically and a missing/corrupt one is rebuilt by
  recomputing `total_cost` from the durable journal streams — so a crash mid-write can
  never silently zero the ledger and defeat the cap. The one accepted gap: a boot-time
  embedding cost logged before the first window (on no durable window/intervention/call
  line) is the only spend a recompute cannot recover.
- **Per-call cost cap (~$10).** Every external LLM call (mutation / meta / DR / fix)
  carries a per-call max-output-token cap sized so one call cannot exceed ~$10 (pro at its
  50k cap ≈ $9; others incl. `gpt-5.5` at 200k ≈ $6; DR at its 200k cap ≈ $8). This is a
  deliberate runaway guard — do not remove or shrink it.
- **No unmonitored LLM calls.** Every call goes through a counted path whose cost lands in
  the ledger; the eval subprocess runs the task's `evaluate.py` (no LLM).
- **Worktree shinka.** The harness asserts `shinka` resolves to THIS repo at startup
  (loud fail otherwise) — you never silently run a different checkout.

## The run loop, end to end

This is the single source of truth; the rest of this doc expands each step.

1. **WARMUP — you are fully awake, inspecting each step.** Before the real run, oversee
   ONE window in a **throwaway workspace** (its own db + journal under
   `<results_dir>/warmup/`) with per-step tracing ON. You read the `steps.jsonl` trace
   after the window — which parent the sampler chose and why, the assembled prompt summary,
   the code/summary the model returned (and whether the patch applied), the eval result
   and its failure type, and what the framework decided next. The moment a step looks
   wrong you STOP and CORRECT the implicated policy file, then RESTART warmup until the
   window is meaningful and the trace confirms it. Then CLEAN UP the warmup workspace.
   Warmup's narrow job: confirm the inner loop is mechanically sound (sampler → prompt →
   eval → novelty → record all wired correctly) on a FRESH archive. It cannot reproduce a
   flaw that only emerges with a populated archive — those surface on the real run's
   per-window diagnostics, which the orchestrator + framework-audit roles handle. (See
   Warmup below for the launch + the common flaw-signals.)
2. **ACTUAL RUN — event-driven; you are woken, you do not poll.** You launch a self-
   caffeinated window-cluster (`run_window.py --until-decision`, background-launched). The
   harness runs windows autonomously and **returns control by exiting** at the cluster
   boundary; that exit re-invokes you — that exit-and-re-invoke IS the "wake". Initially
   control returns after every window; as your recent work tapers it returns less often.
   There is **no max-window cap** — a cluster is bounded only by the budget hard-stop, a
   termination criterion, and stagnation (which always returns control immediately).
   Recover from any kill with `--resume`, and while the cluster is backgrounded hold a short
   self-wake **heartbeat** (see "How you launch the inner loop") so the sandbox does not
   idle-reclaim the run (keep the lid open / stay on AC; a clamshelled laptop hardware-sleeps
   regardless).
3. **EACH WINDOW ENDS WITH AN AUTOMATIC META ROUND — run by the harness, not by you.**
   Deterministic code composes the meta prompt from the current archive + the live island
   list and calls the external LLM (default `azure-gpt-5.5` at medium effort). In one shot
   it returns global directions + a failure caution + ONE differentiated direction per
   live island, auto-recorded as per-island briefs so islands evolve in different
   directions BY DEFAULT. Meta is NOT an orchestrator action and does NOT count as an
   intervention.
4. **WHEN CONTROL RETURNS you do two checks on one shared rhythm:** (a) the **framework-
   audit check** (rewrite a mutable strategy file if a flaw is found), and (b) the **DR
   check** (run a DR round if the stall looks algorithmic and warrants it). Both happen at
   every control-return. Then **record a work score** for what you just did — recorded
   AFTER acting (never let the score you intend to record influence what you choose to do).
5. **THE TAPER (two-stage).** STAGE 1 — the **early phase**: for the first
   `cadence.early_phase_windows` windows (default 5) control returns EVERY window regardless of
   work score, so you inspect each one while the framework is least proven. STAGE 2 — the
   **work-score taper**: past the early phase your recent work score drives the **next** cluster
   size — high recent work → keep checking every window; as low work persists the cluster grows
   (base_low, then doubling: 5 → 10 → 20 → 40 …) with no ceiling. The low-streak is counted FROM
   THE END OF THE EARLY PHASE, so the early per-window returns don't make the first steady cluster
   jump (without that, 5 early low returns would launch a ~80-window cluster). The same cluster
   size is BOTH the framework-audit cadence AND the DR-check cadence — one shared rhythm. If you
   forget to record a work score the taper has no signal and conservatively wakes you every window
   (and the harness prints a reminder). Set `cadence.early_phase_windows:0` to disable Stage 1.

## The work score (record it after every control-return)

After you act on a control-return, append ONE canonical row to `interventions.jsonl` — it
drives BOTH the taper AND termination, so there is exactly one row per control-return:
`{type:"control_return", window_index, stagnation_flag, best_score, work_audit, work_dr,
work_score, intervened}`:
- `work_audit` — framework-audit magnitude: a full core-strategy rewrite ≈ 3, a tiny
  param change ≈ 1, no change 0.
- `work_dr` — DR magnitude: not run 0, run-but-nothing-new 1, new directions worth
  combining into an existing program 2, new directions worth grounding as a NEW island 3.
- `work_score` — their sum (the scalar the taper reads via `journal.recent_work_score`;
  `journal.work_low_streak` counts the recent low-work returns the escalation uses).
- `stagnation_flag` — copy this return's window-diagnostics value (the stall state NOW).
- `intervened` — `work_audit>0 or work_dr>0` (did you actually act this return?). INCLUSIVE:
  a deliberate config-LEVER flip counts (log it as `work_audit` ≥ 1); the automatic per-window
  meta round does NOT (it isn't your action); a pure no-op read leaves it false.

Record it AFTER acting, never before — the row must describe what happened, not steer what
you do. The harness reads `stagnation_flag`+`intervened` across the last rows for the
termination check (below); a forgotten row simply can't advance termination (fail-safe).

## How you launch the inner loop

```
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

`--until-decision` runs windows autonomously and returns control by **exiting** at the
cluster boundary (no turn of yours per window). Background-launch it so the run survives
your turn ending and re-invokes you on exit; recover any kill with `--resume`. A fresh
subprocess each call is how a deployed code rewrite takes effect — the next invocation
imports the new file. (`--windows 1` runs exactly one bounded window; `--windows 1
--trace-steps` is the framework-audit MEASURE window — see the rewrite cycle.)

`run_window` self-caffeinates on macOS (holds `PreventUserIdleSystemSleep` for its
lifetime, auto-released on exit) so a long cluster is not reaped by host idle-sleep. There
is no separate detached launcher — the background-launched `--until-decision` IS the wake
primitive; it returns by exiting and re-invokes you. Keep the lid open / on AC for
unattended runs.

**Heartbeat — the survival leg self-caffeinate can't cover.** Self-caffeinate beats *host*
idle-sleep, but NOT the *sandbox* idle-reclaim of the backgrounded launcher→`run_window`→eval
process group: the agent's OWN long idle is what arms it (tens of minutes of session dormancy
→ the cluster is reaped mid-window with no exit and no wake — the "missed wake", where you
wait forever for a cluster that is already dead). With the default `window_size` a single
window can outlast that threshold, so a deploy-and-yield is exactly what dies. While a cluster
is backgrounded, therefore, do NOT yield into a long idle: arm a short self-wake **heartbeat**
— a backgrounded timer of a few minutes that exits and re-invokes you — and on each wake
confirm `run_window` is still alive and progressing (the process exists; `journal/run.json`
`updated_at` and `windows.jsonl` advancing), then re-arm; keep the interval well under the
reclaim threshold (~4 min is safe). Stop re-arming only when `run_window`'s own clean-exit
notification arrives. The heartbeat does NOT detach `run_window` (it stays a harness-tracked
job, so its exit still wakes you) — it keeps the *session* active so the tracked job is not
reclaimed (the same effect a user's periodic ping has). `--resume` only RECOVERS a kill after
the fact; the heartbeat PREVENTS the missed-wake.

**Re-arm robustly — a single forgotten re-arm kills the run** (the observed failure mode: a run
held alive for hours died the moment a long reasoning stretch lapsed the timer). On every
heartbeat wake, RE-ARM THE NEXT TIMER FIRST — before the liveness check or any other work. Re-arm
UNCONDITIONALLY: even on a wake that found nothing new, and *especially* right before a long
reasoning/rewrite turn (arm the next timer immediately before you start thinking). Before ending
ANY turn while a cluster is backgrounded, run the P0 self-check "is a live heartbeat pending?"
(alongside "did I record a work score?"). Stop re-arming ONLY on `run_window`'s own clean-exit
wake — a stagnation/taper return is followed by another launched cluster, which must be
heartbeated again.

Diagnostics shape (printed to stdout + appended to `journal/windows.jsonl`):

```
{ window_index, iters_completed, best_score_start, best_score_end, delta,
  J_score, threshold, strategy_fingerprint,
  novelty_acceptance_rate (null when no novelty events), novelty_rejected_cost,
  evaluation_failure_rate, apply_exhausted_count, apply_failure_rate,
  timeout_count, wrong_answer_count, errored_fraction,
  model_collapse:{top_arm, top_share, n_arms_active, collapsed},
  repair_mode_on, repair_fail_count, repair_tombstoned_count,
  fix_rate, fix_success_rate, needs_fix_rate,
  llm_bandit_weights, llm_bandit_counts,
  island_health:[{id,best,diversity,stagnation_count,count}],
  stagnation_flag, low_streak, termination_streak, exhausted_retry_slots, exhausted_retry_count,
  trigger_metric, total_programs, correct_programs,
  window_cost, total_cost, budget_remaining, budget_hit, windows_run, return_reason }
```

Read the **progress trajectory** as the best-score gain (`delta`) vs the low-window bar
(`threshold`); rollback uses the multi-signal `rollback_decision.py`, not a single
number. `evaluation_failure_rate` is the *post-repair* rate over EVALUATED slots; a
patch that never applied is counted separately in `apply_exhausted_count` /
`apply_failure_rate` (it produced no candidate, so it is not an eval failure). To see
which KIND of failure dominates, read `timeout_count` (the harness's eval-time-limit
signal) vs `wrong_answer_count` vs `apply_exhausted_count`. `errored_fraction` is
cumulative over all NON-tombstoned programs (distinct from the per-window
`evaluation_failure_rate`). `model_collapse` is a SURFACED counts-share signal (a single
arm's weight caps at `1−epsilon`, so counts is the real signal) — you act on it on your
cadenced check; it is **never auto-corrected** in steady-state. `stagnation_flag` fires
when a window stays "low" for `consecutive_required` windows — low = best-score gain
`Δ ≤ max(stagnation_abs_floor, stagnation_rel_frac·max(s_start,0))` (scale-free above the
floor, so a small-but-real gain doesn't trip it; the floor is the opening-phase bar when
the best score is ≈ 0). Carry `low_streak` → next config's `window_state.prior_low_streak`
and bump `window_state.window_index` (or just pass `--resume`, which reads both from the
journal).

## Warmup (your first real job after authoring the goal)

```
python orchestrator/harness/run_window.py --config <run>/run.json --warmup
# runs ONE window in <results_dir>/warmup (its own db + journal), tracing ON, then prints
# the workspace path. Read <results_dir>/warmup/journal/steps.jsonl, then either fix a
# policy and rerun --warmup, or, when satisfied, clean up and start the real run.
python orchestrator/harness/run_window.py --config <run>/run.json --cleanup-warmup
```

Read the per-step trace after each warmup window and stop-correct-restart on a bad step.
Never fix the evaluator — if warmup fails because of the evaluator, STOP and report (the
evaluator is foundation). Common flaw-signals (read off `steps.jsonl` + the window diag):

- **High eval-failure rate (`wrong_answer_count` dominating)** → the synthesized code
  rarely solves the task → suspect the mutation prompt (`construct_mutation_prompt.py`):
  is the goal precise, are constraints stated, is the patch format right? Read the prompt
  step.
- **Rising `apply_exhausted_count` (mutations returning `applied:false`)** → the model's
  patches don't apply at all → suspect the patch-type mix / diff-vs-full instructions.
  Read the `llm_output` step + the per-slot apply error.
- **Many generations reusing the SAME parent** → the search isn't spreading → suspect the
  parent sampler (`sample_parent.py`): flat all-zero scores (try `validity_floor`) or too
  sharp a `parent_selection_lambda`. Read the sampler step across generations.
- **One model already winning after a few draws** → premature collapse → suspect the
  reward (`compute_reward.py`) / selection (`select_llm.py`): a couple of early bad draws
  may have starved an arm, or `cost_aware_coef` over-penalizes a pricier-but-better arm.
- **Novelty rejecting most candidates / a per-island pool stuck at ~1 genotype** →
  near-duplicate flooding. The gate embeds the parent→candidate DIFF by default
  (`novelty_embed_mode: diff`), so genuine edits separate to low cosine and the pool grows —
  first confirm it is `diff`, not the legacy `code` basis (where a small edit on a large
  program is ~0.994 similar to its parent, so every improvement is mis-flagged as a near-dup
  and evicts its own parent; under `code` you must RAISE `code_embed_sim_threshold`, not lower
  it). With keep-the-better (H5) a flagged near-dup is still EVALUATED and the better of the
  pair kept, so flooding costs evals + plateau drag, not a frozen archive. Also suspect weak
  mutation diversity. Read the dropped-on-novelty decision; watch `novelty_kept_better` (the
  near-dup-flood sensor — H2/M34), `novelty_rejected_cost`, and `embed_failures` (>0 means the
  embedder failed and the gate was BLIND for those slots, not "diverse" — M29).
- **Eval timeouts (`timeout_count` rising)** → the synthesized code is too slow → `record_policy`
  persists each candidate's `runtime_sec`/`timed_out`, and `construct_mutation_prompt` injects a
  bounded "Runtime budget" caution into BOTH the fix and new-mutation prompts when a parent or an
  inspiration ran slow (≥0.8× `task.eval_time`) or timed out — so the LLM keeps the algorithmic win
  but finishes in time. It does NOT penalize a slow-but-correct candidate (still archived/scored
  normally). Confirm it's firing (`runtime_sec` in metadata; the prompt shows the caution).
- **Per-island briefs all reading the same** → islands aren't differentiating → suspect
  the meta producer prompt (confirm `island_directions` are genuinely distinct per island
  in the meta call log).
- **A "successful child" byte-identical to its parent with `num_applied==0`** → the
  apply-exhausted-as-success bug → confirm the failed-apply slot is recorded as a failed
  attempt (no archive row), not silently scored as the parent copy.
- **A measure window with empty / NaN diagnostics or a non-zero exit** → the window
  crashed → treat as no-usable-data and revert (the rewrite cycle fails closed).

## Boot: author the goal (no-spoil) + a spoiling self-check

1. **Author `task_sys_msg` — your first real job.** Read the task's `initial.<ext>` and
   `evaluate.py`, then write a precise problem statement: the goal, the gate set / hard
   constraints, the score's shape, and an abstract caution that each eval has a runtime
   budget so the code must stay efficient (NO specific numbers — reinforced in-loop by the
   numeric, no-spoil runtime-budget caution when a candidate runs slow). The harness REFUSES to
   start with a missing / empty / placeholder `task_sys_msg` (the starter ships the
   sentinel `__UNSET_AUTHOR_AT_BOOT__`); `task.require_sys_msg:false` overrides for a bare
   debug smoke, and `--warmup` flips it off for its throwaway run only.
2. **Do NOT spoil the eval criterion.** A fair-game system message states the gate set +
   score shape + the initial docstring; off-limits are hidden seeds, private metrics, and
   exact thresholds. While authoring, run the **spoiling self-check**: the evaluator's
   error text rides back to the fix/repair prompt via the harness `stdout_log`/`stderr_log`
   backfill, gated by `use_text_feedback` (default true). If that error text could leak
   the held-out metric (let a mutation game the score), STOP and ask the human before
   continuing. The mitigation is `use_text_feedback:false` — a COMPLETE suppression: it
   blanks both channels the fix prompt reads. (Rare; the cnot task's slope feedback is
   safe.)
3. **Tune the proposer `reasoning_effort` — you OWN this knob; never carry the shipped default
   just because it shipped or a run prompt said "keep defaults".** Thinking-heavy proposers are
   GOOD for reliability on hard/algorithmic tasks, but a cheap model at `medium` can emit
   10–35k reasoning tokens for a ~3k-token patch and wedge a single mutate call 50–90 min / a
   window to 2–3 h. Choose proposer effort per model at boot from task difficulty (encode it in
   the bandit arm as `model@effort`, e.g. `azure-gpt-5.4-mini@low` or `…@high`) and RE-TUNE it at
   any control-return — raise for reliability on a hard task, lower when a cheap arm's verbosity
   drags the run. (`evo.meta_reasoning_effort` is the same knob for the meta round.) Verbosity is
   bounded two ways now: lower effort, or keep it — the bg transport's two-level timeout means a
   genuinely-stuck call no longer rides the whole 1h wall (see "How you launch the inner loop").
4. Decide whether to call deep research for SOTA at onset (see the DR section). Use any
   brief to pick the initial program, `num_islands` (L83: the starter ships 4; the ENGINE
   default if you omit it is 2 — set it explicitly; 8 if multiple algorithmic families
   compete), and to sharpen the goal.
5. Author `run.json` (schema below). Default strategy files as shipped. Then warmup.

## What you may change, and what you must not (tiered mutability)

**FOUNDATION — never touch mid-run. Ruining it breaks the consecutive run.**
- The sqlite schema and the JSON stdin/stdout contract (`scripts/_common.py`).
- `scripts/evaluate.py`, `archive_record.py`, `archive_query.py`, `diagnostics.py` (your
  sensor), `repair_record.py`, `journal.py`, `harness/*`.
- The user's task `evaluate.py` and `initial.<ext>` — provided inputs.
- `deep_research.py` — a paid external service wrapper.

If you believe the foundation is wrong, do NOT change it — note it in the end-of-run
ending document under "Future fixes for the user before the next run" (schema/contract
redesign is a human's job between runs).

**POLICY — freely rewritable, always through the rewrite cycle (validate → snapshot →
deploy → measure → revert).** All `scripts/*.py` flagged MUTABLE in the subroutine table.

## The concern map (change related code together, compatibly)

A problem usually lives across several files — where a signal is *generated* and
everywhere it is *consumed*. Rewrite the whole concern as one atomic bundle so the pieces
stay compatible.

| Concern | Generation / decision | Consumption | Spot it via |
|---|---|---|---|
| **Scoring / reward** | `compute_reward.py` | `select_llm.py` (bandit), `sample_parent.py` (score→parent weight) | per-program `reward_used` vs `improvement_over_parent`; bandit counts |
| **Exploration / parent** | `sample_parent.py` | (feeds the prompt) | flat progress with high novelty acceptance |
| **Diversity / novelty** | `novelty_check.py` | `record_policy.py`, `diagnostics` | `novelty_acceptance_rate` (null when no events); `novelty_rejected_cost` |
| **Prompt** | `construct_mutation_prompt.py` (incl. the bounded runtime-budget caution, driven by `eval_budget_sec`/`parent_runtime_sec`/`parent_timed_out` + parent/inspiration runtime metadata) | `mutate.py` (sends it) | `evaluation_failure_rate`, recurring `exhausted_retry_slots`, `timeout_count` |
| **Fix / repair** | the immediate-fix loop in `run_window.py` (`evo.fix_retry_budget`) + repair mode (`sample_parent` `select:"errored"`) | `construct_mutation_prompt.py` (the `sample_fix` prompt), `mutate.py`, `repair_record.py` | `fix_rate`, `fix_success_rate`, `repair_fail_count` |
| **Stagnation trigger** | `stagnation_detector.py` | `diagnostics.py` | the progress trajectory + `low_streak` |
| **Cadence (taper)** | `cadence_policy.py` (per-window for the first `early_phase_windows`, then the work-score taper) | `run_window` (passes `window_index` + the work score) | how often control returns vs `window_index` + your work score |
| **Memory** | `record_policy.py` (now persists `runtime_sec`/`timed_out` for the runtime caution — read via `include_metadata`) | sampler / novelty / diagnostics / prompt readers | which metadata fields exist |
| **Island structure** | `island_policy.py` (+ per-island briefs auto-written by meta) | the foundation DB | `island_health` per-island trajectory |
| **New directions (meta)** | `meta_summarize.py` (automatic per-window) | the harness records its per-island briefs + global directions; you don't author them | persistently flat progress after rewrites |
| **New directions (DR)** | `deep_research.py` (web-grounded, rare) | you TRIAGE its brief (new → ground in a new island; similar → combine; else ignore) | flat progress that meta can't lift |

## The automatic meta round (not yours to trigger)

Every window, the harness calls `meta_summarize.py` once. It now sees the archive grouped
PER ISLAND with a CODE preview of each island's top + failed programs (not just score
trends — H11), and returns global `directions` + a `failure_note` + a RICH per-island
`islands` block: 1–3 directions per live island, each optionally tagged with an
`assigned_program_id` — the existing program that already realizes it. Those per-island
directions + their program assignments are auto-recorded as each island's brief
(`structured_json`), and the SAMPLER reads them (see the islands note below) so islands
diverge in BOTH their prompt direction AND the exemplar code shown — not text alone. You
don't hand-author briefs. Your meta levers: `evo.meta_model` / `evo.meta_reasoning_effort`
(default `azure-gpt-5.5` medium; to escalate set `meta_model: azure-gpt-5.4-pro` AND
`meta_reasoning_effort: high` — two knobs, NOT a `model@effort` suffix; pro rejects `low`);
`evo.meta_code_preview_chars` (default 1200 — shrink if meta cost climbs); or
`evo.auto_meta:false` (suppresses the WHOLE round — global + per-island; islands keep their
last brief). Its cost folds automatically; budget-gated and wrapped so a meta failure never
aborts a window.

## Is a model never being picked? (the framework-audit check)

This is your flagship framework-flaw check, and it is **independent of stagnation** — do
it on your cadenced control-return even on a healthy, rising run. Watch the surfaced
`model_collapse` flag and `llm_bandit_counts`: if one arm's `submitted`/`completed` count
is stuck near zero while the others climb, decide WHY:
- **Locked out (a reward/selection flaw):** the arm has a near-zero count BUT, on the few
  times it ran, shows positive `reward_used` / `improvement_over_parent` (read a program's
  metadata via `archive_query` `include_metadata`). A few early bad draws drove its
  posterior down and the bandit stopped sampling it — the model isn't bad, the selection
  is starving it. **Recover with a CONFIG FLIP first, not a rewrite:** raise `epsilon`, or
  set `evo.force_explore:true` (optionally with `evo.llm_subset:["<that arm>"]`) for a
  window; lower `cost_aware_coef` if a pricier arm is starved on cost. Only if flips don't
  recover it do you rewrite `select_llm.py`.
- **Truly bad:** the arm ran ENOUGH and genuinely underperformed (low reward, high
  per-slot `evaluation_failure_rate`). Leave it starved — that's the bandit working.

The reward floor (`reward_validity_floor`) and the rejected-slot cost feed exist to make
lock-out less likely — but watch for it anyway. "Is it the model, or our framework?" is
the canonical judgment only the framework-audit role makes. `model_collapse` is surfaced
for you to act on; the framework never auto-corrects it in steady-state.

## Deep research (the DR check on control-return)

DR is web-grounded *discovery* (find SOTA), not *instantiation* (write the code). It is
your decision at a control-return, on the same tapering rhythm as the framework-audit
check.

**When.** When the search is stuck and the gap looks *algorithmic* (a technique the search
won't invent) — normally after a meta round and at least one cheaper move haven't moved
the best score. You DECIDE by reading the logs/history yourself (there is no automated
similarity helper — DR returns a text idea, the archive holds code, so only you judge
whether the idea already exists). Examine `journal.read_calls`, `archive_query`
`top_n`/`recent_failures`, and the directions already in `evo.meta_directions`. Always
pass BOTH `results_dir` AND `budget_usd` (M5): `results_dir` makes the call self-log (query +
brief) to `journal/calls/` and fold cost into the ledger; `budget_usd` arms the pre-flight that
SKIPS the spend when the remaining budget can't cover `dr_estimated_cost_usd` (~$5). Passing
`results_dir` alone does NOT bound DR by the budget — without `budget_usd` there is no
pre-flight and DR (the single most expensive action) can overshoot the cap.

**How to write the DR query (you write this).** Ask for the *general SOTA techniques for
the task* — or for a well-defined sub-problem — in the model's OWN words with a citation
(author/year/arXiv id). Do NOT ask for "the exact algorithm from [named paper]" or a
verbatim snippet: that shape reads as "reproduce copyrighted text" and Azure's content
filter refuses it deterministically. Keep it concise — the problem, the constraints, what
you've tried, the sub-question. **Pre-flight self-check before every DR call:** re-read
your drafted query and confirm its GOAL is general SOTA for the task/sub-task, not
"reproduce a specific named paper"; if it asks to reproduce one paper's algorithm
verbatim, STOP and reshape it. A refused/failed DR call returns `refused:true` + a `reason`
(logged with its query intact, no crash) — a `content_filter` refusal almost always means
a reproduce-paper framing, so RESHAPE the query; never re-fire the same shape.

**A server-side terminal `failed` is NOT a content_filter** — it means the `o3-deep-research`
job itself failed, and the journal call's `reason`/`error_code` now carry the real cause (Azure
`error.code`/`message` + `incomplete_details.reason`, surfaced by `run_dr_call`). **The CONFIRMED
failure mode on this setup is `error.code='too_many_requests'`** (exact-payload replay,
2026-06-10): the DR deployment's TPM/RPM quota cannot sustain a full deep-research job — a single
job internally fires many large reasoning+search calls for 30–60 min, so a LIGHT probe
(`scripts/test_dr.py`) succeeds while a REAL job dies mid-research (observed at 8–50 min). The
remedy is Azure-side: RAISE the deployment quota; from your side, scope the query tighter and/or
lower `max_tool_calls`. Other terminal causes to read off `error_code`: a missing/blocked
`web_search_preview` tool on the resource, or a wrong deployment name / model-version. FIX IT ON
THE AZURE SIDE — re-firing the same heavy job won't help. **Cost-on-failure reality:** a
submitted-then-failed DR call DID run web searches + compute server-side, so Azure BILLS it even
though `usage` comes back empty; the framework now floors such a call's logged cost at
`search_surcharge_usd` (≥$0.30) and folds it into the ledger/budget. Treat a failed DR as real
spend, NOT free — do not loop-retry it.

**Triage the returned brief — per technique, deliberately:**
- **Novel** (no archived program or prior direction resembles it) → GROUND it (the
  grounding run below), then give it **its own island** via `spawn_island.py` so it isn't
  out-competed before it matures.
- **History-similar** → combine it into the closest existing program with the grounding
  run (`fix_retry_budget:1`), TARGETING that program via `evo.grounding_parent_id:"<id>"`
  (H9 — pins the parent + its island; without it the grounding mutation lands on an
  arbitrary sigmoid-sampled parent). Use `evo.grounding_island_idx` to pin only the island.
- **Otherwise → ignore it.** Don't dilute the search.

**The grounding run.** Author/override a small `run.json` with `llm_models:
["azure-gpt-5.4-pro@high"]` (pinned — the one time pro is in the mutation pool), a single
weighted direction `evo.meta_directions:[{text:"<technique + reference + steps>",
weight:1}]`, `evo.mutation_web_search:true`, AND `evo.fix_web_search:true` (L85 — web search
on the MUTATION is `mutation_web_search`; the immediate-fix retries are gated SEPARATELY by
`fix_web_search`, default false, so without it up to 3 of 4 attempts run searchless),
`fix_retry_budget:3` (novel) or `1` (similar), and a short window. Pro reads the reference and
implements it. Never run pro with web search on a direction with NO solid reference — that's
the only sanctioned pro+websearch use. AFTER the grounding window REVERT these grounding-only
knobs (`llm_models`, `mutation_web_search:false`, `fix_web_search:false`, clear the pinned
direction) so pro + web search never persist into normal evolution; the grounded program is
already in the shared archive (in-place, shared db — there is no separate-db fold-back).

`spawn_island.py` (stdin `{db_path, db_config, embedding_model, program_id}`) seeds a NEW
island with a copy of the grounded program as its root. It honors `db_config.max_islands`:
at the cap it retires the worst island non-destructively (rows preserved for lineage) and
reuses the index; island 0 and the global-best island are protected. `max_islands:0`
(default) = unbounded.

## The framework-audit rewrite cycle

When you decide to rewrite a mutable strategy file, run this cycle — it is what stops a bad
rewrite from poisoning the run. Helpers: `harness/strategy_store.py`,
`harness/validate_strategy.py`, `harness/rollback_decision.py`.

1. **Check history** — read `strategy_history/index.json`; don't re-deploy a hash already
   `rejected` (both `deploy` and `deploy_bundle` refuse it unless you pass `force=True`).
2. **Generate** the candidate file(s) — same entry point + output keys as the current file
   (the docstring is the contract). Write to `strategy_history/candidate_<target>.py`,
   never directly to `scripts/`.
3. **Validate** — `python orchestrator/harness/validate_strategy.py
   strategy_history/candidate_<target>.py <target>.py`. (Validation smokes ALL of a
   target's modes — e.g. `select_llm`'s select + weights + update — so a rewrite that
   breaks the bandit-counts snapshot is caught before deploy.) Mechanical error → fix,
   retry ≤2; structural → abandon.
4. **Snapshot + deploy.** Pass `results_dir=` so `deploy` first calls `snapshot_state`,
   which snapshots the framework files AND the run state (archive DB + bandit + ledger) so
   the rewrite is recoverable (snapshot only when no window subprocess is live). Single
   file: `strategy_store.deploy(candidate, target, reason, window_index, prior_J,
   concern=, results_dir=)`. A whole concern: `validate_bundle` then
   `deploy_bundle([...], reason, window_index, prior_J, concern=, results_dir=)`. The
   harness stamps the full `strategy_fingerprint` into every window; log the rewrite with
   `journal.append_intervention(...)`.
5. **Measure, STAYING AWAKE.** Run exactly ONE measure window with tracing on so its step
   logs exist: `run_window.py --config <run>/run.json --windows 1 --trace-steps`. Read its
   `steps.jsonl` — do not go to wait-mode yet. (If the effect needs more than one window,
   mark it to check next round — rare.)
6. **Accept or revert.** Call `rollback_decision.decide(prior_window_diag,
   measure_window_diag)` (pass `measure_crashed=true` if the measure subprocess crashed /
   exited non-zero / produced unparseable output). It flags a regression if the rewrite
   collapsed correctness, collapsed diversity, regressed score while the prior window was
   progressing, or collapsed model selection (counts-share) — and it **FAILS CLOSED**: a
   measure window with no usable data (crash / empty / NaN) is assumed worst-case and
   reverted. If `regressed`: `restore_state(results_dir, snap_id)` — a FULL rewind of code
   + archive DB + bandit to the snapshot, **except the cost ledger, which is never rewound
   (spend stays counted; a revert can't be used to exceed the budget)** — then
   `record_outcome(new_hash, J, accepted=False, decision=…, measure_diagnostics=…)` (or
   the bundle variants). Else accept with the same call. After execution, only return to
   wait-mode once you have a satisfactory version; if it broke something, revert to the
   snapshot and redo with the new information. (The one place a collapse signal triggers an
   automatic action is judging THIS just-deployed rewrite's measure window — never
   steady-state, where `model_collapse` is surfaced for your judgment.)

The archive is NEVER reset across strategy changes.

## Failure handling: truthful recording + repair mode

Two repair layers run *inside* the window before you see a failure: (1) `mutate.py` retries
a broken APPLY (patch doesn't apply), bounded, error fed back; if those retries are
exhausted, NO candidate was produced — the slot is recorded as a TRUE failed attempt (the
model's cost charged to the arm, no reward, nothing archived; never a parent-copy
duplicate), surfaced via `apply_exhausted_count`. (2) the immediate-fix loop repairs an
EVAL failure in-place by re-prompting with the error, up to `evo.fix_retry_budget` times
(default 1). So `evaluation_failure_rate` is the post-repair rate.

**Repair mode** turns ON when `errored_fraction ≥ repair_trigger_fraction` (default 0.20,
with tombstoned programs EXCLUDED so the mode RELEASES once dead programs are removed). A
repair generation picks an errored program, uses NO inspirations, and prompts the model
with that program's own failure info. If the repair FAILS, no new child is added; the
truncated error is appended to the errored parent's own record and its repair count goes
up. After it fails repair `repair_attempt_cap` times (default 2) the parent is
TOMBSTONED — a non-destructive removal from the sampling pool (its row + island_idx +
lineage are preserved, it just stops being selectable, and it's reclaimed first when an
island is over capacity). `repair_escalation_model` (off by default) routes the last
attempt before removal to a stronger model. The single combined failure-rate is enough to
read at a glance *because* each trial's specific failure detail is logged and fed verbatim
into the fix prompt; open a failing slot's record for the failure kind.

Escalate to `subagents/debug-agent.md` only when the SAME failure signature recurs across two
DIFFERENT parents in a window (each having exhausted its in-loop repair budget — L91, matching
the subagent's own precondition); write its report to `strategy_history/debug_<w>.md`, act on its one
recommendation, forget the detail. For periodic structural reads, spawn
`subagents/archive-analyst.md`.

## Termination + end of run

**Stop when:** the budget is exhausted; the user says stop; OR **five consecutive
control-returns were each STAGNANT and each had an intervention** (a framework rewrite, a DR,
OR a deliberate config-lever flip — the AUTOMATIC per-window meta round does NOT count, it
isn't your action) that still could not break the stagnation → the search is stuck despite
intervening, so stop. This is now **harness-computed and auto-finalized** (parity with budget): the harness
reads your canonical `control_return` rows (`stagnation_flag`+`intervened`) via
`journal.termination_streak`, and when the streak reaches `cadence.termination_streak`
(default 5) the next `--until-decision` call returns
`return_reason="stagnation_intervention_exhausted"` and finalizes — so two agents can't
disagree on the count. Stagnation ALONE never terminates (only stagnation your interventions
couldn't break); a stagnation-break OR a no-intervention return resets the streak. There is
no longer a "≥1 DR of 5" requirement — a DR simply counts as an intervention. The automatic
per-window meta round does NOT count as an intervention. A pre-assumed/reference score in
the docs does NOT end the run early.

**Per-run NOTES.md.** `orchestrator/NOTES.md` is a transient SINGLE-RUN scratchpad, not a
persistent ledger. CLEAR it at the START of a run (the previous run's note is assumed already
read and handled by the user); during and at the end of the run, write THIS run's handoff there
(what happened + anything the user must fix before the next run). Persistent teaching lives in
this SKILL.md + CLAUDE.md; past runs' notes live in git history.

**End of run:** review the whole history and write an **ending document** — the run's
outcome + a "Future fixes for the user before the next run" section (foundation/outer-loop
changes you could not make mid-run). Seed it from `journal.build_run_summary(results_dir)`,
flip the run's status with the `finalize_run` journal view (the harness auto-finalizes only
on the budget-exhausted terminal return), then **archive** the run's history/logs/artifacts
with the `archive_run` view into `orchestrator/run_archive/<run_id>__<finished_at>/`. **Do
NOT read prior runs' archives while running a new job** — they exist only for the user's
later reference.

## The run journal (your long-term memory, read at any granularity)

`scripts`/grep or `harness/journal.py`:
- `journal/run.json` — run summary (status, windows, best, total_cost) — crash-durable.
- `journal/windows.jsonl` — per-window diagnostics (the trajectory).
- `journal/interventions.jsonl` — every rewrite/decision + your work score + outcome.
- `journal/calls.jsonl` — compact POINTER index of every external LLM call (meta / dr):
  `{kind, timestamp, file, cost, summary}`. Full prompt + raw output live in
  `journal/calls/<kind>_<ts>_<rand>.json`.
- `journal/steps.jsonl` — the per-step oversight trace, present ONLY under tracing (warmup
  and the measure window); cleaned up after warmup.
- `journal/islands/island_<i>.jsonl` — per-island trajectory.
- `strategy_history/` — per-strategy-version snapshots + `index.json`.

Read efficiently — start with the compact views (`journal.read_windows`,
`journal.read_calls`, `journal.read_steps`); only open a full call detail when you need its
prompt. **Logging is automatic — don't hand-roll it.** When you call `meta_summarize.py` /
`deep_research.py`, pass `results_dir`; they self-log AND fold cost into the ledger, so do
NOT also `append_intervention` with the same cost (double-count). `append_intervention` is
for your rewrites/decisions + the work score, never an LLM call's cost.

## The subroutines

JSON on stdin → JSON on stdout (also importable `main(payload)->dict`).

| Script | Purpose | Mutable | LLM |
|---|---|---|---|
| `evaluate.py` | run candidate → score+artifacts | No (foundation) | No |
| `archive_record.py` | persist a candidate | No (foundation) | No |
| `archive_query.py` | read archive (id/score/lineage/failures/summary; `include_metadata`) | No (foundation) | No |
| `repair_record.py` | record a failed repair (append-to-parent / tombstone) | No (foundation) | No |
| `diagnostics.py` | assemble window diagnostics (your sensor) | No (foundation) | No |
| `_common.py` | JSON contract | No (foundation) | No |
| `sample_parent.py` | parent + inspirations + `needs_fix` (+ `select:"errored"` repair mode) | **Yes** | No |
| `novelty_check.py` | near-duplicate decision + incumbent score (caller keeps the better) | **Yes** | No |
| `select_llm.py` | pick model; learn (bandit) | **Yes** | No |
| `compute_reward.py` | reward signal for selection | **Yes** | No |
| `record_policy.py` | derived signals → metadata | **Yes** | No |
| `stagnation_detector.py` | the low-window trigger | **Yes** | No |
| `island_policy.py` | fork/migrate/retire decision | **Yes** | No |
| `cadence_policy.py` | WHEN control returns (early-phase per-window floor, then the work-score taper; not the budget) | **Yes** | No |
| `island_brief.py` | record a per-island direction (auto-called by the meta round) | **Yes** | No |
| `spawn_island.py` | seed a new island from a grounded program | No (foundation) | No |
| `construct_mutation_prompt.py` | build the mutation/fix prompt | **Yes** | No |
| `mutate.py` | call Azure (bg+poll), parse, apply, retry | Body no, prompt yes | **Yes (Azure)** |
| `meta_summarize.py` | the automatic per-window meta round (per-island directions) | prompt yes | **Yes (Azure)** |
| `deep_research.py` | deep-research model (web-grounded directions) | No (paid service) | **Yes (Azure DR)** |
| `_azure.py` | shared Azure background-poll transport | No (foundation) | — |

**Background-poll resilience (transport, FOUNDATION).** Every Azure bg call (`mutate`/`meta`/DR)
is bounded at TWO levels: a SHORT per-HTTP-request cap (`SHINKA_BG_HTTP_TIMEOUT_SEC`, 60s) wraps
each submit/status-GET and RETRIES a hung request, and the LONG total-job wall
(`SHINKA_BG_POLL_TIMEOUT_SEC` / `AZURE_DR_TIMEOUT_SEC`, 3600s) is a true monotonic deadline. So a
wedged socket on one status GET can no longer ride the whole 1h wall (the prior ~1–2h hang); a
genuinely-stuck job still bounds at the wall, then degrades cleanly (mutate → `applied:false`; DR →
a degraded brief whose billed cost is captured). Don't rewrite the transport in a strategy rewrite.

## The run config (you author this)

```json
{ "results_dir": "<run dir>", "run_id": "<id>", "budget_usd": 50,
  "task": {"eval_program_path": "...evaluate.py", "init_program_path": "...initial.py",
           "task_sys_msg": "<precise goal>", "require_sys_msg": true,
           "language": "python", "eval_time": "00:35:00"},
  // M49: eval_time is the harness hard-kill — it MUST exceed the task evaluator's internal
  // wallclock budget, or slow candidates are SIGKILLed before the evaluator's graceful
  // early-abort can write a score (a result-less death). The cnot evaluator's default budget
  // is 30 min, so eval_time >= 00:35:00; set per task.
  "db_config": {"num_islands": 4, "archive_size": 40, "parent_selection_lambda": 10.0,
                "migration_interval": 10, "enable_dynamic_islands": false,
                "max_islands": 0, "island_evict_strategy": "worst_best_fitness"},
  "evo": {"window_size": 10, "patch_types": ["diff","full","cross"], "patch_type_probs": [0.6,0.3,0.1],
          "llm_models": ["azure-gpt-5.4-mini","azure-gpt-5.5"], "llm_dynamic_selection_kwargs": {"cost_aware_coef": 0.25, "epsilon": 0.2},
          "reasoning_effort": "medium", "max_patch_attempts": 3, "fix_retry_budget": 1, "reward_mode": "absolute",
          "auto_meta": true, "meta_model": "azure-gpt-5.5", "meta_reasoning_effort": "medium",
          "repair_trigger_fraction": 0.20, "repair_attempt_cap": 2, "repair_escalation_model": null,
          "embedding_model": "azure-text-embedding-3-small", "enable_novelty": true,
          "code_embed_sim_threshold": 0.99, "stagnation_abs_floor": 0.001,
          "stagnation_rel_frac": 0.05, "consecutive_required": 2},
  "cadence": {"mode": "until_decision", "base_low": 5, "low_threshold": 1, "early_phase_windows": 5},
  "window_state": {"window_index": 0, "prior_low_streak": 0} }
```

`llm_models` set → the bandit picks per candidate; `enable_novelty` → the embedding gate.
An `llm_models` entry may be `"model@effort"` (e.g. `"azure-gpt-5.4-pro@high"`); the bandit
learns each (model, effort) arm separately. Only encode valid combos (pro rejects `low`).
**Use per-arm effort deliberately to manage the reliability-vs-wall-time tradeoff** (thinking
helps hard tasks but a cheap arm at `medium` can wedge a call 50–90 min — Boot step 3); it is a
knob you OWN at boot and re-tune at any control-return, not a default to leave alone.
**Pro policy:** keep `azure-gpt-5.4-pro` OUT of the normal mutation pool by default — it's
reserved for the meta round (when you escalate `meta_model`) and the DR grounding run; a
future outer-loop may add `pro@high` to the pool if a task warrants it.

`cadence.max_windows_per_call` is an OPTIONAL explicit ceiling — unset by default, so the
work-score taper is uncapped (bounded by budget / termination / stagnation).

### Config levers — flip a knob before you rewrite code

Many decisions that *look* like a code rewrite are already `evo.*` knobs. Prefer a knob
(cheap, instant, no protocol) over rewriting a policy.

| Knob | Default | What it does | When to flip |
|---|---|---|---|
| `auto_meta` | true | run the automatic per-window meta round | false to pause meta entirely (suppresses global + per-island briefs) |
| `meta_model` / `meta_reasoning_effort` | `azure-gpt-5.5` / medium | the model for the automatic meta round | raise to `azure-gpt-5.4-pro` / high when directions are worth the high cost |
| `reasoning_effort` (per arm via `model@effort`) | medium (shipped) | the proposer/fix reasoning budget per call — the biggest driver of per-call wall-time + verbosity | RAISE (high) for reliability on hard/algorithmic tasks; LOWER (low, where the model allows — pro rejects low) when a cheap arm emits 10–35k reasoning tokens per ~3k-token patch and wedges calls 50–90 min. You OWN this — not a default to leave alone (Boot step 3) |
| `early_phase_windows` | 5 (cadence) | Stage-1 floor: control returns EVERY window for the first K windows regardless of work score (frequent early inspection) | lower / 0 to taper sooner; raise to inspect every window longer. 0 restores the old immediate work-score taper |
| `repair_trigger_fraction` | 0.20 | errored-fraction at which repair mode turns on | raise if repairs churn; lower to repair sooner |
| `repair_attempt_cap` | 2 | failed repairs before a parent is tombstoned | raise to give a hard failure more tries |
| `repair_escalation_model` | null | stronger model on the last repair before removal | set to e.g. `azure-gpt-5.4-pro@high` for a stubborn class |
| `fix_retry_budget` | 1 | immediate eval-failure repairs per slot | raise for a hard task; the grounding run uses 3 |
| `mutation_web_search` / `fix_web_search` | false | web search on the mutation / fix calls | ONLY on a grounding run nailing a DR reference |
| `cost_aware_coef` | 0.25 shipped (engine default 0.0 when `llm_dynamic_selection_kwargs` is unset) | bandit reward-vs-cheapness blend | raise→0.7 if cheapness should dominate; lower→0 if a pricier arm is the only one improving and is being starved |
| `epsilon` | 0.2 | bandit exploration floor | an arm's share decaying toward 0 while it still occasionally improves → raise to 0.4–0.6 |
| `code_embed_sim_threshold` | 0.99 | near-duplicate cosine gate, over the basis set by `novelty_embed_mode`; a near-dup is now EVALUATED then the BETTER of the pair is kept (worse dropped / tombstoned) — H5 | under the default `diff` basis the 0.99 gate rarely false-rejects; under legacy `code` basis genuine large-program edits cluster ~0.994 and are mis-flagged → switch to `diff` (preferred) or RAISE the threshold (never lower under `code`); watch `novelty_rejected_cost` |
| `novelty_embed_mode` | diff | WHAT the gate embeds: `diff` = parent→candidate unified diff (genuine edits separate to low cosine; the per-island pool can GROW) vs `code` = legacy whole-program embedding (collapses each island to a single-survivor greedy chain on a large program — H2) | keep `diff`; use `code` only to reproduce legacy behavior or on a resumed archive whose stored embeddings are whole-program |
| `novelty_tie_epsilon` | 0.0 | keep-the-better tie margin (H2): an equal-scoring DISTINCT near-dup within epsilon is KEPT and the incumbent tombstoned (`>=`), so the lineage traverses score plateaus instead of dropping every tie after a full eval | raise slightly to keep more near-ties for plateau exploration |
| `stagnation_abs_floor`/`rel_frac` | 0.001 / 0.05 | the "low window" bar | recalibrate to the task's natural per-window climb |
| `validity_floor` | none (inert) | floors VALID parents' selection score (`sample_parent`) | many correct programs pinned at 0 and selection can't separate them |
| `reward_validity_floor` | 0.001 | floors a correct candidate's bandit reward so correct-but-worse beats *failed* | an arm with high eval-success is starved because its children rarely beat the parent |
| `reward_on_reject` | cost_only | a novelty-rejected slot bills the arm's COST only (neutral) vs `penalize` | a duplicate-prone arm should be penalized for waste |
| `force_explore` / `llm_subset` | false / null | ignore the collapsed bandit (uniform) / restrict to a subset | re-open a starved/locked-out arm (the framework-audit check) |
| `use_text_feedback` | true | feed the evaluator's failure reason into the fix/repair/meta prompts | false on a spoil-risk task — a COMPLETE suppression (gates the fix, sampled-ancestor, AND meta error-text channels — H9) |
| `island_policy_driven` | false | drive spawn/migrate via mutable `island_policy.py` at window boundaries | repairing population structure. H10: the policy reads its OWN gates — set `policy_migrate_enabled` / `policy_migrate_interval` / `policy_spawn_enabled` / `policy_spawn_stagnation` and keep the db_config auto-triggers OFF (`enable_dynamic_islands:false`, `migration_rate:0`) to avoid double-execution; the executor result + any crash are surfaced on stderr (M17). `retire` has no executor yet (M16). |
| `brief_compose_mode` | replace | how a per-island brief combines with the global `meta_directions`: `replace` (default) = the brief STANDS IN for the global direction; `augment` = the brief LAYERS on top | NOTE (H8): under the default `replace`, once an island has a brief (window 1+) the global `meta_directions` is computed then DISCARDED per-gen — set `augment` to keep BOTH. The DR-grounding recipe relies on `replace` so its pinned direction steers the run (target it via the H9 parent/island pin, not the global direction). |
| `island_selection_strategy` / `enforce_island_separation` (db_config) | uniform / true | island selection pressure (`uniform`=`equal` are aliases; `proportional`=by population count; `weighted`=by island best-fitness) / same-island vs cross-island inspirations | concentrate sampling on the LARGEST island → `proportional`; on the best-fitness island → `weighted`. ⚠️ BOTH REINFORCE the leader — NEITHER rescues a starved/small island (no live strategy does; small-island rescue needs `archive_floor_per_island` ↑ or a `_select_island` rewrite). cross-pollination → separation `false`. (These are `db_config` knobs, not `evo.*`.) |
| `archive_floor_per_island` | 3 (db_config) | per-island archive floor — a dominant island can't evict another island below this many members (H2) | islands collapsing to one lineage → raise; a single-family task → 0 (pure global fitness) |
| `migration_rate` / `migration_interval` | 0 / 10 (db_config) | island elite migration; **0 = OFF by default**. Execution IS wired (every archive add runs deferred migration), so flipping it on is a live config change — NOT a code rewrite | turn it on at BOOT (run.json `db_config`) OR mid-run (edit `db_config` at a control-return; the next relaunched cluster reads it — schema-safe) → `migration_rate` ≈ 0.05 |
| `termination_streak` | 5 (cadence) | consecutive stagnant+intervened control-returns that auto-terminate the run | raise to give a stuck search more intervention attempts before stopping |
| `meta_failures_first_frac` | 0.5 | how much of the meta context is recent failures vs top performers | failure-dominated window where the failure_note comes back vague → 0.75 |
| `extra_guidance` | none | free text appended to the next window's mutation system prompt | nudge the search without a code rewrite |

**Islands differentiate BY DEFAULT — in direction AND code (H1/H2).** The automatic
per-window meta round writes each live island a STRUCTURED brief (1–3 directions, each
optionally tagged with the program that realizes it). `sample_parent` then samples ONE of
that island's directions per generation and pulls the programs ASSIGNED to it as the
inspirations (else just the direction text) — so the brief changes WHICH code the island
sees, not only the prompt text; `brief_compose_mode` (replace) makes that direction stand
in for the global one. Genetic separation is protected by `archive_floor_per_island`
(default 3 — a dominant island can't evict a young one below its floor) and, if enabled,
`migration_rate`. Hand-authoring a brief via `island_brief.py` is the override, not the
default path. (A cap-hit incomplete model response always returns its billed partial text,
and an unpriced billed response logs a warning and is billed $0 — neither is a tunable knob.)

The rollback basket has tuning knobs passed to `rollback_decision.decide()` (NOT `evo.*`):
`abs_eval_floor` (0.05), `bandit_collapse_count_frac` (0.85) / `bandit_collapse_min_pulls`
(8) for the primary counts-share collapse arm (the weights-fraction arm is legacy /
near-unreachable because a single arm's weight caps at `1−epsilon`), `measure_crashed`
(fail-closed), and `min_eval_success`/`eval_drop`/`nov_drop`/`score_ratio`. Two things the
gate now does on its own: (H4) it AUTO-fails-closed when the measure window evaluated ZERO
candidates — every slot apply-exhausted, `apply_failure_rate` 1.0 (e.g. a patch-format-breaking
prompt rewrite) — so you no longer hand-pass `measure_crashed` for that case; and (H5) the
counts-share arm reads THIS window's submitted counts (`llm_bandit_window_counts`), so a
mid-run selection collapse is actually detectable (the run-cumulative total could never move
the share mid-run).

### Scalability — deferred

Per-generation novelty is an O(N) cosine over CACHED embeddings of a size-capped archive
with bounded per-island membership, so it's cheap and intentionally left as-is; evaluation
is serial. Revisit only at a full inspection; the end-of-run document lists scalability as
a standing future-fix candidate (trigger: `archive_size` raised enough to bottleneck
novelty or serial eval).

## What never to do

- Never modify a `scripts/` file directly without the rewrite cycle.
- Never edit FOUNDATION files (schema, contract, evaluate, archive_record/query,
  diagnostics, repair_record, journal, harness, deep_research, the task's evaluate/init).
  Defer foundation ideas to the ending document.
- Never run an inner-loop mutation in your own context. Always call `mutate.py`.
- Never make two rewrites in one control-return. Never call deep research twice per
  stagnation cluster. Never let subagent output linger in your context.
- Never read a prior run's archive while running a new job.
- Never stop while a termination criterion is unmet.

## When in doubt

Do less. Your value is the rare code change the inner loop's hand-coded policies cannot
make, and the rare DR call that brings in knowledge the search can't invent. Every
intervention you skip is one less chance to break something.

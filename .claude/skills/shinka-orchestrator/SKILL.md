---
name: shinka-orchestrator
description: Use this skill when running an LLM-driven evolutionary search on a code optimization problem in this repo. You wear two hats — the ORCHESTRATOR (the operational, in-the-flow jobs the run can't proceed without) and the OUTER-LOOP / FRAMEWORK-AUDIT role (judging whether the deterministic framework code itself is flawed and rewriting it). Invoke at problem onset, during warmup, when the inner loop returns control, when a budget is exhausted, or to resume a run.
---

# Shinka Orchestrator

You drive an evolutionary search whose inner loop is fast, deterministic, and runs
WITHOUT you. You set it up, oversee it, intervene when it needs you, and stop it when
it's done. You are not in the path of every mutation — you are woken when a window-
cluster returns control.

## What you do (the two hats)

You wear both hats at the **same moment** — each time a window-cluster returns control to you.

**ORCHESTRATOR — operational, in the critical path.** The jobs the run cannot proceed correctly
without. At boot: read the task's initial code + evaluator, infer the goal, and author the
system-message problem statement (the goal, the hard constraints, the *shape* of the score), plus
an abstract runtime-efficiency caution (no specific numbers). Be careful the evaluator does not
leak the answer — held-out numbers live under its `private` metrics; if a value it must SHOW would
reveal the trick rather than the real objective, stop and ask the user. When you decide a
**discovery round** is warranted: hand-author the discovery prompt, triage each returned direction,
ground EACH triaged direction (up to 3), seed/ground islands, fold results in. (Terminology: a
**discovery round** == a **DR round** == one discovery pass via EXACTLY ONE OF **R1** = Azure deep
research (`deep_research.py`) OR **R2** = the `subagents/archive-analyst.md` multi-agent read.
Simple naming rule: if a term says "Azure" it means R1 specifically; otherwise "discovery round /
DR round" is the umbrella over both.)

**FRAMEWORK-AUDIT — improvement, NOT in the critical path.** Read the logs and history to judge
whether the deterministic **framework code itself** is flawed, and rewrite the mutable strategy or
prompt code when it is. One example: a model that is never being picked — is it *truly bad* (it ran
enough and genuinely underperformed) or *locked out by a reward/selection flaw* (near-zero
selection count but a positive reward the few times it ran)? The run continues without this role,
but it improves the framework over time.

Both hats are checked at every control-return, on the **same rhythm — which is enforced by code,
not by you.** The cluster returns control either on stagnation or after its set number of windows;
that number is small early (the framework is least proven, so you check often) and grows as the run
proves stable (see the taper, below).

## Two rules you never break

1. **The inner loop's LLM calls go to Azure, never to you.** Every per-window mutation, fix,
   repair, and meta call is made by a `scripts/` subroutine calling Azure in background-poll mode.
   Never "simulate" the per-window loop in your own context — that pays an agent turn (~100×) for a
   stateless API call and destroys the cost economics. Your tokens go to control-return reasoning,
   hand-authoring the discovery and grounding prompts, and the rare carve-outs (R2 discovery; the
   program-rescue grounding) described in "Running a discovery round and grounding it".
2. **Do not stop until a termination criterion is met.** A run is long and consecutive; the
   healthiest run is many control-returns read with no NEED for intervention. Keep launching the
   next cluster, and keep the heartbeat armed until the run terminates. Idleness is not done-ness —
   only the termination checklist is (see "When the run ends").

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
- **Per-call cost cap (~$10).** Every external LLM call (mutation / meta / DR / fix / grounding)
  carries a per-call max-output-token cap sized so one call cannot exceed ~$10 (pro at its
  50k cap ≈ $9; others incl. `gpt-5.5` at 200k ≈ $6; DR at its 200k cap ≈ $8). This is a
  deliberate runaway guard — do not remove or shrink it.
- **No unmonitored LLM calls.** Every call goes through a counted path whose cost lands in
  the ledger; the eval subprocess runs the task's `evaluate.py` (no LLM).
- **Worktree shinka.** The harness asserts `shinka` resolves to THIS repo at startup
  (loud fail otherwise) — you never silently run a different checkout.

## The run loop, end to end

This is the single source of truth; the rest of this doc expands each step.

1. **WARMUP — you are fully awake, inspecting each step.** Before the real run, oversee a
   throwaway warmup (its own db + journal under `<results_dir>/warmup/`) with per-step tracing ON.
   You read the `steps.jsonl` trace — which parent the sampler chose and why, the assembled prompt
   summary, the code/summary the model returned (and whether the patch applied), the eval result
   and its failure type, and what the framework decided next. The moment a step looks wrong you STOP
   and CORRECT the implicated **mutable** policy file (warmup is PRE-run, so a plain edit-and-rerun
   is fine — no snapshot/measure/revert ceremony, that's only for *mid-run* rewrites; FOUNDATION
   stays off-limits, never the evaluator), then RESTART warmup (it auto-resets the workspace) until
   the trace looks clean. Once a warmup looks completely normal you simply KEEP it
   (`--accept-warmup`): it folds that warmed population + its spend into the real run, which then
   continues from there. There is no discard step — a bad attempt is just rerun. Warmup's narrow
   job: confirm the inner loop is mechanically sound (sampler → prompt → eval → novelty → record all
   wired correctly) on a FRESH archive. It cannot reproduce a flaw that only emerges with a
   populated archive — those surface on the real run's per-window diagnostics, which the orchestrator
   + framework-audit roles handle. (See "Boot and warmup" below for the launch + the common
   flaw-signals.)
2. **ACTUAL RUN — event-driven; you are woken, you do not poll.** You launch a self-caffeinated
   window-cluster (`run_window.py --until-decision`, background-launched). The harness runs windows
   autonomously and **returns control by exiting** at the cluster boundary; that clean exit
   re-invokes you — that IS the "wake". (Because a backgrounded task can be reclaimed if the session
   sits idle too long, you ALSO keep a short heartbeat that re-invokes you every few minutes; the
   clean exit still wakes you as designed when the cluster finishes.) Initially control returns after
   every window; as your recent work tapers it returns less often. There is **no max-window cap** — a
   cluster is bounded only by the budget hard-stop, a termination criterion, and stagnation (which
   always returns control immediately). Recover from any kill with `--resume`, and while the cluster
   is backgrounded hold the self-wake **heartbeat** (see "Launch the inner loop and get woken"). The
   user keeps the laptop lid open and on AC so the host doesn't hardware-sleep under it.
3. **EACH WINDOW ENDS WITH AN AUTOMATIC META ROUND — run by the harness, not by you.**
   Deterministic code composes the meta prompt from the current archive + the live island list and
   calls the external LLM (default `azure-gpt-5.5` at medium effort). In one shot it returns, FOR
   EACH live island, its own differentiated direction list — what already works there (tagged with
   the program that realizes it) plus what looks promising — and a common eval-failure caution.
   Every direction is assigned to exactly one island (none duplicated), so islands evolve in
   different directions BY DEFAULT; these are auto-recorded as per-island briefs. A brand-new island
   with no brief yet just gets a neutral placeholder ("no explicit direction yet — follow the rest of
   the prompt"), which is enough because the mutation prompt still carries its modes, inspirations,
   and the task message. Meta is NOT an orchestrator action and does NOT count as an intervention.
4. **WHEN CONTROL RETURNS you make two checks at the same time:** (a) the **framework-audit check**
   (rewrite a mutable strategy file if a flaw is found), and (b) the **discovery check** — if the
   archive looks short on fruitful directions and the stall looks algorithmic, run a discovery round
   (R1 by default, R2 as the narrow fallback). Only act on a check when it is actually warranted; for
   a framework-code change, reason to a deeper level first to be sure the change is sound.
   **Grounding gate: a grounding (or any `spawn_island`) is only valid if a fresh, usable discovery
   from THIS control-return interval exists** — a discovery from a prior interval does not count, and
   `spawn_island` refuses to run without one. Do the discovery + grounding BEFORE you write this
   return's work-score line, then **record the work score** for what you just did — recorded AFTER
   acting, so the score you intend to record never influences what you choose to do.
5. **THE TAPER (code-enforced, two stages).** The cluster size is computed by the harness from your
   recorded work score — it is guaranteed by code, not by your discretion. STAGE 1 — the **early
   phase**: for the first `cadence.early_phase_windows` windows (default 5) control returns EVERY
   window regardless of work score, so you inspect each one while the framework is least proven.
   STAGE 2 — the **work-score taper**: past the early phase, low recent work lets the next cluster
   grow (base_low, then doubling: 5 → 10 → 20 → 40 …) with no ceiling. A HEAVY intervention records a
   high work score, which resets the low-streak to zero — so the very next cluster drops back to one
   window (you inspect after every window again), and the doubling ramp only restarts (from base_low,
   5) once low work resumes. The low-streak is counted FROM THE END OF THE EARLY PHASE, so the early
   per-window returns don't inflate the first steady cluster. The same cluster size is BOTH the
   framework-audit cadence AND the discovery-check cadence — one shared rhythm. If you forget to
   record a work score the taper has no signal and conservatively wakes you every window (and the
   harness prints a reminder). And no matter how long the cadence grows, keep the heartbeat armed —
   it is what prevents a backgrounded cluster from being silently reclaimed.

## The work score (record it after every control-return)

After you act on a control-return, append ONE canonical row to `interventions.jsonl` — it
drives BOTH the taper AND termination, so there is exactly one row per control-return:
`{type:"control_return", window_index, stagnation_flag, best_score, work_audit,
work_discovery, work_grounding, work_score, intervened}`:
- `work_audit` — framework-audit magnitude: a full core-strategy rewrite ≈ 3, a tiny
  param change ≈ 1, no change 0.
- `work_discovery` — DISCOVERY magnitude, set ONLY by a logged R1/R2 discovery stub THIS
  interval: 0 not-run, 1 run-but-nothing-new, 2 a combinable direction, 3 a novel direction
  worth a new island. A merely *brainstormed* technique (no R1/R2 stub) scores **0** — it is
  not discovery.
- `work_grounding` — GROUNDING magnitude: 0 none, 1 combined into an existing program (= path (ii)
  SIMILAR: `archive_record` `parent_id`=closest, NO spawn), 2 a new-island root (= path (i) NOVEL:
  `archive_record` `parent_id`=null THEN `spawn_island.py`). Settable ONLY when the grounded
  technique came from a discovery in THIS interval (grounding cannot be logged without the discovery
  it grounded).
- `work_score` — `work_audit + work_discovery + work_grounding` (the scalar the taper reads
  via `journal.recent_work_score`; `journal.work_low_streak` counts the recent low-work
  returns the escalation uses).
- `stagnation_flag` — copy this return's window-diagnostics value (the stall state NOW).
- `intervened` — `work_audit>0 or work_discovery>0` (did you actually act this return?).
  **`work_grounding` ALONE never flips `intervened`** — a grounding counts only WITH the
  discovery it grounded, so a grounding over a stale/absent discovery cannot pad the streak; the
  stagnation-breaking intervention is the DR the grounding rides (`work_discovery>0`), not the
  grounding itself.
  INCLUSIVE: a deliberate config-LEVER flip counts (log it as `work_audit` ≥ 1); the automatic
  per-window meta round does NOT (it isn't your action); a pure no-op read leaves it false.

Record it AFTER acting, never before — the row must describe what happened, not steer what
you do. The harness reads `stagnation_flag`+`intervened` across the last rows for the
termination check (below); a forgotten row simply can't advance termination (fail-safe).

## Launch the inner loop and get woken

```
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

`--until-decision` runs windows autonomously and returns control by **exiting** at the
cluster boundary (no turn of yours per window). Background-launch it so the run survives
your turn ending and re-invokes you on exit; recover any kill with `--resume`. A fresh
subprocess each call is how a deployed code rewrite takes effect — the next invocation
imports the new file. (`--windows 1` runs exactly one bounded window; `--windows 1
--trace-steps` is the **measure window** you run right after a framework-code change — exactly
one traced window so you can read its step logs and confirm the change works before continuing.
See the rewrite cycle.)

`run_window` self-caffeinates against host idle-sleep for its lifetime (auto-released on
exit) — on macOS via `PreventUserIdleSystemSleep`, on Windows via
`SetThreadExecutionState(ES_SYSTEM_REQUIRED)` (Linux is a no-op) — so a long cluster is not
reaped by host idle-sleep. There is no separate detached launcher — the background-launched
`--until-decision` IS the wake primitive; it returns by exiting and re-invokes you. (For
unattended runs the user keeps the laptop lid open and on AC — a clamshelled laptop
hardware-sleeps regardless, which self-caffeinate can't prevent.)

**Heartbeat — the survival leg self-caffeinate can't cover.** Self-caffeinate beats *host*
idle-sleep, but NOT the *sandbox* idle-reclaim of the backgrounded launcher→`run_window`→eval
process group: the agent's OWN long idle is what arms it (tens of minutes of session dormancy
→ the cluster is reaped mid-window with no exit and no wake — the "missed wake", where you
wait forever for a cluster that is already dead). With the default `window_size` a single
window can outlast that threshold, so a deploy-and-yield is exactly what dies. While a cluster
is backgrounded, therefore, do NOT yield into a long idle: arm a short self-wake **heartbeat**
— a backgrounded timer of a few minutes that exits and re-invokes you — and on each wake
confirm `run_window` is still alive and progressing by JOURNAL PROGRESS in this run's
`results_dir` ONLY — `journal/run.json` `updated_at` advancing + `windows.jsonl` gaining
lines; **never probe or kill by PID / `Get-Process` / `tasklist`** (see "Run identity,
stopping, recovery" below), then re-arm; keep the interval well under the
reclaim threshold (~5 min is safe). Stop re-arming only when `run_window`'s own clean-exit
notification arrives. The heartbeat does NOT detach `run_window` (it stays a harness-tracked
job, so its exit still wakes you) — it keeps the *session* active so the tracked job is not
reclaimed (the same effect a user's periodic ping has). `--resume` only RECOVERS a kill after
the fact; the heartbeat PREVENTS the missed-wake.

**Re-arm robustly — a single forgotten re-arm kills the run** (the observed failure mode: a run
held alive for hours died the moment a long reasoning stretch lapsed the timer). On every
heartbeat wake, RE-ARM THE NEXT TIMER FIRST — before the liveness check or any other work. Re-arm
UNCONDITIONALLY: even on a wake that found nothing new, and *especially* right before a long
reasoning/rewrite turn (arm the next timer immediately before you start thinking). Before ending
ANY turn while a cluster is backgrounded, run the self-check "is a live heartbeat pending?"
(alongside "did I record a work score?"). Stop re-arming ONLY on `run_window`'s own clean-exit
wake — a stagnation/taper return is followed by another launched cluster, which must be
heartbeated again.

**Run identity, stopping, recovery — never touch the OS process.** A run IS its
`results_dir`: `run_window` holds an exclusive OS lock on `<results_dir>/.run.lock` for its
whole lifetime (the kernel frees it on ANY death — clean, crash, or kill), and writes
`<results_dir>/.run_owner.json` for forensics. Therefore — **liveness** is journal progress
in THIS run's `results_dir` (above), NEVER `Get-Process`/`tasklist`/a PID probe (OS PIDs are
reused across worktrees, so a PID check or kill can land on ANOTHER session's `run_window`).
**To stop or pause a running cluster** (e.g. to interrupt for a framework rewrite), write
`<results_dir>/.stop` (optionally `{"target_run_id": "<id>"}`); it exits 0 at the next window
boundary — NEVER `Stop-Process`. **To recover a dead/wedged run**, relaunch `--resume`: it
re-acquires the lock and, if the original is somehow still alive, refuses to start instead of
double-writing — so a wrong "it's dead" guess is harmless, not a corrupted archive. Two runs
aimed at one `results_dir` (e.g. a copied config) likewise make the second refuse loudly.
Don't `cat`/edit `.run.lock` (it is OS-locked). This is what makes concurrent worktree
sessions independent — distinct configs ⇒ distinct `results_dir` ⇒ distinct lock ⇒ no
crosstalk, with nothing in your recipe reaching the global PID space.

Diagnostics shape (printed to stdout + appended to `journal/windows.jsonl`):

```
{ window_index, iters_completed, best_score_start, best_score_end, delta,
  J_score, threshold, strategy_fingerprint,
  novelty_acceptance_rate (null when no novelty events), novelty_rejected_cost,
  novelty_sim_histogram,
  evaluation_failure_rate, apply_exhausted_count, apply_failure_rate,
  timeout_count, wrong_answer_count, errored_fraction,
  model_collapse:{top_arm, top_share, n_arms_active, collapsed},
  repair_mode_on, repair_fail_count, repair_tombstoned_count,
  fix_rate, fix_success_rate, needs_fix_rate,
  llm_bandit_weights, llm_bandit_counts,
  island_health:[{id,best,diversity,diversity_kind,cosine_spread,member_count,stagnation_count,count}],
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

## Boot and warmup

Your first job on any task is to author the goal and config, then warm up the inner loop and watch
it run before the real run begins.

**1. Author `task_sys_msg`.** Read the task's `initial.<ext>` and `evaluate.py`, then write a
precise problem statement: the goal, the gate set / hard constraints, the score's shape, and an
abstract caution that each eval has a runtime budget so the code must stay efficient (NO specific
numbers — reinforced in-loop by the numeric runtime-budget caution when a candidate runs slow). The
harness REFUSES to start with a missing / empty / placeholder `task_sys_msg` (the starter ships the
sentinel `__UNSET_AUTHOR_AT_BOOT__`); `task.require_sys_msg:false` overrides for a bare debug smoke,
and `--warmup` flips it off for its throwaway run only.

**2. Author `task.objective_brief` — do not skip it.** It is a qualitative gloss rendered directly
above the live metric numbers in every mutation/fix prompt, and it is the ONE slot that tells the
proposer **what we optimize + the hard constraints + WITH WHAT** — the building blocks a valid
candidate may use and any it must avoid. There is no fixed field to fill: pitch the "with what" at
whatever the task's OWN defining property is — for one task a discrete operation/move set (e.g.
`cnot_grid_synth`: the adjacency-restricted CNOT/Clifford gate set the circuit may use), for another
the permitted libraries/primitives (and any forbidden ones), for another a structural invariant or a
resource budget the solution must respect. The proposer cannot reliably infer that surface from the
code alone, so naming it here is the highest-leverage context you supply. Division of labor:
`objective_brief` carries the prose (what + constraints + with-what), `perf_str` carries the live
numbers — do NOT add a separate "available ops" field, which would duplicate this slot and confuse
the model. It costs tokens only when authored and is byte-identical (`""`) when null. Keep the
evaluator leak-proof here (held-out numbers under `private`; stop-and-ask if a shown value would
reveal the trick) — the shinka-setup / shinka-convert skills carry that design.

**3. Pick the onset discovery, the initial program, and `num_islands`.** Decide whether to call deep
research for SOTA at onset (see "Running a discovery round and grounding it"). Use any brief to pick
the initial program, `num_islands` (the starter ships 4; the engine default if you omit it is 2 —
set it explicitly; 8 if multiple algorithmic families compete), and to sharpen the goal.

**4. Author `run.json`** (schema in Reference). Default strategy files as shipped. At boot
`run_window` green-lights the discovery-gate contract (alongside its assert that `shinka` resolves to
this worktree) — it confirms `journal.discovery_in_interval` exists and `journal.recent_work_axes`
returns the three work axes, printing `[setup] discovery-gate contract OK`. If that line is absent
the gate is not wired — STOP and report rather than run ungated. (You also OWN the proposer
`reasoning_effort` knob from here on — set it per task difficulty; it is documented in the Config
levers table.)

**5. Run warmup and inspect it.**

```
python orchestrator/harness/run_window.py --config <run>/run.json --warmup
# runs a few traced iters in <results_dir>/warmup (its own db + journal), then prints the path.
# Read <results_dir>/warmup/journal/steps.jsonl, fix a policy and rerun --warmup until clean.
python orchestrator/harness/run_window.py --config <run>/run.json --warmup --iters 5   # widen
python orchestrator/harness/run_window.py --config <run>/run.json --accept-warmup      # KEEP it
```

Each `--warmup` **auto-resets the throwaway workspace** at start: it wipes any prior
`<results_dir>/warmup/` before running, so a rerun after a fix always validates against a FRESH
archive — never against the previous broken attempt's population / bandit / errored-fraction (a stale
workspace could silently flip the rerun into repair mode and mask the very fix you are checking).
Warmup runs a small **configured** number of iterations (default 3, set `warmup.iters` in `run.json`
or pass `--iters`), NOT one — a single iteration can't surface the sampler-spread / bandit-collapse /
brief-differentiation signals warmup exists to observe.

**Keep it once it's clean.** A failed/abandoned attempt is just rerun (the next `--warmup`
auto-resets it). Once a warmup looks mechanically sound, `--accept-warmup` KEEPS it: it folds the
warmup archive into the real `programs.sqlite` (the real run then CONTINUES from that warmed,
reviewed population instead of re-seeding from scratch) and folds its spend into the real ledger as a
durable `warmup_accepted` intervention (so the budget cap counts the tokens already burned). Run it
**BEFORE** the first real window — it refuses if the real archive already exists (it will not take
over a started run) and refuses an all-tombstoned warmup (nothing worth keeping).

Read the per-step trace in the warmup window and stop-correct-restart on a bad step.
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
  it). With keep-the-better a flagged near-dup is still EVALUATED and the better of the
  pair kept, so flooding costs evals + plateau drag, not a frozen archive. Also suspect weak
  mutation diversity. Read the dropped-on-novelty decision; watch `novelty_kept_better` (the
  near-dup-flood sensor), `novelty_rejected_cost`, and `embed_failures` (>0 means the
  embedder failed and the gate was BLIND for those slots, not "diverse").
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
  crashed → treat as no-usable-data and revert (no data is no evidence the rewrite is good).

## What you may change, and what you must not (tiered mutability)

**FOUNDATION — never touch mid-run. Ruining it breaks the consecutive run.**
- The sqlite schema and the JSON stdin/stdout contract (`scripts/_common.py`).
- `scripts/evaluate.py`, `archive_record.py`, `archive_query.py`, `diagnostics.py` (your
  sensor), `repair_record.py`, `journal.py`, `harness/*`.
- `scripts/cadence_policy.py` + the termination logic: the wake-decay schedule and when
  the run ends are FOUNDATION — you must not be able to change how often you wake or extend
  your own run. The knobs (`cadence.early_phase_windows` / `base_low` / `low_threshold` /
  `max_windows_per_call` / `termination_streak`) are tunable but BOOT-ONLY (set in run.json
  before the run, never edited mid-run).
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
| **Diversity / novelty** | `novelty_check.py` | `record_policy.py`, `diagnostics`, `journal/novelty.jsonl` (per-candidate) | `novelty_acceptance_rate` (null when no events); `novelty_rejected_cost`; per-pair via `journal/novelty.jsonl` |
| **Prompt** | `construct_mutation_prompt.py` (incl. the bounded runtime-budget caution, driven by `eval_budget_sec`/`parent_runtime_sec`/`parent_timed_out` + parent/inspiration runtime metadata) | `mutate.py` (sends it) | `evaluation_failure_rate`, recurring `exhausted_retry_slots`, `timeout_count` |
| **Fix / repair** | the immediate-fix loop in `run_window.py` (`evo.fix_retry_budget`) + repair mode (`sample_parent` `select:"errored"`) | `construct_mutation_prompt.py` (the `sample_fix` prompt), `mutate.py`, `repair_record.py` | `fix_rate`, `fix_success_rate`, `repair_fail_count` |
| **Stagnation trigger** | `stagnation_detector.py` | `diagnostics.py` | the progress trajectory + `low_streak` |
| **Cadence (taper)** | `cadence_policy.py` (per-window for the first `early_phase_windows`, then the work-score taper) | `run_window` (passes `window_index` + the work score) | how often control returns vs `window_index` + your work score |
| **Memory** | `record_policy.py` (now persists `runtime_sec`/`timed_out` for the runtime caution — read via `include_metadata`) | sampler / novelty / diagnostics / prompt readers | which metadata fields exist |
| **Island structure** | `island_policy.py` (+ per-island briefs auto-written by meta) | the foundation DB | `island_health` per-island trajectory |
| **New directions (meta)** | `meta_summarize.py` (automatic per-window) | the harness records its per-island briefs; you don't author them | persistently flat progress after rewrites |
| **New directions (discovery round / DR)** | `deep_research.py` (R1, web-grounded, the default) or `subagents/archive-analyst.md` (R2, narrow fallback) | you TRIAGE each direction (novel → ground in a new island; similar → combine; useless → ignore), grounding EACH triaged direction up to 3 | flat progress that meta can't lift |

## The automatic meta round (not yours to trigger)

Every window, the harness calls `meta_summarize.py` once. It sees the archive grouped PER ISLAND
with a code preview of each island's top + failed programs (not just score trends), and returns, for
each live island, a `failure_note` plus that island's own direction list: 1–3 directions, each
optionally tagged with an `assigned_program_id` — the existing program that already realizes it.
Every direction is assigned to exactly one island (none duplicated across islands). Those per-island
directions + their program assignments are auto-recorded as each island's brief (`structured_json`),
and the SAMPLER reads them so islands diverge in BOTH their prompt direction AND the exemplar code
shown — not text alone. A brand-new island with no brief yet just carries a neutral placeholder, and
that is enough: its mutation prompt still has its modes, inspirations, and the task message. You
don't hand-author briefs. Your meta levers: `evo.meta_model` / `evo.meta_reasoning_effort` (default
`azure-gpt-5.5` medium; to escalate set `meta_model: azure-gpt-5.4-pro` AND `meta_reasoning_effort:
high` — two knobs, NOT a `model@effort` suffix; pro rejects `low`); `evo.meta_code_preview_chars`
(default 1200 — shrink if meta cost climbs); or `evo.auto_meta:false` (suppresses the whole round;
islands keep their last brief). Its cost folds automatically; budget-gated and wrapped so a meta
failure never aborts a window.

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

**Reward baseline is sign-aware and repair-aware.** `compute_reward.py` (a MUTABLE strategy cell)
builds the absolute reward against `max(parent_score, 0)` — the bandit shifts by `max(baseline, 0)`
and asymmetric-clamps, so on a NEGATIVE-score task a correct-but-low candidate would otherwise
collapse to the same `r=0` as a failure; the sign-aware baseline keeps it strictly above the floor
(positive-score tasks are byte-identical). The repair-aware half is done by the harness BEFORE it
calls `compute_reward`: on a REPAIR gen it credits the nearest CORRECT ancestor's score (the
last-good version), NOT the errored parent's ≈0 — so a routine bug-fix doesn't look like a full-score
gain and blow out the bandit's `obs_max` (which would then normalize every normal delta to ≈0). The
bandit geometry itself (`exploration_coef` / `cost_aware_coef` / `exponential_base`) is not
foundation — it is tunable through the config levers plus a calibration measure window. Just don't
silently hand-rewrite the geometry mid-run; a real geometry change belongs in the end-of-run ending
document.

## Running a discovery round and grounding it

A **discovery round** is *discovery* (find SOTA), not *instantiation* (write the code). It
is your decision at a control-return, on the same tapering rhythm as the framework-audit
check. Discovery is valid via EXACTLY ONE OF two routes (and nothing else):

- **R1 — Azure deep research (`deep_research.py`): the near-always default.** Web-grounded,
  web-cited, external — it surfaces techniques absent from your archive that the search
  cannot invent.
- **R2 — the `subagents/archive-analyst.md` multi-agent read: a NARROW fallback.** Permitted
  only when, for the SAME question: an R1 DR already ran, you have strong confidence a good
  answer exists, yet all the R1 directions aren't helping. It is NOT a "prefer up front"
  route — introspection over your own archive cannot surface a technique that is not already
  in it; that needs R1.

**An ad-hoc tournament / sort over your OWN hypotheses is NOT discovery** (it surfaces no
new knowledge) — only R1 or R2 produce a triageable direction.

**Why default to external, and why trust it.** The discovery/triage
step has an observed inclination to **deny ideas** — refusing to even try grounding, dismissing
a direction on sight "by reading its name." Routing to external R1 by default (fresh, web-cited,
harder to dismiss) counteracts that. INCLINE TO TRUST discovery and INITIATE grounding: bias
triage toward novel→ground / similar→combine, use useless→ignore sparingly, and **never kill an
idea by its name** or for being "similar / renamed."

**When.** When the search is stuck and the gap looks *algorithmic* (a technique the search
won't invent) — normally after a meta round and at least one cheaper move haven't moved
the best score. You DECIDE by reading the logs/history yourself (there is no automated
similarity helper — DR returns a text idea, the archive holds code, so only you judge
whether the idea already exists). Examine `journal.read_calls`, `archive_query`
`top_n`/`recent_failures`, and the directions already recorded. Always
pass BOTH `results_dir` AND `budget_usd`: `results_dir` makes the call self-log the
machine-readable **discovery stub** — a `kind=dr` pointer `{query, brief, timestamp, usable}`
in `calls.jsonl` (full detail under `journal/calls/`) — and fold cost into the ledger; this is
the R1 stub the recency gate reads. `usable = bool(brief)` (true iff ≥1 direction returned); a
refused / empty-brief DR logs `usable:false` and does NOT unlock grounding. `budget_usd` arms
the pre-flight that SKIPS the spend when the remaining budget can't cover
`dr_estimated_cost_usd` (~$5). Passing `results_dir` alone does NOT bound DR by the budget —
without `budget_usd` there is no pre-flight and DR (the single most expensive action) can
overshoot the cap.

**How to write the DR query (you write this).** Ask for the *general SOTA techniques for
the task* — or for a well-defined sub-problem — in the model's OWN words with a citation
(author/year/arXiv id). Do NOT ask for "the exact algorithm from [named paper]" or a
verbatim snippet: that shape reads as "reproduce copyrighted text" and Azure's content
filter refuses it deterministically. Keep it concise — the problem, the constraints, what
you've tried, the sub-question. Beyond the bare problem, characterize the task by its CORE
structure — the objective + score shape, the hard constraints, and what makes a candidate solution
ADMISSIBLE (the space a valid solution must live in) — so DR returns techniques that fit THIS
problem rather than ones that assume structure it does not have. There is no fixed field to fill:
pitch the characterization at whatever the task's own defining property is (one task's admissible
space is a discrete move/operation set, another's is a set of permitted dependencies, another's is
a structural invariant or a resource budget). (The DR system prompt is FOUNDATION — you steer DR
only through this query/context you author, never by editing `deep_research.py`.)
**Pre-flight self-check before every DR call:** re-read
your drafted query and confirm its GOAL is general SOTA for the task/sub-task, not
"reproduce a specific named paper"; if it asks to reproduce one paper's algorithm
verbatim, STOP and reshape it. A refused/failed DR call returns `refused:true` + a `reason`
(logged with its query intact, no crash) — a `content_filter` refusal almost always means
a reproduce-paper framing, so RESHAPE the query; never re-fire the same shape.

**If a DR call fails** it returns `refused:true` + a `reason` (a `content_filter` refusal almost
always means a reproduce-paper framing — reshape per the pre-flight above; never re-fire the same
shape), or a server-side terminal `failed` whose `reason`/`error_code` the journal carries — read
it (e.g. a missing/blocked `web_search_preview` tool, or a wrong deployment name / model-version)
and fix the cause; don't loop-retry. The deployment quota (30,000,000 TPM / 30,000 RPM) is ample
for a full job. If R1 ever keeps failing, fall back to the R2 Claude-native pass — spawn
`subagents/archive-analyst.md` (a multi-agent read over your own archive + literature). R2 is the
**narrow fallback** (same question, an R1 already ran, you are confident a good answer exists, yet
the R1 directions aren't helping or can't be grounded) — NOT a "prefer up front" route. R2 MUST leave its own discovery stub: a `kind=archive_analyst` pointer logged via
`journal.py log_call` (cost 0.0, Claude-native — never `append_intervention` it, that would
double-count), with `usable:false` when it surfaced nothing. Triage R2 output exactly like a DR
brief (below). A discovery round counts the same for the termination streak whichever route ran.
(`archive-analyst` is a DISCOVERY route — it reads the archive and the literature to surface a
missing direction; it is NOT a framework-code-audit tool and never edits code or the archive.)

**Triage discovery output by the THREE PATHS — per idea, one by one, identically whether the
ideas came from an R1 (Azure DR) brief OR an R2 (archive-analyst) analysis. Only R1/R2 ideas are
triageable (a self-invented hypothesis fails provenance and is dropped). A discovery round returns
one or more (direction, citation) pairs; do NOT take only the single "best" one — decide a path for
EACH, and GROUND each triaged direction, up to a MAX of 3 per round. Lean toward acting:**
- **path (i) NOVEL** (no archived program or prior direction resembles it) → GROUND it with a
  HAND-AUTHORED prompt (the grounding recipe below), then give it **its own island** via
  `spawn_island.py`: `archive_record.py` with `parent_id=null`, THEN `spawn_island.py` copies it
  onto a fresh island as that family's root so it isn't out-competed before it matures. Scores
  `work_grounding=2`.
- **path (ii) SIMILAR-TO-EXISTING** → COMBINE it into the closest existing program. This is a
  FIRST-CLASS path, not a fallback: (1) HAND-AUTHOR the grounding prompt (NOT the mutation sampler)
  AND additionally hand the model the CLOSEST existing program as the thing to combine the new
  direction into; (2) on a CORRECT result, `archive_record.py` it with `parent_id` = that closest
  program's id, so it enters as a LINEAGE CHILD in the SAME island/family — NO new island, NO
  `spawn_island.py`; (3) the existing similar program is NOT overwritten, evicted, or replaced —
  both coexist and the inner loop refines from there. "Similar to something we already have" is
  NEVER a kill — it is this path. Scores `work_grounding=1`.
- **path (iii) USELESS** → ignore it (use this sparingly). Don't dilute the search.

The adversarial verification step (yours, or a subagent's) is **LENIENT — its job is
provenance-authentication + path-assignment, NOT rejecting directions.** It must (1) authenticate
that each idea came from an in-interval R1/R2 stub (a self-invented hypothesis fails this and is
dropped) and (2) map each authenticated idea to ONE of the three paths. It must NOT kill an idea
by reading its name, nor merely for being "similar to existing" or "a renamed version of existing
code" — that is the SIMILAR-TO-EXISTING path (combine it), never a rejection. Authenticated
directions go to grounding anyway.

**PRECONDITION (HARD GATE, both executors):** grounding may act ONLY on a technique from an
in-interval triaged R1/R2 discovery stub. Before authoring a grounding prompt, confirm a usable
`kind∈{dr,archive_analyst}` stub exists for the CURRENT control-return interval (strictly newer
than the prior control-return); a stale stub does NOT satisfy it. `spawn_island.py` enforces
this in code: it refuses to seed an island when no in-interval stub exists, and
`subagents/grounding-engineer.md` REFUSES if the spawn prompt carries no in-interval R1/R2
provenance reference.

**Grounding is HAND-AUTHORED — it does NOT go through the diff/full/cross mutation sampler.** YOU
author the prompt directly and feed it to EITHER Azure `mutate.py` OR the
`subagents/grounding-engineer.md` subagent (`mutate.py` accepts a ready-made
`patch_sys`/`patch_msg`/`patch_type:"full"` and never calls the sampler):
- `patch_sys` = the `task_sys_msg` + a "# Required structural replacement" mandate + the DETAILED
  technique (key steps + reference pointers: author/year/arXiv) + the objective/constraints (your
  `task.objective_brief`, which carries the admissible-solution constraints so the grounded program
  cannot violate them) + the full-rewrite NAME/DESCRIPTION/CODE response format (import the
  `FULL_SYS_FORMAT_DIFFERENT` body from `shinka.prompts.prompts_full` so `mutate.py`'s parser is
  satisfied — it is NOT re-exported from the `shinka.prompts` package, so importing it from there
  raises ImportError). The "# Required structural replacement" framing is the **path (i) NOVEL**
  framing (a from-scratch pivot). For **path (ii) SIMILAR-TO-EXISTING**, frame the prompt instead
  as COMBINING the new direction INTO the closest existing program (hand THAT program as the base
  to EXTEND, not a from-scratch replacement) — otherwise the recipe (Azure `mutate.py` or
  `grounding-engineer`, web search ON) is identical for both paths.
- `patch_msg` = for **path (i) NOVEL**, the INIT seed program (`initial.<ext>`) framed "reference
  interface ONLY — do NOT improve/refactor; you are REPLACING its algorithm", plus "write a
  program with the SAME inputs/outputs implementing this idea". For **path (ii) SIMILAR**, hand the
  CLOSEST existing program as the base to extend ("combine this new direction INTO this program,
  same inputs/outputs").
- **Run it on Azure** via `mutate.py` with `model_name:"azure-gpt-5.5"`,
  `reasoning_effort:"medium"` (the default; escalate to `azure-gpt-5.4-pro@high` only if 5.5 won't
  land it), and **`enable_web_search:true` on EVERY grounding run** — a grounding always turns web
  search on so the model can read its reference (this `enable_web_search:true` is passed straight to
  the standalone `mutate.py` call; it is unrelated to the inner-loop `evo.mutation_web_search` knob).
  The discovery-before-grounding rule is enforced at `spawn_island.py` (the PRIMARY gate, for a NOVEL
  new-island grounding) and by `subagents/grounding-engineer.md`'s refusal. If the Azure model keeps
  REFUSING the pivot, hand the SAME ingredients to `subagents/grounding-engineer.md` and let Claude
  author the code — do NOT keep firing more Azure mutate calls at it.
- **Result parity (both executors handled identically) — but the ARCHIVE step BRANCHES BY PATH:**
  evaluate via `evaluate.py`; on a CORRECT result, embed it, then:
  * **path (i) NOVEL** → `archive_record.py` with `parent_id=null`, THEN `spawn_island.py` (its own
    island; `work_grounding=2`).
  * **path (ii) SIMILAR** → `archive_record.py` with `parent_id` = the closest existing program's id
    (a lineage child in THAT program's island), and do NOT call `spawn_island.py` — no new island,
    and the existing similar program is not overwritten / evicted / replaced (`work_grounding=1`).
  A first injection that scores 0.0 / below baseline is EXPECTED — the value is seeding (path i) or
  extending (path ii) a structural family for the inner loop to refine, not an immediate win.

`spawn_island.py` (stdin `{db_path, db_config, embedding_model, program_id, results_dir,
discovery_provenance?}`) seeds a NEW island with a copy of the grounded program as its root. It is
the **path (i) NOVEL executor ONLY**: path (ii) SIMILAR never calls it — that grounding is a plain
`archive_record.py` with `parent_id` = the closest existing program (a lineage child in the SAME
island), with NO spawn and the existing program left intact. `results_dir` is **REQUIRED** — it is
what the PRIMARY gate reads: at the top of `main()` (before opening the DB) it calls
`journal.discovery_in_interval(results_dir)` and returns `{ok:false}` / non-zero (NO island seeded)
when no in-interval usable R1/R2 stub exists.
`discovery_provenance` is an OPTIONAL exact-match tightener (the stub reference the grounding acted
on). It honors `db_config.max_islands`:
at the cap it retires the worst island non-destructively (rows preserved for lineage) and
reuses the index; island 0 and the global-best island are protected. `max_islands:0`
(default) = unbounded.

**The rare program-rescue — a different thing from discovery grounding.** Sometimes a NORMAL
inner-loop mutation is about to be tombstoned because Azure keeps failing to realize a direction you
judge worth saving (even its fix rounds failed). If you deem it worth it, you may hand-author the
program yourself via `subagents/grounding-engineer.md` to push it onto that direction; if it then
evaluates correct, archive it normally and it need not be tombstoned. This rescue is NOT tied to a
discovery (discovery has its own two grounding paths above), so it needs NO discovery stub and NO
Azure grounding call — you just do it yourself. It still sets web search ON, and it does NOT count as
an intervention on its own.

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
4. **Snapshot + deploy.** ALWAYS pass `results_dir=` so `deploy` first calls `snapshot_state`,
   which snapshots the framework files AND the run state (archive DB + bandit + ledger) so
   the rewrite is recoverable (snapshot only when no window subprocess is live — the store flags a
   snapshot taken during a live window). A deploy WITHOUT `results_dir` is stamped
   `revertible:False` (code-only, no state snapshot) and warns — so never omit it for a real
   mid-run rewrite, or a regression can't be fully rewound. Single file:
   `strategy_store.deploy(candidate, target, reason, window_index, prior_J, concern=,
   results_dir=)`. A whole concern: `validate_bundle` then `deploy_bundle([...], reason,
   window_index, prior_J, concern=, results_dir=)`. The harness stamps the full
   `strategy_fingerprint` into every window; log the rewrite with
   `journal.append_intervention(...)`.
5. **Measure, STAYING AWAKE.** Run exactly ONE measure window with tracing on so its step
   logs exist: `run_window.py --config <run>/run.json --windows 1 --trace-steps`. Read its
   `steps.jsonl` — do not go to wait-mode yet. (You do this at a control-return, so the
   cluster has already EXITED and the `results_dir` run-lock is free; if you ever need to
   interrupt a still-running cluster first, write `<results_dir>/.stop` and wait for its clean
   exit — otherwise the measure window refuses on the held lock, by design.) (If the effect needs more than one window,
   mark it to check next round — rare.) **A measure window can run for many minutes
   (one full window of eval subprocesses), so survive idle-reclaim the same way the main
   cluster does:** launch it in the BACKGROUND (it returns control by EXITING) and hold the
   short self-wake **heartbeat** (see "Launch the inner loop and get woken") so the sandbox can't
   idle-reap the launcher→run_window→eval group while you wait; on a kill, `--resume` recovers
   and you re-measure. `run_window` self-caffeinates against host idle-sleep, but the heartbeat
   is what beats sandbox idle-reclaim of a backgrounded measure window — do NOT block the
   session waiting on it synchronously.
6. **Accept or revert.** Call `rollback_decision.decide(prior_window_diag,
   measure_window_diag)` (pass `measure_crashed=true` if the measure subprocess crashed /
   exited non-zero / produced unparseable output). It flags a regression if the rewrite
   collapsed correctness, collapsed diversity, regressed score while the prior window was
   progressing, or collapsed model selection (counts-share) — and it **reverts on no data**: a
   measure window with no usable data (crash / empty / NaN) is treated as a regression and
   reverted (no data is no evidence the rewrite is good). If `regressed`: `restore_state(results_dir, snap_id)` — a FULL rewind of code
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
DIFFERENT parents in a window (each having exhausted its in-loop repair budget, matching
the subagent's own precondition); write its report to `strategy_history/debug_<w>.md`, act on its one
recommendation, forget the detail. For periodic structural reads, spawn
`subagents/archive-analyst.md`.

## Termination + end of run

**Stop when EXACTLY ONE of these three criteria is met — there are no others:** (a) the budget is
exhausted [harness-decided, auto-finalized]; (b) **five consecutive control-returns were each
STAGNANT and each had an intervention** (a framework rewrite, a discovery round [R1 or R2] — which is
then grounded — OR a deliberate config-lever flip — the AUTOMATIC per-window meta round does NOT count) that still
could not break the stagnation [harness-decided, auto-finalized]. A hand-authored **grounding does
NOT count on its own** — `work_grounding` never flips `intervened`; it counts only WITH the
in-interval discovery it grounded (`work_discovery>0`), so a grounding over a stale/absent
discovery can't pad the streak;
(c) **a LITERAL, real user stop message typed in THIS live conversation** — the only termination you
finalize by hand, and only when you can quote that actual user turn. NEVER finalize `stopped_by_user`
from an inferred/remembered/assumed/"it feels done" signal — confabulating a user stop is the single
worst failure here. If you feel stuck but the user has NOT literally said stop and neither harness
criterion has fired, KEEP GOING (launch the next cluster). This is **harness-computed and auto-finalized** (parity with budget): the harness
reads your canonical `control_return` rows (`stagnation_flag`+`intervened`) via
`journal.termination_streak`, and when the streak reaches `cadence.termination_streak`
(default 5) the next `--until-decision` call returns
`return_reason="stagnation_intervention_exhausted"` and finalizes — so two agents can't
disagree on the count. Stagnation ALONE never terminates (only stagnation your interventions
couldn't break); a stagnation-break OR a no-intervention return resets the streak. There is
no longer a "≥1 discovery of 5" requirement — a discovery round simply counts as an intervention
(via `work_discovery>0`). The automatic per-window meta round does NOT count as an intervention,
and a grounding alone (without its discovery) does NOT either. A pre-assumed/reference score in
the docs does NOT end the run early.

**Per-run NOTES.md.** `orchestrator/NOTES.md` is a transient SINGLE-RUN scratchpad, not a
persistent ledger. CLEAR it at the START of a run (the previous run's note is assumed already
read and handled by the user); during and at the end of the run, write THIS run's handoff there
(what happened + anything the user must fix before the next run). Persistent teaching lives in
this SKILL.md + CLAUDE.md; past runs' notes live in git history.

**End of run:** review the whole history and write an **ending document** — the run's
outcome + a "Future fixes for the user before the next run" section (foundation/outer-loop
changes you could not make mid-run). Include a check of whether the cadence taper fit THIS task:
across the run, did each control-return ask for a roughly constant amount of work, or did the
clusters grow too fast (errors piling up before you attended to them) or too slow (you woke with
nothing to do)? Think of it as error accumulation — fast early, slower later — and a good taper keeps
the action-rate roughly even. If it was uneven, note that the next run should adjust the taper knobs
(`cadence.*`) for this task. Seed it from `journal.build_run_summary(results_dir)`,
flip the run's status with the `finalize_run` journal view — but `budget_exhausted` and
`stagnation_intervention_exhausted` are finalized by the HARNESS (never call `finalize_run` for
those), and you finalize `stopped_by_user` BY HAND only when you can quote the literal user turn
that said stop; the ending document's "Termination reason" must quote that turn (or name the
harness criterion), never just "user said stop". Then **archive** the run's history/logs/artifacts
with the `archive_run` view into `orchestrator/run_archive/<run_id>__<finished_at>/`. **Do
NOT read prior runs' archives while running a new job** — they exist only for the user's
later reference.

## The run journal (your long-term memory, read at any granularity)

`scripts`/grep or `harness/journal.py`:
- `journal/run.json` — run summary (status, windows, best, total_cost) — crash-durable.
- `journal/windows.jsonl` — per-window diagnostics (the trajectory).
- `journal/interventions.jsonl` — every rewrite/decision + your work score + outcome.
- `journal/calls.jsonl` — compact POINTER index of every external LLM call (`kind` ∈
  `meta / dr / archive_analyst`): `{kind, timestamp, file, cost, summary}`. Full prompt + raw
  output live in `journal/calls/<kind>_<ts>_<rand>.json`. The discovery kinds the recency gate
  recognizes are `{dr, archive_analyst}` (each is a **discovery stub**, carrying `usable`);
  `journal.discovery_in_interval(results_dir)` returns the in-interval usable ones (the single
  source of truth for the recency rule — if empty, grounding is refused), and
  `journal.recent_work_axes` exposes the three work axes (`work_audit`/`work_discovery`/
  `work_grounding`) so a grounding-without-discovery stretch is detectable.
- `journal/novelty.jsonl` — ONE compact record per EVALUATED CORRECT candidate whose novelty gate
  ran: `{window_index, generation, candidate_id, parent_id, island_idx, decision in
  {accepted_novel|kept_better_evicted|dropped_worse|idle_no_compare}, max_similarity,
  most_similar_id, most_similar_score, candidate_score, n_compared, diff_lines, threshold}` (ids +
  numbers, never code). The per-call audit trail behind the aggregate `novelty_acceptance_rate`.
  Readers: `journal.read_novelty`, `journal.novelty_near_threshold`.
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
| `island_policy.py` | fork/migrate/retire decision (spawn fires ≤once per stagnation episode via the durable `last_policy_spawn_generation` marker; a rewrite may decide a `retire_island`, executed non-destructively + protecting island 0 + the global best) | **Yes** | No |
| `cadence_policy.py` | WHEN control returns (early-phase per-window floor, then the work-score taper; not the budget) | No (FOUNDATION; knobs are boot-only) | No |
| `island_brief.py` | record a per-island direction (auto-called by the meta round) | **Yes** | No |
| `spawn_island.py` | seed a new island from a grounded program (refuses to seed unless `journal.discovery_in_interval(results_dir)` is non-empty) (path (i) NOVEL only; path (ii) SIMILAR is a plain `archive_record` `parent_id`=closest, no spawn) | No (foundation) | No |
| `construct_mutation_prompt.py` | build the mutation/fix prompt | **Yes** | No |
| `mutate.py` | call Azure (bg+poll), parse, apply, retry | Body no, prompt yes | **Yes (Azure)** |
| `meta_summarize.py` | the automatic per-window meta round (per-island directions) | prompt yes | **Yes (Azure)** |
| `deep_research.py` | R1 discovery — deep-research model (web-grounded directions); writes the `kind=dr` discovery stub with a `usable` bool | No (paid service) | **Yes (Azure DR)** |
| `_azure.py` | shared Azure background-poll transport | No (foundation) | — |

**Background-poll resilience (transport, FOUNDATION).** Every Azure bg call (`mutate`/`meta`/DR)
submits a job and then polls its status. While the model works, the job reports `queued` and/or
`in_progress` before `completed` — BOTH are normal pre-completion states (per OpenAI's docs), and
`queued` does NOT mean the job is stuck. The only time bound is the LONG total-job wall
(`SHINKA_BG_POLL_TIMEOUT_SEC` / `AZURE_DR_TIMEOUT_SEC`, 3600s, a true monotonic deadline); a SHORT
per-HTTP-request cap (`SHINKA_BG_HTTP_TIMEOUT_SEC`, 60s) wraps each submit/status-GET and RETRIES a
hung request so a wedged socket on one status GET can't ride the whole wall. There is no separate
"queued too long" abort — a genuinely-stuck job simply bounds at the wall, then degrades cleanly
(mutate → `applied:false`; DR → a degraded brief whose billed cost is captured). Don't rewrite the
transport in a strategy rewrite.
**And never manually kill a slow in-flight Azure bg call** (TaskStop / bash-kill) to end it sooner —
cost books only on a TERMINAL status, so a kill leaks unlogged-but-billed spend; let it ride to the
3600s wall, deciding for yourself with the knobs you own (reasoning effort, `@medium` vs `@high`,
prompt scope) how to handle a pathologically slow call. (This is the Azure CALL only. To stop or pause
a `run_window` cluster, write `<results_dir>/.stop` and let it exit at the next window boundary, then
`--resume` — never `Stop-Process`/`Get-Process` a run by PID; see "Run identity, stopping, recovery".)

## The run config (you author this)

```json
{ "results_dir": "<run dir>", "run_id": "<id>", "budget_usd": 50,
  "task": {"eval_program_path": "...evaluate.py", "init_program_path": "...initial.py",
           "task_sys_msg": "<precise goal>", "require_sys_msg": true,
           "objective_brief": "<optional 'what we optimize + hard constraints + the building blocks a valid candidate may use' gloss, or null>",
           "language": "python", "eval_time": "00:35:00"},
  // eval_time is the harness hard-kill — it MUST exceed the task evaluator's internal
  // wallclock budget, or slow candidates are SIGKILLed before the evaluator's graceful
  // early-abort can write a score (a result-less death). The cnot evaluator's default budget
  // is 30 min, so eval_time >= 00:35:00; set per task.
  "db_config": {"num_islands": 4, "archive_size": 40, "parent_selection_lambda": 10.0,
                "migration_interval": 10, "enable_dynamic_islands": false,
                "max_islands": 0, "island_evict_strategy": "worst_best_fitness"},
  "evo": {"window_size": 10, "patch_types": ["diff","full","cross","fix"], "patch_type_probs": [0.55,0.3,0.1,0.05],
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
helps hard tasks but a cheap arm at `medium` can wedge a call 50–90 min); it is a
knob you OWN at boot and re-tune at any control-return, not a default to leave alone.
**Pro policy:** keep `azure-gpt-5.4-pro` OUT of the normal mutation pool by default — it's
reserved for the meta round (when you escalate `meta_model`) and a grounding run; a
future outer-loop may add `pro@high` to the pool if a task warrants it.

`cadence.max_windows_per_call` is an OPTIONAL explicit ceiling — unset by default, so the
work-score taper is uncapped (bounded by budget / termination / stagnation).

### Config levers — flip a knob before you rewrite code

Many decisions that *look* like a code rewrite are already `evo.*` knobs. Prefer a knob
(cheap, instant, no protocol) over rewriting a policy. EXCEPTION: the `cadence.*` rows
below (`early_phase_windows` / `base_low` / `low_threshold` / `max_windows_per_call` /
`termination_streak`) are BOOT-ONLY — set them in run.json before the run; do NOT change the
wake/termination cadence mid-run (cadence_policy.py is FOUNDATION).

| Knob | Default | What it does | When to flip |
|---|---|---|---|
| `auto_meta` | true | run the automatic per-window meta round | false to pause meta entirely (islands keep their last brief) |
| `meta_model` / `meta_reasoning_effort` | `azure-gpt-5.5` / medium | the model for the automatic meta round | raise to `azure-gpt-5.4-pro` / high when directions are worth the high cost |
| `reasoning_effort` (per arm via `model@effort`) | medium (shipped) | the proposer/fix reasoning budget per call — the biggest driver of per-call wall-time + verbosity | RAISE (high) for reliability on hard/algorithmic tasks; LOWER (low, where the model allows — pro rejects low) when a cheap arm emits 10–35k reasoning tokens per ~3k-token patch and wedges calls 50–90 min. You OWN this — not a default to leave alone |
| `early_phase_windows` | 5 (cadence) | Stage-1 floor: control returns EVERY window for the first K windows regardless of work score (frequent early inspection) | lower / 0 to taper sooner; raise to inspect every window longer. 0 restores the old immediate work-score taper |
| `repair_trigger_fraction` | 0.20 | errored-fraction at which repair mode turns on | raise if repairs churn; lower to repair sooner |
| `repair_attempt_cap` | 2 | failed repairs before a parent is tombstoned | raise to give a hard failure more tries |
| `repair_escalation_model` | null | stronger model on the last repair before removal | set to e.g. `azure-gpt-5.4-pro@high` for a stubborn class |
| `fix_retry_budget` | 1 | immediate eval-failure repairs per slot | raise for a hard task |
| `mutation_web_search` / `fix_web_search` | false | web search on the INNER-LOOP mutation / fix calls | rarely needed and unused in practice (no run config has set it). NOT the grounding signal: a grounding run passes `enable_web_search:true` straight to the standalone `mutate.py` call, and discovery-before-grounding is enforced at `spawn_island.py` (PRIMARY gate) + the grounding-engineer refusal — not via this knob |
| `cost_aware_coef` | 0.25 shipped (engine default 0.0 when `llm_dynamic_selection_kwargs` is unset) | bandit reward-vs-cheapness blend | raise→0.7 if cheapness should dominate; lower→0 if a pricier arm is the only one improving and is being starved |
| `epsilon` | 0.2 | bandit exploration floor | an arm's share decaying toward 0 while it still occasionally improves → raise to 0.4–0.6 |
| `code_embed_sim_threshold` | 0.99 | near-duplicate cosine gate, over the basis set by `novelty_embed_mode`; a near-dup is now EVALUATED then the BETTER of the pair is kept (worse dropped / tombstoned) | under the default `diff` basis the 0.99 gate rarely false-rejects; under legacy `code` basis genuine large-program edits cluster ~0.994 and are mis-flagged → switch to `diff` (preferred) or RAISE the threshold (never lower under `code`); watch `novelty_rejected_cost`. Audit per-pair via `journal/novelty.jsonl` + `journal.novelty_near_threshold`; see "Tuning the novelty threshold" |
| `novelty_embed_mode` | diff | WHAT the gate embeds: `diff` = parent→candidate unified diff (genuine edits separate to low cosine; the per-island pool can GROW) vs `code` = legacy whole-program embedding (collapses each island to a single-survivor greedy chain on a large program) | keep `diff`; use `code` only to reproduce legacy behavior or on a resumed archive whose stored embeddings are whole-program |
| `novelty_tie_epsilon` | 0.0 | keep-the-better tie margin: an equal-scoring DISTINCT near-dup within epsilon is KEPT and the incumbent tombstoned (`>=`), so the lineage traverses score plateaus instead of dropping every tie after a full eval | raise slightly to keep more near-ties for plateau exploration |
| `stagnation_abs_floor`/`rel_frac` | 0.001 / 0.05 | the "low window" bar | recalibrate to the task's natural per-window climb |
| `validity_floor` | none (inert) | floors VALID parents' selection score (`sample_parent`) | many correct programs pinned at 0 and selection can't separate them |
| `reward_validity_floor` | 0.001 | floors a correct candidate's bandit reward so correct-but-worse beats *failed* | an arm with high eval-success is starved because its children rarely beat the parent |
| `reward_on_reject` | cost_only | a novelty-rejected slot bills the arm's COST only (neutral) vs `penalize` | a duplicate-prone arm should be penalized for waste |
| `reward_mode` | absolute | how `compute_reward` frames the bandit reward (absolute reward vs parent-delta) | leave `absolute` unless deliberately recalibrating the reward; a geometry change belongs in the ending document |
| `force_explore` / `llm_subset` | false / null | ignore the collapsed bandit (uniform) / restrict to a subset | re-open a starved/locked-out arm (the framework-audit check) || `island_policy_driven` / `policy_spawn_cooldown` | false / 0 | drive spawn/migrate/retire via mutable `island_policy.py` at window boundaries | repairing population structure. The policy reads its OWN gates — set `policy_migrate_enabled` / `policy_migrate_interval` / `policy_spawn_enabled` / `policy_spawn_stagnation` and keep the db_config auto-triggers OFF (`enable_dynamic_islands:false`, `migration_rate:0`) to avoid double-execution; the executor result + any crash are surfaced on stderr. A spawn fires at most ONCE per stagnation episode (the harness carries `last_policy_spawn_generation`; a new best re-arms it; `policy_spawn_cooldown` > 0 lets it re-fire after N gens). `retire` has a non-destructive executor (protects island 0 + global best) — a rewrite may set `retire_island`. |
| `brief_compose_mode` | replace | legacy knob for combining a per-island brief with a global direction | inert now that meta emits no global directions — the per-island brief is always the island's direction |
| `island_selection_strategy` / `enforce_island_separation` (db_config) | uniform / true | island selection pressure (`uniform`=`equal` are aliases; `proportional`=by population count; `weighted`=by island best-fitness) / same-island vs cross-island inspirations | concentrate sampling on the LARGEST island → `proportional`; on the best-fitness island → `weighted`. ⚠️ BOTH REINFORCE the leader — NEITHER rescues a starved/small island (no live strategy does; small-island rescue needs `archive_floor_per_island` ↑ or a `_select_island` rewrite). cross-pollination → separation `false`. (These are `db_config` knobs, not `evo.*`.) |
| `archive_floor_per_island` | 3 (db_config) | per-island archive floor — a dominant island can't evict another island below this many members | islands collapsing to one lineage → raise; a single-family task → 0 (pure global fitness) |
| `migration_rate` / `migration_interval` | 0 / 10 (db_config) | island elite migration; **0 = OFF by default**. Execution IS wired (every archive add runs deferred migration), so flipping it on is a live config change — NOT a code rewrite. Migration iterates the ACTIVE island_idx set, so a dynamically SPAWNED island participates | turn it on at BOOT (run.json `db_config`) OR mid-run (edit `db_config` at a control-return; the next relaunched cluster reads it — schema-safe) → `migration_rate` ≈ 0.05 |
| `termination_streak` | 5 (cadence) | consecutive stagnant+intervened control-returns that auto-terminate the run | raise to give a stuck search more intervention attempts before stopping |
| `meta_failures_first_frac` | 0.5 | how much of the meta context is recent failures vs top performers | failure-dominated window where the failure_note comes back vague → 0.75 |
| `extra_guidance` | none | free text appended to the next window's mutation system prompt | nudge the search without a code rewrite |

**Islands differentiate BY DEFAULT — in direction AND code.** The automatic per-window meta round
writes each live island a STRUCTURED brief (1–3 directions, each optionally tagged with the program
that realizes it). `sample_parent` then samples ONE of that island's directions per generation and
pulls the programs ASSIGNED to it as the inspirations (else just the direction text) — so the brief
changes WHICH code the island sees, not only the prompt text. Genetic separation is protected by
`archive_floor_per_island` (default 3 — a dominant island can't evict a young one below its floor)
and, if enabled, `migration_rate`. **By default islands have NO genetic interaction**: `migration_rate`
is 0 and `enforce_island_separation` is true, so islands diverge only by DIRECTION (briefs), never by
exchanging genes; if you want cross-island gene flow you must enable migration (`migration_rate` ≈
0.05) or cross-island inspirations (`enforce_island_separation:false`). Cross-island inspirations key
the child to its PARENT's island (`island_idx` = parent's, `sampled_island_idx` = the
originally-selected island for provenance). Hand-authoring a brief via `island_brief.py` is the
override, not the default path. (A cap-hit incomplete model response always returns its billed partial text,
and an unpriced billed response logs a warning and is billed $0 — neither is a tunable knob.)

The rollback basket has tuning knobs passed to `rollback_decision.decide()` (NOT `evo.*`):
`abs_eval_floor` (0.05), `bandit_collapse_count_frac` (0.85) / `bandit_collapse_min_pulls`
(8) for the primary counts-share collapse arm (the weights-fraction arm is legacy /
near-unreachable because a single arm's weight caps at `1−epsilon`), `measure_crashed`
(forces a revert), and `min_eval_success`/`eval_drop`/`nov_drop`/`score_ratio`. Two things the
gate does on its own: it auto-reverts when the measure window evaluated ZERO candidates — every
slot apply-exhausted, `apply_failure_rate` 1.0 (e.g. a patch-format-breaking prompt rewrite) — so
you no longer hand-pass `measure_crashed` for that case; and the counts-share arm reads THIS
window's submitted counts (`llm_bandit_window_counts`), so a mid-run selection collapse is actually
detectable (the run-cumulative total could never move the share mid-run).

## Tuning the novelty threshold (the novelty.jsonl audit loop)

`evo.code_embed_sim_threshold` (default 0.99) is the cosine gate over the parent→candidate DIFF
embedding (`novelty_embed_mode:diff`). It is THE knob separating a scalar / hyperparameter tweak
(tiny diff, near-identical text → HIGH similarity → should be a keep-best near-dup) from a few-line
NEW-DIRECTION edit (distinctive diff → LOWER similarity → should be a SEPARATE archive entry). The
orchestrator OWNS this knob and re-checks it on a control-return when the population looks
**monocultural** (too many near-dups surviving) or **churny** (real variants evicted).

**THE DATA.** Every evaluated correct candidate writes ONE `journal/novelty.jsonl` row
`{window_index, generation, candidate_id, parent_id, island_idx, decision in
{accepted_novel|kept_better_evicted|dropped_worse|idle_no_compare}, max_similarity, most_similar_id,
most_similar_score, candidate_score, n_compared, diff_lines, threshold}` pairing it with its single
most-similar program (`most_similar_id`) + both scores + `max_similarity` + `n_compared` +
`diff_lines` + the threshold in force. The per-window `novelty_sim_histogram` (bins
`<0.90 / 0.90-0.95 / 0.95-0.97 / 0.97-0.99 / 0.99-1.00`) shows where the mass sits at a glance.

**READ IT EFFICIENTLY (numbers first — do NOT dig out full programs to start):**
1. glance at `novelty_sim_histogram` across recent windows;
2. `journal.novelty_near_threshold(results_dir, margin=0.02)` returns ONLY borderline rows
   (`abs(max_similarity - threshold) <= margin`) — ids + numbers, no code;
3. for JUST those borderline rows, fetch the TWO programs by id (`archive_query` on `candidate_id` +
   `most_similar_id`) and eyeball whether they are truly the same idea.

**WHICH WAY TO TURN IT:**
- borderline pairs look like the SAME idea, only numbers changed (small `diff_lines`, high
  `max_similarity`) yet were `accepted_novel` → the gate is letting near-dups proliferate → LOWER the
  threshold (or confirm `novelty_embed_mode:diff`);
- borderline pairs look GENUINELY DIFFERENT (larger `diff_lines`) yet were `dropped_worse` /
  `kept_better_evicted` → the gate is killing real variants → RAISE the threshold;
- `diff_lines` is the tie-breaker cosine alone misses: a large-diff edit caught as a near-dup is
  almost always a real new direction (raise); a 1–2 line edit accepted as novel is almost always a
  scalar tweak (lower).

A threshold change is a config-LEVER flip (log it as `work_audit ≥ 1` on the work score) — a knob,
not a code rewrite; the next relaunched cluster reads it.

## What never to do

- Never modify a `scripts/` file directly without the rewrite cycle — and never rewrite
  `cadence_policy.py` AT ALL (it is FOUNDATION; the wake/termination knobs are boot-only config).
- Never edit FOUNDATION files (schema, contract, evaluate, archive_record/query,
  diagnostics, repair_record, journal, harness, deep_research, the task's evaluate/init).
  Defer foundation ideas to the ending document.
- Never read `docs/archive/` as current guidance — it holds APPLIED/SUPERSEDED `FIX_PLAN_*` /
  `AUDIT_*` docs kept for historical reference ONLY (each describes a PAST state). The live
  guidance is `CLAUDE.md` + this file; a stale "PLAN ONLY" banner inside an archived plan is not a to-do.
- **Never ground a DISCOVERY technique that did not come from an in-interval triaged R1/R2 discovery stub.**
  `spawn_island.py` refuses to seed a new island without one,
  and `subagents/grounding-engineer.md` refuses a spawn prompt with no in-interval R1/R2 provenance;
  a stale stub from a prior interval does not satisfy them. (A path (ii) SIMILAR COMBINE grounding
  into an existing island is ordinary `archive_record` insertion with `parent_id`=closest — no
  `spawn_island`, not separately gated; the `work_discovery`/`work_grounding` split keeps it from
  padding the termination streak.)
- **Never treat a tournament / sort over your OWN hypotheses as discovery.** The only sanctioned
  Claude-native discovery is `subagents/archive-analyst.md` (R2); the only sanctioned multi-agent
  grounding is `subagents/grounding-engineer.md`. A bracket over self-invented ideas surfaces no
  new knowledge and produces no discovery stub.
- Never manually kill a slow in-flight Azure bg call (mutate/meta/DR) before the 3600s wall — cost
  books only on a terminal status, so a kill leaks unlogged-but-billed spend (the
  `run_window`/`--resume` recovery is different and allowed). On a refused verified pivot, switch to
  `grounding-engineer` rather than firing more Azure mutate calls.
- Never run the **per-window** inner mutation/fix loop in your own context — always call
  `mutate.py`. The ONLY exceptions are the two rare Claude powers: a Claude-native DISCOVERY pass
  (`archive-analyst`) and a hand-authored grounding (`grounding-engineer` / a hand-authored
  `patch_sys`/`patch_msg`) when the Azure model refuses a verified pivot.
- Never make two rewrites in one control-return. Never call the paid Azure deep research (R1) twice
  per stagnation cluster (a single R2 archive-analyst pass per cluster is the separate bound).
  Never let subagent output linger in your context.
- Never read a prior run's archive while running a new job.
- Never stop while a termination criterion is unmet.

## When in doubt

Do less — but "do less" applies to **interventions** (framework rewrites, config flips), NOT to
**discovery**. Your value is the rare code change the inner loop's hand-coded policies cannot
make, and the DISCOVERY round — R1 (Azure deep research, the default) or R2
(`subagents/archive-analyst.md`, the narrow fallback) — that brings in knowledge the search can't
invent. Discovery is exactly R1 or R2; never an ad-hoc tournament over your own hypotheses
(introspection cannot surface a technique absent from the archive — that needs R1). Once a
discovery round returns triaged directions, INCLINE TO TRUST it and ground each (up to 3): a
skipped framework rewrite is one less chance to break something, but a skipped grounding of a real
discovered technique is the documented failure this gate exists to prevent.

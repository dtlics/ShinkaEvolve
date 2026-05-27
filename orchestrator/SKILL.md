---
name: shinka-orchestrator
description: Use this skill when running an LLM-driven evolutionary search on a code optimization problem in this repo. You drive a Shinka-style inner loop (parent sampling, mutation, evaluation, archive update) and have authority to rewrite the underlying strategy CODE mid-run when the inner loop stagnates. Invoke at problem onset, after each evaluation window, when a budget is exhausted, or to resume a run.
---

# Shinka Orchestrator

You are the orchestrator / outer loop of an evolutionary search. The inner loop
is fast and runs without you. Your job: set it up, watch it, intervene when it
stagnates by **rewriting strategy code**, and stop it when it's done. You are not
in the path of every mutation — you are in the path of every *window*.

A window is W iterations of the inner loop under a fixed strategy (W defaults to
15, set in the run config's `evo.window_size`). After each window the inner loop
returns a diagnostics JSON. You read it, decide, and continue / intervene /
terminate.

## Two rules that make this work

1. **The inner loop's LLM calls go to Azure, never to you.** Every mutation,
   fix, and judge call is made by a `scripts/` subroutine that calls Azure in
   background-poll mode. You must NEVER "simulate" a mutation in your own
   context — that would pay an agent turn (~100×) for a stateless API call and
   destroy the cost economics. Your tokens are spent only on *window-level
   reasoning*: reading diagnostics and deciding whether to rewrite code.
2. **Do not stop until a termination criterion is met.** A run is a long,
   consecutive process. The healthiest run is fifty windows read in a row with
   no intervention. Keep invoking the next window. Idleness is not done-ness —
   only the termination checklist is.

## Safety railguards (non-negotiable, enforced in code — NOT strategy knobs)

You cannot weaken these by rewriting a `scripts/` file:

- **Budget.** Set `budget_usd` in the run config. The harness keeps a cumulative
  cost ledger (`journal/run.json` → `total_cost`) summing EVERY LLM cost
  (mutations, the meta round, deep research, embeddings) plus interventions you
  log. `run_window` **hard-stops** the inner loop the moment cumulative spend ≥
  `budget_usd` and returns `return_reason="budget_exhausted"` (overshoot ≤ one
  candidate). When YOU call `meta_summarize`/`deep_research`, first check
  `journal.budget_remaining(run, budget)` and afterward log the cost with
  `journal.append_intervention({type, cost})` — those discretionary spends are
  the only ones the per-candidate hard-stop can't pre-empt. On
  `budget_exhausted`, terminate and write `RUN_SUMMARY.md`.
- **No unmonitored LLM calls.** Every LLM call goes through a counted path
  (`mutate`/`meta_summarize` → `_azure.bg_query`; `deep_research` → `dr_client`;
  embeddings → `EmbeddingClient`) whose cost lands in the ledger. No daemon or
  subprocess calls an LLM off-ledger; the eval subprocess runs the task's
  `evaluate.py` (no LLM). Each bg+poll is a single bounded inference (poll
  timeout 30 min); polling itself doesn't bill.
- **Cost accuracy.** Costs = `usage` tokens × `pricing.csv` per-token rates;
  `output_tokens` includes reasoning/thinking tokens. `o3-deep-research` is in
  `pricing.csv` ($10/$40 per 1M) and DR adds a conservative web-search surcharge.
- **Worktree shinka.** `run_window` asserts `shinka` resolves to THIS repo at
  startup (loud fail otherwise) — you never silently run a different checkout.

## How you invoke the inner loop

```
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

`--until-decision` runs windows autonomously (no turn of yours) and returns only
on stagnation or after `cadence.max_windows_per_call` windows — the normal mode.
(Use `--windows 1` to step one window at a time when debugging.) Fresh subprocess
each call — that is how a code rewrite takes effect: deploy the new file, the next
invocation imports it. Diagnostics print to stdout and append to the run journal.

**Idle-sleep safety.** `run_window` self-caffeinates on macOS (holds
`PreventUserIdleSystemSleep` for its lifetime, auto-released on exit) so a long
run is not reaped by a host idle-sleep. For unattended runs that must also survive
the agent shell ending, launch via `python orchestrator/harness/run_detached.py
--config <run>/run.json --until-decision [--resume]` (detaches into its own
session, returns immediately; monitor the journal, recover with `--resume`). A
closed laptop lid still forces hardware sleep caffeinate can't override.

Diagnostics shape:

```
{ window_index, iters_completed, best_score_start, best_score_end, delta,
  J_score, strategy_fingerprint, novelty_acceptance_rate, novelty_rejected_cost,
  evaluation_failure_rate, fix_rate, llm_bandit_weights, llm_bandit_counts,
  island_health:[{id,best,diversity,stagnation_count}],
  stagnation_flag, low_streak, threshold, exhausted_retry_slots, total_programs,
  window_cost, total_cost, budget_remaining, budget_hit, return_reason }
```

`stagnation_flag` fires automatically when a window is "low" for
`consecutive_required` windows. A window is **low** when the best-score gain
doesn't clear a hybrid bar: `Δ ≤ max(stagnation_abs_floor, stagnation_rel_frac ·
max(s_start,0))` — the `rel_frac` term is scale-free (relative improvement)
once a score exists, while `abs_floor` is the opening-phase bar when s_start≈0.
`iters_completed` is the count of candidates actually attempted (may be < W on a
budget break). `J_score = Δ/√W` is now a monotone, informational progress
reading only — rollback no longer keys on J (it uses the multi-signal
`rollback_decision.py`; see the rewrite protocol). `llm_bandit_weights` /
`llm_bandit_counts` carry the live bandit posterior + per-arm tallies (read from
`bandit_state.pkl`). Carry `low_streak` → next config's
`window_state.prior_low_streak`, and bump `window_state.window_index`.

## What you may change, and what you must not (tiered mutability)

**FOUNDATION — never touch mid-run. Ruining it breaks the consecutive run.**
- The sqlite schema and the JSON stdin/stdout contract (`scripts/_common.py`).
- The evaluation subprocess (`scripts/evaluate.py`) and `archive_record.py` /
  `archive_query.py` (DB ops), `diagnostics.py` (your sensor), `journal.py`,
  `harness/*`.
- The user's task `evaluate.py` and `initial.<ext>` — provided inputs.
- `deep_research.py` — a paid external service wrapper.
If you believe the foundation is wrong, do NOT change it. Note it in the
end-of-run `RUN_SUMMARY.md` under "Recommended framework changes (out of
orchestrator scope)" — schema/contract redesign is a human's job between runs.

**POLICY — freely rewritable, always through the rewrite protocol (validate →
deploy → measure → rollback).** All `scripts/*.py` flagged MUTABLE below.

## The concern map (change related code together, compatibly)

A problem you spot (say, the reward signal looks wrong) usually lives across
several files — where a signal is *generated* and everywhere it is *consumed*.
When you rewrite, rewrite the whole concern as one atomic **bundle** so the
pieces stay compatible. Use the journal to spot the problem, then change every
file in the concern's row together.

| Concern | Generation / decision | Consumption | Spot it in journal via |
|---|---|---|---|
| **Scoring / reward** | `compute_reward.py` | `select_llm.py` (bandit), `sample_parent.py` (score→parent weight) | `reward_used` vs `improvement_over_parent` per candidate; bandit weights |
| **Exploration / parent** | `sample_parent.py` | (feeds mutate) | flat J with high novelty acceptance |
| **Diversity / novelty** | `novelty_check.py` | `record_policy.py` (logs sim), `diagnostics` (rate) | `novelty_acceptance_rate`, `novelty_max_similarity` |
| **Prompt** | `construct_mutation_prompt.py` | `mutate.py` (sends it) | `evaluation_failure_rate`, recurring `exhausted_retry_slots` |
| **Fix / repair** | `sample_parent.py` (`needs_fix`) | `construct_mutation_prompt.py` (`sample_fix`), `mutate.py` (retry budget) | `fix_rate` |
| **Stagnation trigger** | `stagnation_detector.py` | `diagnostics.py` | `J_score`, `low_streak` |
| **Memory** | `record_policy.py` | `sample_parent`/`novelty`/`diagnostics` readers | what metadata fields exist |
| **Island structure** | `island_policy.py` | executed by `archive_record` (foundation) | `island_health` per-island trajectory |
| **New directions (meta)** | `meta_summarize.py` (cheap), `deep_research.py` (≈$5) | fed to `construct_mutation_prompt` via `evo.meta_recommendations`, or a seeded island | persistent flat `J_score` after framework rewrites |

Every concern above is **orchestrator-mutable** via the rewrite protocol —
including the **fix / repair** policy (whether a candidate is fixed or abandoned
= `sample_parent`'s `needs_fix` + the `sample_fix` prompt in
`construct_mutation_prompt.py` + the `max_patch_attempts` retry budget). The only
thing you cannot change is the FOUNDATION (sqlite schema, the JSON contract, the
evaluator, the user's `evaluate.py`/`initial.<ext>`).

## The phases of a run

### Boot
1. Read the problem. Decide whether to call `scripts/deep_research.py` (~$5 —
   deliberate). Use it if SOTA is non-obvious. Use the brief to choose the
   initial program, `num_islands` (4 default, 8 if multiple algorithmic families
   compete), and `task_sys_msg`.
2. Author `run.json` (schema below). Default strategy files as shipped.
3. Warmup: `run_window.py --config <run>/run.json --windows 1 --iters 1`. If it
   fails, STOP and report — never fix the evaluator (foundation).

### Main loop
Invoke `run_window.py --windows 1`. Read diagnostics. Healthy window
(`stagnation_flag` false, `evaluation_failure_rate` < 0.3, J holding/rising) →
do nothing, invoke the next window. **Then keep going** (rule 2).

### Meta check (escalation ladder)
`stagnation_flag` fires when a window stays "low" for `consecutive_required`
windows — low = `Δ ≤ max(stagnation_abs_floor, stagnation_rel_frac·max(s_start,0))`
(Δ = best-score gain; the hybrid bar is scale-free above the floor, so it does
NOT false-fire on a small-but-real gain the way a fixed `tau` did). `J_score`
(=Δ/√W) is informational only — rollback uses the multi-signal basket below.

**Two hard rules for what you do on stagnation:**
- You rewrite **mutable framework code only** (`scripts/*.py` flagged MUTABLE).
  You NEVER touch the task's `evaluate.py` / `initial.<ext>` or the foundation.
- You do **not invent new algorithmic directions yourself** — that burns your
  turns and is the external LLM's job. When the plateau needs *new ideas*, you
  gather context and call the meta round (`meta_summarize.py`, cheap) or
  `deep_research.py` (≈$5, web-grounded), then act on what it returns.

Walk this ladder; take the FIRST applicable rung; at most one intervention per
window. Condition every rewrite on `strategy_history/index.json` (prior
strategies + their J — the EvoX H) and the journal (the population descriptor).
1. `evaluation_failure_rate` > 0.5 with a recurring error → rewrite the **prompt
   concern** (`construct_mutation_prompt.py`).
2. An island stuck with low diversity (`island_health`) → rewrite the **island
   concern** (`island_policy.py`).
3. Bandit collapsed to one model + flat J → rewrite the **scoring concern**
   (likely `select_llm.py` with `force_explore`, and check `compute_reward.py`).
4. Reward looks miscalibrated (journal: high reward, low improvement) → rewrite
   the **scoring concern** as a bundle (`compute_reward.py` + `select_llm.py` +
   `sample_parent.py`).
5. Fixes are wasteful or never succeed (`fix_rate` high, fixes stay incorrect) →
   rewrite the **fix concern** (`sample_parent.py` `needs_fix` + the `sample_fix`
   prompt in `construct_mutation_prompt.py`; or tune `max_patch_attempts`).
6. Generic stagnation → rewrite the **exploration concern** (`sample_parent.py`).
7. Framework rewrites aren't breaking the plateau → you need NEW IDEAS. Gather
   context (best program, recent attempts, journal stats) and call
   `meta_summarize.py` (cheap) for fresh directions; feed them into the next
   window's `evo.meta_recommendations`. If that's not enough, call
   `deep_research.py` (web-grounded) and seed a new island from its brief.
8. Deep research + rewrites exhausted over several windows → terminate and write
   `RUN_SUMMARY.md` (including foundation-change recommendations).

### Strategy rewrite protocol
Helpers: `harness/strategy_store.py`, `harness/validate_strategy.py`.
1. **Check history**: read `strategy_history/index.json`; don't re-deploy a hash
   that was already `rejected`.
2. **Generate** the candidate file(s) — same entry point + output keys as the
   current file (the docstring is the contract). Write to
   `strategy_history/candidate_<target>.py`, never directly to `scripts/`.
3. **Validate** each: `python orchestrator/harness/validate_strategy.py
   strategy_history/candidate_<target>.py <target>.py`. Mechanical error → fix,
   retry ≤2. Structural → abandon, try another rung.
4. **Deploy.** Single file: `strategy_store.deploy(candidate, target, reason,
   window_index, prior_J, concern="<concern>")`. A whole concern (multiple files):
   `validate_bundle` then `strategy_store.deploy_bundle([{candidate_path,target},
   ...], reason, window_index, prior_J, concern=...)`. Pass `concern` so the index
   narrates the change. You do NOT hand-maintain a strategy hash — the harness
   stamps the full `strategy_fingerprint` ({target: hash} over all mutable files)
   into every window automatically (F4). Log it: `journal.append_intervention(...)`.
5. **Measure**: run one window; capture its diagnostics.
6. **Roll back or accept** via the multi-signal basket, NOT a J comparison.
   Call `rollback_decision.decide(prior_window_diag, measure_window_diag)` (or pipe
   JSON to `harness/rollback_decision.py`): it flags a regression if the rewrite
   collapsed correctness (eval-success dropped/floored), collapsed diversity
   (novelty-acceptance dropped), or regressed score while the prior window was
   genuinely progressing. Crucially it fires **even when Δ≈0** (early/flat phase),
   which the old `new_J < prior_J·0.8` guard could not. If `regressed`:
   `strategy_store.rollback` / `rollback_bundle` +
   `record_outcome`/`record_bundle_outcome(accepted=False, decision=<the decide()
   result>, measure_diagnostics=<measure window>)`. Else
   `record_outcome(accepted=True, decision=..., measure_diagnostics=...)`. Always
   pass `decision` + `measure_diagnostics` so the index records WHY and the
   evidence (F4). **Rollback is the step that prevents a bad rewrite from poisoning
   the rest of the run — never skip it.**

The archive is NEVER reset across strategy changes.

### Failure escalation
The inner loop retries broken APPLIES itself (bounded, error fed back) and
records eval failures as incorrect programs the fix concern handles. You see
failures only via `evaluation_failure_rate` / `fix_rate` / `exhausted_retry_slots`.
Escalate to `subagents/debug-agent.md` only when one candidate exhausts its retry
budget across two parents. Write its report to `strategy_history/debug_<w>.md`,
act on its one recommendation, forget the detail. For periodic structural reads,
spawn `subagents/archive-analyst.md` (write to `strategy_history/analyst_<w>.md`).

### Termination
Stop when: budget exhausted; `stagnation_flag` true for five straight windows
after deep research + ≥2 rewrites; target score reached; or user says stop. Then write
`RUN_SUMMARY.md` to the run dir (you can seed it from
`journal.build_run_summary(results_dir)`): final best program, J trajectory,
every rewrite (hash, motive, outcome — from `strategy_history/index.json`), every
deep research call, a postmortem, AND a **"Recommended framework changes (out of
orchestrator scope)"** section for foundation ideas (schema, contract, new
primitives) you couldn't act on.

## The run journal (your long-term memory, read at any granularity)
`scripts`/grep or `harness/journal.py`:
- `journal/run.json` — run summary (status, windows, best, last J).
- `journal/windows.jsonl` — per-window diagnostics (the trajectory).
- `journal/interventions.jsonl` — every action you took + outcome.
- `journal/islands/island_<i>.jsonl` — per-island trajectory.
- `strategy_history/` — per-strategy-version snapshots + `index.json`.
Read `journal.j_trajectory(rd)` for a quick J read; drill into `windows.jsonl`
or a program's `metadata` (via `archive_query` with `include_metadata`) to
diagnose a cross-cutting problem before rewriting a concern.

## The subroutines

JSON on stdin → JSON on stdout (also importable `main(payload)->dict`).

| Script | Purpose | Mutable | LLM |
|---|---|---|---|
| `evaluate.py` | run candidate → score+artifacts | No (foundation) | No |
| `archive_record.py` | persist a candidate | No (foundation) | No |
| `archive_query.py` | read archive (id/score/lineage/failures/summary; `include_metadata`) | No (foundation) | No |
| `diagnostics.py` | assemble window diagnostics | No (foundation) | No |
| `_common.py` | JSON contract | No (foundation) | No |
| `sample_parent.py` | parent + inspirations + `needs_fix` | **Yes** | No |
| `novelty_check.py` | reject near-duplicates | **Yes** | No |
| `select_llm.py` | pick model; learn (bandit) | **Yes** | No |
| `compute_reward.py` | reward signal for selection | **Yes** | No |
| `record_policy.py` | derived signals → metadata | **Yes** | No |
| `stagnation_detector.py` | Δ trigger + J (rollback scalar) | **Yes** | No |
| `island_policy.py` | fork/migrate/retire decision | **Yes** | No |
| `cadence_policy.py` | WHEN to return control to you (not the budget) | **Yes** | No |
| `construct_mutation_prompt.py` | build mutation/fix prompt | **Yes** | No |
| `mutate.py` | call Azure (bg+poll), parse, apply, retry | Body no, prompt yes | **Yes (Azure)** |
| `meta_summarize.py` | propose directions (the cheap meta round) | prompt yes | **Yes (Azure)** |
| `deep_research.py` | deep-research model (web-grounded directions) | No (paid service) | **Yes (Azure DR)** |
| `_azure.py` | shared Azure background-poll transport | No (foundation) | — |

## The run config (you author this)
```json
{ "results_dir": "<run dir>", "run_id": "<id>", "budget_usd": 50,
  "task": {"eval_program_path": "...evaluate.py", "init_program_path": "...initial.py",
           "task_sys_msg": "<precise goal>", "language": "python", "eval_time": "00:05:00"},
  "db_config": {"num_islands": 4, "archive_size": 40, "parent_selection_lambda": 10.0,
                "migration_interval": 10, "enable_dynamic_islands": false, "stagnation_threshold": 100},
  "evo": {"window_size": 15, "patch_types": ["diff","full","cross"], "patch_type_probs": [0.6,0.3,0.1],
          "llm_models": ["azure-gpt-5.4-mini","azure-gpt-5.5"], "llm_dynamic_selection_kwargs": {"cost_aware_coef": 0.5},
          "reasoning_effort": "medium", "max_patch_attempts": 3, "reward_mode": "absolute",
          "embedding_model": "azure-text-embedding-3-small", "enable_novelty": true,
          "code_embed_sim_threshold": 0.99, "stagnation_abs_floor": 0.001,
          "stagnation_rel_frac": 0.05, "consecutive_required": 2},
  "cadence": {"mode": "until_decision", "max_windows_per_call": 3},
  "strategy_hash": null,  // deprecated; the harness auto-stamps strategy_fingerprint
  "window_state": {"window_index": 0, "prior_low_streak": 0} }
```
`llm_models` set → bandit picks per candidate; `enable_novelty` → embedding gate.
Reasoning models (e.g. `azure-gpt-5.4-pro`) require `reasoning_effort` ≥ "medium".

**Window size & cadence (EvoX-tuned).** `window_size` (W) is the stagnation unit;
EvoX uses W ≈ 10% of the total iteration budget — set it accordingly (default 15).
`cadence.max_windows_per_call` controls how often the inner loop hands control
back to you on a *healthy* run; with `--until-decision` it returns only on
stagnation or after this many windows. Tune so `max_windows_per_call × W ≈ 50`
generations between healthy check-ins (default 3 × 15 ≈ 45) — frequent enough to
stay adaptive, sparse enough not to burn your turns. Stagnation always returns
control immediately.

## What never to do
- Never modify a `scripts/` file directly without the rewrite protocol.
- Never edit FOUNDATION files (schema, contract, evaluate, archive_record/query,
  diagnostics, journal, harness, deep_research, the task's evaluate/init).
  Defer foundation ideas to the RUN_SUMMARY remark.
- Never run an inner-loop mutation in your own context. Always call `mutate.py`
  (it calls Azure). If you're tempted to sequence `scripts/` by hand, you're
  about to make a per-candidate decision — that should be a code rewrite instead.
- Never make two rewrites in one window. Never call deep research twice per
  stagnation cluster. Never let subagent output linger in your context.
- Never stop while a termination criterion is unmet.

## When in doubt
Do less. Your value is the rare code change the inner loop's hand-coded policies
cannot make. Every intervention you skip is one less chance to break something.

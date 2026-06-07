# NOTES.md — what's done, what's deferred

> **Loop redesign (2026-06).** The run loop is now: **warmup** (hands-on oversight in a
> throwaway db) → background-launched **window-cluster** woken on a **work-score taper**
> (uncapped; bounded by budget / termination / stagnation) → an **automatic per-window
> meta round** that writes **per-island briefs** (islands differentiate by default) →
> **framework-audit + DR checks** on one shared control-return rhythm → **termination**
> (5 consecutive STAGNANT + intervened control-returns, harness-computed) → **end-of-run ending document +
> structured archive**. Two named roles: ORCHESTRATOR (operational/critical-path) and
> OUTER-LOOP/FRAMEWORK-AUDIT (improvement/tapering). The older "round" notes below predate
> this and describe the prior windows-only loop — they are kept as history; `SKILL.md` /
> `CLAUDE.md` are the live teaching. (De-jargoned: the EvoX/WS-n/J-score-formula framing in
> the old notes is superseded — the progress signal is the best-score gain vs the
> low-window bar; rollback uses the multi-signal `rollback_decision.py`.)

> **Post-audit fixes (2026-06-03; see `docs/archive/2026-06-03/AUDIT_LOGIC_WORKFLOW_20260603.md` + `…/FIX_PLAN_20260603.md`).**
> Foundation + strategy fixes landed: **C1** a framework revert now rewinds the strategy
> `.py` too (was DB+bandit only); **H10** the ledger is recomputed from streams (never
> lowered) if `run.json` is corrupt at revert; **H5** novelty now EVALUATES a near-duplicate
> and KEEPS THE BETTER of the pair (evicts the worse; tombstoned rows no longer block);
> **H1/H11/M13** the meta round is per-island + code-grounded and emits direction→program
> assignments that make the inspiration SAMPLER direction-oriented; **H2** archive eviction
> is island-aware with a per-island floor (default 3), migration is a per-run knob; **H9**
> `use_text_feedback:false` is a COMPLETE spoil suppression (fix + sampled-ancestor + meta);
> **termination** is harness-computed (`stagnation_intervention_exhausted` over canonical
> `control_return` rows; the "≥1 DR" requirement is dropped). `orchestrator/tests` green.

## Code ↔ doc consistency contract (P9-T0)

The rule: **no behavior a doc describes that the code doesn't do, and no code behavior the
docs don't explain.** Every code change is paired with its doc change; the doc-lint test
(`test_skill_doc_teaches_run_loop_and_roles`) is the enforcement, keyed on durable
behavioral language (never a codename). `orchestrator/SKILL.md` is the **single real file**
— `.claude/skills/shinka-orchestrator/SKILL.md` is a symlink to the same inode, so one edit
updates both views.

| Behavior | Code that does it (file:function) | Doc that teaches it |
|---|---|---|
| Crash-durable ledger / budget hard cap | `journal.py:_write_json_atomic` + `read_run` recompute; `run_window.main` budget break | SKILL "Safety railguards" |
| Truthful recording (apply-exhausted) | `run_window._run_one_candidate` (`mut.get("applied") is False` branch); `mutate.py` return | SKILL "Failure handling" |
| Per-step trace + warmup | `journal.log_step`; `run_window` `--warmup`/`--trace-steps` + `_trace` sinks + `cleanup_warmup` | SKILL "Warmup" |
| Sensor fields (errored_fraction, model_collapse, failure types) | `diagnostics.py:main` + `_model_collapse` | SKILL "Diagnostics" |
| Per-window meta: per-island CODE-grounded blocks → rich `islands` directions + program assignments → direction-oriented sampler (H1/H11/M13) | `meta_summarize.py` (`islands`/`_build_user_msg`); `run_window._one_window` meta block → `island_brief.py` `structured_json`; `sample_parent` reads it | SKILL "The automatic meta round" + islands note |
| Uncapped work-score taper | `journal.recent_work_score`/`work_low_streak`; `cadence_policy.main`; `run_window` cluster loop | SKILL "The run loop" / "The taper" |
| Repair mode (trigger / append / two-strike tombstone / release) | `sample_parent` `select:"errored"`; `dbase.append_program_error`/`tombstone_program`; `repair_record.py`; `run_window` gate | SKILL "Failure handling" |
| Boot guard + COMPLETE spoil mitigation (fix + sampled-ancestor + meta channels — H9) | `run_window.main` sentinel guard; `construct_mutation_prompt` central sanitizer + meta `use_text_feedback` gate | SKILL "Boot" + `use_text_feedback` lever |
| Snapshot/measure/revert: FULL rewind of CODE + DB + bandit (C1), ledger preserved/recomputed-on-corrupt (H10), fail-closed + counts-collapse | `strategy_store.snapshot_state`(`prior_code`)/`restore_state`; `rollback_decision.decide` | SKILL "The framework-audit rewrite cycle" |
| Novelty KEEP-THE-BETTER (eval near-dup → keep better, evict worse; tombstoned skipped) — H5 | `novelty_check.py` (`most_similar_score` + tombstone-skip); `run_window` post-eval resolve → `repair_record` tombstone | SKILL novelty rows / `code_embed_sim_threshold` |
| Island-aware archive eviction (per-island floor) + migration knob — H2 | `dbase._pick_archive_victim`/`_update_archive_*`; `DatabaseConfig.archive_floor_per_island`/`migration_rate` | SKILL islands note / levers |
| DR refusal (no crash) | `dr_client.run_dr_call` (`.cost` on raise); `deep_research.main` try/except | SKILL "Deep research" |
| Termination (harness-computed, auto-finalized) + end-of-run archive | `journal.termination_streak` over canonical `control_return` rows; `run_window` → `stagnation_intervention_exhausted`; `journal.build_run_summary`/`finalize_run`/`archive_run` | SKILL "Termination + end of run" |


The system works end-to-end offline (see `orchestrator/tests/`: parity, smoke,
improvements — all green). [HISTORICAL] This round followed the EvoX-derived protocol
(window → J → stagnation → validate → deploy → measure → rollback); the current loop
supersedes it — see the banner at the top of this file and `SKILL.md` for the live design.

## Third round (cadence + eval + Azure-only prune)
- **Run-until-decision cadence.** `run_window --until-decision` runs windows
  autonomously and returns to the orchestrator only on stagnation or a window
  cap (`cadence.max_windows_per_call`) — few Claude turns when healthy, prompt
  response on stagnation. A rewrite resets `low_streak`, so a new strategy gets
  ≥`consecutive_required` windows before it can re-flag.
- **Eval timeout enforced + reported.** `evaluate.py` now monitors WITH the
  `eval_time` cap (killing overruns) and reports a clear `EvaluationTerminated:
  ...` error + `timed_out` flag — usable by the fix policy. Timed-out candidates
  are recorded incorrect and addressed by fix-mode if they become the fix target.
- **Azure-only prune.** Removed `async_runner` (the old 6962-line runner), the
  agentic-proposer layer, meta/novelty/prompt-evolver/DR-summarizer modules, the
  async DB + prompt DB, all non-Azure providers (+ google_genai/local_openai),
  plots/webui/docs/cli, extra examples, the old `shinka_run` launch path + skill,
  and the old test suite. `shinka/` ≈ halved. The repo stays reusable for new
  tasks via the orchestrator + the kept authoring skills (setup/convert/inspect),
  which now emit an `orchestrator_run.json` instead of the old `run_evo.py`.
  NOTE: the global editable install may still backfill deleted modules from the
  main repo; the worktree itself is self-consistent (no refs to deleted code).

## Resolved in the second (improvement) round
- **Inner-loop LLM → Azure background-poll.** `mutate.py` now calls Azure via
  `responses.create(background=True)` + poll (cost computed from usage), the
  resilient transport `deep_research` uses; legacy sync client only for non-Azure
  providers. The orchestrator never spends its own tokens on a mutation.
- **Reward is mutable.** `compute_reward.py` (scoring concern, generation half)
  feeds the bandit; `select_llm.py` + `sample_parent.py` are the consumption
  halves. The concern map in SKILL.md tells the orchestrator to change them
  together.
- **Memory is mutable.** `record_policy.py` decides which derived signals
  (improvement, reward_used, novelty sim, transport, fix_mode…) get written to
  the program `metadata` blob; `archive_query` surfaces metadata so consumers and
  the orchestrator can read them. The sqlite schema stays immutable.
- **Fix-mode wired + mutable.** `sample_parent.needs_fix` → `construct_mutation_prompt`
  uses `sample_fix`; `mutate` retries APPLY failures with error feedback; `fix_rate`
  is in diagnostics.
- **Hierarchical run journal.** `harness/journal.py` writes greppable JSON/JSONL at
  four granularities (run / windows / interventions / per-island) + a
  `build_run_summary` draft. The orchestrator reads at whatever level it needs.
- **Concern-bundle rewrites.** `strategy_store.deploy_bundle`/`rollback_bundle`
  change a whole concern's files atomically (one validate → deploy → measure →
  rollback). `validate_strategy.validate_bundle` gates them.
- **Tiered mutability + concern map + escalation ladder** documented in SKILL.md;
  **standing orchestrator role + "don't stop"** in CLAUDE.md; skill registered
  repo-locally (`.claude/skills/shinka-orchestrator` → `orchestrator/`).

## Fourth round (safety railguards + cost accuracy + cadence mutable)
- **Budget hard-cap in code.** `journal` is a cost ledger (`total_cost`) summing
  every LLM cost (mutate/meta/deep-research/embeddings) + logged interventions;
  `run_window` hard-stops at `budget_usd` (`budget_exhausted`). Not a strategy
  knob — a rewrite can't disable it. Tested (`test_budget_hardstop`).
- **Cost accuracy.** Added `o3-deep-research` to `pricing.csv` ($10/$40 per 1M);
  fixed `run_dr_call` to compute token cost from usage (was hard-coded 0);
  `deep_research` adds a conservative web-search surcharge; **embedding cost is
  now captured** (was discarded in `_embed`). bg+poll `output_tokens` includes
  reasoning tokens, so thinking is billed.
- **No unmonitored LLM calls** — audited: every call (`_azure.bg_query`,
  `dr_client`, `EmbeddingClient`) returns cost into the ledger; the eval
  subprocess runs the task `evaluate.py` (no LLM); bg+poll is one bounded
  inference (30-min poll cap), polling doesn't bill.
- **Cadence is mutable.** The WHEN-to-return-control decision moved to a mutable
  `cadence_policy.py`; the budget railguard stays hard-coded.
- **Worktree-shinka guarantee.** `_common` forces the repo root first on
  `sys.path` + `run_window` asserts `shinka` resolves here (loud fail). The
  editable install is no longer needed (eval subprocess gets a repo-root
  PYTHONPATH); it can be removed without affecting the orchestrator.

## Still deferred (revisit after live runs prove the basics)
- **Richer multi-step meta.** `meta_summarize.py` is the extracted, mutable "meta
  round" — a single Azure call proposing weighted `directions` + a `failure_note`
  (→ `evo.meta_directions` / `evo.meta_failure_note`, sampled per-gen). Shinka's
  original 3-step cycle
  (drift→cache→summarize, `shinka/core/summarizer.py`) was pruned; re-introduce the
  extra stages inside `meta_summarize.py` only if a single call proves too shallow.
- **Prompt evolution** (upstream `shinka/core/prompt_evolver.py`, pruned here) —
  the orchestrator subsumes that role for strategy code; re-introduce a mutable
  task-prompt evolver only if evolving the prompt (not just the code) proves worth it.
- **Reasoning-effort as a bandit arm — SHIPPED, no longer deferred.** An
  `llm_models` entry may be `"model@effort"` and the bandit learns each
  (model, effort) arm separately (`run_window` splits the arm for the call).
- **Per-island progress signal.** `stagnation_detector` computes a global progress
  reading; a per-island version would let interventions target the stalled island.
- **`archive_query` full-table scans** for top_n/summary on very long runs —
  add indexed queries if archives reach tens of thousands of programs.
- **`island_policy` retire execution.** It recommends `{spawn, migrate, retire}`;
  shinka executes spawn/migrate, but has no native "retire island" path — wiring
  retire is future work.
- **Non-atomic bandit `save_state` (known bug — fix between runs).** `BanditBase.save_state`
  (`shinka/llm/prioritization.py:194-200`) does `open(path, "wb")` + `pickle.dump`; the open
  truncates first, so a process kill mid-write leaves a corrupt pickle. On the next run
  `select_llm._make_bandit` (`orchestrator/scripts/select_llm.py:57-60`) swallows the load
  failure (`except Exception: pass`) and silently restarts from a uniform posterior — losing
  every learned (model, effort) preference — and the "logged by caller" the comment promises
  is never wired. Fix: atomic write (tmp + fsync + `os.replace`) in `save_state`, and actually
  log the discard. (Surfaced by the 2026-06-03 reports, now in `docs/archive/2026-06-03/`.)

## Accepted limitations — deliberate no-ops (post-2026-05-29 design audit)
The design audit folded into commit `f719bdb` intentionally left these UNFIXED — each
is negligible or only fires on a path the framework does not ship by default. A future
agent should NOT "rediscover" and over-engineer them:
- **Embedding cost is billed to a bandit arm on a novelty REJECT but not on an ACCEPT** —
  a <1% cost asymmetry that can make a duplicate-prone arm look marginally cheap.
- **A novelty-rejected slot bumps the arm's submitted count with no matching completed**,
  so its UCB exploration bonus deflates slightly on rejects (the reward floor + the
  rejected-slot cost feed handle the larger cheap-arm-entrenchment drivers; this
  visit-count side-channel is left).
- **Scheduled migration stamps the run's max generation, not the triggering one** —
  dormant (`migration_rate` ships at 0).
- **`enable_web_search` is silently ignored on the legacy non-Azure provider path** —
  dead in this Azure-only fork (documented in `mutate.py`, not warned).
- **Sequential harness vs. async pipeline.** `run_window` runs one candidate at a
  time (clean reference order); the real `ShinkaEvolveRunner` is concurrent.
  Parallelize within a window if wall-clock becomes the bottleneck.

## Design rationale the docs lean on (one-liners)
- **Why model-collapse is judged on COUNTS-share, not weights:** a single bandit arm's
  weight is capped at `1 − epsilon` (≈0.80), so a weight can never reach a collapse
  threshold — the submitted-COUNT share is the only faithful collapse signal
  (`diagnostics._model_collapse`; the `rollback_decision` weights-arm is legacy/near-unreachable).
- **Why rollback FAILS CLOSED:** a measure window with no / NaN / crashed (non-zero exit or
  unparseable output) data is treated as worst-case and reverted, so a broken rewrite can
  never be silently accepted (`rollback_decision.decide`).
- **Scalability is deliberately deferred:** per-generation novelty is O(N) cosine over
  CACHED embeddings of a SIZE-CAPPED archive with bounded per-island membership, so it's
  cheap and left as-is; evaluation is serial. Revisit only at a full inspection (trigger:
  `archive_size` raised enough to bottleneck novelty or serial eval) — it's listed as a
  standing future-fix candidate in the end-of-run ending document.
- **`timed_out` is a REAL harness-synthesized field:** `orchestrator/scripts/evaluate.py`
  sets it when a candidate's runtime reaches the eval-time limit, and the active cnot task's
  per-trial timeouts feed it — so `timeout_count` is genuinely non-zero, not a doc/code lie.

## Foundation = out of the orchestrator's scope (by design)
The sqlite schema, the JSON contract (`_common.py`), the evaluator subprocess,
`archive_record`/`archive_query`/`diagnostics`/`journal`, the user's
`evaluate.py`/`initial.*`, and `deep_research` are **immutable**. The orchestrator
records foundation-change ideas (e.g. a schema column it wishes existed) in the
end-of-run `RUN_SUMMARY.md` under "Recommended framework changes" — a human pass
between runs, not an outer-loop action. This is what keeps a run consecutive and
unbreakable.

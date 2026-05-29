# NOTES.md — what's done, what's deferred

The EvoX-derived system works end-to-end offline (see `tests/`: parity, smoke,
improvements — all green). We followed the EvoX protocol (window → J → stagnation
→ validate → deploy → measure → rollback), not a homegrown one.

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
- **Reasoning-effort as a bandit arm — SHIPPED (WS6), no longer deferred.** An
  `llm_models` entry may be `"model@effort"` and the bandit learns each
  (model, effort) arm separately (`run_window` splits the arm for the call).
- **Per-island J-score.** `stagnation_detector` computes a global J; a per-island
  J would let interventions target the stalled island.
- **`archive_query` full-table scans** for top_n/summary on very long runs —
  add indexed queries if archives reach tens of thousands of programs.
- **`island_policy` retire execution.** It recommends `{spawn, migrate, retire}`;
  shinka executes spawn/migrate, but has no native "retire island" path — wiring
  retire is future work.
- **Sequential harness vs. async pipeline.** `run_window` runs one candidate at a
  time (clean reference order); the real `ShinkaEvolveRunner` is concurrent.
  Parallelize within a window if wall-clock becomes the bottleneck.

## Foundation = out of the orchestrator's scope (by design)
The sqlite schema, the JSON contract (`_common.py`), the evaluator subprocess,
`archive_record`/`archive_query`/`diagnostics`/`journal`, the user's
`evaluate.py`/`initial.*`, and `deep_research` are **immutable**. The orchestrator
records foundation-change ideas (e.g. a schema column it wishes existed) in the
end-of-run `RUN_SUMMARY.md` under "Recommended framework changes" — a human pass
between runs, not an outer-loop action. This is what keeps a run consecutive and
unbreakable.

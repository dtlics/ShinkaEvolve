# ShinkaEvolve Orchestrator — Test Run Findings

**Run**: `cnot_grid_synth`, test run to shake out bugs before trusting the system.
**Config**: budget $20 hard-cap, models `[azure-gpt-5.4-mini, azure-gpt-5.5]`,
`reasoning_effort=low`, `embedding_model=azure-text-embedding-3-small`,
`window_size=10`, `num_islands=4`. Run dir:
`tasks/cnot_grid_synth/results/testrun/`.
**Deliverable**: this bug list (not a high score).

Severity legend: **[BLOCKER]** stops the run · **[BUG]** wrong behavior ·
**[ROUGH]** confusing / friction · **[DOC]** stale docs · **[COST]** cost-ledger.

---

## Fix status (updated 2026-05-27)

Load-bearing findings have been **fixed in this branch** (to be merged to main):

| Finding | Status | Change |
|---|---|---|
| F1 `.env` in worktree | **FIXED** | `shinka/env.py` now walks parent dirs to the nearest `.env` (worktree inherits main-repo creds). Verified: creds load with no local `.env`. |
| F3 stale docs | **FIXED** | Fast-forwarded onto main's `65880d9` doc purge; cnot README + others point at the orchestrator harness. |
| F8 `iters_completed` | **FIXED** | `run_window` reports the real attempted-candidate count. Verified: 4 (not 10) on a budget stop. |
| F9 bandit weights blind | **FIXED** | `select_llm` gained a read-only `weights` mode; `run_window` feeds the live posterior + per-arm tallies into diagnostics (`llm_bandit_weights` + new `llm_bandit_counts`). |
| F11 asyncio noise | **FIXED** | `_azure` closes the client inside the coroutine. Verified: no traceback after a real call + GC. |
| F16 J discontinuity | **FIXED** | `J = Δ/√W` (dropped the `log1p` scale term) — monotone, continuous. |
| F12 `tau` mis-scaled | **FIXED** | New hybrid trigger `Δ ≤ max(stagnation_abs_floor, stagnation_rel_frac·max(s_start,0))`; starter config + SKILL updated. Verified: the real run's productive windows no longer false-flag. |
| F14 rollback inert at J≈0 | **FIXED** | New `harness/rollback_decision.py` multi-signal basket (correctness/diversity/score); fires even at Δ≈0. SKILL protocol step 6 updated. Verified on 3 cases. |
| F2 DR creds absent | **FIXED** | `AZURE_DR_*` written to the main repo `.env`; `get_dr_async_client()` builds with `base_url=.../openai/v1`. |
| F7 bootstrap seed not embedded | **FIXED** | `_bootstrap_initial` embeds the seed (novelty on) + folds the cost into the ledger. Verified: seed embedding stored; novelty now actually rejects early duplicates (was a no-op). |
| F13 novelty-rejection waste | **FIXED (surfaced)** | New `novelty_rejected_cost` diagnostics field tracks spend on rejected (un-evaluated) slots so the orchestrator can act. (See note below — it's *predominantly*, not purely, novelty.) |
| F15 hand-maintained `window_state` | **FIXED** | `run_window --resume` reads `window_index`/`low_streak` from the journal. Verified: resumed to window 1 from a stale config. |
| F10 island diversity is a count | **FIXED (made mutable)** | Metric definition moved from immutable `diagnostics.py` into mutable `island_policy.island_health()` (still a toy count for now) so the orchestrator can later evolve it. |
| F4 strategy-log self-containment | **FIXED** | `strategy_store.current_fingerprint()` ({target:hash} over 11 mutable files) auto-stamped into every window + run.json (replaces the ambiguous single `strategy_hash`); `deploy(concern=)` + `record_outcome(decision=, measure_diagnostics=)` make `index.json` narrate why/outcome. Snapshots already hold content. |
| F5 `--windows` ignored under until_decision | **FIXED** | Explicit `--windows N` now forces bounded mode. Verified: `--windows 1` → `windows_done`. |
| F6 stagnation on 2 tiny windows | subsumed by F12 | Was an artifact of the `--iters 1` warmup + coarse `tau`; the new scale-aware hybrid trigger no longer false-fires on real progress. |
| F17 long runs killed mid-run | **FIXED** | Root cause: macOS host **idle-slept** (battery, and AC where `pmset sleep`=1 min) during long gaps and reaped the run process — not a code crash (no clean-exit line; the eval/loop ran fine while awake). Fix: `run_window` self-caffeinates (holds `PreventUserIdleSystemSleep`, auto-released via `caffeinate -w <pid>`); new `run_detached.py` adds session-detach for unattended runs; `--resume` recovers either way. Verified live (assertion held 100 s+; resumed run continued from window 1). |

**All findings F1–F17 are now addressed in this branch.**

### F17 detail — the mid-run kill (root-caused, not "run shorter")
Two long background runs died mid-run. Investigation:
- The run log had a `START` but **no `EXIT`** line → the whole shell+python was
  terminated by an external **signal**, not an internal crash (a crash would still
  run the trailing `echo EXIT`).
- `pmset -g log` showed the host repeatedly entering sleep during the idle gap:
  *"Entering Sleep state due to 'Sleep Service Back to Sleep' … Using Batt"*.
- `pmset -g custom`: `sleep 1` on **both** AC and battery (system idle-sleep after
  1 min) — so "just keep it plugged" would **not** have prevented it; the macOS
  `displaysleep` GUI setting is unrelated (that's display-off, not system sleep).
- The run process was a child of the agent's background-bash shell, so when the
  host slept the tracked job was reaped.

Fix (verified live): `run_window._hold_no_idle_sleep()` spawns
`caffeinate -i -m -w <own pid>` at CLI startup → asserts PreventUserIdleSystemSleep
for the run's lifetime and **auto-exits when run_window exits** (even on SIGKILL —
no orphaned assertion). Confirmed: the assertion was held 100 s+ on **battery**
while the run advanced gen 9→15. `run_detached.py` additionally detaches the run
into its own session (`start_new_session`, `PPID=1`) so it survives the agent
turn/app ending, and sets `SHINKA_CAFFEINATED=1` so the inner run_window doesn't
double-assert. Recovery from any kill is `run_window.py --resume` (archive is
written per candidate; the killed verification run resumed from window 1 with no
lost work). CLAUDE.md + SKILL.md updated so the next agent uses this by default.
Residual hardware limit: a closed laptop lid forces clamshell sleep caffeinate
cannot override — keep the lid open (or on AC) for long unattended runs.

**F13 correction (per review):** the ~49% gap is *predominantly* novelty rejection,
not strictly "purely" it — the ledger−bandit gap also includes embedding cost
(negligible here, ~$0.0002) and any failed-apply slots that then get
novelty-rejected. In this run eval/apply failures were ~0, so novelty rejection
dominated. The fix surfaces the rejected cost rather than asserting a single cause.

11/11 orchestrator tests pass after all the changes (parity + budget hardstop intact).

---

## Phase 0 — Setup & environment

### F1 [BLOCKER, worked around] `.env` is invisible when running from a git worktree
`shinka.env.load_shinka_dotenv` only looks for `.env` in two places:
`package_root` (= `Path(shinka/env.py).parents[1]`, i.e. the repo/worktree root)
and `cwd`. When the orchestrator runs inside a git worktree
(`.claude/worktrees/<name>/`), **neither** path holds a `.env`: it is gitignored,
so it is absent from the worktree checkout, and the canonical credentials file at
the **main** repo root (`/Users/dantongli/GIthub/ShinkaEvolve/.env`) is never
discovered. Result: all `AZURE_*` vars are unset and every live LLM call would
fail with an auth error.
- **Repro**: from the worktree, `python -c "import shinka, os; print(os.environ.get('AZURE_API_ENDPOINT'))"` → `None`.
- **Workaround used**: `cp /Users/dantongli/GIthub/ShinkaEvolve/.env .env` into the
  worktree root (it is gitignored there too, so git stays clean).
- **Fix**: `load_shinka_dotenv` should walk parent directories (à la
  `dotenv.find_dotenv`) so a worktree inherits the main repo's `.env`; or the
  orchestrator launch should resolve `.env` from the git common dir. This will
  bite *every* worktree-based run, which is exactly how this harness is launched.

### F2 [ROUGH] Deep-research credentials are absent from `.env`
`.env` contains only `AZURE_OPENAI_API_KEY`, `AZURE_API_ENDPOINT`,
`AZURE_API_VERSION`. The DR trio (`AZURE_DR_ENDPOINT`, `AZURE_DR_API_KEY`,
`AZURE_DR_API_VERSION`) is missing, so `orchestrator/scripts/deep_research.py`
(and `evo.dr_model` default) would fail if invoked. Not exercised in this run
(budget $20, no DR planned), but a run that escalates to deep research would hit
this only at the moment of the ~$5 call. Worth a preflight check.

### F3 [DOC] Task README references removed `run_evo.py` / `shinka_run`
`tasks/cnot_grid_synth/README.md` "How to run" section and the Files table point
at `tasks/cnot_grid_synth/run_evo.py` and the `shinka_run` CLI. Both were removed
in the orchestrator rewrite + Azure-only prune; `run_evo.py` does not exist. A new
user following the README's run instructions would be stuck. README needs a pass
to point at the orchestrator harness (`orchestrator/harness/run_window.py`).

### F4 [ROUGH] `strategy_hash` config field is ambiguous
`run.json` carries a single `strategy_hash`, but there are 11 mutable strategy
files and `strategy_store.current_hash(target)` hashes exactly one. Which file
should the field track, especially after a *bundle* deploy touching several? It
is only informational (flows through to `diagnostics.current_strategy_hash`), so
I left it `null`, but the SKILL's "update run.json's strategy_hash" instruction is
under-specified.

### What worked in setup
- `shinka` correctly resolves to the worktree (`_common.py` forces the worktree
  to the front of `sys.path`); `assert_worktree_shinka` would pass.
- Evaluator smoke test matched the README exactly: `correct=true`,
  `combined_score=0.0`, `slope≈4.8527`, `r²≈0.99998`. Baseline cache built in ~22s.
- Eval subprocess interpreter resolves via `sys.executable` (no `conda_env`
  needed in the config) **provided** `run_window.py` is launched with the shinka
  env python — which is the documented invocation.

---

## Phase 1 — Warmup (live)

Ran `run_window.py --config <run> --windows 1 --iters 1`. Exit 0, ~2 min, empty
stderr. The full per-candidate live path works:

- **Azure background-poll mutation path works.** Both candidates recorded
  `transport="background"`, `mutation_attempts=1` (patch applied first try). The
  bandit exercised both models: gen 1 → `azure-gpt-5.5` ($0.065015, diff patch,
  2 hunks, name `commuting_cx_cancellation`); gen 2 → `azure-gpt-5.4-mini`
  ($0.020529, full patch, name `dualpath_kms`). Prompt → LLM → `<NAME>`/parse →
  apply → eval → record all functioned.
- **Cost ledger reconciles exactly.** `total_cost=0.08569982` =
  $0.065015 + $0.020529 (mutation `api_cost`) + ~$0.000156 (2 embeddings). The
  per-window `window_cost` (0.0205883) tracked the last window only and the
  ledger summed across windows correctly.
- Both candidates `correct=1`, `combined_score=0.0` (no slope improvement yet —
  expected for early low-effort mutations on a genuinely hard problem).

### F5 [BUG] `--windows 1` / `--windows N` is silently ignored when the config sets `cadence.mode: "until_decision"`
I passed `--windows 1`, but the run executed **2 windows** (`windows_run=2`,
`return_reason="stagnation"`). Cause: the config file sets
`cadence.mode="until_decision"`, and `main()` computes
`until_decision = cadence.get("mode")=="until_decision"`, which takes the
`while True` branch and **never reads `cfg["windows"]`**. The CLI sets
`cfg["windows"]=1` but it is dead in that branch. Only `--iters 1` took effect
(it sets `window_size`). So a "1-window warmup" is impossible without editing the
config to drop `cadence.mode`. Fix: when `--windows` is passed explicitly on the
CLI, it should override `cadence.mode` (force the bounded branch), or `--windows`
should cap the until_decision loop.

### F6 [ROUGH] Stagnation fired after 2 tiny (size-1) windows at score 0
With `--iters 1`, each window was a single candidate; two flat-score windows
(`delta=0 < tau=0.05`) tripped `low_streak=2 == consecutive_required` →
`stagnation_flag=true` on the *second generation of the run*. This is an artifact
of size-1 windows, but it highlights that on a hard task where score legitimately
sits at 0 for a long warm-up, the delta-trigger calls "stagnation" almost
immediately. The orchestrator must distinguish "early-run flat" from "true
stagnation" by hand; the trigger gives no help. (Re-evaluated with `window_size=10`
in the main run below.)

### F7 [ROUGH, minor] `novelty_n_compared=0` for the first candidates
Both candidates recorded `novelty_max_similarity=0.0, novelty_n_compared=0`, i.e.
the novelty gate compared against **zero** prior embeddings even though the seed
populates all 4 islands. The bootstrap seed is recorded without an embedding
(`_bootstrap_initial` never calls `_embed`), so within-island there is nothing to
compare the first mutant against. Novelty gating is effectively inert until a few
embedded candidates accrue per island. Low impact, but means the
`code_embed_sim_threshold` dedup does nothing early.

### Not a bug (initially looked like one)
`total_programs=6` after "1" warmup looked wrong, but is correct: bootstrap
replicates the seed into **all 4 islands** (4 rows, `parent=None`) + 2 candidate
mutants = 6. Standard shinka island seeding.

## Phase 1b — Budget hard-stop (verified via mock mode)

A live $20 burn would take hours (the bandit favors the cheap `gpt-5.4-mini` at
~$0.02–0.07/candidate, so $20 ≈ many hundreds of candidates). To exercise the
railguard directly I ran an **offline mock** (separate results dir
`/tmp/budget_mock_test`, `mock.enabled=true`, `mutate_cost=$0.6`, `budget_usd=2.0`,
`window_size=10`). Result — **the hard-stop works**:

- `return_reason="budget_exhausted"`, `budget_hit=true`.
- 4 candidates ran (total_programs=6 = 2 island seeds + 4); the 5th was refused.
- `total_cost=2.4` vs cap `2.0` → overshoot $0.4, which is **< one candidate's
  cost ($0.6)**, matching the documented "overshoot ≤ one candidate".
- `budget_remaining=-0.40` (negative = over budget, as documented); ledger
  persisted to `journal/run.json` (`total_cost=2.4`).

### F8 [BUG] `iters_completed` is hardcoded to `window_size`, not the real count
The budget-stop diagnostic reported `iters_completed=10` although only **4**
candidates actually ran before the hard-stop (confirmed by `total_programs=6`).
In `run_window._one_window`, the diagnostics are built with
`"iters_completed": window_size` — a constant, never the loop's true completion
count. It happens to be correct on a full window, but is wrong exactly in the
early-break cases (budget exhausted, and presumably any future early exit) where
an accurate count matters most. Fix: track the number of started/finished
candidates in `counters` and report that.

### F9 [BUG] `llm_bandit_weights` in diagnostics is always `{}` (sensor blind spot)
The bandit genuinely works (warmup picked both models and `bandit_state.pkl` is
written), but the diagnostics field the orchestrator reads is dead:
`diagnostics.py:114` returns `payload.get("llm_bandit_weights", {})`, and
`run_window.py:512` fills that payload key from `cfg.get("llm_bandit_weights", {})`
— a **config** field that is never populated by anything. Nothing reads the live
bandit posterior from `bandit_state.pkl` back into diagnostics. Consequence: the
SKILL's rung-3 intervention ("bandit collapsed to one model + flat J → rewrite the
scoring concern") is undetectable from diagnostics — the orchestrator always sees
`{}`. Fix (foundation): `run_window` should read the bandit's current
probs/weights (e.g. a `select_llm` "peek" mode, or load the pkl) and pass them
into the diagnostics payload. Until then, an intervening orchestrator must load
`bandit_state.pkl` itself.

### F10 [ROUGH] `island_health.diversity` is just the program count, not diversity
`diagnostics.py:98` sets `"diversity": isl.get("count")` and `stagnation_count`
is hardcoded `None`. So "diversity" is a population-size proxy (warmup showed
1–2 = counts), and per-island stagnation isn't tracked at all. The code comments
acknowledge this as an approximation, but rung-2 ("island stuck with low
diversity") has no real diversity signal and no per-island stagnation count to act
on — the orchestrator would have to derive both itself. Naming invites
misreading count as spread.

### F11 [ROUGH] Every Azure call leaks an "Event loop is closed" traceback to stderr
Each `_azure.bg_query` wraps its call in `asyncio.run(...)`, which opens a fresh
event loop and closes it on return. The underlying httpx `AsyncClient` (OpenAI/
Azure SDK) registers a finalizer that calls `aclose()` on GC — but by then the
loop is closed, so stderr gets:
```
Task exception was never retrieved ... RuntimeError: Event loop is closed
  ... httpx/_client.py aclose -> asyncio/base_events.py _check_closed
```
It is **harmless** (the response is fully retrieved before the loop closes;
costs and applied patches are correct), but it prints a multi-line traceback per
LLM call, which (a) makes the run log noisy and (b) could bury a *real* error in
stderr. Fix: in `_azure`, explicitly `await client.close()` inside the same
coroutine before `asyncio.run` returns (or reuse one persistent loop/client).

## Phase 2 — Main loop (live)

**Run call 1**: 2 windows / 20 candidate attempts, ~16 min, exit 0, stderr clean
apart from F11's asyncio noise. The loop works and **evolution makes real
progress**: best score 0 → 0.00451, `evaluation_failure_rate=0.0` (every
evaluated candidate is a correct Clifford circuit). Returned on
`return_reason="stagnation"`. Per-window:

| window | best Δ | J | novelty accept | cost | stagnation |
|---|---|---|---|---|---|
| w0 | +0.00451 | 0.001427 | 0.60 | $0.4719 | False |
| w1 | +0.00000 | 0.0 | 0.30 | $0.4088 | True |

### F12 [BUG/ROUGH] `tau=0.05` (the starter-config default) is miscalibrated for this task → false stagnation
Per-window best-score gains here are ~0.001–0.005, an order of magnitude below
`tau=0.05`. So even **w0, a genuinely productive window (+0.00451)**, was scored
"low" (Δ < τ), and two windows later `stagnation_flag` fired despite real
progress. The delta-trigger will cry "stagnation" on essentially every window for
this task forever. The SKILL *does* say "set τ relative to the task's score
scale", but (a) the `shinka-setup` starter `orchestrator_run.json` ships
`tau:0.05` with no scale guidance, so an orchestrator that copies it uncritically
(as I did) inherits a broken trigger, and (b) nothing auto-scales τ or warns when
window gains are systematically ≪ τ. Suggest: default `trigger_metric` to a
*relative* gain (Δ / max(best,ε)) or auto-derive τ from early-window gain
magnitudes. I lowered τ → 0.002 for the continued run.

### F13 [COST] ~49% of mutation spend went to novelty-rejected near-duplicates
Cross-checking the ledger against the bandit's cost accounting:
- `journal` ledger `total_cost` = **$0.8807** (all 20 mutation attempts + embeddings).
- `bandit_state.pkl` `total_costs` = **$0.4517** (only the 9 *completed* candidates;
  `update_cost` runs only after a candidate survives novelty and is recorded).
- Gap ≈ **$0.43 (49%)** = the 11 novelty-rejected mutations' API cost.

The mutation LLM is billed *before* the novelty gate (`run_window.py:339` adds
cost, novelty rejects at `:357`), so near-duplicate rejects are pure waste. At
`code_embed_sim_threshold=0.99` (very permissive — only near-identical code is
rejected), a 55–70% rejection rate means the **low-effort models are emitting
trivially-similar patches**. This is a real efficiency problem on a hard task:
half the budget produced nothing to evaluate. Levers: a bolder mutation prompt
(fewer no-op edits) or surfacing rejection waste so the orchestrator can act.
This motivated the Phase 2 intervention below.

### F9 confirmed in practice — bandit detail only visible by loading the pkl
The real bandit posterior (`n_submitted=[15,5]` mini:gpt-5.5, cost-aware split
favoring the cheaper arm; `total_costs=[$0.144,$0.308]`) is healthy and
informative — but I could only see it by unpickling `bandit_state.pkl` myself,
exactly because diagnostics' `llm_bandit_weights` is dead (F9).

### Intervention #1 — prompt concern (validate → deploy → measure → outcome)

Motivated by F13 (half the spend wasted on near-duplicate rejects), I rewrote the
**prompt concern** (`construct_mutation_prompt.py`): added an always-on standing
guidance block to `patch_sys` demanding a *structurally distinct* change per
mutation (no cosmetic/no-op edits) and naming concrete levers. Ran it through the
full protocol:

1. **Generate** → wrote `strategy_history/candidate_construct_mutation_prompt.py`
   (contract preserved: same `main`, same output keys).
2. **Validate** → `validate_strategy.py` returned `valid:true` (parse + sandboxed
   smoke with synthetic payload; confirmed `patch_sys/patch_msg/patch_type`). **Works.**
3. **Deploy** → `strategy_store.deploy` snapshotted prior `254e7d6b` → new
   `9491e188`, copied candidate over `scripts/`, appended a `deployed` index entry.
   Verified both snapshots exist and the prior is byte-identical to the committed
   HEAD version. Logged the intervention to `journal/interventions.jsonl`; updated
   `run.json` `strategy_hash` + `window_state`. **Works.**
4. **Measure** → ran window 2 under the new prompt. **The intervention helped**:
   best score **tripled** (0.00451 → 0.01513, Δ=0.01062 — the largest single-window
   gain of the run), novelty acceptance recovered 0.30 → 0.50, `evaluation_failure_rate=0.0`.
   **Outcome: ACCEPTED** (`record_outcome(accepted=True)`, index now `accepted`).
   Both the protocol J-guard and the real signal agreed to accept — but for
   *different* reasons: the real signal because Δ/novelty improved; the J-guard
   only because `prior_J=0` made it inert (F14). `total_cost` after: $1.370.
5. **Rollback plumbing** → because the live J-guard cannot fire here (see F14), I
   verified rollback **in isolation** via `SHINKA_ORCH_SCRIPTS_DIR`/
   `SHINKA_ORCH_HISTORY_DIR` overrides (a throwaway copy, so the running window
   was untouched): deploy changed the file, `rollback` restored it
   **byte-identical** (hash matched original), and the index recorded both
   `rejected` + `rolledback`. **The rollback mechanism works.**

### F14 [BUG/subtle] The rollback safety net is inert during the early/flat phase (J≈0)
The protocol rolls back when `new_J < prior_J * 0.8`. But `J = Δ·scale/√W` and the
task's best score is **monotonic non-decreasing** (a worse candidate is simply not
recorded as the new best), so `new_J ≥ 0` always, and during the long opening
phase `prior_J = 0` (no window has yet cleared the noise). Then
`new_J < 0 * 0.8 = 0` is **never true** → every rewrite is auto-accepted, no matter
how much it degrades mutation quality. The safety net only arms itself *after* a
window posts a positive J — which is exactly when you least need to experiment.
A careless rewrite in the first dozen windows cannot be caught by the J-guard. The
orchestrator must apply its own judgment to roll back early-phase rewrites; the
guard won't. Suggest a secondary trigger (e.g. roll back if novelty-acceptance or
eval-success drops sharply vs the prior window, not just J).

### F15 [ROUGH] Cross-invocation `window_state` is hand-maintained and easy to desync
`run_window` reads `window_state.window_index` / `prior_low_streak` from the
config but never writes them back. Between every invocation the orchestrator must
manually edit `run.json` to bump `window_index` and carry `low_streak →
prior_low_streak` (and update `strategy_hash` after a deploy). The SKILL documents
this, but it is fragile bookkeeping: forget it and the journal gets colliding
window indices / a wrong stagnation streak. A `--resume` that reads the last
window's state from the journal would remove the footgun.

### Cost accuracy (addresses "did total_cost match actual spend?")
I cannot read the Azure billing portal from here, but the ledger is **internally
consistent and computed from real usage**:
- Mutation costs = `response.usage` tokens × `shinka/llm/providers/pricing.csv`
  rates (output includes reasoning tokens). Verified per-candidate `api_cost`
  values sum to the ledger.
- Embedding costs = real (`EmbeddingClient.get_embedding` returns `(vec, cost)`;
  `azure-text-embedding-3-small` priced $0.02/1M in the *separate*
  `shinka/embed/providers/pricing.csv`); captured by the harness `_embed`.
- Ledger `total_cost` ($0.8807) = Σ all mutation attempts + embeddings; the
  bandit's `total_costs` ($0.4517) counts only completed candidates — the
  documented $0.43 gap is the rejected-mutation waste (F13), not a ledger error.

So `journal/run.json:total_cost` is a faithful sum of every billed call given
correct pricing rows; the one caveat is it trusts `pricing.csv` (if a deployment's
real Azure rate differs from the CSV, the ledger drifts — worth a periodic
reconcile against the portal).

### F16 [BUG, important] The J-score formula is discontinuous & non-monotonic near s_start=0 → unreliable rollback scalar for small-score tasks
`J = Δ · log1p(s_start) / √W` (with a special `scale=1.0` branch when `s_start ≤ 0`).
For the **same** improvement Δ=0.01062, measured `compute_J` at different starting
scores:

| s_start | J |
|---|---|
| 0.0 | 3.36e-03 |
| 0.00451 | **1.51e-05** |
| 0.05 | 1.64e-04 |
| 0.5 | 1.36e-03 |

Crossing from `s_start=0` to a *tiny* positive score collapses J by **~222×** for
an identical gain, and J is non-monotonic in `s_start`. Consequences for a
small-score task (scores ≪ 1, like this one where the ceiling is ~0.85 and
per-window gains are ~0.01):
- J is dominated by the `log1p(s_start)≈s_start` term, not the actual Δ, so a
  *bigger* real improvement can score a *smaller* J (w2's Δ was 2.3× w0's, but
  w2's J was ~94× smaller — purely the scale discontinuity at the 0→positive
  boundary).
- The rollback comparison `new_J < prior_J·0.8` compares values that aren't on a
  stable scale across windows → the core safety decision is built on noise.

Combined with F14 (guard inert at J=0), the rollback safety net is **doubly
unreliable** on this task. Suggest: for the rollback scalar use the raw Δ (or
Δ normalized by a fixed task-scale constant), not `log1p(s_start)`; or gate
rollback on a basket of signals (Δ, novelty-accept, eval-success), not J alone.

## Phase 3 — Cost reconciliation & termination

Covered inline under "Cost accuracy" above (ledger is a faithful usage×pricing
sum; can't cross-check the Azure portal from this environment). **Termination:**
reached ~30 generation-attempts across 3 windows with the full loop +1 accepted
intervention exercised, so I stopped at the user's "~30 generations" cap rather
than burning the remaining ~$18.6 (a full $20 live burn would take ≈3–4 more
hours of wall-clock for mostly-repeating coverage). Run finalized
`status=completed_test`. Final: best **0.01513**, **$1.370** spent, 18 programs,
1 intervention (accepted).

### What worked well (validated, no issues)
- Azure background-poll mutation transport (`transport=background`, retries, parse/apply).
- Cost ledger accuracy (mutations + embeddings from real usage tokens).
- Budget hard-stop (`return_reason=budget_exhausted`, overshoot ≤ 1 candidate).
- The strategy-rewrite **machinery**: `validate_strategy` smoke test, `deploy`
  (snapshot + index + audit), `rollback` (byte-identical restore), `record_outcome`.
- `shinka` worktree-isolation assertion; island seeding; bandit cost-awareness;
  zero eval failures (every evaluated candidate was a correct Clifford circuit);
  the eval timeout/crash backstop path exists in `evaluate.py`.
- A real prompt intervention measurably improved both progress and novelty yield.

## Summary of concrete fixes (priority order)

| # | Severity | Fix |
|---|---|---|
| F1 | **BLOCKER** | `load_shinka_dotenv` should walk parent dirs (or use git common-dir) so a **worktree** inherits the main-repo `.env`. Every worktree run hits this. |
| F16 | **BUG** | Replace the `log1p(s_start)` J scale with raw/fixed-scale Δ (or gate rollback on a multi-signal basket). J is unusable for small-score tasks today. |
| F14 | **BUG** | Add an early-phase rollback trigger (novelty/eval-success drop), since the `new_J < prior_J·0.8` guard is inert while `prior_J≈0`. |
| F9 | **BUG** | Feed the **real** bandit posterior (from `bandit_state.pkl` / a `select_llm` peek) into diagnostics; `llm_bandit_weights` is always `{}` today, blinding rung-3. |
| F8 | BUG | Report the **actual** completed-iteration count in diagnostics, not the constant `window_size` (`iters_completed` lied as 10 vs 4 on the budget stop). |
| F12 | BUG | Don't ship `tau=0.05` in the starter config with no scale guidance; auto-scale τ or default to a relative-gain trigger. Caused false stagnation every 2 windows. |
| F13 | COST | ~49% of spend went to novelty-rejected near-duplicates. Surface rejection-waste in diagnostics; the prompt intervention helped — consider making bolder-mutation guidance a default. |
| F15 | ROUGH | Add `--resume` that reads `window_index`/`low_streak` from the journal instead of hand-edited `window_state` (desync footgun). |
| F11 | ROUGH | Close the httpx async client inside `_azure`'s coroutine to stop the per-call `Event loop is closed` stderr traceback spam. |
| F2 | ROUGH | Preflight-check the DR credential trio (`AZURE_DR_*`) before a deep-research call; they're absent from `.env`, so DR would fail at the $5 moment. |
| F3 | DOC | Task README still points at the removed `run_evo.py` / `shinka_run`; repoint to the orchestrator harness. |
| F4 | ROUGH | Define what the single `run.json:strategy_hash` should track (esp. for bundle deploys); it's ambiguous. |
| F7/F10 | ROUGH | Embed the bootstrap seed so early novelty checks aren't no-ops (F7); `island_health.diversity` is just a count and `stagnation_count` is always null (F10). |

### Recommended framework changes (out of orchestrator scope, for a human pass)
F16, F14, F9, F8 all live in **foundation** files (`stagnation_detector.py` is
mutable, but `diagnostics.py`/`run_window.py`/`journal.py` are not) — they need a
human edit between runs, not a mid-run strategy rewrite. F1 is in `shinka/env.py`
(framework, not orchestrator). The JSON contract is otherwise solid and the
mutable/immutable split held up well under a live intervention.

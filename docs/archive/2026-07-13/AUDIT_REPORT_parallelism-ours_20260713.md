> **STATUS: APPLIED (2026-07-13).** Historical record of the three-track audit
> (upstream parity / SimpleTES / concurrency) and its implementation batch.
> Live guidance is CLAUDE.md + .claude/skills/shinka-orchestrator/SKILL.md.

# Parallelization/Pipelining Audit — Inner Loop (run_window)

All paths relative to the fork worktree root `C:\Users\dtlic\Documents\GitHub\ShinkaEvolve\.claude\worktrees\xenodochial-beaver-de0e76`. Upstream snapshot paths relative to the scratchpad `upstream/` tree.

## 0. Headline findings

- The driver is **deliberately sequential**: "The driver is sequential (one candidate at a time), the clean reference order" (`orchestrator/harness/run_window.py:15`), and the file is marked harness plumbing, not a strategy file (`run_window.py:19-20`) — so parallelizing it is a between-runs engineering change, not a mid-run orchestrator rewrite.
- **Upstream's parallel machinery was deleted from this fork**: `shinka/core/async_runner.py` (−5851 lines), `runtime_slots.py` (`LogicalSlotPool`, −50), `pipeline_timing.py` (−137) all removed per the diff stat (`scratchpad/audit/shinka_diff_stat.txt`). Our `shinka/core/` contains only `config.py`, `sampler.py`, `wrap_eval.py`, `__init__.py` (Glob result). However, the **scheduler-level async job machinery survives**: `JobScheduler` still has a `ThreadPoolExecutor(max_workers=max_workers)` with default `max_workers=4` (`shinka/launch/scheduler.py:106,111`), `submit_async` (`scheduler.py:266`), `submit_async_nonblocking`/`batch_check_status_async` (`scheduler.py:384,410`). `orchestrator/scripts/evaluate.py` uses it strictly synchronously: `submit_async` then blocking `monitor_local` (`evaluate.py:123-126`).
- **Multiple Azure calls CAN be in flight**: `bg_query` is a self-contained blocking function that creates its own client (`orchestrator/scripts/_azure.py:220`), runs its own event loop via `asyncio.run` (`_azure.py:221`), and polls one response id. No module-global mutable state. Concurrency needs threads/subprocesses (or splitting submit/poll — the API is monolithic today, `_azure.py:187-227`), but nothing in the transport forbids K in-flight calls.

## 1. Step dependency graph (one candidate slot, `_run_one_candidate`, `run_window.py:545`)

| Stage | Reads | Writes | True dependency on prior slots |
|---|---|---|---|
| patch-mode draw (`run_window.py:592`) | seed+generation only | — | none |
| `sample_parent` (`run_window.py:598`) | full archive via sqlite (correct pool, children counts for the `1/(1+n_children)` bonus, `sample_parent.py:101-115`; island briefs) | — | **soft**: benefits from slot N's archived child + children-count update; **hard only in repair mode** (needs current `repair_attempts`/tombstone state, `sample_parent.py:26-29`) |
| parent/inspiration fetch (`run_window.py:624,643`) | sqlite read-only | — | soft (row could be tombstoned by a concurrent keep-better eviction) |
| `construct_mutation_prompt` (`run_window.py:721`) | inputs + island brief (`run_window.py:708`) | — | brief written by the **per-window meta round** (`run_window.py:1526-1615`) — cross-window only, never intra-window |
| `select_llm` select (`run_window.py:765`) | `bandit_state.pkl` | **saves pkl** (`select_llm.py:156-158,165-167` via `update_submitted`+`save_state`) | **soft on rewards** (stale posterior = slightly worse pick) but **read-modify-write on the pkl is a lost-update hazard** |
| `mutate` bg call (`run_window.py:844`) | stateless | candidate file in per-gen dir | none |
| novelty embed (`run_window.py:906`) | stateless Azure embed | — | none |
| `evaluate` (`run_window.py:915` → `evaluate.py:123-137`) | candidate file | per-gen `results/` dir (pre-wiped, `evaluate.py:120-121`) | none — fully isolated per gen dir |
| immediate fix loop (`run_window.py:936` → `_attempt_immediate_fixes`, `run_window.py:397`) | same-slot error | same-slot files; reads `journal.total_cost` (`run_window.py:450`) | serial **within** the slot only |
| novelty resolve keep-the-better (`run_window.py:1017-1125`) | ALL embedded programs (`novelty_check.py:72-81`) | may **tombstone the incumbent** (`run_window.py:1107-1114`) | **soft**: a stale archive misses a near-dup (two dups both kept); eviction is a DB write racing other slots' parent fetches |
| `compute_reward`, `record_policy` (`run_window.py:1140,1150`) | pure functions | — | none |
| `archive_record` (`run_window.py:1204`) | — | sqlite insert + island assignment + post-add maintenance/migration (`archive_record.py:75`, `dbase.py:1009,1056-1214`) | none on other slots' results; generation number pre-assigned as `next_gen + i` (`run_window.py:1336,1388`) — no DB round-trip needed |
| bandit update (`run_window.py:1217`) | pkl | **saves pkl** | **soft on ordering**, hard on file integrity |

**Explicit answers:** parent sampling for step N+1 does **not** require step N's eval result (it samples from whatever rows exist); it does not require step N's bandit update (bandit only picks the model); it does not require step N's novelty embedding (embeddings are compared, not chained); the **meta round runs once per window after `append_window`** (`run_window.py:1514,1526`) and only shapes the *next* window's briefs.

## 2. Hard vs soft sequentiality

**Correctness-critical (breaks/corrupts if concurrent):**
1. `bandit_state.pkl` load→mutate→save on every select and every update (`select_llm.py:60-73,146-147,156-158,165-167`). Two concurrent slots silently lose each other's updates; a torn concurrent save is caught by the corrupt-state reset (`select_llm.py:63-73`) — degradation to cold start, not crash.
2. Repair-mode accounting on a shared errored parent: `repair_record` append_fail + attempt-cap tombstone (`run_window.py:988-1011`) is read-modify-write on one row — two concurrent repairs of the same parent double-bump.
3. Keep-the-better eviction (`run_window.py:1107`) tombstoning a program another in-flight slot sampled as parent — its child would then archive with a tombstoned `parent_id` (lineage survives, but sampling invariants blur).
4. In-process `counters` dict (cost, eval_total, etc., `run_window.py:1337-1357`) — needs a lock under threads; the budget railguard reads `prior_total + counters["cost"]` per slot (`run_window.py:1384`), so K in-flight slots widen worst-case overshoot from 1 to K candidates' cost.

**Staleness-tolerant (worse decisions only):**
- Parent pool freshness (K parents drawn from one archive snapshot → duplicated parents since the children-count bonus doesn't update).
- Bandit posterior freshness (delayed reward feedback is standard in batched bandits).
- Novelty gate freshness (missed near-dup pair → both archived; the gate self-corrects next comparisons).
- Island brief / failure-note freshness (per-window anyway).

## 3. Shared-state safety today

- **sqlite:** WAL mode + 60s busy_timeout + synchronous=NORMAL (`shinka/database/dbase.py:424-429`), 60s connect timeout (`dbase.py:344`). Insert is transactional (`BEGIN TRANSACTION` at `dbase.py:1056`, commit `dbase.py:1111`), but `add()` does island assignment *before* the transaction (`dbase.py:1009`) and post-add maintenance (archive/best/migration) in separate commits (`dbase.py:1135-1214`) — read-then-write logic not covered by one transaction. Two concurrent `add()`s (each `archive_record` opens a fresh connection, `archive_record.py:72,84`) won't corrupt the file (WAL serializes writers) but can make inconsistent island-spawn/migration decisions.
- **Journal appends:** fsync'd, torn-tail-guarded appends (`journal.py:73-91`); `run.json` writes are atomic temp+rename (`journal.py:119-151`). But `add_cost` is read-modify-write (`journal.py:297-305`) — concurrent adders lose increments (not corruption). Inside a window, cost flows through in-process `counters` and folds once at `append_window` (`run_window.py:1509,1514`), so this only bites if concurrent slots each called `add_cost` directly — they don't.
- **Budget/ledger invariant:** cost books on terminal status only in the Azure transport (billed-failure cost rides the exception, `_azure.py:166-170`, folded at `mutate.py:249-252`) — this is per-call and unaffected by concurrency.
- **Fix rounds:** entirely slot-local state (`ev`, `mut`, `error_history`), except `journal.total_cost` read (`run_window.py:450`) and shared `counters` — same lock covers it.
- **One-process guarantee:** the `.run.lock` exclusive lock (`run_window.py:1856-1899`) already ensures at most one `run_window` per results_dir, so all concurrency introduced would be *intra-process* threads — the easiest kind to serialize at the commit points.

## 4. Feasibility verdicts

**(a) Batch-K — FEASIBLE, moderate effort.** Sample K parents + build K prompts + run K `select_llm` selects up front (accepting one stale snapshot), dispatch K `mutate.main` calls from a thread pool (`bg_query` is thread-safe per §0), embed+eval as they land (`evaluate.py` gen dirs are disjoint; pre-assign `next_gen + i` exactly as the loop already does at `run_window.py:1388`), then run each slot's **commit phase** (novelty resolve → repair/tombstone → archive_record → bandit update → counters) under a single in-process mutex, in landing order. Code changes: restructure `_one_window`'s `for i in range(window_size)` loop (`run_window.py:1383-1396`) plus a lock around counters and the commit phase; optionally a "no-duplicate-parent within batch" guard in the sampling pass. **Touches only harness code (`run_window.py`)** — no sqlite schema change, no change to any script's stdin/stdout JSON contract (each script is already a stateless per-call unit), evaluator untouched. Not FOUNDATION by CLAUDE.md's definition, but `run_window.py` is explicitly excluded from mid-run strategy rewrites (`run_window.py:19-20`) — do it between runs. Costs: budget overshoot up to K candidates; repair mode (`repair=(repair_on and i == 0)`, `run_window.py:1388`) should stay a solo slot; the between-candidate `.stop` check (`run_window.py:1394`) becomes between-batches.

**(b) Pipeline (evalN ∥ mutateN+1) — FEASIBLE, and strictly weaker than batch-K.** All N+1 stages up to `mutate` depend only on the archive snapshot (soft). Implementation is a 2-deep version of batch-K; the only extra wrinkle is that windows would half-overlap a barrier: diagnostics/`append_window`/meta/island_policy all assume a quiesced archive at the window boundary (`run_window.py:1403-1622`), so drain in-flight slots before the boundary. Same files, same non-FOUNDATION assessment. Given eval and LLM latencies are comparable (§5), pipelining alone buys ≤2× while batch-K generalizes it; there is no reason to build (b) instead of (a) with K=2.

**(c) Fix rounds under either scheme.** The fix loop is an intra-slot serial chain (fail → fix-mutate → re-eval, up to `fix_retry_budget`, default 1, `run_window.py:930`; grounding uses 3, `run_window.py:433`). Under batch-K it only makes slot durations heterogeneous — an "eval as they land" design absorbs it naturally. Under a rigid 2-stage pipeline it breaks the stage abstraction (a slot bounces LLM→eval→LLM), forcing a per-slot state machine — another reason to prefer (a). Fix rounds add no new shared state; their cost folds into slot-local `_slot_mut_cost`/`counters` (`run_window.py:945,509-511`) and their railguard read (`run_window.py:454`) just joins the counters lock.

## 5. Current wall-clock shape

Per slot, minimum 1 LLM call + 1 embed + 1 eval; on failure up to `max_patch_attempts` (3, `run_window.py:832`) apply-retries **inside one mutate call** and `fix_retry_budget` extra (LLM+eval) pairs. From code: the LLM transport polls every 3s with a 3600s wall (`_azure.py:23,29`) and per-HTTP-request 60s cap (`_azure.py:34`); the eval monitor polls every 0.5s (`shinka/launch/local.py:186`) with a config wall of 00:35:00 (starter, `configs/orchestrator_run.default.json:11`) / 00:48:00 (`tasks/pbb_code_discovery/orchestrator_run.json:11`). So both stages are minutes-scale: a medium-effort reasoning mutation typically runs single-digit minutes (wall sized 1h only for stuck jobs, `_azure.py:24-27`), while evals are budgeted up to 35–48 min — the eval cap is the larger design bound, and a fix round doubles both. Nothing in the code makes either stage negligible; that comparability is exactly why batch-K (which overlaps both) dominates a pure mutate/eval pipeline. The per-window meta round adds one more Azure call per window on the critical path (`run_window.py:1526-1562`), skippable only via `auto_meta:false`.

**Absences (expected but not found):** no `max_parallel_jobs` knob anywhere in our `shinka/` (grep hit none); no split submit/poll API in `_azure.py` (response id is never surfaced to callers); no file lock around `bandit_state.pkl`; no upstream-style `AsyncRunner`/`LogicalSlotPool` in the fork tree.

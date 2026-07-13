> **STATUS: APPLIED (2026-07-13).** Historical record of the three-track audit
> (upstream parity / SimpleTES / concurrency) and its implementation batch.
> Live guidance is CLAUDE.md + .claude/skills/shinka-orchestrator/SKILL.md.

# TRACK 3 — Throughput / Concurrency Proposal

Audited fork: `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/xenodochial-beaver-de0e76`.
Verified inputs: `audit/reports/{parallelism-ours,upstream-core,simpletes-code,azure-caching,ours-orchestrator}.md` (all present); load-bearing code claims re-checked in source (`run_window.py:1383-1396`, `_azure.py:187-227`, `select_llm.py:146-167`).

---

## 1. GROUND TRUTH — correcting the framing

**The user's memory is wrong about upstream, right about us.** Upstream ShinkaEvolve is NOT "one proposal one evaluation sequential." Its runner is `ShinkaEvolveRunner` in `shinka/core/async_runner.py` — "Fully async evolution runner with concurrent proposal generation" — with rolling in-flight caps `max_evaluation_jobs=4`, `max_proposal_jobs=6`, `max_db_workers=2`, no generational barrier, generation numbers assigned at submission, and **parent selection from a stale archive snapshot by design** (up to 6 concurrent proposals sample from the same persisted archive; in-flight results are invisible). This is a textbook asynchronous steady-state EA.

**Our fork deleted all of it deliberately**: `async_runner.py` (−5,851 lines), `runtime_slots.py` (`LogicalSlotPool`), `pipeline_timing.py`. `orchestrator/harness/run_window.py:15` says why: "The driver is sequential (one candidate at a time), the clean reference order." What survived: `JobScheduler` still carries a `ThreadPoolExecutor(max_workers=4)` (`shinka/launch/scheduler.py:106-111`) with `submit_async`/nonblocking variants, and the Azure transport `bg_query` (`orchestrator/scripts/_azure.py:187-227`) is a self-contained blocking function — own client, own `asyncio.run`, no module globals — i.e., **thread-safe for K in-flight calls today**. Nothing in the transport or the DB (WAL sqlite, 60s busy_timeout) forbids concurrency; only the harness loop does.

So the question is not "can we add a layer of parallelism nobody considered" — it is "how much of the parallelism we deleted do we re-add, in miniature, without re-importing 5,851 lines of oversubscription machinery."

Two consequences worth internalizing:
- **Stale-parent sampling is pre-validated.** Upstream ran it at depth 6; SimpleTES runs gen/eval fully overlapped with per-chain backpressure. Depth 2–3 staleness in our loop is well inside proven territory.
- **The 100× cost asymmetry is untouched** by any design below: all concurrency is intra-`run_window`-process threads dispatching Azure calls; Claude stays out of the per-mutation path.

---

## 2. DESIGN EVALUATION

Notation: `T_llm` = one mutate call (medium-effort reasoning, typically single-digit minutes; 3600s wall is a stuck-job bound, `_azure.py:29`); `T_eval` = one evaluation (budgeted up to `task.eval_time` 00:35:00 default; actual is task-dependent, minutes-scale for cnot_grid_synth); fix round = +1 of each. Sequential slot time = `T_llm + T_eval` (+ ~10–20s embed/archive, negligible). Both stages are minutes-scale and comparable — neither dominates universally, which shapes everything below.

### (a) User's batch-2 lockstep (2 LLM calls ∥, then 2 evals ∥, barrier, repeat)

- **Throughput**: per pair, `max(T_llm_1,T_llm_2) + max(T_eval_1,T_eval_2)` vs `ΣT_llm + ΣT_eval`. Speedup ≤ 2×, minus **two straggler taxes per pair** (E[max of 2] > E[one], at each barrier). With heavy-tailed LLM latencies and fix rounds this bites: a slot that enters a fix round (`+T_llm+T_eval`) holds the barrier while its partner idles.
- **Requires parallel eval.** If the eval saturates the machine's cores (the eval-bound case the user flagged), the two "parallel" evals contend and you gain ≈ nothing on the eval leg — you only keep the LLM-leg overlap.
- **Invariants**: identical staleness profile to (d) at K=2 (see below), so it buys no safety over (d) — it only adds barrier idle time.
- **Verdict: do not build this shape.** It is strictly dominated by (d) with the same K: same staleness, same shared-state work, worse wall-clock. The user's own instinct ("at least we can pipeline") is the better idea.

### (b) Pure pipelining (submit LLM call N+1 while eval N runs)

- **Throughput**: steady state `1/max(T_llm, T_eval)` per slot ⇒ speedup `(T_llm+T_eval)/max(T_llm,T_eval)`, i.e. **2× when balanced, `1 + T_llm/T_eval` when eval-bound, `1 + T_eval/T_llm` when LLM-bound**. Crucially it needs **no parallel evals** — only one eval runs at a time — so it is the one design that pays off even when the eval saturates the machine. For `T_llm=5min, T_eval=15min`: 20→15 min/slot, +33%. For balanced 8/8: 16→8, +100%.
- **Dependency check** (verified in `parallelism-ours.md` §1): parent sampling for N+1 needs neither N's eval result, nor N's bandit update, nor N's embedding — only the archive snapshot. Hard dependency exists **only in repair mode** (needs current `repair_attempts`/tombstone state, `sample_parent.py:26-29`) — and repair is already confined to slot 0 of a window (`run_window.py:1388`), so "repair slot runs solo" is a one-line rule.
- **Fix rounds**: this is where a *rigid two-stage* pipeline breaks — a failed eval bounces its slot back to the LLM stage (fix prompt), violating the stage abstraction. Solution: don't build stages; build **slots with a per-slot state machine** (propose → eval → maybe-fix → commit) and cap in-flight work with semaphores. Then a fix round is just that slot re-acquiring the LLM semaphore. This is exactly design (d) at K=2/E=1.
- **Verdict: yes — but implemented as (d) with caps (2,1), not as a bespoke pipeline.**

### (c) SimpleTES-style K sibling samples per parent

What SimpleTES actually does (verified): one `select()` → one prompt → K=4 samples sharing a `gen_id`, dispatched as K independent k=1 tasks; **only the best-of-K finite-score child is committed to the chain**; errored children never join chains; no archive DB, no novelty dedup, no islands, no bandit.

- **Two separable pieces.** (i) *K siblings as a concurrency layer*: dispatch K mutate calls off one sampled parent+prompt. (ii) *best-of-K commit semantics*: discard all but the winner. **Adopt (i), reject (ii).** Our archive/novelty/island machinery already handles multiple children of one parent gracefully: the novelty gate (cosine 0.99, keep-the-better, `run_window.py:1017-1125`) auto-dedups near-identical siblings, and distinct siblings are legitimately independent archive entries feeding parent-sampling diversity and the bandit. Best-of-K-discard would throw away paid-for information our design is built to keep.
- **Throughput/economics**: K siblings share an **identical full prompt** (same instructions + same input), so with Azure prompt caching siblings 2..K pay ~10% input price on the *entire* prompt (see §4) — the one fan-out pattern where caching pays on more than the sys-msg slice. Search-quality-wise this is variance reduction on a good parent (exploitation), not more coverage of parent space — it competes with, not substitutes for, drawing K distinct parents.
- **Invariants**: bandit gets K rewards for one arm pull-set — fine, upstream's `update_submitted` counts in-flight pulls the same way; window accounting must count each sibling as a slot (it consumes generation numbers and budget).
- **Verdict: worthwhile as an opt-in knob** (`evo.sibling_samples`, default 1 = off) **after** the concurrency substrate of (d) exists — it is 30 lines on top of it and zero lines without it. Not the first thing to build.

### (d) Async rolling in-flight cap (miniaturized upstream)

Keep the window as the accounting unit (its 10 generation numbers pre-assigned `next_gen+i` exactly as today, `run_window.py:1336,1388`), but run its slots through **two semaphores**: `K` = max concurrent LLM calls (mutate + fix + embed), `E` = max concurrent evals. Refill as slots land; **drain all in-flight slots at the window boundary** before diagnostics/`append_window`/meta. Commit phase per slot (novelty resolve → repair/tombstone bookkeeping → `archive_record` → bandit update → counters) runs under **one in-process mutex, in landing order**.

- **Throughput**: `≈ min(K,E·T_llm/T_eval+1, ...)`-bounded; concretely (K=2,E=1) = pipelining (b); (K=3,E=2) approaches 2.5–3× when eval doesn't saturate the box. No barrier straggler tax; fix rounds just make slot durations heterogeneous, absorbed by refill.
- **Invariant-by-invariant** (this is the table that matters; all of it verified against source):
  | Invariant | Under (d), small K | Acceptable? / Mitigation |
  |---|---|---|
  | Parent freshness | K parents from one snapshot; children-count bonus `1/(1+n)` stale ⇒ possible duplicate parent | Yes (upstream depth 6). Add a **no-duplicate-parent-within-in-flight guard** in the sampling pass — cheap and removes the main pathology. |
  | Bandit (`bandit_state.pkl`) | load→mutate→save is a lost-update hazard (`select_llm.py:146-167`); torn save ⇒ silent cold-start reset (`:63-73`) | **Must fix**: route every select/update through the commit mutex (all concurrency is intra-process — the `.run.lock` guarantees one process, so a `threading.Lock` fully suffices). Delayed rewards = standard batched-bandit staleness, fine. |
  | Novelty dedup | Two in-flight near-dups can both archive (gate compares only persisted rows) | Acceptable at K≤3: gate self-corrects on later comparisons; commit-mutex ordering means the *second* to land is checked against the first. Residual: none. |
  | Keep-the-better eviction vs in-flight parents | Eviction can tombstone a program another in-flight slot already sampled as parent | Lineage survives (verified); add "don't evict a program pinned as an in-flight parent" to the commit phase — 5 lines against an in-flight-parents set. |
  | Repair-mode accounting | `repair_record` read-modify-write on one row | Keep today's rule strict: repair slot runs **solo** (drain before, dispatch alone). Zero cost — it's already one slot per window. |
  | Meta cadence / island briefs | Meta reads whole archive once per window | Unchanged — window-boundary drain quiesces the archive exactly as today. |
  | Stagnation / diagnostics / window accounting | Sensors read per-window counters + quiesced DB | Unchanged given drain; `counters` dict needs the commit mutex (`run_window.py:1337-1357`). |
  | Budget railguard | Overshoot widens from 1 to K candidates' cost (`run_window.py:1384`) | Check **at dispatch admission**: `prior_total + counters.cost + inflight×avg_slot_cost ≥ budget` ⇒ stop admitting (upstream's committed-cost pattern). Per-call ~$10 output cap already bounds the tail. |
  | Ledger / crash durability | Cost books on terminal status per call (unchanged, per-call property); crash with K in flight loses ≤K unlogged-but-billed calls vs ≤1 today | Same exposure class as today, ×K. Keep K≤3. `.stop` handling: **drain, never kill** — cooperative stop stops *admitting* slots and waits for in-flight Azure calls (consistent with the never-kill rule). `--resume` semantics unchanged (each slot commits atomically before counting). |
  | Eval isolation | Per-gen `results/` dirs are disjoint, pre-wiped (`evaluate.py:120-121`) | Already safe; `JobScheduler`'s existing pool handles E>1. Watch CPU contention (below). |
- **Eval contention is the real E>1 risk**: two eval subprocesses sharing cores slow each other and can flip a pass into a timeout, corrupting the score signal. Default **E=1**; raise to 2 only after measuring one eval's CPU footprint on the actual task (`numeric_threads_per_job` in `JobConfig` exists to pin threads).
- **Verdict: this is the design.** (a) and (b) are special cases of it; (c) rides on it.

---

## 3. RECOMMENDED STAGED PLAN (ranked by value-for-effort)

All concurrency work lands in `run_window.py`, which is **harness plumbing, explicitly excluded from mid-run strategy rewrites** (`run_window.py:19-20`; `strategy_store.MUTABLE_TARGETS` excludes it). So every stage below marked [C] is **between-runs foundation work** — none of this is deployable into a live run. The [A] items are knobs those stages expose.

**Stage 1 — [C] Slot state machine with (K=2, E=1): the pipelined assembly line. Effort M. DO FIRST.**
- *What*: restructure `_one_window`'s `for i in range(window_size)` loop (`run_window.py:1383-1396`) into: prepare (sample parent + prompt + `select_llm` select, under mutex, with in-flight-parent no-dup guard) → dispatch `mutate.main`+embed+`evaluate.main` from a small thread pool (`bg_query` verified thread-safe; per-slot state machine handles fix rounds by re-acquiring the LLM semaphore) → commit phase under one `threading.Lock` in landing order → refill; drain at window boundary; repair slot solo; admission-time budget check; `.stop` = stop admitting + drain.
- *Knobs exposed* [A]: `evo.parallel_llm_slots` (default **2**), `evo.parallel_eval_slots` (default **1**). Defaults reproduce the user's "assembly line" with zero eval contention.
- *Benefit*: +33% (strongly eval-bound) to +100% (balanced) window throughput; no search-semantics change; meta/stagnation/diagnostics untouched.
- *Risk + mitigation*: threading bugs in commit ordering — mitigate with the single coarse mutex (contention is irrelevant at minutes-scale slots) and an offline test in `orchestrator/tests/` faking `mutate`/`evaluate` with sleeps, asserting archive/bandit/counter parity with the sequential driver at K=1 (K=1 must be bit-identical: keep the sequential path as the K=1 degenerate case, not a separate branch).

**Stage 2 — [C] Ledger cache-awareness + `prompt_cache_key` plumbing. Effort S. Do in the same between-runs batch.**
- *What*: read `usage.input_tokens_details.cached_tokens` in `_usage_cost` (`_azure.py:69-96`) and `get_openai_costs` (`shinka/llm/providers/openai.py:62-101`); add a cached-input price column to `shinka/llm/providers/pricing.csv` + `calculate_cost`. Add an optional `prompt_cache_key` parameter to `bg_query` (`_azure.py:187`) and pass `f"{run_id}:{island_id}"` from `mutate.py`. **Verify first** with one live background call that `cached_tokens` appears (background+caching interaction is documented-silent).
- *Benefit*: today the ledger prices cached tokens at full rate — conservative but wrong; must be fixed before any caching-economics decision. Suggested default: on unconditionally.
- *Risk*: Azure Standard cached price is UNVERIFIED (OpenAI lists 10%; do not assume parity) — read the actual number off a live response bill or the Azure pricing page before hard-coding.

**Stage 3 — [A] Raise E to 2 after measuring eval CPU footprint. Effort S. Conditional.**
- *What*: flip `evo.parallel_eval_slots: 2` (+ set `numeric_threads_per_job` to half the cores) only if one eval measurably leaves >half the machine idle. For cnot_grid_synth, measure before touching.
- *Benefit*: unlocks K=3 territory (~2.5×) on non-saturating tasks. *Risk*: eval-time distortion → timeout misclassification; the measurement gate is the mitigation.

**Stage 4 — [C]+[A] Sibling sampling (`evo.sibling_samples`, default 1). Effort S–M on top of Stage 1. Optional, opinionated-yes.**
- *What*: in the prepare phase, optionally emit N identical-prompt slots for one sampled parent (same arm, distinct seeds/generations), staggering dispatches by ~30s so siblings 2..N hit the prefix cache (prefill of a ~10k-token prompt completes in seconds; 30s is noise against minutes-long calls). Children flow through the **normal** novelty/archive/bandit path — no best-of-K discard.
- *Benefit*: cheapest marginal candidates in the system (~90% off input for siblings, §4) + variance reduction on strong parents. *Risk*: exploitation tilt (siblings crowd a window's 10 slots) — cap at 2 siblings and let the meta round's per-island directions keep diversity; novelty gate absorbs true duplicates.

**Do NOT adopt:**
- **Lockstep batch-2 (design a)** — dominated by Stage 1 at equal risk; barrier idle is pure waste once fix rounds exist.
- **Resurrecting upstream `async_runner.py`** (oversubscription, EWMAs, adaptive pipeline targets, stuck-recovery) — 5,851 lines of machinery solving problems (SLURM queues, 6-deep proposal buffers) we don't have; it would gut the fork's audit-ability, which is the whole point of the rewrite.
- **SimpleTES chain/best-of-K commit semantics** — discards paid evaluations and duplicates what islands+novelty+bandit already do; SimpleTES needs it only because it has no archive.
- **Azure Global Batch** — hard-wired 24h completion window; useless for a loop that needs results in minutes.
- **Chat-completions `n>1`** — the Responses API has no `n` (verified in installed SDK); `gpt-5.4-pro`/`gpt-5.3-codex` are Responses-only anyway. K samples = K requests, full stop; caching is the mitigation.
- **Warm-then-fan-out that awaits full completion** of call 1 before dispatching 2..K — for minutes-long reasoning calls that burns more wall-clock than caching saves; the ~30s stagger gets the same cache hit for free.

---

## 4. PROMPT CACHING EXPLOITATION

**Azure facts that bind** (from the caching audit, all sourced): caching is automatic, no opt-out, all four deployments covered; hit requires ≥1,024-token prompt with the **first 1,024 tokens identical**, then 128-token granularity; routing by prefix hash + optional `prompt_cache_key` (present in installed SDK's Responses params); in-memory TTL 5–10 min idle (gpt-5.5 gets 24h retention by default); keep each prefix+key under ~15 RPM (we're at ~1 RPM — non-issue); cached input ≈10% of input price on OpenAI, **Azure number unverified**; `gpt-5.4-pro` likely has no cached discount at all.

**Our prompt anatomy vs the 1,024-token cliff** (verified): the only content stable across *all* mutations is `task_sys_msg` at the head of `instructions`; immediately after it comes the per-attempt "# Direction for this attempt" (island brief), failure note, then a **randomly sampled** FULL-format variant (`shinka/core/sampler.py:168`) — so today's guaranteed shared prefix is `task_sys_msg` alone, and if it's under 1,024 tokens the cache never fires at all.

**Where the money actually is — arithmetic (gpt-5.5, OpenAI-parity assumption: $5/M in, $0.50/M cached, $30/M out):**
- Typical mutation call: ~10k input tokens, ~8k reasoning+output ⇒ ~$0.05 in + $0.24 out ≈ **$0.29/call; input is only ~17% of call cost**.
- *Generic fan-out / consecutive sequential calls* sharing only a ~1.5k-token `task_sys_msg` prefix: save 1.5k × $4.5/M ≈ **$0.007/call (~2%)**. Negligible — **do not contort the prompt architecture for this.**
- *Sibling calls (Stage 4)*, identical full ~10k prompt: siblings 2..K save ~10k × $4.5/M ≈ **$0.045/call, ~90% of input, ~15% of total call cost**. Real but modest — output tokens dominate; caching makes siblings cheap, it does not make the run cheap.
- *Meta round*: prompt is a fresh whole-archive dump each window — no reusable prefix; nothing to do.

**Recommendations, in priority order:**
1. **[A] Author `task_sys_msg` + `objective_brief` comfortably >1,024 tokens** at boot (they're agent-authored anyway). Zero cost, turns every same-window call into a small hit, and is the precondition for everything else. Suggested default: just do it.
2. **[C] `prompt_cache_key=f"{run_id}:{island}"`** via Stage 2 plumbing — co-routes same-island calls (which share direction/failure-note text beyond the sys msg). Effort S.
3. **[C] Pin one FULL-format variant per window** (thread a window-seeded choice through `construct_mutation_prompt.py` [B-file] into `PromptSampler` instead of per-call random, `sampler.py:168`) and **move the per-attempt direction/failure-note after the format block or into `input`** — extends the shared prefix from ~1.5k to potentially 2.5–3k tokens for same-island same-window calls. Effort S–M, saves another ~1–2%/call. Worth doing only as a rider on other prompt work — not on its own.
4. **Stagger sibling dispatches ~30s** (Stage 4) instead of any warm-await scheme.
5. **Verify once, live**: one background call, read `usage.input_tokens_details.cached_tokens` and the actual Azure cached rate; until then treat all savings above as upper bounds. (Background+caching is expected to work but documented nowhere.)

**Bottom line on caching**: it is a ~2% economics tweak for the general loop and a ~15% tweak for sibling fan-out — pursue it as cheap riders on Stage 1/2, never as a reason to redesign prompts. The throughput win (Stage 1) is worth ~10–50× more budget-per-day than the caching win.

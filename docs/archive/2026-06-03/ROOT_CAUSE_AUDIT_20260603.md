> **CORRECTED / SUPERSEDED (archived 2026-06-08).** The "subagent vehicle" root cause asserted below was
> determined INACCURATE and the user corrected it. The real cause of the 2026-06-03 run death was the background
> `run_window` job being KILLED (reaped) or ERRORING OUT — not a subagent-vs-main-loop distinction. The mitigation
> that holds: launch the job so its completion/kill re-invokes you, keep clusters short, and recover losslessly
> with `run_window.py --resume`. This file is retained for history only.

## Problem A — root cause & fix

**Root cause: the subagent VEHICLE, not an "ephemeral sandbox reclaiming detached jobs on idle."** The 2026-06-03 run died because the orchestrator was deployed as a *spawned subagent*. Two subagent-specific facts — both proven by a same-day **$0 / no-Azure controlled experiment** (`/Users/dantongli/.claude/projects/-Users-dantongli-GIthub-ShinkaEvolve/memory/orchestrator-runs-need-main-loop.md:15-31`) — are the mechanism:
1. A spawned subagent is **never re-invoked** when a background job it launched completes (background-completion wake-up *and* ScheduleWakeup are main-loop-only): a subagent told to launch a 60 s job and "wait to be woken" returned in **6.8 s**, never woken.
2. When the subagent's turn ends, **its background jobs are killed with it** (the 60 s heartbeat died after ~2 s).

The decisive contrast in the same experiment: a background Bash job launched **from the main loop** *did* survive idle and re-invoked the main loop on completion (memory note line 28). So "idle" is not the killer — the **vehicle** is. The live agent's "ephemeral sandbox reclaims detached jobs on idle" is the *symptom* (job gone, no wake) with the mechanism misattributed.

The taught wake model is otherwise sound and load-bearing (`SKILL.md:83-91`, `:136-147`; `CLAUDE.md:135-140` — "the background-launched `run_window --until-decision` … returns control by EXITING … and re-invokes you, so you stay alive and in the loop"), but it silently assumes the one precondition — a **main loop** — that a subagent violates. `caffeinate` (`run_window.py:1321-1354`) addresses a *different*, 2026-05-27 failure (host idle-sleep) and gives zero protection against subagent reclaim. The survival surface is fully orthogonal to Problem B: the mutation/novelty mechanics live inside `run_window`'s per-candidate loop (`run_window.py:879-905`) and cannot affect whether the process survives its parent turn.

**The agent's fix-model — partly the durable answer, partly a workaround:**
- **(b) Relaunch-on-every-notification (complete OR killed) + `--resume` — CORRECT and code-backed.** `--resume` (`run_window.py:1444-1456`) restores `window_index = last+1` and `prior_low_streak` from the journal; the archive is per-candidate SQLite-committed; the termination/taper streaks are recomputed fresh from durable streams every relaunch (`journal.py:363-428`; `run_window.py:1234-1249`). A kill loses **at most the in-flight candidate**, and the small-batch loop is behaviorally identical to one long cluster.
- **(d) Main-loop-only — the actual root-cause fix.**
- **(a) Small 2-window batches — NOT required by code** (`cadence_policy.py:61-72` is an *uncapped* escalating taper; `max_windows_per_call` defaults to `None`). They only shrink the per-kill loss window; useless inside a subagent (no wake at all).
- **(c) Safety-net scheduled wakeup — NOT wired** anywhere in `orchestrator/` and itself main-loop-only, so it cannot rescue a subagent. Worthwhile robustness *only* in a main loop.

**Re-adding the detached daemon?** Only as an explicit fallback. The removed `run_detached.py` (`start_new_session` reparenting, git `3cdb0c7`, removed in `1d7793f`) would structurally survive a subagent turn-end but **loses the auto-wake** (must poll). For the chosen main-loop design, auto-wake background-launch is strictly better; detached+poll is right only when a main loop is truly impossible.

**Durable code/doc changes:** (1) bake **MAIN-LOOP-ONLY** into `CLAUDE.md` (~135), `SKILL.md` (~83, ~136-147), and the `NOTES.md` banner — it lives *only* in being-cleared auto-memory today (grep of all three docs finds **zero** subagent-precondition warning; only orchestrator-spawns-helper mentions at `SKILL.md:444/447/621`); (2) teach "**killed == relaunch `--resume`, losslessly**"; (3) teach the optional main-loop-only safety-net wakeup; (4) separate the two historical kills (2026-05-27 host-sleep→caffeinate; 2026-06-03 subagent→main-loop); (5) fix the one real code-bug below; (6) add the new phrases to the doc-lint asserted set so the teaching can't regress.

**Code-bug surfaced (survival):** `BanditBase.save_state` (`prioritization.py:194-200`) is a **non-atomic** in-place `open(path,"wb")+pickle.dump`, called ~2K times/window; a mid-write kill silently resets the learned posterior to uniform, and `select_llm._make_bandit` (`select_llm.py:56-60`) swallows the unpickle error with `try/except: pass` (despite the comment promising a caller log). Make it `tmp+fsync+os.replace` and log the discard. This is the *only* non-atomic overwrite of durable learned state in the loop; the kill-and-relaunch model makes the exposure routine.

## Problem B — root cause, the verdict on the user's hypothesis, & fix

**Root cause: a COMBINATION, dominated by the FLAT 0.99 THRESHOLD on a WHOLE-PROGRAM embedding**, with the mutation-prompt/patch design as a real-but-secondary upstream contributor and full-file (vs diff) representation as a genuine-but-on-this-task marginal third factor.

**Verdict on the user's hypothesis** ("such a high similarity I'd suspect it's something else's problem like the prompt for the mutation LLM"): **PARTIALLY CORRECT — honor it, but it is not the whole story, and it is not a code-bug.** The mutation prompt genuinely biases toward small diffs *by design*: `prompts_diff.py:71` literally says "Do not rewrite the entire program - focus on targeted improvements," and the default mix is diff-heavy (`construct_mutation_prompt.py:26` `patch_type_probs [0.6,0.3,0.1]` = 60 % diff). Applied to a ~370-line parent, those edits yield children ~98-99 % textually identical (`mutate.py:190-194` returns the whole `updated` file with only the SEARCH blocks replaced) — so the embeddings are near-identical **upstream of the gate**, exactly as the user suspected. But this is a **design property of incremental hill-climbing, not a defect emitting tiny/empty diffs**: `mutate.py:191` returns `applied=True` only when `n>0` real changes were made; a 0-change patch returns `applied=False` (`mutate.py:209`) and is dropped *before* eval (`run_window.py:682-697`). No identity/empty candidate reaches the gate in the live path.

Ranked mechanism:
1. **THRESHOLD (primary, independently sufficient).** `0.99` is hard-coded at `novelty_check.py:57`, `run_window.py:804`, and shipped by both skill starters, with **no size scaling anywhere**. On a large program a real 5-line edit ≈ 0.998, 10-line ≈ 0.994, 20-line ≈ 0.980 — the improvement band straddles and mostly *exceeds* 0.99. `SKILL.md:213/571` already notes large programs cluster 0.96-0.98. The live evidence is conclusive: 3 rejects in a row, archive frozen at 11, and **disabling novelty immediately set a record** — proving the *gate*, not the cosine math, blocked the search.
2. **MUTATION-PROMPT/PATCH (secondary, by design — the user's hypothesis).** 60 % diff + "do not rewrite the whole program" = near-parent children. Real, upstream, but *correct* for refinement; harmful only because the gate punishes it.
3. **REPRESENTATION (full-file embedding, confirmed root-cause-class but only marginally fixable here).** `run_window.py:707` embeds the full `candidate_code`; `_embed` (`run_window.py:99`) passes the raw string to `get_embedding` with no slicing/truncation. Cosine of a dense whole-text vector conflates "near-identical text" with "not novel." But block-only embedding does **not** rescue the dominant small-edit case — the evolve-block is already ~72 % of this file (lines 88-358) and a 10-line edit is ~1 % of the block too, so block-only cosine of a 10-line edit is still ~0.994 (>0.99). Scope-redaction buys only ~0.003-0.004. The reliable representation fix is embedding the **unified diff** (`run_window.py:887` carries `code_diff`), which separates two different small edits sharply. `redact_immutable` (`apply_diff.py:165`, exported `edit/__init__.py:6`) has **zero call sites** — dead wiring a future agent will mistake for the fix.

**Does H5 keep-the-better resolve the live stall? YES for the exact observed failure — but it does not fix the measure.** OLD live-run code (git `8942c2d:run_window.py`) rejected a near-dup **pre-eval** — `if not nov.get("accept"): counters["novelty_rejects"] += 1; return` — so its real score was never observed and the archive could not advance past 11. CURRENT code **defers** novelty to after eval (`run_window.py:699-702`): a correct near-dup is evaluated (line 712), and if strictly better is **kept** while the worse incumbent is **tombstoned/evicted** (`run_window.py:836-846`; `tombstone_program` DELETEs from archive *and* sets `repair_tombstoned`, `dbase.py:867-894`; `novelty_check.py:85` skips tombstoned rows so the evicted incumbent stops blocking). The live candidates were real improvements, so they would now be kept and the record would advance — H5 structurally removes the freeze.

**Residual fixes still warranted:**
- **Strict `>` tie-break** (`run_window.py:813-814`): an equal-scoring or neutral-refactor distinct near-dup is **dropped** (line 832), so the search cannot traverse score **plateaus**. Change to `>=` (or `> inc - ε`) and still tombstone the older incumbent.
- **Eval tax:** H5 leaves the generator/representation untouched, so *every* large-program candidate trips the near-dup branch and pays a **full eval** (`SKILL.md:571` "a near-dup now costs an eval"). Fix the measure (size-aware threshold ~0.96-0.97 and/or diff-embedding) so the near-dup branch becomes rare, as designed.
- **Blind spot:** `novelty_kept_better` is counted (`run_window.py:837`) but has **zero reads** (absent from the diagnostics payload `run_window.py:1090-1106` and `diagnostics.py`'s return dict) — the very near-dup-flooding symptom that should trigger the rewrite is invisible to the agent.

## Ranked fixes

1. **[survival][doc]** Bake **MAIN-LOOP-ONLY** into `CLAUDE.md`/`SKILL.md`/`NOTES.md` (the single load-bearing Problem-A fix; currently only in being-cleared memory).
2. **[survival][doc]** Teach "**killed == relaunch `--resume`, losslessly**" as the operating reflex (the exact reflex the agent lacked).
3. **[novelty][doc+code]** Make the 0.99 gate **size-aware** (immediate: set ~0.96-0.97 for large-program tasks; durable: scale by editable line count in `novelty_check.main`). The dominant cause.
4. **[novelty][code]** Fix H5's strict `>` to `>=`/`> inc-ε` at `run_window.py:813-814` for plateau traversal (still tombstone the loser).
5. **[survival][code]** Make `BanditBase.save_state` atomic (`prioritization.py:194-200`, tmp+fsync+os.replace) and log the discarded-state case (`select_llm.py:56-60`).
6. **[novelty][code]** Embed the **unified diff** (`run_window.py:887`) — the reliable representation fix; pair with the threshold.
7. **[novelty][code]** Surface `novelty_kept_better` through `diagnostics.py` so near-dup eval-waste is visible.
8. **[survival][doc]** Teach an optional **safety-net scheduled wakeup** (main-loop only) as a dropped-notification backstop.
9. **[survival][doc]** Add survival phrases ("main loop", "safety net", relaunch/bounded-batch, "killed"/"--resume") to the doc-lint asserted set (`test_improvements.py:1440-1452`).
10. **[novelty][doc]** Reframe bounded batches as operating discipline, not an engine cap (`SKILL.md:88`); do not mandate 2-window batches in code.
11. **[novelty][code]** Short-circuit the no-op identity candidate (`mutate.py:117-118` mock; treat 0-change full/cross as apply-exhausted).
12. **[novelty][doc+code]** Wire `redact_immutable` as a complementary signal or comment it as unwired (`apply_diff.py:165`) so it isn't mistaken for the fix.
13. **[novelty][doc]** Optionally raise full/cross weight for large programs + stagnation `extra_guidance` (`construct_mutation_prompt.py`) — secondary diversity lever only.
14. **[survival][code]** Fold per-candidate spend into the ledger as it completes (`journal.add_cost`) so a partial-window kill doesn't drop spend / orphan candidates.

## Evidence map (finding → file:line)

- Subagent root cause (no wake + bg job dies) → `…/memory/orchestrator-runs-need-main-loop.md:15-31`
- Taught wake model (main-loop assumption) → `orchestrator/SKILL.md:83-91`, `:136-147`; `CLAUDE.md:135-140`
- caffeinate scoped to host-sleep only → `orchestrator/harness/run_window.py:1321-1354`
- `--resume` restores `window_index`/`prior_low_streak` → `orchestrator/harness/run_window.py:1444-1456`
- Streaks recomputed from journal → `orchestrator/harness/journal.py:363-428`; `run_window.py:1234-1249`
- Uncapped taper, no required 2-window batches → `orchestrator/scripts/cadence_policy.py:61-72`
- Detached daemon removed → git `3cdb0c7:orchestrator/harness/run_detached.py`, removed in `1d7793f`
- Non-atomic bandit save → `shinka/llm/prioritization.py:194-200`; silent swallow → `orchestrator/scripts/select_llm.py:56-60`
- OLD pre-eval reject (live-run code) → git `8942c2d:orchestrator/harness/run_window.py` (`if not nov.get("accept"): … return`)
- CURRENT defer + keep-the-better + tombstone → `orchestrator/harness/run_window.py:699-702`, `:799-846`
- Strict `>` tie-break drops ties/worse → `orchestrator/harness/run_window.py:813-814`, `:832`
- Tombstone DELETEs + sets flag; novelty skips it → `shinka/database/dbase.py:867-894`; `orchestrator/scripts/novelty_check.py:85`
- Full-file embedding (no slicing) → `orchestrator/harness/run_window.py:707`, `:99`; cosine math → `novelty_check.py:45-52`
- Flat 0.99 default (3 sites + starters) → `novelty_check.py:57`; `run_window.py:804`; skill starters; cluster note `SKILL.md:213/571`
- Mutation prompt "do not rewrite the whole program" → `shinka/prompts/prompts_diff.py:71`; 60 % diff → `construct_mutation_prompt.py:26`
- 0-change → `applied=False`, dropped before eval → `orchestrator/scripts/mutate.py:191`, `:209`; `run_window.py:682-697`
- `code_diff` available to embed → `orchestrator/harness/run_window.py:887`
- `novelty_kept_better` write-only → `run_window.py:837` (zero reads; absent from `diagnostics.py`)
- `redact_immutable` unused → `shinka/edit/apply_diff.py:165`; `shinka/edit/__init__.py:6`
- Doc-lint omits survival phrases → `orchestrator/tests/test_improvements.py:1440-1452`

## Refuted / low-confidence (for transparency)

- **"74 % fixed scaffolding pins every child" — REFUTED/INVERTED.** The evolve-block is lines 88-358 = ~72 % **editable**, only ~28 % fixed. The true driver is "a small edit on a large program is near-identical under *any* whole-region embedding," which block-scoping does not solve alone (block-only 10-line edit still ~0.994 > 0.99).
- **Embed-the-diff/block as the *primary* novelty fix — DOWNGRADED to secondary.** It buys only ~0.003-0.004 on this task; the threshold is the operative lever.
- **No-op/identity patch caused the live rejects — REFUTED.** `mutate.py:191` requires `n>0` for `applied=True`; a 0-change patch is an apply-failure (retry → `applied=False`) dropped before eval. The rejected near-dups were *real applied edits*, so the 0.99 is a property of the measure, not a degenerate prompt. (The identity path is mock-only, `mutate.py:117-118`.)
- **Embedding caching/normalization/degenerate-vector bug — REFUTED.** `_cosine` L2-normalizes and zero-guards (`novelty_check.py:45-52`), each candidate is freshly embedded, and an embed failure → `[]` → **ACCEPT** (never false reject). The ≥0.99 values are genuine.
- **SQLite WAL unclean-shutdown branch — REFUTED as a risk.** Its `size==0` guard only ever discards an empty pre-schema DB; committed candidates are durable (`dbase.py:326-342`, `:1104-1110`). Only the in-flight uncommitted candidate is lost on a mid-candidate kill.
- **`caffeinate` keeps a subagent run alive — REFUTED.** It holds only `PreventUserIdleSystemSleep` and self-cleans on the child's death (`-w <pid>`); zero protection against subagent reclaim.

---

_Generated by the `root-cause-survival-novelty` workflow (run `wf_98ec3950-265`): 13 agents — 6 investigators (survival + novelty) each adversarially verified, then synthesized. Read-only. 2026-06-03._

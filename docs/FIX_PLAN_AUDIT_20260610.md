# Fix Plan — ShinkaEvolve Orchestrator Audit (2026-06-10)

**Addresses:** `docs/archive/2026-06-10/AUDIT_LOGIC_WORKFLOW_20260610.md` — 180 findings (13 H · 49 M · 95 L · 23 N) + the S1–S9 design-divergence supplement (189 items total).
**Authored:** 2026-06-12. Read-only audit + this plan; **no code/doc changed yet.**
**Method:** each finding was re-grounded against the live code in *this* worktree and given an exact fix, then every proposed fix was put through an adversarial reviewer that tried to break it. The reviewer caught real defects in ~20% of first-draft fixes (test-breakage, wrong line, wrong closure scope, mis-stated root cause); **those corrections are baked into the fixes below** — the naive version of several "obvious" one-liners would redden the offline suite.

---

## 0. Framing (read first)

- **The docs are TARGETS, not authority.** `CLAUDE.md`, `.claude/skills/shinka-orchestrator/SKILL.md`, `taxonomy.md`, `orchestrator/strategy_history/README.md`, `configs/README.md`, the subagent files, and the audit-workflow `.js` are all *artifacts to be corrected*. A large fraction of the findings (H7, H8-doc, H11, H12, M9, M40, M41, M42, the S-items, and ~40 lows/nits) are doc bugs whose fix is to rewrite the doc to match correct behavior. Nothing a doc currently says constrains a fix.
- **This is a BETWEEN-RUN campaign, so FOUNDATION is fair game** — sqlite schema (`shinka/database/*`), the scripts' JSON contract, `orchestrator/scripts/evaluate.py`, `shinka/launch/*`, `orchestrator/harness/*`. The mid-run "never touch foundation" rule does not apply to a deliberate, committed, test-gated fix campaign. **But every foundation-touching fix is flagged `[FND]`** because those carry contract/migration risk and must land as committed changes with the offline suite green, not as live strategy rewrites.
- **Every fix is additive-or-guarded where it touches a contract.** New diagnostics keys, new journal fields, and new payload keys are added without changing existing fields; old readers ignore them.
- **Ordering principle (matches the report's dependency note):** fix the two showstoppers (H1, H2) first — they shrink M34/L17/L11/H13/M46; then the fail-open safety nets; then the dangerous doc corrections; then the rest; foundation-schema work and the design-gated items last. Within that, the adversarial reviewer's `depends_on` / `ordering` notes are honored.

### Legend
`[FND]` touches foundation (schema / JSON contract / harness / launch / eval). `[DOC]` doc-only. `[RULING]` needs a user design decision before the code branch lands (recommended branch given). `→shrinks` = fixing this makes the named findings smaller/moot.

---

## Wave 0 — Design rulings the run's owner must make (these gate code branches)

Each has a **recommended branch** already specced below; if you accept all recommendations the plan is fully ordered. These are surfaced because the repo (code+docs, internally consistent) diverges from the *stated design intent*, or because the choice materially changes run lifetime / cost / recoverability.

### ✅ Rulings applied (2026-06-13)

The run owner ruled on the four gating forks:

- **S1 = FOUNDATION.** `cadence_policy.py` + `termination_streak` are reclassified immutable; `early_phase_windows`/`base_low`/`termination_streak` become **boot-only** config. **Consequence:** L47's code change is **dropped** (it breaks `test_cadence_policy` and isn't a clear improvement → doc-only note); L52/L58 become **regression-test + doc** work, not rewrite gates; M44's damping is a foundation change (apply with the user's blessing, not a live rewrite). Move the cadence/termination knobs out of the SKILL.md lever table into a fixed-config section, add them to the `[FND]` "never edit" list, and flip the `cadence_policy.py:3` header to FOUNDATION.
- **H12 = Inclusive.** A deliberate config-knob flip **counts** as an intervention (matches `journal.termination_streak`'s `work_audit>0 or work_dr>0` derivation). Make SKILL.md:124-131 + 514-516, CLAUDE.md:48-50, and the `journal.py:407-409` docstring all say "a framework rewrite, a DR, OR a deliberate config-lever flip; the auto-meta round does not count."
- **H8 = keep `replace`** (NOT augment). **Consequence:** H8 becomes **doc-only** — no change to the compose default. Document at SKILL.md:661,669-679 that `replace` discards `evo.meta_directions` per-gen once any island has a brief (window 1 onward), and add the DR-grounding caution (the recipe already wants `replace`, so it's now consistent). This *reshapes M1*: the global-directions channel is intentionally secondary to per-island briefs, so M1's re-hydration value collapses to the **`failure_note` channel** (which is appended every gen regardless of compose mode) + window-1/pre-brief/no-brief islands — keep M1 but scope it to those.
- **S2 = HYBRID (keep the approved warmup).** Failed/abandoned warmup attempts stay **throwaway** (M30/M35 auto-reset between attempts), but the **final warmup the orchestrator green-lights to continue is KEPT** — folded into the real `programs.sqlite` and its spend counted into the real ledger. **Consequence:** this is the **upstream "kept" branch, conditioned on a GO decision** — a `[FND]` fold-back, NOT the doc-only throwaway branch. See the revised **Warmup (S2-hybrid)** block in Wave 5. L6 follows: the *approved* warmup's spend counts; discarded attempts' spend stays free.

| # | Question | Recommended | Gates |
|---|---|---|---|
| **S1** | Are `cadence_policy.py` + `termination_streak` **FOUNDATION** or **MUTABLE-with-railguard**? | **RULED → FOUNDATION.** Reclassify immutable; `early_phase_windows`/`base_low`/`termination_streak` boot-only. Drops L47's code change; L52/L58 become regression+doc. | L47, L52, L58, M44, S1 doc moves |
| **H12** | Does a deliberate **config-knob flip** count as an "intervention" toward the auto-termination streak? | **RULED → Inclusive** — a knob flip counts (matches `journal.termination_streak`'s `work_audit>0 or work_dr>0` derivation). Unify all 4 doc sites + the journal docstring. | L58, L59, run lifetime |
| **H8** | Default `brief_compose_mode`: `augment` or `replace`? | **RULED → keep `replace`.** H8 becomes **doc-only** (document that `replace` discards `meta_directions` per-gen once briefs exist; the DR recipe already wants `replace`). Reshapes M1. | H8 doc-only, M1 scope |
| **S2 + L6** | Is warmup throwaway, or are warmup gens kept? | **RULED → HYBRID. ✅ DONE (Wave 5d/5e).** Failed attempts throwaway (M30/M35 auto-reset + L80 honest cleanup); the **orchestrator-approved final warmup is KEPT** via `--accept-warmup` (`accept_warmup`) — copies the warmup archive into the real `programs.sqlite` (run continues from the warmed population) + folds its spend into the real ledger as a durable `warmup_accepted` intervention; refuses to clobber a started run or accept an all-tombstoned warmup. | M30, M31, L6, L11, M36 |
| **M36** | `strategy_history/`: **relocate per-run** under `results_dir` (gitignored, self-contained archives — matches CLAUDE.md's current wording) or **stay repo-level + git-tracked** (fix docs + `.gitignore` + the archive source)? | **Either is fine; recommend the minimal code fix first** (archive reads from `strategy_store.index_path()`), then pick relocation as a follow-up. Decide before finalizing the README (L61). | L61, archive completeness |
| **M45** | Strengthen the rollback verdict with a **larger `measure_window_size`** (≈2× cost) or **doc-only + arm-3 mean-reversion gate** (zero cost)? | **Arm-3 gate + doc** now (zero cost); add `measure_window_size` only if a cheap measure run shows arms 1–3 are too noisy. | M45 code scope |
| **M42** | Ship a small default `migration_rate` (≈0.05) so islands get baseline gene flow, or keep migration off-by-default? | **Keep off; document** that default islands have no genetic interaction. (If enabled later: needs M18+L39+L30+L37 first.) | default config |
| **M11** | **Delete** the dead/contradictory `shinka/database` sampling stack or **quarantine** it (DEAD docstring + fix `taxonomy.md:41`)? | **Delete** (cleaner) — but preserve `parents.stable_sigmoid`/`sample_with_powerlaw` (used by `test_parity.py`). The `taxonomy.md:41` pointer fix lands either way. | M11 code scope |
| **M23/M24/M26/M43** | Is **bandit reward geometry** (`prioritization.py` exp-scaling + obs-range) FOUNDATION (parity/pickle-locked; geometry frozen) or mutable? | **Geometry = foundation** (defer math to the ending doc); only the *config levers* (`exploration_coef`/`cost_aware_coef`/`exponential_base`) and the M23/M24/M26 baseline/attribution fixes are in-scope. | M43 scope |
| Smaller confirmations | **S4** (errored near-dups kept, not "dropped" — doc), **S5** (novelty island-scoped — doc), **S6** (meta = bounded slice, not whole archive — doc), **S7** (global archive + per-island floor, no per-island capacity — doc), **S8** (strike-two = non-destructive tombstone — doc), **S9** (DR entries normal by construction; "merge at original" now implementable via H9), **L9** (abandoned-job cost = `max(partial_usage, floor)`), **M16** (implement non-destructive `retire` vs delete the promise), **M46** (auto-reseed an all-tombstoned archive vs halt), **M33-tier traces**. | Confirm-the-repo + fix-doc for S4–S8; implement `retire` (M16) and auto-reseed (M46); `max(partial,floor)` for L9. | local |

> **Cross-cutting consequence of S1:** if cadence/termination is ruled FOUNDATION, the L47 cadence-math change is **dropped** (it would break `test_cadence_policy` and isn't clearly an improvement), and L52/L58 become regression-test + doc work, not rewrite gates. The plan below assumes the recommended S1=FOUNDATION.

---

### ✅ Wave 0 verification
- [ ] All four rulings (S1/H12/H8/S2) recorded above and propagated to every affected fix entry (H8→doc-only, S2→hybrid fold-back, L47 dropped, cadence knobs boot-only, M1 rescoped).
- [ ] No code changed in this wave (decisions only) — `git diff` shows only the plan doc.

---

## Wave 1 — The two showstoppers (land first; everything else shrinks)

### H1 — Every fix/repair attempt is a paid no-op → route `patch_type="fix"` to the full applier
- **Fix:** [mutate.py:83](orchestrator/scripts/mutate.py:83) — `func = apply_full_patch if patch_type in ("full","cross","fix") else apply_diff_patch`. Keep the `"fix"` label everywhere else (archive forensics, L10). Update the `mutate.py:33` docstring to list `"fix"` and add a one-line comment that fix replies are full-code. **Do NOT** change `sample_fix` to return `"full"` — one routing word fixes both the immediate-fix loop ([run_window.py:425](orchestrator/harness/run_window.py:425)) and sampled-repair path ([:597-622](orchestrator/harness/run_window.py:597)).
- **Why safe:** `FIX_SYS_FORMAT` emits a ```{language}``` fence that `apply_full_patch` extracts; the cnot seed has a single EVOLVE block so the marker-less branch also resolves. No test exercises `_apply` with a real fix patch.
- **Verify:** new mock-mode test driving `mutate.main(patch_type="fix", mock_patch=<full-code reply>)` asserts `applied=True, num_applied==1`; full suite green.
- **effort:** trivial · **→shrinks** L10, S3, M34/L17/L11/H13 residual-case reasoning.

### L5 — Add a fix-mode smoke contract so a future rewrite can't silently re-break H1 `[FND]`
- **Reviewer correction:** the proposed smoke as first drafted **cannot fire** — `validate_strategy`'s `extra_payloads` loop ([:351-364](orchestrator/harness/validate_strategy.py:351)) only checks `ok` + `required_keys`, and `patch_type` is *already* in `required_keys`, so a dropped `needs_fix` branch (returning `"diff"`) would still pass.
- **Corrected fix:** (1) extend the `extra_payloads` mechanism to support an optional per-extra `invariant` callable (`inv(out, ctx)` → error-or-None). (2) Add a `construct_mutation_prompt` fix-mode extra payload (`needs_fix=True`, a parent carrying `metadata.stderr_log`, `ancestor_inspirations=[]`) with `invariant = lambda out,_: None if out["patch_type"]=="fix" else "fix payload must yield patch_type=='fix'"`. (3) Document `needs_fix`/`ancestor_inspirations` in the `construct_mutation_prompt.py:15-36` INPUT contract. **depends_on H1.**

### H2 — Flat-0.99 novelty gate + strict `>` collapses each island to a greedy chain `[FND]`
- **⚠ Directional correction (C17 reviewer, mechanically verified — overrides the audit's & my first draft's "lower the threshold"):** the gate rejects when `max_sim ≥ threshold`. Under the **current whole-program embedding** ([run_window.py:707](orchestrator/harness/run_window.py:707) embeds the full `candidate_code`), a genuine large-program edit embeds at ~0.994, so it trips the 0.99 gate. **Lowering** the threshold to 0.96–0.97 makes *more* candidates trip the near-dup branch, not fewer — the audit's own source (ROOT_CAUSE_20260603) is self-contradictory here. So "lower" is right **only paired with diff-embedding**; under whole-program embedding you would have to **RAISE** above ~0.994 to stop false-flagging genuine progress. Also: with **H5 keep-the-better already evaluating the near-dup and keeping the better of the pair**, a false-flag is no longer an archive freeze — it's an **eval-tax + the strict-`>` plateau issue**, which lowers the urgency the audit assigned.
- **Corrected fix (3 parts):**
  1. **Representation change = the real fix (durable):** embed the **unified diff** (`code_diff`, already carried per slot) instead of / in addition to the whole program at the embed sites ([run_window.py:707,764](orchestrator/harness/run_window.py:707) + `novelty_check`). Distinct small edits then separate to **low** cosine, so a threshold ~0.96–0.97 correctly rejects **only true dups**. This is `[FND]` (changes what `novelty_check` compares) and is the core of H2 — it was mis-filed as "deferred" in the audit; it is not deferrable, because the threshold knob points the wrong way without it.
  2. **`>=` keep-better** ([run_window.py:840](orchestrator/harness/run_window.py:840)) — relax `>` to `>=` (or `> inc − novelty_tie_epsilon`, default `>=`) so an equal-scoring distinct near-dup is KEPT (incumbent still tombstoned) → restores plateau traversal. Independent of the representation change; correct either way. (Reviewer: `>=` alone does NOT restore diversity — it still tombstones the equal loser; pair it with #1.)
  3. **Size-aware threshold becomes a tuning knob *after* #1:** with diff-embedding, optionally scale the cut for large programs (~0.96–0.97); without #1, the only honest whole-program stop-gap is to RAISE the large-program threshold above ~0.994 (weak — true dups and genuine edits both sit ~0.99+).
- **Docs:** rewrite `novelty_check.py` docstring + SKILL.md:246-247,653,689-693 to the **conditional** teaching (whole-program → genuine edits cluster ~0.994 above the gate; the durable fix is diff-embedding then a ~0.96–0.97 cut; with keep-the-better a false-flag is an eval-tax not a freeze; do NOT bare-flip "raise"→"lower"). **This is exactly the M41 correction — land them together.**
- **depends_on:** M34 + N6 (kept-better observable/contract-uniform before verification). **effort:** medium–large (the diff-embedding representation change is the bulk). **→shrinks** M34, L17, parts of L11/H13, M46.

---

### ✅ Wave 1 verification (run before moving on)

> **STATUS 2026-06-13 — Wave 1 CODE LANDED + GREEN (74 passed).** H1 (routing + regression test), L5 (invariant-mechanism + fix-mode smoke, positive+negative proof), N6 (contract), H2 part 2 (`>=` keep-better), and H2 core (diff-embedding via `evo.novelty_embed_mode`, default `"diff"`; ruled by the owner) all landed with passing tests (`mutate_fix_routes_to_full_applier`, `h2_diff_embedding_separates_distinct_edits`). Items 3 & 6 below (live warmup repair-applies; multi-window pool-growth) are **mechanism-verified by unit tests**; a full live/mock multi-window confirmation is pending. SKILL.md novelty teaching (M41 + conditional framing) is batched into the Wave 3 doc pass (with the doc-lint gate). No commit yet (awaiting owner go-ahead).

1. `conda run -n shinka python -m pytest orchestrator/tests -q` → green (baseline + new tests). ✅ 74 passed.
2. **H1:** new mock test `mutate.main(patch_type="fix", <full-code reply>)` → `applied is True, num_applied==1`; existing diff/full/cross mock tests still pass.
3. **H1 end-to-end:** a warmup window whose first eval fails then repairs now applies the repair (`fix_success>0`, was ~0) — confirm via the trace/steps (or the M33 `fix_eval` record once Wave 5 lands).
4. **L5:** `validate_strategy.py construct_mutation_prompt.py` → `valid=true` with the fix-mode payload exercised; temporarily stubbing the `needs_fix` branch makes the smoke **FAIL** (proves the gate arms).
5. **H2:** (a) two distinct small edits embed to LOW cosine under diff-embedding (new test); (b) a true duplicate still rejects at the configured cut; (c) an equal-score distinct near-dup is **KEPT** (archived) under `>=`, incumbent tombstoned — not dropped; (d) `test_parity.py` + `test_novelty_*` green.
6. **Sanity:** a short mock run shows each island's archived-correct pool GROWING across windows (not pinned at ~1 genotype).

---

## Wave 2 — Fail-open safety nets + the foundation-additive diagnostics batch `[FND]`

> **Batch the diagnostics additions in ONE edit** (they all add output keys to `orchestrator/scripts/diagnostics.py`, which is the immutable sensor contract): `eval_total` (H4), `llm_bandit_window_counts` (H5), `errored_tombstoned_count`/`tombstone_reason` flow (H3), `novelty_kept_better`/`embed_failures`/`novelty_idle_count`/`novelty_evict_fail_count` (M29/M34/L17), `meta_health` is attached by run_window not diagnostics (M14). Then wire each consumer.

### H4 — Zero-eval measure window reads as "perfect" → fail closed on it `[FND]`
- **Reviewer correction (critical):** keying the guard on `eval_total==0` would **redden 3 passing rollback tests** (they omit `eval_total`, so `.get(...,0)==0` is true on valid flat windows). **Key it solely on `apply_failure_rate >= 1.0`.**
- **Fix:** (1) diagnostics emits `eval_total` (observability only). (2) [rollback_decision.py:103](orchestrator/harness/rollback_decision.py:103) — add `_no_evals = float(measure.get("apply_failure_rate",0.0) or 0.0) >= 1.0` to the fail-closed condition; reason string "measure window evaluated zero candidates (all apply-exhausted) — fail closed". A valid flat window has `apply_failure_rate < 1.0` → not caught (preserves the K14 contract `test:755`). (3) SKILL.md:260-261,470-471 — the gate now auto-fails-closed on all-apply-exhausted; the agent no longer hand-detects via `measure_crashed`.

### H5 — Bandit-collapse arm reads run-cumulative counts (unreachable mid-run) → feed per-window counts `[FND]`
- **Reviewer correction:** the increment hook must be a **single** increment after `_parse_arm` ([run_window.py:652](orchestrator/harness/run_window.py:652)), guarded on `arm_id` — incrementing at both :635 (unconditional static) and :648 (select) double-counts. Repair-escalation changes `model_name` but not `arm_id`, so keying on `arm_id` stays correct.
- **Fix:** (1) add `counters["arm_submitted"]={}`; increment `arm_submitted[arm_id]` once per candidate after arm resolution (before the apply-exhausted early-return, mirroring `update_submitted`). (2) diagnostics emits `llm_bandit_window_counts` from it. (3) [rollback_decision.py:164](orchestrator/harness/rollback_decision.py:164) — read `measure/prior.get("llm_bandit_window_counts") or ...("llm_bandit_counts")` (cumulative fallback keeps the existing test green). Keep the cumulative `llm_bandit_counts` feeding the steady-state `model_collapse` sensor (its cumulative semantics are correct there). **obsoletes N18's "source-of-counts" half only — N18's INPUT-block additions are separate (do them too).**

### H3 — `errored_fraction` double-subtracts correct keep-better evictees → disambiguate tombstone reason `[FND]`
- **Reviewer correction:** also update the existing `test_diagnostics_sensor_fields` assertion (`_summary(5,3,tomb=2)` expects `0.0`) — it must supply `errored_tombstoned_count`; and the absent-key default must be `tombstoned_count` (NOT 0) so an old DB preserves pre-fix semantics.
- **Fix:** (1) `dbase.tombstone_program(..., reason="repair")` writes `metadata.tombstone_reason` alongside `repair_tombstoned`. (2) `repair_record.py` threads `reason`; [run_window.py:866](orchestrator/harness/run_window.py:866) keep-better call passes `reason="novelty_evict"`. (3) `archive_query` summary emits `errored_tombstoned_count` = rows with `reason != "novelty_evict" AND not correct`. (4) [diagnostics.py:155-157](orchestrator/scripts/diagnostics.py:155) — `err_tomb = int(summary.get("errored_tombstoned_count", summary.get("tombstoned_count",0)))`; `errored_fraction = max(0,(total−correct)−err_tomb)/(total−tombstoned)`. **depends_on:** consumes M34's `novelty_kept_better` mechanism. **effort:** medium.

### H13 — Stale gen-dir `metrics.json`/`correct.json` fabricates a score → pre-clean before every eval `[FND]`
- **Fix:** [evaluate.py:61](orchestrator/scripts/evaluate.py:61) (after `results_dir = payload["results_dir"]`, before submit) — `shutil.rmtree(results_dir, ignore_errors=True); os.makedirs(results_dir, exist_ok=True)`. This is the single choke point for the real path; a result-less death now yields an empty dir → `load_results` returns `correct=False`+`{}` → the `timed_out/crashed` synthesis at :130-144 fires. The candidate code lives in `gen_dir/main.<ext>` (parent of `results_dir`), so the wipe can't delete it.
- **Reviewer note (couples to L11):** keep the pre-clean scoped to `gen_dir/results` **only** — never delete `gen_dir` itself, or L11's disk-scan high-water source vanishes. **effort:** small · **→shrinks** the stale-read half of L11.

### M47 — Eval timeout kills only the `conda run` shim; the real evaluator grandchild survives `[FND]`
- **Fix:** [local.py:108-118](shinka/launch/local.py:108) start the child with `start_new_session=True` (POSIX) / `CREATE_NEW_PROCESS_GROUP` (Windows); replace `process.kill()` at [:183](shinka/launch/local.py:183) and `scheduler.py:335,433` with a `_kill_tree` using `psutil.Process(pid).children(recursive=True)` (psutil already a dep) → fall back to `os.killpg`/`taskkill`. Pairs with H13 (H13 neutralizes a late write; M47 stops the CPU/$ burn). **effort:** medium.

### H6 — Lost `run.json` + intact streams → init_run recreates a $0 ledger → recompute on restart `[FND]`
- **Fix:** [journal.py:157](orchestrator/harness/journal.py:157) `init_run` — if `run.json` missing AND `_has_journal_streams(results_dir)`: call `_reconstruct_run(...)` (recomputes `total_cost` from streams, sets `recovered_from_corruption=True`) then return; else the existing fresh-$0 path. Fresh boots write only `programs.sqlite` (no streams) so the new branch never fires spuriously. Also reconcile `SKILL.md:52-53` ("can never silently zero the ledger" — currently false for deletion). **effort:** small.

### M2 — Mid-window crash loses the window's spend → durable per-candidate cost `[FND] [RULING-lite]`
- **Fix (recommended):** add `journal.add_window_partial_cost(results_dir, gen, amount)` → `journal/window_partials.jsonl`, summed by `_recompute_total_cost` and **deduped** against any `windows.jsonl` row for the same `window_index` (so a clean window counts once). Call it after each cost fold in `_run_one_candidate`. **depends_on L70+L72** (torn-tail/unique-tmp hardening) so the new stream is crash-safe. The simpler `try/finally` partial-window marker is the fallback. **effort:** medium.

### M27 — `tau:0.0` injection shadows the 1e-3 abs_floor fallback
- **Fix:** [run_window.py:1110](orchestrator/harness/run_window.py:1110) and [diagnostics.py:101](orchestrator/scripts/diagnostics.py:101) — `evo.get("tau")` (default **None**, not 0.0) so the detector's `_DEFAULT_ABS_FLOOR=1e-3` engages when a hand-authored config omits `stagnation_abs_floor`. Record the real knobs (`stagnation_abs_floor`/`stagnation_rel_frac`/`consecutive_required`) in `config_digest` ([run_window.py:991-996](orchestrator/harness/run_window.py:991)) instead of only deprecated `tau`. **effort:** trivial.

### M29 / M34 / L17 — Novelty observability (silent embed failure, write-only kept-better counter, idle-gate=1.0) `[FND]`
- **M29:** `_embed` ([run_window.py:99-120](orchestrator/harness/run_window.py:99)) logs the swallowed exception; add `embed_failures` counter (++ at the two embed sites when novelty on); change the fallback name `"text-embedding-3-small"` → `"azure-text-embedding-3-small"` ([:113](orchestrator/harness/run_window.py:113)); emit `embed_failures` in diagnostics. **Reviewer:** remove the bogus `obsoletes N12` — N12 fixes *different* files.
- **M34:** pass `novelty_kept_better` into diagnostics + emit it; add a `kept_better_evicted` trace after the tombstone ([run_window.py:870](orchestrator/harness/run_window.py:870)); replace the fail-open `except: pass` ([:871](orchestrator/harness/run_window.py:871)) with a `novelty_evict_fail_count`++ + trace (never re-raise).
- **L17:** only `novelty_accepts++` when `nov["n_compared"]>0`; else `novelty_idle_count++`, so `novelty_acceptance_rate` is None (not phantom 1.0) for an idle gate. **All three feed H2's verifiability and rollback's `nov_drop` arm — land with/just-before H2.**

### L16 — Keep-better is destructive-first → archive-first reorder
- **Fix:** defer the incumbent tombstone until AFTER `archive_record` ([run_window.py:926-931](orchestrator/harness/run_window.py:926)); a crash in the gap then leaves both near-dups live (benign, self-heals) instead of losing both. Preserve M34's trace/counters. **depends_on M34.**

### M8 — None/NaN `combined_score` crashes pre-brief sampling (self-specced; C7 agent dropped it)
- **Fix:** [sample_parent.py:233](orchestrator/scripts/sample_parent.py:233) and [:306](orchestrator/scripts/sample_parent.py:306) — wrap the sort keys in the existing `_finite_score(getattr(p,"combined_score",0.0))` (already used on the main weighted path at :269), so a stored `None`/`NaN` can't raise `TypeError`/produce NaN probs. Optionally wrap the candidate loop ([run_window.py:1062](orchestrator/harness/run_window.py:1062)) so a sampler crash degrades rather than `--resume`-crash-loops. **effort:** trivial.

### M46 — All-tombstoned archive crash-loops → re-seed (or halt) `[RULING: auto-reseed recommended]`
- **Fix:** (1) `_bootstrap_initial` ([run_window.py:214-224](orchestrator/harness/run_window.py:214)) gates on **live** rows — add a `count_live` archive query (excludes tombstoned) and re-seed `init_program_path` when `live==0` even if `total>0`, with a journal/diag event. (2) Catch `sample_parent`'s `RuntimeError` at the call site and route to re-seed. (3) Fix the false comment at [sample_parent.py:230](orchestrator/scripts/sample_parent.py:230) (L14). **effort:** medium.

### L14 — Bootstrap fallback doesn't filter tombstones + false invariant comment
- **Fix:** [sample_parent.py:228](orchestrator/scripts/sample_parent.py:228) — `and not _is_tombstoned(p)`; correct the "correct programs are never tombstoned" comment (false since keep-better). **effort:** trivial.

---

### ✅ Wave 2 verification

> **STATUS 2026-06-13 — Wave 2 ~85% LANDED + GREEN (78 passed).** Committed in 5 sub-commits:
> 2a `H13`+`M47` (eval ground-truth); 2b `H6`+`L68/L70/L72` (ledger durability); 2c `H4`+`H5`+`M27`+`N18` (rollback safety nets); 2d `H3` (errored_fraction); 2e `M8`+`L14`+`N12` (NaN-safe sampling + azure embedding defaults). Each landed code + docs + tests.
> **STILL TODO in Wave 2** (deferred to the next continuation — all delicate/medium and best done fresh): `M29`/`M34`/`L17`/`L16`/`N4` (novelty observability — more diagnostics fields + the keep-better block reorder), `M46` (all-tombstoned re-seed), `M2` (per-candidate durable cost stream — the C6 reviewer's double-count-risk item).

1. Full suite green — the diagnostics-additive batch changed `diagnostics.py`/`archive_query` output shape, so confirm `test_diagnostics_sensor_fields` was updated and passes and the 3 `test_rollback_*` accepts are unchanged.
2. **H4:** `decide({}, {...,"apply_failure_rate":1.0})["regressed"] is True`; a valid flat window (`apply_failure_rate<1.0`) → `False`.
3. **H5:** prior+measure with balanced cumulative `llm_bandit_counts` but collapsed per-window `llm_bandit_window_counts` → `regressed True` (arm 4a now reachable mid-run).
4. **H3:** synthetic summary 10 progs / 6 correct / 1 novelty-evict tombstone → `errored_fraction==4/9`; `test_repair_mode_lifecycle` green.
5. **H13 + M47:** stale `metrics.json`/`correct.json` in a reused gen dir + a crashing/timeout eval → returned `correct=False`, synthesized `EvaluationTerminated` (NOT a fabricated score); no eval-PID descendant alive after a timeout on the conda path.
6. **H6:** delete `run.json` with streams intact → `init_run` → `total_cost` recomputed (not 0), `recovered_from_corruption=True`.
7. **M2 / L68–L72:** torn-tail append, deleted-then-restart, and partial-window-cost cases recover correctly with **no double-count** (assert the budget upper-bound test still holds).
8. **M8/M27/M46/L14/L16/L17/M29/M34** unit tests pass; a `None`/`NaN` score no longer crashes pre-brief sampling; `embed_failures`/`novelty_kept_better`/`novelty_idle_count` appear in the diag.

---

## Wave 3 — Doc corrections that mislead the orchestrator in the dangerous direction

### H7 — `meta_model: pro@high` silently kills every meta round
- **Fix (recommended — make the shorthand work AND fix docs):** in `meta_summarize.main`, immediately after reading model/effort (before the mock branch at [:330](orchestrator/scripts/meta_summarize.py:330)): `if model and "@" in model: model, effort = model.split("@",1)` (override effort). Then **CLAUDE.md:112** and **SKILL.md:343-344** → two-knob form (`meta_model: azure-gpt-5.4-pro` + `meta_reasoning_effort: high`; never a `@effort` suffix — only bandit arm ids accept that). SKILL.md:643 is already correct. **effort:** small.

### H8 — `replace` discards `evo.meta_directions` after window 1 + defeats DR grounding `[DOC]` (RULED: keep `replace`)
- **Ruling:** keep `replace` as the default — so this is **doc-only**, no code change to the compose default.
- **Fix:** (1) SKILL.md:661,669-679 — state that under the default `replace`, once any island has a brief (window 1 onward) the per-gen weighted global-direction sample is computed and **discarded**; `evo.meta_directions` only steers gens with NO island brief (window 1, an island with no brief, and — separately — the `failure_note` is appended every gen regardless). (2) DR recipe (SKILL.md:423-430) — the grounding run already wants `replace` (so the configured DR direction is what steers the pinned-pro mutation), so the recipe is now **consistent**; add the one-line note that on a populated archive each live island also has an auto-meta brief, so target the grounding via the H9 parent/island pin rather than relying on the global direction. (3) Reshapes **M1** (below): keep M1 but scope its win to the `failure_note` channel + pre-brief/no-brief islands.

### H9 / S9 — COMBINE can't target the closest program → plumb a parent/island pin
- **Fix:** add a `parent_id` override to `sample_parent.main` (alongside the existing `island_idx` one) — if it names an archived **correct** program, pin it and set `island_idx` to its island (else fall back + log). Forward `evo.grounding_parent_id`/`evo.grounding_island_idx` from `_sp_payload` ([run_window.py:487-497](orchestrator/harness/run_window.py:487)). This makes S9's "merge lands at the original entry" real. **effort:** medium.

### H10 / M17 / L37 — `island_policy_driven` is a no-op as documented + double-fires + discards results `[FND]`
- **Reviewer correction (M17):** the logging must NOT use `_trace` (a closure inside `_run_one_candidate`, out of scope at the policy call site in `_one_window`). Use `journal.log_step(cfg["results_dir"], {...})` (in scope) on success and in the `except`.
- **Fix:** (H10) decouple the policy gates from the foundation auto-trigger knobs — read `policy_spawn_enabled`/`policy_spawn_stagnation`/`policy_migrate_enabled`/`policy_migrate_interval` from the payload (defaulting to the `db_config` values for back-compat), so the policy can decide spawn/migrate without `enable_dynamic_islands`/`migration_rate>0` being set (which would double-fire the add()-time path). (M17) capture + log the executor result; never `except: pass` silently. (L37) [dbase.py:2801-2803](shinka/database/dbase.py:2801) — `done["migrated"]=bool(perform_migration(...))`. **depends_on:** none; gates M15, L30.

### H11 — `proportional` lever advice is inverted
- **[DOC]** SKILL.md:662 "When to flip" — `proportional` concentrates on the **most-populous** island (exploit a deep lineage); `weighted` on best-fitness; **neither rescues a starved small island** (no live strategy does — small-island rescue needs `archive_floor_per_island` or a `_select_island` rewrite). Land with **M9** (mark the row `(db_config)`) and **N8** (sample_parent.py:22 comment) since all three encode the same semantics. **effort:** trivial.

### H12 — Two conflicting "intervention" definitions `[DOC] [RULING: inclusive]`
- **Fix:** pick **inclusive** (knob-flip counts; matches `journal.termination_streak`'s derivation). Make SKILL.md:124-131, 514-516, CLAUDE.md:48-50, and the `journal.py:407-409` docstring all say "a framework rewrite, a DR, OR a deliberate config-lever flip; the auto-meta round does not count." **effort:** small.

### M41 — Novelty-threshold teaching contradicts the measured root cause
- **[DOC]** SKILL.md:246-247,653 — replace "large programs cluster 0.96–0.98 / false-rejects → raise" with the measured reality (~0.994 for a 10-line edit on a large program; remedy is size-aware ~0.96–0.97, auto once H2 lands). **Land with H2.**

### M5 / L88 — DR budget pre-flight + DR-at-onset attribution undocumented `[DOC]`
- SKILL.md:382-385 — "pass **both** `results_dir` AND `budget_usd`; `results_dir` alone does NOT bound DR by the budget." Add an onset-DR bullet (Boot step 4 reconciliation) with cost/work attribution.

### M40 / L85 — Grounding-run mechanics undefined `[DOC]`
- SKILL.md:418-431 → explicit checklist: in-place on the shared db (no fold-back exists); one-window override of `llm_models`/`meta_directions`/`mutation_web_search`/**`fix_web_search:true`** (fixes L85 — web search otherwise skips the fixes); a REVERT checklist for every grounding-only knob; bookkeeping = normal window; COMBINE pin via H9. **depends_on H9.**

### M9 / M12 / N8 — Sampling lever-table doc bugs `[DOC]`
- **M9:** mark `island_selection_strategy`/`enforce_island_separation` `(db_config)` (SKILL.md:662). **M12:** `validity_floor`'s real use is rescuing very-**negative**-scored valid parents — an all-equal pool is provably inert; fix SKILL.md:240-241,655. **N8:** sample_parent.py:22 comment "auto-select per `island_selection_strategy`" not "uniformly".

---

### ✅ Wave 3 verification (doc-heavy)

> **STATUS 2026-06-13 — Wave 3 core LANDED + GREEN (82 passed). ALL 13 HIGHS NOW CLEARED.**
> 3a `H7`+`H11`+`H12`+`H8-doc`+`M9`+`N8` (doc/code traps); 3b `H9` (parent/island pin for COMBINE);
> 3c `H10`+`M17`+`L37` (island_policy real lever). M41 landed with Wave 1. Tests added:
> h7_meta_model_effort_shorthand, h9_parent_pin_targets_program, h10_island_policy_decoupled_gates.
> **STILL TODO (next continuation):** Wave 3 doc tail (M5/M40 DR docs, M37/M38/M39/L-series doc
> corrections); Wave 4 subsystem mediums (islands M15/M16/M18/M28/M42; bandit M23/M24/M25/M26/M43;
> meta M1/M14/L19-L23; DR M6/L40-L46; strategy-store M19-M22/L60-L67; eval M48/M49); Wave 5 lows/nits;
> the deferred Wave 2 tail (L16 reorder, M2 cost stream); and the S1/S2 design-ruling *implementations*
> (S1 reclassify cadence as FOUNDATION; S2 warmup keep-approved fold-back — both [FND]).

1. **Doc-lint** `test_skill_doc_teaches_run_loop_and_roles` GREEN — all required phrases present, killed jargon absent (re-run after every SKILL.md/CLAUDE.md edit).
2. **grep gates:** no `@high`/`pro@high` near `meta_model` in CLAUDE.md/SKILL.md; the `proportional` "When to flip" cell matches the code (most-populous); ONE consistent "intervention" definition across the 4 sites + the journal docstring; novelty teaching uses the conditional/diff-embedding framing (no bare "raise"/"lower"); `island_selection_strategy`/`enforce_island_separation` tagged `(db_config)`; the DR section requires `budget_usd` for the pre-flight; `audit-evolution-logic.js` still parses (template literals intact).
3. **H7 code:** `meta_summarize.main(model_name="azure-gpt-5.4-pro@high")` resolves to model `azure-gpt-5.4-pro` + effort `high` (split BEFORE the mock branch).
4. **H9 code:** `sample_parent.main(parent_id=<correct prog>)` pins that parent + its island; an unknown id falls back without crashing.
5. **H10/M17/L37:** `island_policy.main` with `policy_migrate_enabled` but db_config auto-triggers OFF decides migrate; `executed.migrated` tracks the real `perform_migration` bool; the executed-actions result is journaled (not swallowed).
6. Full suite green.

---

## Wave 4 — Remaining mediums (subsystem correctness)

**Islands `[FND]`:** **M15** spawn stagnation reset — make the durable marker (`last_policy_spawn_generation`) the *primary* guard so `island_policy.main` (archive-derived) suppresses repeat spawns (the `best_score_generation` write alone is insufficient — reviewer). **M16** implement a non-destructive `retire` executor via `_evict_island` (protect island 0 + global-best) **or** delete the promise from SKILL/taxonomy `[RULING]`. **M18** migration over *active* island indices (`get_island_populations().keys()`), not `range(num_islands)` — spawned islands currently can't migrate. **M28** disambiguate `island_health.diversity` units (emit `cosine_spread`+`member_count`+`diversity_kind`). **M10** cross-island mode: return the **parent's** island as `island_idx` (+ `sampled_island_idx` for provenance) so brief/novelty key on the child's actual island. **M42** `[DOC]` default islands have no genetic interaction.

**Bandit:** **M23/M26** `[scoring]` pass a sign-aware baseline (`max(parent,0)`) so the floor stays strictly-above-fail for negative parents, and feed repairs the **pre-error** parent score (or `relative` mode for repair gens) so one repair success doesn't blow out `obs_max`. **M24** escalated-repair slots must NOT credit the cheap arm — skip the bandit feed when `_escalated`, account spend only to `counters["cost"]`. **M25** atomic bandit pickle write (`tmp`+`os.replace`) + a `bandit_state_reset` signal when a present-but-unloadable pkl is discarded (distinguish from cold start). **M43** `[RULING: geometry=foundation]` document `exploration_coef`/`cost_aware_coef`/`exponential_base` levers + run a calibration measure window after M23/M26; no silent geometry rewrite.

**Meta:** **M1** re-hydrate `evo.meta_directions`/`meta_failure_note` from the last logged meta call at startup (new `_common.recent_meta_output`) so the channel survives early-phase relaunches. *(Scoped by the H8=`replace` ruling: the primary win is the durable `failure_note` — appended to every gen regardless of compose mode — plus `meta_directions` for window-1/pre-brief/no-brief islands; under `replace`, briefs override directions per-gen once they exist, by design.)* **M14/N11** attach `meta_health` (status/coverage/`islands_missing`/`islands_hallucinated`) to the returned diag + unify the two "headline" defs (highest-weight). **L22** validate per-island coverage (skip hallucinated ids, surface omitted live islands). **L19/L23** add a per-island recency floor to meta's context (new `recent_by_island` query) so a quiet island isn't `(no recent attempts)` and a failure-heavy island still shows ≥1 assignable correct program.

**DR:** **M6** `[code-bug]` [deep_research.py:80](orchestrator/scripts/deep_research.py:80) — `range(len(candidate), 0, -1)` (the trim lower bound must be candidate-relative, not blob-coordinate) — currently silently empties valid briefs. **L45** harden `_parse_brief` for cap-hit truncation + second-fence JSON. **L40** move `get_dr_async_client()` inside the `try` so missing env → degraded `refused` envelope. **L46** cancel the abandoned poll-wall-timed-out DR job (`responses.cancel`) to stop billing the quota-constrained deployment.

**Strategy store `[FND]`:** **M21** atomic `index.json`/`meta.json` writes + read fail-**closed** on a present-but-corrupt index (provides the atomic writer the rest of the cluster depends on — land first). **M19** `deploy()` must also reject bundle-rejected hashes (shared `_hash_was_rejected` helper). **M22** `[RULING: warn-and-stamp]` — hard-requiring `results_dir` breaks `smoke_test.py:178` + 3 bundle tests; instead warn + stamp `revertible:False`. **L66** `restore_state` deletes managed state files absent at snapshot time (a measure-window-born `bandit_state.pkl` currently survives a "full rewind"). **L64** re-stamp the preserved ledger so a write failure can't silently rewind `total_cost` — but **keep copying the snapshot `run.json`** (reviewer: skipping it breaks `test:735` and the corrupt-recompute test); only RAISE on the atomic-write failure, tolerate a corrupt read. **L60** pin unresolved-deploy snapshots against pruning. **L63** all-or-nothing bundle restores. **M20** `[RULING]` snapshot during a live window — detect/flag (or refuse) via a `window_active` sentinel. **L67/L81** don't grant a fresh fair-trial streak reset on a REVERT, and version-stamp trace steps so a reused gen doesn't merge two strategy versions. **N14** `record_outcome` raises on an unmatched hash + stops fabricating phantom dirs. **M36** `[RULING]` archive reads `strategy_history` from `strategy_store.index_path()` (not `results_dir`); fix CLAUDE.md:154-156 + `.gitignore`.

**Eval foundation `[FND]`:** **M49** bump shipped `eval_time` `00:05:00`→`00:35:00` (the cnot evaluator's own invariant is `eval_time > 30-min wallclock`); SKILL.md:606 note + an optional boot-time guard. **M48** add a dependency-free real eval-foundation smoke test (correct / crash / timeout sub-cases) — **depends_on H13, M47, M49**. **L11** monotonic gen counter via disk-scan (`1 + max(_max_generation, highest gen_<k> dir)`) — reviewer: this is **cross-window/post-revert** reuse, not within-window; the disk-scan variant needs **no** journal field (so `touches_foundation:false`) and couples to H13 staying scoped to `gen_dir/results`.

---

### ✅ Wave 4 verification (per subsystem)

> **STATUS 2026-06-14 — Wave 4 ~60% LANDED + GREEN (86 passed).** Committed: 4a DR parse/env
> (M6/L45/L40); 4b eval keystone (M48 + M49); 4c bandit robustness (M25/M24/L73/L74/L75); 4d DR
> cancel + docs (L46/M5/L85/M40); 4e meta (M1 re-hydrate + M14 meta_health + L22 + N11); 4f
> strategy-store durability (M21 atomic/fail-loud index + N14 record_outcome). Tests added:
> m48_eval_foundation_smoke, dr_parse_and_env_robustness, m1_recent_meta_output_rehydrates,
> m21_index_failclosed_and_n14_record_outcome (+ auto_meta meta_health asserts).
> **STILL TODO in Wave 4:** islands (M15/M16/M18/M28/M42/L30–L39); strategy-store revert-completeness
> (M19/M20/M22/L60–L67); bandit reward baseline (M23/M26; M43 geometry = foundation→ending doc);
> M2 (per-candidate cost stream), M45 (rollback noise); meta L19/L23 (per-island context floor);
> the Wave 3 doc tail (M37/M39, L41/L43/L88, + the L-series doc corrections); Wave 5 lows/nits;
> and the S2 keep-approved [FND] fold-back. All queued + resumable from the commits + this plan.
>
> **STATUS 2026-06-14 — Wave 5d (warmup foundation, S2 throwaway half) LANDED + GREEN (88 passed).**
> Committed: S1 cadence→FOUNDATION reclassify (5c); then 5d — **M30/M35** every `--warmup`
> auto-resets `<results_dir>/warmup/` at start (no stale population leaking into a rerun);
> **M31** warmup runs a configured iters (default 3, `warmup.iters`/`--iters`), not 1; **L80**
> `cleanup_warmup` returns the REAL removal result + warns on a Windows lock; **M38** warmup
> "STOP and CORRECT" doc disambiguated (mutable-only, pre-run, no measure/revert ceremony).
> Test added: l80_cleanup_warmup_honest; SKILL.md warmup section + run-loop step 1 updated.
>
> **STATUS 2026-06-14 — Wave 5e (S2 keep-approved fold-back, [FND]) LANDED + GREEN (89 passed).**
> New `--accept-warmup` / `accept_warmup(cfg)` in run_window.py: copies the approved warmup
> archive into the real `programs.sqlite` (so `_bootstrap_initial` sees a populated, live archive
> and the real run CONTINUES from it instead of re-seeding) and folds the warmup spend into the
> real ledger as a DURABLE `warmup_accepted` intervention (recoverable by a corrupt-run.json
> recompute). Pre-creates run.json via a factored `_run_meta(cfg)` (shared with main()) so the
> add_cost path is the plain one — NOT the reconstruct path that would double-count — and main()'s
> idempotent init_run no-ops cleanly with no config_digest drift. Refuses to clobber a started run
> or accept an all-tombstoned warmup. Test added: s2_accept_warmup_folds_approved (incl. the
> durability + clobber-refusal cases); SKILL.md warmup section documents the keep-vs-discard call.
> **S2 is now COMPLETE** (both halves of the HYBRID ruling).
>
> **STATUS 2026-06-14 — Wave 5f (strategy-store revert-completeness) LANDED + GREEN (90 passed).**
> The full-rewind safety net behind the framework-audit power: **M19** a SINGLE deploy is now
> blocked by a hash a BUNDLE outcome rejected (shared `_hash_was_rejected` helper used by both
> deploy paths). **L63** `rollback_bundle` is all-or-nothing — it verifies every snapshot exists
> BEFORE copying any (no more half-rewound scripts/ on a missing snapshot). **L66** `restore_state`
> DELETES a managed state file that did NOT exist at snapshot time (a measure-window-born
> `bandit_state.pkl` / `programs.sqlite` no longer survives a "full" rewind); the ledger is exempt.
> **L64** the ledger re-stamp tolerates a corrupt READ but RAISES on a WRITE failure (the old
> blanket `except: pass` could silently leave the ledger rewound to the snapshot's lower total).
> **L60** `_prune_state_snapshots` pins any state snapshot still referenced by an UNRESOLVED
> `deployed` index entry, so a rewrite still under measurement can always be reverted. **M20**
> `snapshot_state` detects `<results_dir>/.window_active` (run_window now writes it around the
> sqlite/bandit-writing candidate loop), flags `window_active_at_snapshot` in state_meta, and
> REFUSES under `SHINKA_REFUSE_SNAPSHOT_DURING_WINDOW`. Test added: revert_completeness_cluster.
> **STILL TODO (strategy store):** M22 (warn-and-stamp `revertible:False` — verify/land), L61/M36
> (archive reads `strategy_history` from `index_path()`), L67/L81 (no fair-trial reset on revert +
> trace-step version stamping).
>
> **STATUS 2026-06-14 — Wave 5g (islands foundation) LANDED + GREEN (93 passed).** **M15** spawn
> fires ≤once per stagnation episode — `island_policy` reads a durable `last_policy_spawn_generation`
> marker (the harness carries it across windows in the diag), suppressing repeat spawns until best
> improves or `policy_spawn_cooldown` elapses. **M28** `island_health` emits `diversity_kind` +
> typed `cosine_spread`/`member_count` so a spread is never compared against a raw count. **M18**
> `ElitistMigrationStrategy.perform_migration` iterates the ACTIVE island_idx set (not
> `range(num_islands)`), so a dynamically spawned island can finally send/receive migrants. **M16**
> a non-destructive `retire_island` executor (protects island 0 + the global-best island) wired
> into `apply_island_actions` — a policy rewrite may now decide a `retire_island`. **M10**
> cross-island mode keys the child to its PARENT's island (`island_idx`) with `sampled_island_idx`
> for provenance, so the brief + novelty gate match the child's actual island. **M42** (doc) default
> islands have no genetic interaction — documented in SKILL.md. Tests added:
> islands_m15_spawn_once_and_m28_diversity_kind, islands_m18_migration_active_and_m16_retire,
> m10_cross_island_keys_child_to_parent_island. **Islands cluster COMPLETE** (M15/M16/M18/M28/M10/M42).

1. **Islands:** M15 (spawn fires ≤once per stagnation episode), M18 (a spawned idx≥`num_islands` participates in migration), M28 (`diversity_kind` discriminator present), M10 (cross-island child's island == parent's), M16 retire executor protects island 0 + global-best.
2. **Bandit:** M23/M26 (neg-parent floored arm ≠ failed arm; one repair success doesn't flip the posterior), M24 (escalated repair credits no arm; spend still in the ledger), M25 (atomic pkl + reset signal on a corrupt load).
3. **Meta:** M1 (re-hydrate `failure_note` across relaunch), M14 (`meta_health` in the returned diag), L19/L23 (per-island recency floor — no `(no recent attempts)` for a populated island).
4. **DR:** M6 (`_parse_brief` recovers a brief preceded by long prose — the offline repro returns 1 item, was 0), L40/L45/L46.
5. **Strategy store:** M21 (atomic index + fail-closed on corrupt), M19 (deploy rejects a bundle-rejected hash), L66/L64/L60 revert-completeness, M36 (archive captures `strategy_history/index.json`).
6. **Eval:** M49 (shipped `eval_time > 30-min` invariant), then **M48** — the new dependency-free eval-foundation smoke (correct / crash / timeout) GREEN. *Keystone:* it pins the field-name contract that 0 tests covered.
7. Full suite green.

---

## Wave 5 — Lows & nits (grouped; mostly doc/trivial)

**Inner loop / prompt:** **L7/N1** thread `extra_guidance`+`slow_caution_frac` into the immediate-fix construct call. **L10** carry forward original-mutation metadata when an immediate fix fails to apply. **L9** `[RULING]` cancel+floor (`max(partial_usage, ~$0.30)`) an abandoned Azure bg job. **N3** report the real `attempts` on early-exit failures. **N22** drop "circuit" vocabulary from the task-agnostic caution. **L8** `[DOC]` relabel the immediate-fix loop as foundation (not "MUTABLE fix concern"). **L2** make the no-spoil strip recurse into `metadata` (the channels `sample_fix` reads) — default path safe only because run_window blanks at source. **S3** `[RULING]` fix mode sampled-vs-latch (knob-gated, default off).

**Novelty/sampling:** **N4/N6/N12** stale `un-evaluated` comment / missing `most_similar_score` / bare embedding name (spawn_island.py:47, sample_parent.py:187). **L15** `[DOC]` novelty compares the unbounded all-time programs table, not a size-capped archive. **L12** allow `repair_attempt_cap=0` (fix the `or 2` coercion at all 5 sites + floor negatives). **L13** shift-invariant `weighted` island selection (works on negative scores). **L93** `[RULING]` gate the MAD=0 plateau softening **strictly** on `mad<eps` (reviewer: the unconditional `max()` blend changes non-plateau pools + breaks parity). **L94** ship `"seed": null` + a lever row (reproducible sampling trail). **L74** forward the seed to the bandit instance RNG + fix the misleading comment. **L73** rename the `k` clobber in `_posterior_batch`. **L75** soft-fail an unknown arm in `llm_subset`/`force_explore` (don't crash the cluster).

**Cadence/termination `[FND, gated on S1]`:** **L49/L56** harden `termination_streak`'s `float()` against a non-numeric agent row (crash-loop risk) + dedup by `window_index`; **reviewer: do NOT add the `_work_scores` type-filter** (breaks `test_work_score_readers`, which deliberately uses non-`control_return` rows) — document the contract instead. **L54** `termination_streak:0` must disable (fix `or 5` coercion). **L51** pre-cluster terminal-status/budget guard (+ finalize if over-cap-but-not-finalized). **L53** `[FND]` finalize covers budget AND stagnation; make `finalize_run` idempotent on `finished_at`. **L47** `[gated on S1]` — **drop the code change** (breaks `test_cadence_policy`); doc the constant-offset design. **L48** stale-work-score reminder (diagnostic-only). **L50** `[DOC]` mark the post-serialization windows.jsonl fields as stdout-only. **L52** `[FND]` wrap the bare `cadence_policy` call + broaden its deploy smoke (regression test if S1=foundation). **L55/L57/L58/L86/L87/L90/N13/N20** `[DOC]` fencepost note, RUN_SUMMARY.md filename + status vocabulary + stagnation_flag source, DR-cadence reconciliation, never-rules vs revert-redo, measure-window-not-a-separate-return, help-text, low-streak token disambiguation. **L59** no change (the redesigned termination contract is correct; H12+L53 close the residual phrasing).

**Budget/journal `[FND]`:** **L68/L69/L70/L72** harden the two primitives in one pass — parent-dir fsync (POSIX), torn-tail repair + log dropped lines, unique tmp name + Windows `os.replace` retry, and the reconstruction self-double-count guard. **L21** thread a realistic `meta_estimated_cost_usd` (~6.0). **L42** `[DOC]` fix CLAUDE.md:89 purpose tags (`proposer` for mutate+fix, `dr_stage_c` for DR). **L71** remove the dead-and-fatal legacy synchronous mutate branch. **N5** attribute re-embed/accept-path embed cost to the bandit arm (ledger already correct). **N15** repo-anchor `archive_run` default dest. **N23** azure-prefix the dead-path default model pool.

**Islands lows `[FND]`:** **L30** crossed-a-boundary migration cadence (don't skip on misaligned window/interval). **L31/S7** `[DOC]` global archive + per-island floor (no per-island capacity). **L32** `[DOC]` elitist migration spreads random non-elite (protects the elite). **L33** `[RULING]` document the two soft-cap `max_islands` escape hatches + surface the breach. **L34** route island-copy archive inserts through the capped path (callback). **L38** clear `meta_briefs` on `_evict_island` + journal eviction marker (reused index leaks the retired island's brief — live bug even before M16). **L39** size migrant count on eligible (correct, gen>0) members. **L44** (self-specced) verify the spawn seed is a correct/archived program; else `{ok:False}` (no unsampleable dead island). **L35/L36** delete dead `DefaultIslandAssignmentStrategy` + body-less `_get_programs_for_island`. **N9** compute `gens_stagnant` before the reset.

**Brief coupling:** **M13** restore a seeded-random archive-inspiration component to the pre-brief path. **L24** `[DOC]` `cross` gens omit the direction text. **L25** honor `num_archive_inspirations` + multi-assigned-program directions. **L26** persist/trace `sampled_direction`. **L28** `[DOC]` document the `structured_json` shape. **L29** hand-authored briefs win by default (auto-pick a winning generation).

**Boot/no-spoil `[DOC]`:** **L1** reconcile the warmup-order comment vs SKILL. **L3** task_sys_msg REPLACES BASE_SYSTEM_MSG (carry the role/iterative framing). **L89** classify spoiler-adjacent vs fair-game facts in the cnot README + CLAUDE.md route. **N21** fix the Boot-step-3 cross-reference.

**Warmup (S2-HYBRID: discard failed attempts, KEEP the approved one) `[FND]`:** the ruling makes warmup a two-mode flow. (a) **Between failed attempts — throwaway:** **M30/M35** auto-reset the warmup workspace at the *start* of each `--warmup` (so a rerun never validates a fix against the prior broken attempt's population/bandit/`errored_fraction`; depends_on L80). **M31** configurable warmup iters (default 3) + disclose `--iters`. (b) **On the GO decision — fold-back `[FND]`:** add a `run_window` path (e.g. `--accept-warmup` / a `warmup.keep:true` flag) that, when the orchestrator green-lights the *final* warmup, copies that warmup's `programs.sqlite` rows into the real `programs.sqlite` (re-keyed generations via the L11 monotonic counter so they don't collide) **and** folds its spend into the real ledger (`journal.add_cost(<real results_dir>, warmup_total)` before any cleanup). This is the upstream "kept-in-DB" semantics conditioned on a GO — schema/ledger touch, sequence with M36/L11, and **must run the fold-back BEFORE `cleanup_warmup`**. Discarded attempts' spend stays free. (c) **Observability (independent):** **M32/M33** thread `_trace` into `_attempt_immediate_fixes` (zero trace calls today — it's a sibling fn, not a closure) + enrich step records (`description`, `patch_msg_head`, per-slot apply error, `fix_attempt`/`fix_eval`). **L80** `cleanup_warmup` returns the real removal result (not a false `True` on a Windows lock — critical now that auto-reset depends on it). **L82** `[DOC]` add a healthy-trace GO-criteria subsection (depends_on M32/M33). **Docs:** SKILL.md/CLAUDE.md must state the two-mode rule — failed warmups discarded, the approved warmup's generations + spend are kept and seed the real run (supersedes the throwaway-only wording).

**Diagnostics:** **L77** flag a total single-arm monopoly (`n_active==1` with `len(subs)>=2`). **L78** `[DOC]` clarify `repair_mode_on` predicts the NEXT window (depends_on H3). **L79** `[RULING: negative-score support]` thread None-baseline so a negative-score task's first correct program doesn't fabricate a huge delta. **N16** normalize J by actual candidates (reviewer: use an explicit branch, not the buggy chained `or`). **N17** emit the detector's real `trigger_metric`. **N9** see islands.

**Docs cross-cutting (C17) `[DOC]`:** **M37** the deleted P9-T0 contract table still anchors `audit-evolution-logic.js` (lines 176,199,248,265,272,297,305) — remove the references / re-establish a behavior→code map; also drop the stale latency-routing claim (**L76**, line 265) and the old termination rule. **M38** reconcile warmup's "STOP and CORRECT the policy file" with "never modify scripts/ without the rewrite cycle." **M39** add a staying-awake recipe (heartbeat) to the multi-hour measure step. **L4** shinka-setup skill: `task.language`/`task.init_program_path` (not the pruned `evo_config.*`). **L83** num_islands engine default is 2 vs docs "4" (reconcile). **L84** prune `azure_default.yaml`'s dead surface (`meta_llm_models`, `novelty_llm_models`, `llm_kwargs`, `llm_dynamic_selection:ucb1`, `max_api_costs`) + README. **L91** fix the incoherent debug-agent spawn precondition (SKILL.md:507-508). **L92** update `.githooks/pre-push` (lints the pruned `tests/` with uv). **N2** shinka-convert frontmatter ("runner" pruned). **N19** README inventory omits a third of the mutable set + 3 foundation scripts + `test_dr.py`. **L20** delete dead `prompts_meta.py` (META_STEP1/2/3) + the audit-workflow pointer (preserve nothing else imports it). **L18/L27** fix the stale meta-summarize budget-guard + output-contract comments. **N10** delete the phantom `num_islands` meta input. **L41** delete dead `DeepResearchModel` + fix stale DR pricing/summarizer docstrings + `taxonomy.md:154-166`. **L43** reconcile the DR-timing gate (docstring vs SKILL). **L61** rewrite `strategy_history/README.md` (depends_on M36). **M11** `[RULING]` delete/quarantine the dead sampling stack + fix `taxonomy.md:41`.

---

### ✅ Wave 5 verification (lows & nits)
1. Full suite green after each subsystem batch (bundle same-file doc edits to avoid conflicting rewrites).
2. **Targeted code:** L12 (`repair_attempt_cap=0` honored at all 5 sites), L13/L93 (negative-score + plateau sampler — parity reconciled or strictly gated), L74 (a seeded run reproduces its sampler trail), L75 (a bad `llm_subset` entry no longer aborts the cluster).
3. **C17 doc/grep gates:** no `evo_config.*` in shinka-setup; no `max_api_costs`/`meta_llm_models` in `azure_default.yaml`; README inventory matches the SKILL subroutine table + lists `test_dr.py`; `audit-evolution-logic.js` has no P9-T0 / latency-routing / ≥1-DR anchors and still parses; the pre-push hook runs the real suite portably (no `uv`, no `tests/`).
4. **Doc-lint** `test_skill_doc_teaches_run_loop_and_roles` green.

---

## Suggested execution order (if all recommendations accepted)

1. **Wave 0 rulings** (S1, H12, H8, S2/L6, M36, M45, M42, M11, plus the small confirmations).
2. **H1 + L5** (repair works again) → **H2 + M34/N6/L17/M29/L16** (diversity restored & observable).
3. **Diagnostics-additive `[FND]` batch** (H3, H4, H5, eval_total/window_counts/tombstone_reason/novelty fields) wired to their consumers; **H13 + M47** (eval ground-truth); **H6 + M2 + L68/L70/L72** (ledger durability); **M27, M8, M46, L14**.
4. **Dangerous doc corrections** (H7, H8-doc, H9, H10/M17/L37, H11/M9/N8, H12, M41, M5, M40, M12).
5. **Subsystem mediums** (islands M15/M16/M18/M28/M10/M42; bandit M23/M24/M25/M26/M43; meta M1/M14/L19-L23; DR M6/L40/L45/L46; strategy-store M21→M19/M22/L60-L67; eval M49→M48; L11).
6. **Lows & nits** by subsystem (Wave 5), bundling doc edits to the same SKILL.md/CLAUDE.md sections to avoid conflicting rewrites.
7. **Deferred to the ending document** (foundation experiments the campaign shouldn't blind-edit): bandit reward *geometry* math (M43), the diff-embedding novelty representation (H2 fix #6), warmup fold-back if S2=upstream, the generic eval_time invariant contract.

## Test/verification posture
- Each fix above lists a concrete test. **Run `pytest orchestrator/tests` after each `[FND]` change**; the reviewer flagged the specific assertions several fixes would otherwise break (H3 `test_diagnostics_sensor_fields`, H4 the 3 rollback accepts, H8 compose-default tests, M4 `test_dr_refusal_graceful`, M22 `smoke_test.py:178` + bundle tests, L47/L93/L13 parity & cadence tests, L49 `test_work_score_readers`). Update those assertions in lockstep with the code.
- **M48 is the keystone test:** once H13+M47+M49 land, the new dependency-free eval-foundation smoke (correct/crash/timeout) closes the "0 real eval-path tests" gap and pins the field-name contract.

## Coverage
All 189 items are placed: H1–H13, M1–M49, L1–L95, N1–N23, S1–S9. Two findings the spec agents dropped (**M8**, **L44**) were self-specced from code above. The C17 doc cluster is covered at report-grounding; line numbers will be re-confirmed at edit time.

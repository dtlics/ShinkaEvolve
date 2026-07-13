> **STATUS: APPLIED (2026-07-13).** Historical record of the three-track audit
> (upstream parity / SimpleTES / concurrency) and its implementation batch.
> Live guidance is CLAUDE.md + .claude/skills/shinka-orchestrator/SKILL.md.

# Track 2 — SimpleTES Φ(S) prompt construction + RPUCG selection: what to adopt

Sources verified against: SimpleTES clone (`scratchpad/simpletes/simpletes/{policies/rpucg.py, policies/base.py, generator.py, templates/generation.py}`), paper PDF (arXiv 2604.19341), and our fork (`orchestrator/scripts/{construct_mutation_prompt.py, sample_parent.py, record_policy.py, meta_summarize.py}`, `shinka/core/sampler.py`, `shinka/database/dbase.py`). Prior audit reports: `audit/reports/{simpletes-paper,simpletes-code,ours-prompts,ours-orchestrator,upstream-core}.md`.

---

## 1. Φ(S) vs our `construct_mutation_prompt.py`

SimpleTES Φ(S) = history selection (policy `select()`) + prompt formatting (`generator.py:242-348`,
`templates/generation.py`). Ours = `sample_parent.py` (selection) + `construct_mutation_prompt.py` →
`shinka.core.PromptSampler` (formatting). Side-by-side, per sub-mechanism:

| Sub-mechanism | SimpleTES | Ours | Whose is better? |
|---|---|---|---|
| Task statement | static instruction `x0` + eval configs (timeout/limits), always present | orchestrator-authored `task_sys_msg` + `objective_brief` rendered next to live metrics; runtime caution is *conditional* on observed slow/timeout (`construct_mutation_prompt.py:140-176`) | **Ours.** Richer and adaptive; their static eval-config line adds nothing we lack. |
| Exemplar panel width | `num_inspirations` default 5; **paper ablation (Table 18): sweet spot 3–5; insp=1 marginal, insp=10 crowds context** | **≤2 programs** (`num_top_k_inspirations=1` + `num_archive_inspirations=1`, `shinka/database/dbase.py:65-66`, not overridden in `configs/orchestrator_run.default.json`) plus the cross partner on cross gens | **Theirs — this is their single best-evidenced Φ finding and we sit at the "marginal" end.** See P1. |
| Exemplar serialization | score + full metrics dict + optional reflection + **full code**, sorted score-**descending**, no truncation | score + public metrics + **always-on evaluator `text_feedback`** + full code, sorted **ascending** (best last = recency-position bias, upstream-deliberate) | **Tie.** Text feedback is a real edge they lack (their `m` is thin verifier messages). No evidence on sort order; keep ascending. |
| Failure memory | **deterministic per-chain error histogram**: top-10 error signatures with frequency %, auto-accumulated (`base.py:317-328`, `generator.py:120-150`). **Table 19: this is the strong foundation; reflections on top are marginal** | `failure_note` written by the per-window **meta LLM** (`run_window.py:1571-1573`) — depends on the LLM noticing and summarizing; no deterministic aggregation anywhere | **Theirs.** An LLM-mediated failure memory can drop or dilute the dominant error; a counted histogram cannot. See P2. |
| Reflection memory | per-committed-node LLM "Approach:/Insight:" reflection (1 extra LLM call per batch), appended to inspirations | none per-node; the per-window meta round (1 call/window reading the whole archive) writes per-island direction lists + failure note | **Ours for our cost model.** Their own ablation says reflection is marginal once failure patterns exist; per-node reflection would add ~1 Azure call per candidate. Do not adopt. |
| Directional guidance | none — generic "try diverse approaches" bullet | per-island structured meta briefs; ONE direction weight-sampled per gen with its `assigned_program_ids` as exemplars (`sample_parent.py:333-347`); `extra_guidance` lever | **Ours, decisively.** This is the biggest structural thing they lack. |
| Anti-redundancy in the panel | **anti-inbreeding**: greedy pick excluding 1-hop neighborhood (self+parents+children) of already-picked nodes (`rpucg.py:213-231`) | none — top-k/direction-assigned picks can include the parent's own ancestor/child (redundant context) | **Theirs.** Cheap, deterministic, riskless. See P4. |
| Mutation encoding | full-rewrite only (`EXACT_PREFIX + block + SUFFIX`), no diffs | diff (0.55) / full (0.3) / cross (0.1) / fix (0.05) with 5 full-format variants | **Ours.** Diff mode saves output tokens; cross with embedding-most-distant partner is recombination they approximate only via multi-parent context. |
| Packages / warm-start artifacts | `requirements.txt` package list; `GLOBAL_BEST_CONSTRUCTION` artifact re-injected into eval subprocess | neither | Packages: irrelevant (fixed conda env; one line in `task_sys_msg` at boot if ever needed). Artifact injection: touches the evaluator = **foundation**; ending-document idea only. |
| Context budget | none (only 240-char error clips) | none (only error-text caps at source) | Tie; neither budgets the prompt. Not a SimpleTES adoption point. |

**Verdict on "is theirs better":** Not wholesale. Theirs is a *leaner prompt around a wider,
better-deduplicated exemplar panel plus a deterministic failure histogram*; ours is a *richer,
directed prompt* (islands, meta directions, always-on text feedback, objective gloss, runtime
caution) with a **too-narrow panel (2)** and **LLM-only failure memory**. For a long-running
island search with per-window meta rounds, adopt exactly their two evidenced wins (panel width,
failure histogram) and their anti-inbreeding trick; keep everything else ours.

---

## 2. RPUCG vs our selection stack

Theirs (`rpucg.py`): recompute-from-scratch DAG value `U_i = max(r_i, γ·max_{j∈Ch(i)} U_j)` (γ=0.8),
global percentile ranks for both Q and prior P, selection `U-rank + c·P-rank·√(1+T)/(1+n_i)` where
`n_i` = times node i was used as an inspiration (per-chain counts), then greedy anti-inbreeding pick.

Ours: uniform island draw → same-island pool → weight `sigmoid(10·(s−median)/MAD) × 1/(1+children_count)`
(`sample_parent.py:300-313`); inspirations from meta-brief directions or top-k; the `AsymmetricUCB`
bandit is over **LLM arms**, orthogonal to node selection.

**Where RPUCG genuinely helps:**

1. **Lineage credit assignment.** Our weight looks only at a node's own score, and our
   `1/(1+children_count)` factor actively *penalizes* fertile parents (coverage motive). A
   mediocre-scoring node whose descendants turned out strong is invisible to us as a re-branch
   point; RPUCG's γ-propagated `U` makes "this subtree pays off" a first-class signal. Our archive
   already stores `parent_id` + `children_count` + `archive_inspiration_ids` per row
   (`dbase.py:170-189, 440-457`), so `U` is computable inside `sample_parent.py` from
   `get_all_programs` with no schema change.
2. **Principled staleness exploration.** Their exploration bonus decays with inspiration-usage
   count `n_i`. We count committed children but never count how often a program was *shown as an
   inspiration*, so a program can saturate prompts without any decay.
3. **Panel de-duplication** (anti-inbreeding) — covered in §1.

**Where our island+meta design already covers RPUCG's job:**

- **Global exploration structure**: their C chains are unmanaged parallel trajectories; our islands
  have separation, spawn/migration policy, per-island differentiated meta directions, and
  novelty-gated admission. RPUCG's per-chain visit counters are a much weaker diversity mechanism.
- **Semantic credit assignment**: the per-window meta round reads the entire archive (every row +
  top code) and routes directions to islands — functionally their `llm_elite` selector, which
  **tied RPUCG in their own ablation** (Table 18: differences "relatively modest"; the paper
  concedes budget scaling, not the selector, is the primary driver, `paper_text:2953-2958`).
- Their implementation is also weaker than the paper's pitch: values recomputed O(n) per select with
  correctness resting on timestamp order, per-chain visit counts vs global |S| in the formula, and
  no persistence of V.

**Verdict:** Do **not** replace the sigmoid sampler with a PUCT clone — expected gain is small by
their own numbers, and it would discard the parity-tested `WeightedSamplingStrategy` math plus the
island semantics for a mechanism whose one formal-theory section explicitly excludes Φ/selector
complexity. **Adopt the one idea our stack truly lacks — γ-propagated lineage value — as a bonus
term blended into the existing weight (P3), plus anti-inbreeding (P4) and inspiration-usage
counting (P4b).** All are deterministic, in-file `sample_parent.py` changes.

---

## 3. Adoption proposals (ranked by value-for-effort)

### P1 — Widen the inspiration panel to 4 [A] — Effort S — ADOPT FIRST
- **What**: run-config lever: `db_config.num_top_k_inspirations: 2`, `db_config.num_archive_inspirations: 2`
  (read by `sample_parent.py:344-356` for both the direction-assigned slice and the pre-brief top-k path;
  rendered by `PromptSampler` unchanged).
- **Default**: 2+2 = 4 exemplars (inside their 3–5 sweet spot; we are at 2 today).
- **Benefit**: the best-evidenced Φ result in the paper (Table 18); more recombination surface per
  prompt at zero code change.
- **Risk**: longer prompts → higher input cost per mutation. Mitigation: input tokens are cheap on
  the mini/5.5 workhorses; watch `journal` per-window cost and revert the config lever at any
  control-return.

### P2 — Deterministic failure histogram appended to `failure_note` [B] — Effort S/M
- **What**: rewrite `meta_summarize.py` (MUTABLE): it already loads every archive row to build the
  meta prompt; add a pure-Python aggregation of recent errored rows (normalize `error_traceback`
  first line / error class, count per island), and append a compact
  `Top error signatures (deterministic count): ...` block to the returned `failure_note` before it
  is persisted to `evo.meta_failure_note`. Rides the existing always-rendered channel
  (`sampler.py:155-161`) — no new cross-script field through the immutable harness.
- **Default**: top-5 signatures with counts, window-scoped (last ~2 windows), ≤400 chars.
- **Benefit**: their Table 19 "strong foundation" — negative constraints the LLM cannot drop or
  dilute; complements (not replaces) the meta LLM's qualitative note.
- **Risk**: stale signatures lingering after the failure mode is fixed. Mitigation: recompute fresh
  each meta round from a recency window; it self-clears.
- **This doubles as the required minimal mid-run experiment**: snapshot → deploy the
  `meta_summarize.py` rewrite → one measure window (`--windows 1 --trace-steps`) → compare
  errored-fraction and fix-round consumption vs the prior window → revert on regression
  (standard `strategy_store` cycle).

### P3 — RPUCG-lite lineage-value bonus in parent weighting [B] — Effort M
- **What**: in `sample_parent.py`, after building the island pool: reconstruct child lists from
  `parent_id` over `archived_correct`, compute `U_i = max(s_i, 0.8·max_child U_j)` bottom-up
  (topological over parent edges; fall back to generation order), then feed `U_i` instead of raw
  `s_i` into the existing `sigmoid((·−median)/MAD)` weight. Keep the `1/(1+children_count)` factor
  (it now balances against lineage credit instead of fighting nothing). Guard: identical behavior
  when no program has children (warmup parity).
- **Default**: γ=0.8 (their default), same λ=10.
- **Benefit**: fertile-but-mediocre ancestors become re-branch points; the one credit-assignment
  signal neither our sampler nor the meta round expresses numerically.
- **Risk**: over-concentration on hot lineages → island diversity drop. Mitigation: islands still
  isolate pools; deploy via snapshot → measure-awake window watching parent-diversity /
  islands-health sensors; revert restores the parity behavior exactly.
- **Experiment**: same one-window measure cycle as P2; success metric = J-score slope and distinct
  parent count per window not degrading.

### P4 — Anti-inbreeding inspiration exclusion [B] — Effort S
- **What**: in `sample_parent.py`, when composing `top_k`/`archive_insp` (both the
  direction-assigned and pre-brief paths), exclude the parent's own `parent_id` and the parent's
  children (derivable from pool `parent_id` edges); if the pool is too small to fill the counts,
  fall back to the unfiltered ranking.
- **Benefit**: exemplar slots stop being spent on near-copies of the parent — more information per
  token, exactly the redundancy their 1-hop exclusion targets.
- **Risk**: negligible (fallback preserves old behavior on tiny islands).
- **P4b (optional, same deploy)**: have `record_policy.py` persist `top_k_inspiration_ids` into
  `metadata` so `sample_parent.py` can later count inspiration-usage `n_i`
  (`archive_inspiration_ids` is already a schema column; top-k ids currently are not persisted)
  and demote panel-saturated programs. Metadata-blob-only ⇒ still [B].

### P5 — Local best-of-K sampling [C] — between runs only, and only for cheap-eval tasks
- **What**: their `K` (one prompt → K candidates → evaluate all → commit argmax) needs parallel
  candidate evaluation and a multi-sample slot in `run_window.py` — immutable harness + JSON
  contract = foundation change.
- **Verdict for the active task**: **do not adopt.** `cnot_grid_synth` evals run up to 35 min
  (`task.eval_time`); eval wall-clock, not LLM sampling, is our bottleneck — K=2 alone doubles it.
  Their K=16 default presumes seconds-scale evaluators. Record in the ending document as an option
  gated on a future task with sub-minute evals; their own scaling data says K only compounds at
  large depth L.

### NOT worth adopting (with reasons)
1. **Per-node LLM reflection (Approach/Insight)** — one extra Azure call per committed candidate;
   their own Table 19 shows marginal gain once failure patterns exist; our always-on evaluator
   `text_feedback` + per-window meta round deliver the same signal at 1 call/window.
2. **Full RPUCG/PUCT replacement of the parent sampler** — their ablation shows selector choice is
   a second-order effect; it would trade our parity-tested weighted sampler + island semantics for
   new persistent visit-count state with no formal backing (paper's theory explicitly excludes Φ).
3. **Full-rewrite-only mutation encoding** — a simplification, not an improvement; diff mode is
   output-token-cheaper and our apply/retry machinery already handles failures.
4. **`GLOBAL_BEST_CONSTRUCTION` warm-start artifact injection** — modifies the eval subprocess
   contract (foundation) and is task-shape-specific; ending-document candidate at most.
5. **Best-solution restart** — the paper itself found it plateaus after one restart and excluded it
   from the main design space.
6. **Their engine substrate** (in-memory dict DB, JSON checkpoints, asyncio pipeline, no cost
   ledger) — strictly weaker than our crash-durable sqlite + journal + budget ledger; nothing to
   learn operationally.
7. **Descending inspiration sort** — no evidence either way; upstream chose ascending (best-last
   recency bias) deliberately; churn without payoff.
8. **C chains as a substitute for islands** — chains have no migration, no per-chain guidance, no
   admission gate; our island layer is a superset.

**Deployment order**: P1 (config, next control-return) → P2 (first [B] measure cycle) → P4 (bundle
with the next `sample_parent.py` touch) → P3 (own measure cycle, watch diversity) → P5 never
mid-run.

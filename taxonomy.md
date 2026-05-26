# taxonomy.md — Four-Cell Classification of Shinka's Decision Points

> Step 2 of the rewrite. From `AUDIT.md`, every decision point is placed into one
> of four cells along two axes: **does it embed an LLM call?** × **may the
> orchestrator rewrite it mid-run?** This file is the contract for what becomes a
> mutable `scripts/*.py` strategy file vs. immutable plumbing.
>
> Guiding principle from the brief: *when in doubt, lean immutable.* Freedom is
> earned by a clear stagnation signal, not granted by default.

## The two axes

```
                        MUTABLE (orchestrator may rewrite)   IMMUTABLE (never touch)
  PURE CODE (no LLM)    A  strategy policies                 B  plumbing
  EMBEDS LLM CALL       C  prompt/template editable          D  paid-service wrapper
```

- **Mutable** = the orchestrator, on a clear stagnation signal, writes a new
  version of this file via the strategy-rewrite protocol (validate → deploy →
  measure → rollback). The *entry-point signature stays fixed*; the body evolves.
- **Immutable** = correctness-critical. The SKILL.md forbids editing it. If it
  looks buggy, report to the user, don't patch.

---

## Cell A — pure-code, MUTABLE (the strategy files)

These implement decision policies with no LLM call. They are the EvoX rewrite
targets. Each becomes a `scripts/*.py` with a stable JSON contract.

| Subroutine | Wraps (from audit) | Policy it owns | Why mutable |
|---|---|---|---|
| `sample_parent.py` | `ProgramDatabase.sample_with_fix_mode` → island_sampler + parents + inspirations ([dbase.py:1372](shinka/database/dbase.py), [parents.py](shinka/database/parents.py), [island_sampler.py](shinka/database/island_sampler.py), [inspirations.py](shinka/database/inspirations.py)) | which island, which parent (power_law/weighted/beam/…), which inspirations + ordering, fix-mode fallback | exploration/exploitation balance is the #1 lever when search stalls |
| `novelty_check.py` | `NoveltyJudge.assess_novelty_with_rejection_sampling` ([novelty_judge.py:60](shinka/core/novelty_judge.py)) | embedding-cosine threshold + accept/reject/resample logic | rejection rate directly throttles diversity; classic plateau knob |
| `select_llm.py` | `AsymmetricUCB.select_llm`/`posterior`/`update` ([prioritization.py:170/601/470](shinka/llm/prioritization.py)) | arm-selection (UCB vs Thompson vs forced mix), reward shaping, cost blending | when the bandit collapses to one model and J is flat, re-exploration must be forced |
| `stagnation_detector.py` | **new** (no Shinka equivalent; cf. `is_stagnant` [dbase.py:2499](shinka/database/dbase.py)) | compute J = Δ·log(1+s_start)/√W, set `stagnation_flag` when J<τ for 2 windows | the entire meta-loop trigger; τ and the J formula are tuning surfaces |
| `island_policy.py` | `CombinedIslandManager` migrate/spawn/retire ([islands.py:498](shinka/database/islands.py)), `check_and_spawn_island_if_stagnant` | when to fork a fresh island, retire a collapsed one, migrate elites | population-structure repair when an island's diversity dies |

**Non-obvious calls, justified:**

- *Inspiration selection folded into `sample_parent.py`, not its own file.* Shinka
  already returns `(parent, archive_inspirations, top_k_inspirations)` from one
  `sample` call; splitting them would force two round-trips for one coupled
  decision. One file, one contract.
- *"Which island to sample" lives in `sample_parent.py`; "fork/migrate/retire"
  lives in `island_policy.py`.* The former runs every candidate (it's part of
  sampling); the latter runs at window boundaries (structural change). Different
  cadence → different file.
- *`novelty_check.py` sits in cell A even though the novelty judge can call an
  LLM.* The default and dominant path is the pure-code embedding-cosine gate; the
  LLM-as-judge is opt-in (`novelty_llm_models`), gated, and rare. The *policy*
  worth evolving is the threshold/accept logic, which is pure code. **Caveat
  recorded:** if a rewrite enables the LLM judge, its prompt is mutable (cell C
  semantics) but the file stays in A because the call is conditional, not
  structural. Lean-immutable on the LLM sub-call: a rewrite must not change how
  the judge response is *parsed*.
- *`select_llm.py` is mutable in selection/reward logic but must preserve the
  bandit state contract.* `get_state`/`set_state` pickle persistence
  ([prioritization.py:914](shinka/llm/prioritization.py)) is plumbing — a rewrite may change `posterior()`
  but must keep load/save compatible so resume works. The SKILL.md notes this.
- *`island_policy.py` is mutable, but the DB writes it triggers are not.* The
  *decision* (which programs migrate where, when to spawn) is policy; the
  `UPDATE island_idx` it causes goes through immutable `archive_record.py`.

---

## Cell B — pure-code, IMMUTABLE (plumbing)

No LLM, no policy worth evolving, correctness-critical. The SKILL.md marks these
out of bounds.

| Subroutine | Wraps | Why immutable |
|---|---|---|
| `evaluate.py` | `run_shinka_eval` ([wrap_eval.py:128](shinka/core/wrap_eval.py)) + `JobScheduler` subprocess ([scheduler.py](shinka/launch/scheduler.py)) | the score is ground truth; corrupting it corrupts everything downstream |
| `archive_record.py` | `add_program_async` + post-add maintenance + `_update_archive*` ([dbase.py:845/2256](shinka/database/dbase.py)) | sqlite writes, island assignment side effects, schema integrity |
| `archive_query.py` | `get`/`get_ancestry`/`get_best_program`/`get_all_programs`/`sample` reads + agent `query_db` ([tools/query_db.py](shinka/llm/agent/tools/query_db.py)) | read API the orchestrator and harness depend on; must be stable |
| `diagnostics.py` | **new** — assembles the window-end JSON from the archive; PCA/embedding recompute ([dbase.py](shinka/database/dbase.py)); `pipeline_timing` | the orchestrator's only sensor; if it lies, every decision is wrong |

**Non-obvious calls, justified:**

- *`diagnostics.py` is immutable even though it reports the J-score/stagnation
  flag.* It *calls* `stagnation_detector.py` (cell A) for the J/flag fields and
  *assembles* the rest (best_score_start/end, novelty_acceptance_rate,
  evaluation_failure_rate, llm_bandit_weights, island_health, exhausted_retry_slots).
  The reporting plumbing is fixed; only the J/flag *computation* is mutable, and
  that lives in its own cell-A file. Clean separation: sensor (B) vs. the
  threshold logic it embeds (A).
- *Early-stopping strategies (`eval_stop.py`) stay inside `evaluate.py`,
  immutable.* They are technically a policy (Bayesian/CI/Hybrid), but they govern
  *measurement*, not *search*. Letting the orchestrator weaken the evaluator to
  chase a higher J would be exactly the corruption lean-immutable guards against.
- *Complexity scoring (`complexity.py`) stays in `archive_record.py`, immutable.*
  It only feeds `archive_criteria` weighting; the criteria *weights* are config,
  not code worth rewriting mid-run.
- *Patch application engines (`apply_diff`/`apply_full`/`apply_patch_async`) are
  immutable* and live behind `mutate.py` (cell C). The orchestrator may change the
  *prompt* that produces a patch, never the parser that applies it — a SEARCH/REPLACE
  bug would silently drop edits.
- *Slot pools, async wrappers, scheduler, embedding client, cost math* — all
  immutable; they are infrastructure with no search-policy content.

---

## Cell C — LLM-embedded, MUTABLE (prompt editable, call fixed)

The body that calls the LLM and parses the response is fixed; the *prompt
construction* is the evolvable part.

| Subroutine | Wraps | Mutable part | Immutable part |
|---|---|---|---|
| `construct_mutation_prompt.py` | `PromptSampler.sample`/`sample_fix` ([sampler.py:79/261](shinka/core/sampler.py)) + templates ([prompts/](shinka/prompts/)) | the prompt assembly: how parent/inspirations/goal/recommendations/brief are framed; patch-type weighting | — (pure code; emits a string, no call) |
| `mutate.py` | `LLMClient.query` / `AgentLLMClient.run_agent` + `apply_patch_async` + `<NAME>/<DESCRIPTION>` parse + 3-retry fix loop ([async_runner.py:4498/3883](shinka/core/async_runner.py)) | the prompt it sends (delegated to `construct_mutation_prompt.py`) | the call, the response parse, the patch-apply, the retry-budget mechanics |

**Non-obvious calls, justified:**

- *`construct_mutation_prompt.py` is cell A in form (pure code) but classified C
  by intent.* It builds the prompt string and does **not** call the LLM (the call
  is in `mutate.py`). It is listed here because its whole purpose is to shape an
  LLM call, and the brief's resolution — "just stack parent+inspirations+goal;
  rewrite the construction file only on a recurring failure pattern" — is a cell-C
  prompt-engineering lever. Mechanically it's pure-code mutable; conceptually it's
  the prompt half of the LLM-embedded mutation operator. Either lens lands it in
  the mutable set. (The SKILL table marks it Mutable / No-LLM-inside, consistent
  with this.)
- *`mutate.py` body is immutable; only its prompt is mutable.* Per the brief: the
  agent must not modify response parsing, only prompt construction. The retry
  loop (3 attempts, error fed back) and patch application are correctness paths.
- *The fix proposer (`_run_fix_patch_async`, `prompts_fix`) is part of `mutate.py`*,
  not a separate file — it's the same "call LLM with error feedback, apply,
  retry" mechanic the brief describes as inner-loop retry. `prompts_fix` is the
  mutable prompt for that branch.
- *Meta recommendations (`MetaSummarizer` 3-step, [summarizer.py](shinka/core/summarizer.py)) and the
  novelty-judge prompt are cell-C-adjacent but not standalone subroutines.* Their
  prompts are mutable; their output is an *input* to `construct_mutation_prompt.py`
  / `novelty_check.py`. To honor "don't over-fragment," the meta cycle stays a
  harness-internal composition (its prompts editable like the mutation prompt),
  with `meta_summarize.py` proposed as an *optional* extraction in the plan, not a
  mandatory file. **Judgment call flagged for user review.**

---

## Cell D — LLM-embedded, IMMUTABLE (paid-service wrapper)

| Subroutine | Wraps | Why immutable |
|---|---|---|
| `deep_research.py` | `DeepResearchSummarizer` (stages A–D) ([deep_research_summarizer.py](shinka/core/deep_research_summarizer.py)) + `dr_client` (`o3-deep-research`, separate Azure resource) | wraps a ~$5 paid external service with a fixed interface and cost model; the agent *calls* and *interprets* it, never rewrites it |

**Non-obvious calls, justified:**

- *The whole 4-stage DR pipeline is immutable, not just the stage-C call.* The
  drift gate (A), novelty cache (B), and grounding (D) are the cost-control and
  quality scaffolding around an expensive call. Letting the orchestrator rewrite
  them risks blowing the budget (`dr_max_calls_per_run`) — exactly the failure
  doom-Fix-2 closed. The orchestrator's freedom is limited to *whether to call*
  `deep_research.py` and *how to use its brief*.
- *DR stage A's drift judge uses a cheap LLM but stays immutable* for the same
  reason: it gates spend. Its prompt is technically editable, but it lives inside
  the immutable DR unit; lean-immutable wins.

---

## Subroutines that are NEW (no existing code to wrap)

`stagnation_detector.py` (cell A) and `diagnostics.py`'s window-JSON assembly
(cell B) have no Shinka equivalent — they implement the EvoX window/J-score that
§9 of AUDIT.md showed is absent. They are written fresh, drawing the J formula
from the brief and the "gens since best improved" idea from `is_stagnant`.

## Existing subsystems consciously left OUT of the new harness

- **Prompt evolution** (`prompt_evolver.py` + `prompt_dbase.py`): a real
  meta-evolution mechanism, but it evolves the *task system prompt*, and the new
  orchestrator subsumes that role for *strategy code*. Preserved in the original
  tree, **not wired into the new harness initially** (off by default). Revisit in
  `NOTES.md` if useful. Treated as immutable-by-omission.
- **Agentic per-candidate proposer** (`_run_agent_proposal`): the brief forbids
  agent agency inside the per-candidate loop (cost asymmetry). `mutate.py`
  defaults to the **stateless** `_run_patch_async` path; the agentic proposer
  remains available as an *optional* mutation operator, never the heartbeat.

## One-line summary table (everything → its cell)

| Decision point | Cell | Subroutine |
|---|---|---|
| island + parent + inspiration sampling | A | `sample_parent.py` |
| novelty accept/reject + threshold | A | `novelty_check.py` |
| LLM arm selection + reward | A | `select_llm.py` |
| J-score + stagnation flag | A | `stagnation_detector.py` |
| island fork/migrate/retire/spawn | A | `island_policy.py` |
| run candidate → score+artifacts | B | `evaluate.py` |
| persist candidate + archive maintenance | B | `archive_record.py` |
| read archive (id/score/lineage/failures) | B | `archive_query.py` |
| window diagnostics JSON | B | `diagnostics.py` |
| mutation-prompt assembly | C | `construct_mutation_prompt.py` |
| LLM mutate + apply + retry | C | `mutate.py` |
| deep research brief (A–D) | D | `deep_research.py` |
| meta recommendations (3-step) | C* | harness-internal (opt. `meta_summarize.py`) |
| prompt evolution | — | out of scope (preserved in original tree) |
| early stop, complexity, patch engines, slot pools, scheduler, embeddings, cost | B | inside `evaluate.py`/`archive_record.py`/`mutate.py` |

\* prompt mutable; not a mandatory standalone file (see judgment call above).

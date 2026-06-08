export const meta = {
  name: 'audit-evolution-logic',
  description: 'Deep logic + doc audit of the ShinkaEvolve orchestrator framework: 15 concern dimensions + 5 cross-cutting sweeps, each adversarially verified, synthesized into a findings report. Read-only — flags bugs/mismatches/gaps, fixes nothing.',
  whenToUse: 'Run when you want an exhaustive correctness + code/doc-consistency + orchestrator-teachability audit of orchestrator/ (and the shinka/ it leans on) before or between evolution runs.',
  phases: [
    { title: 'Audit', detail: 'one auditor per concern dimension reads code + the docs that describe it' },
    { title: 'Verify', detail: 'an independent skeptic re-checks every finding against the cited code/doc and adds anything missed' },
    { title: 'Cross-cut', detail: 'consistency sweep, teachability critique, first-principles design critique, edge-case hunt, completeness critic' },
    { title: 'Synthesize', detail: 'dedup, grade, group, render the report' },
  ],
}

// ---------------------------------------------------------------------------
// Run parameters (args is optional so the workflow is also runnable by name).
// ---------------------------------------------------------------------------
const ROOT_OVERRIDE = (args && args.root) || null
const TODAY = (args && args.today) || 'unknown-date'

// Full file inventory, baked in so the consistency + completeness agents have a
// deterministic checklist (no separate map phase).
const INVENTORY = `
orchestrator/scripts/: _azure.py _common.py archive_query.py archive_record.py cadence_policy.py
  compute_reward.py construct_mutation_prompt.py deep_research.py diagnostics.py evaluate.py
  island_brief.py island_policy.py meta_summarize.py mutate.py novelty_check.py record_policy.py
  repair_record.py sample_parent.py select_llm.py spawn_island.py stagnation_detector.py
orchestrator/harness/: journal.py rollback_decision.py run_window.py strategy_store.py validate_strategy.py
orchestrator/subagents/: archive-analyst.md debug-agent.md
orchestrator/tests/: smoke_test.py test_improvements.py test_parity.py
orchestrator docs: orchestrator/SKILL.md orchestrator/NOTES.md
shinka/ (framework the scripts lean on): core/{config,sampler,wrap_eval}.py
  database/{dbase,islands,island_sampler,parents,inspirations,complexity,display}.py
  llm/{client,llm,query,kwargs,constants,prioritization}.py llm/agent/{dr_client,background_model}.py
  llm/providers/{model_resolver,openai,pricing,result}.py embed/{client,embedding} embed/providers/pricing.py
  prompts/{prompts_base,prompts_diff,prompts_full,prompts_cross,prompts_fix,prompts_meta,prompts_novelty,prompts_deep_research,prompts_init}.py
  edit/{apply_diff,apply_full,summary}.py utils/{eval_stop,load_df,languages,general}.py
repo docs: CLAUDE.md taxonomy.md (HISTORICAL) README.md configs/README.md
  .claude/skills/{shinka-setup,shinka-convert,shinka-inspect}/SKILL.md
  tasks/cnot_grid_synth/{README.md,evaluate.py,initial.py} examples/circle_packing/{evaluate.py,initial.py}
`.trim()

// ---------------------------------------------------------------------------
// Shared instructions every auditor gets.
// ---------------------------------------------------------------------------
const PREAMBLE = `You are a senior auditor of an LLM-driven evolutionary code-optimization framework (ShinkaEvolve, Azure-only, "Claude-as-orchestrator" rewrite). FIRST resolve the repo root PORTABLY: run \`git rev-parse --show-toplevel\` (works on macOS, Linux, and Windows Git-Bash). ${ROOT_OVERRIDE ? `An explicit root was provided: ${ROOT_OVERRIDE} — use it.` : 'Use that as REPO_ROOT.'} All paths below are relative to REPO_ROOT. ALWAYS read the actual code before trusting any doc — docs can be aspirational or stale. Use Read/Grep/Glob/Bash freely. This is a READ-ONLY audit: do NOT edit, write, or run anything that mutates the repo (no fixes — fixes are discussed later by a human).

THE SYSTEM AT A GLANCE (the design you are checking the repo against). A human "orchestrator" agent wears two hats: (1) ORCHESTRATOR — boot the task (author a no-spoil system message from the user's initial code + evaluator), and decide/run deep-research rounds; (2) OUTER-LOOP / FRAMEWORK-AUDIT — read run logs and rewrite the *mutable strategy code* when the deterministic framework itself is flawed. The inner loop runs WITHOUT the agent: sample a parent + inspirations from a per-island archive DB → call an external Azure LLM to mutate the code → retry/fix on failure → novelty-check → record code+score+metadata into the right island. A fixed number of generations = one window. After every window an automatic "meta" round asks a strong LLM for per-island future directions (recorded as per-island "briefs" so islands differentiate). A window-cluster returns control to the agent on stagnation or at a tapering boundary; the agent then does a framework-audit check and a deep-research check, records a "work score", and relaunches — until a termination criterion. A budget is hard-capped in code with a crash-durable cost ledger. Some files are FOUNDATION (schema, JSON contract, evaluator, diagnostics, journal, harness, the user's evaluate.py/initial.*) and must never change mid-run.

WHAT TO LOOK FOR (be an independent expert, not a rubber stamp — flag even tiny bugs that could affect a real run):
 • code-bug — logic that is wrong, fragile, or will crash/misbehave on a real run (off-by-one, wrong sign/direction, NaN/None/empty-collection, division-by-zero, non-atomic write, wrong island, fabricated reward, double-count, unmonitored cost, resume corruption, race).
 • code-doc-mismatch — the code does something different from what SKILL.md / CLAUDE.md / NOTES.md / the user's design says it does.
 • doc-unclear — a doc that is supposed to TEACH a future orchestrator agent how to make a decision is ambiguous, incomplete, contradictory, or would leave a competent agent guessing.
 • doc-stale — a doc references a file/function/field/lever that no longer exists or has changed.
 • phantom-lever — a config knob / flag the docs tell the agent to flip that is not actually read anywhere in code (or has no effect).
 • dead-code — a code path that is never invoked on any default route.
 • design-gap — a behavior the intended design requires that is simply not implemented (wired only halfway, or only as doc intent).
 • edge-case — a boundary state (empty archive, first window, all-errored, single island, cap-hit, budget edge, refusal) that is mishandled or unhandled.
 • inconsistency — two places in the repo that disagree (two docs, doc vs default, two defaults).

SEVERITY (rate by impact on a REAL evolution run):
 • critical — will break or silently CORRUPT a run: data loss, fabricated/false reward, ledger zeroing, eval/score corruption, wrong-island writes, run won't start or won't stop, a rewrite that can't be rolled back.
 • high — materially degrades search quality or the agent's decisions: a model locked out by a reward flaw, islands that don't actually differentiate, the brief→inspiration coupling not wired, a fail-OPEN rollback, a teachability gap that would make a fresh orchestrator make a wrong load-bearing call.
 • medium — real but bounded / recoverable / only on a non-default path.
 • low — minor correctness or robustness nit.
 • nit — cosmetic / wording.

For EVERY finding give: a precise file:line location (or several), what the design/doc INTENDS, what the code/doc ACTUALLY does, the concrete IMPACT on a run, and your confidence. Prefer fewer, well-evidenced findings over speculation — but do not suppress a real small bug. If a concern is healthy, say so in the summary and return few/no findings.`

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------
const FINDING_ITEM = {
  type: 'object',
  additionalProperties: false,
  properties: {
    title: { type: 'string', description: 'one-line finding title' },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'nit'] },
    kind: { type: 'string', enum: ['code-bug', 'code-doc-mismatch', 'doc-unclear', 'doc-stale', 'phantom-lever', 'dead-code', 'design-gap', 'edge-case', 'inconsistency'] },
    locations: { type: 'array', items: { type: 'string' }, description: 'file:line references' },
    intended: { type: 'string', description: 'what the design/doc says should happen' },
    actual: { type: 'string', description: 'what the code/doc actually does (cite evidence)' },
    impact: { type: 'string', description: 'concrete effect on a real evolution run' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
  },
  required: ['title', 'severity', 'kind', 'locations', 'intended', 'actual', 'impact', 'confidence'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    dimension: { type: 'string' },
    health: { type: 'string', enum: ['healthy', 'minor-issues', 'significant-issues', 'broken'] },
    summary: { type: 'string', description: '2-4 sentence read of this concern: what is correct, what is suspect' },
    findings: { type: 'array', items: FINDING_ITEM },
  },
  required: ['dimension', 'health', 'summary', 'findings'],
}

const VERIFIED_ITEM = {
  type: 'object',
  additionalProperties: false,
  properties: {
    title: { type: 'string' },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'nit'] },
    kind: { type: 'string', enum: ['code-bug', 'code-doc-mismatch', 'doc-unclear', 'doc-stale', 'phantom-lever', 'dead-code', 'design-gap', 'edge-case', 'inconsistency'] },
    locations: { type: 'array', items: { type: 'string' } },
    intended: { type: 'string' },
    actual: { type: 'string' },
    impact: { type: 'string' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    verdict: { type: 'string', enum: ['confirmed', 'adjusted', 'refuted'] },
    verdict_note: { type: 'string', description: 'why it stands/falls; corrected location or severity if adjusted' },
    newly_found: { type: 'boolean', description: 'true if the verifier found this, not the original reviewer' },
  },
  required: ['title', 'severity', 'kind', 'locations', 'intended', 'actual', 'impact', 'confidence', 'verdict', 'verdict_note', 'newly_found'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    dimension: { type: 'string' },
    findings: { type: 'array', items: VERIFIED_ITEM },
  },
  required: ['dimension', 'findings'],
}

const XCUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    dimension: { type: 'string' },
    summary: { type: 'string' },
    findings: { type: 'array', items: FINDING_ITEM },
  },
  required: ['dimension', 'summary', 'findings'],
}

const SYNTH_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    stats: {
      type: 'object',
      additionalProperties: false,
      properties: {
        critical: { type: 'number' }, high: { type: 'number' }, medium: { type: 'number' },
        low: { type: 'number' }, nit: { type: 'number' }, total_kept: { type: 'number' }, refuted_dropped: { type: 'number' },
      },
      required: ['critical', 'high', 'medium', 'low', 'nit', 'total_kept', 'refuted_dropped'],
    },
    top_findings: { type: 'array', items: { type: 'string' }, description: 'one-line headlines of the most important findings, highest severity first' },
    report_md: { type: 'string', description: 'the FULL markdown report body, ready to write to disk' },
  },
  required: ['stats', 'top_findings', 'report_md'],
}

// ---------------------------------------------------------------------------
// The 15 concern dimensions. Each carries an INTENDED CONTRACT (from the user's
// design + the docs) and KEY CHECKS. Known-suspect items are framed as "verify
// whether…", never asserted — the auditor confirms against CURRENT code.
// ---------------------------------------------------------------------------
const DIMENSIONS = [
  {
    key: 'boot-nospoil-setup',
    title: 'Boot, no-spoil system message, and task setup',
    files: 'orchestrator/scripts/construct_mutation_prompt.py, orchestrator/scripts/mutate.py, orchestrator/harness/run_window.py (main: sentinel guard + use_text_feedback backfill), .claude/skills/shinka-setup/SKILL.md, .claude/skills/shinka-convert/SKILL.md',
    docs: 'orchestrator/SKILL.md "Boot" section, CLAUDE.md boot guidance',
    spec: `INTENDED: At boot the orchestrator authors task_sys_msg = goal + hard constraints + the SHAPE of the score + an abstract runtime caution — and must NOT spoil the held-out metric (no hidden seeds, private numbers, exact thresholds, or eval loopholes a mutation could game). The harness REFUSES to start if task_sys_msg is missing/empty or still equals the sentinel __UNSET_AUTHOR_AT_BOOT__ (unless task.require_sys_msg:false, or --warmup which flips it off for the throwaway run). The system message must actually be injected into EVERY mutation prompt. The spoiling self-check: the evaluator's error text rides back into the fix/repair prompt via the harness backfilling stdout_log/stderr_log, gated by use_text_feedback (default true); use_text_feedback:false must COMPLETELY blank BOTH channels.
KEY CHECKS: (a) Is the sentinel guard real, correctly placed (before any LLM spend), and does the sentinel string match exactly? (b) Does require_sys_msg default true, and does --warmup actually flip it off? (c) Trace the sys-msg from run.json → construct_mutation_prompt → mutate: is it truly threaded into every mutation prompt (and the fix prompt)? (d) Does use_text_feedback:false blank BOTH stdout and stderr backfill, with no other leak path for eval text into the prompt? (e) The user's 2D-nearest-neighbor example: do the docs teach that an eval CONSTRAINT (e.g. a compilation pass) should be SURFACED in the sys msg while held-out numbers must not — i.e. is "spoil vs fair-game" teachable and unambiguous? (f) Do the setup skills emit the sentinel + teach the author-at-boot step + the evaluator contract (return a single combined NUMBER; surface errors as a rejection signal)?`,
  },
  {
    key: 'mutation-truthful-recording',
    title: 'Inner-loop mutation generation + truthful recording of failures',
    files: 'orchestrator/scripts/mutate.py, orchestrator/scripts/_azure.py, orchestrator/harness/run_window.py (_run_one_candidate / _one_window / apply-exhausted branch / immediate-fix loop), orchestrator/scripts/archive_record.py, orchestrator/scripts/record_policy.py',
    docs: 'orchestrator/SKILL.md "Failure handling: truthful recording + repair mode", "How you launch", NOTES.md P9-T0 truthful-recording row',
    spec: `INTENDED: One generation = build prompt → Azure background-poll mutate → parse <NAME>/<DESCRIPTION> + apply the patch → bounded apply-retry (max_patch_attempts) with the apply error fed back → if apply is EXHAUSTED: NO candidate was produced; the slot is recorded as a TRUE failed attempt (the model's COST is charged to the arm, NO reward, NOTHING archived, and crucially NOT a byte-identical parent-copy with a fabricated reward), surfaced via apply_exhausted_count → else run eval → an immediate-fix loop repairs an EVAL failure in place by re-prompting with the error up to fix_retry_budget times (default 1) → novelty → record code+score+metadata into the correct island. evaluation_failure_rate is the POST-repair rate over EVALUATED slots only.
KEY CHECKS: (a) Confirm the apply-exhausted slot is NOT stored as a parent copy and does NOT receive a positive/fabricated reward (the historical "F-INNER-1" bug) — read the exact branch in run_window where applied is False. (b) Confirm the bandit/reward update on an exhausted slot charges cost only. (c) Confirm an eval-failed child IS archived with its error metadata in the correct island (so repair mode can later target it). (d) Confirm max_patch_attempts and fix_retry_budget are wired and the error text is actually fed back. (e) Confirm every call's cost lands in the ledger. (f) Robustness: model returns applied:true but an empty/no-op diff; missing <NAME>/<DESCRIPTION>; first window with an empty archive.`,
  },
  {
    key: 'parent-inspiration-sampling',
    title: 'Parent + inspiration sampling',
    files: 'orchestrator/scripts/sample_parent.py, shinka/database/parents.py, shinka/database/inspirations.py, shinka/database/island_sampler.py, shinka/database/dbase.py (sample paths)',
    docs: 'orchestrator/SKILL.md concern map "Exploration / parent", config levers parent_selection_lambda / validity_floor / island_selection_strategy / enforce_island_separation',
    spec: `INTENDED: Sample a parent weighted by score (power-law / weighted, sharpness via parent_selection_lambda; validity_floor floors VALID parents' selection score). Sample inspirations from the SAME island by default, or cross-island when enforce_island_separation:false (this is the "information flow among islands"). Produce needs_fix / a select:"errored" path for repair-mode targeting.
KEY CHECKS: (a) Does parent selection actually weight by score in the documented direction (higher score → more likely), and does parent_selection_lambda sharpen as described? (b) Does validity_floor behave as documented (inert by default; floors valid parents when set)? (c) Does enforce_island_separation actually toggle same-island vs cross-island inspirations, and are inspirations drawn from the parent's island by default? (d) Edge cases: empty or single-program island; an all-zero-score (flat) archive — does selection degrade gracefully or crash / always pick index 0? (e) Does select:"errored" return an errored parent with NO inspirations (per the repair-mode contract)? (f) Are inspiration count / ordering deterministic and sane?`,
  },
  {
    key: 'novelty-check',
    title: 'Novelty check (near-duplicate suppression, keep-the-better)',
    files: 'orchestrator/scripts/novelty_check.py, shinka/embed/client.py, shinka/embed/embedding.py, shinka/database/dbase.py (embedding cache)',
    docs: 'orchestrator/SKILL.md diversity/novelty rows, levers code_embed_sim_threshold / reward_on_reject, diagnostics novelty_rejected_cost / novelty_acceptance_rate',
    spec: `INTENDED: After the LLM returns code, embed it and cosine-compare against cached archive embeddings; if similarity ≥ code_embed_sim_threshold (default 0.99) it is a near-duplicate → compare the two programs' EVALUATOR scores and KEEP THE BETTER one, reject the worse (this is the user's explicit requirement — it is not merely "drop the new one"). A novelty-rejected slot bills the arm's COST only by default (reward_on_reject=cost_only) vs penalize. Embedding cost is captured into the ledger.
KEY CHECKS: (a) Does it genuinely keep the BETTER-scoring of the two near-duplicates, or does it unconditionally drop the newcomer regardless of score? This is the load-bearing check. (b) Is the threshold comparison in the right direction (≥ rejects, not ≤)? (c) Is embedding cost actually added to the ledger (the historical "_embed discarded cost" bug)? (d) Behavior with <2 programs / no cached embeddings / enable_novelty:false. (e) Is the keep/drop decision recorded truthfully (and does novelty_rejected_cost / novelty_acceptance_rate reflect reality, incl. null-when-no-events)?`,
  },
  {
    key: 'meta-round',
    title: 'Automatic per-window meta round (per-island directions)',
    files: 'orchestrator/scripts/meta_summarize.py, orchestrator/scripts/island_brief.py, orchestrator/harness/run_window.py (meta block), shinka/prompts/prompts_meta.py',
    docs: 'orchestrator/SKILL.md "The automatic meta round", levers auto_meta / meta_model / meta_failures_first_frac / brief_compose_mode',
    spec: `INTENDED: Every window the harness calls meta_summarize ONCE → global directions + a failure_note + ONE differentiated direction PER LIVE ISLAND, auto-recorded as per-island briefs (via island_brief.py) so islands differentiate BY DEFAULT. The user also intends that meta ASSIGNS directions that already correspond to working code to the existing entries (vs net-new creative directions). Meta is budget-gated and wrapped so a meta failure NEVER aborts the window; its cost folds into the ledger and is NOT double-counted.
KEY CHECKS: (a) Does meta actually emit one DISTINCT direction per live island (count + differentiation), and does run_window persist each into the correct island's brief? (b) Does meta receive the live island list + the current archive as context? (c) Does the "assign already-working directions to existing program entries" behavior EXIST in code, or is it only the user's intent (a design-gap)? (d) Is a failure_note produced and surfaced? (e) Does auto_meta:false suppress BOTH the global directions AND the per-island briefs (islands keep their last brief)? (f) Is a meta exception truly swallowed (window continues)? (g) Is meta cost logged once (self-log) and not also appended as an intervention?`,
  },
  {
    key: 'brief-inspiration-coupling',
    title: 'Island-brief → inspiration coupling (random → direction-oriented switch)',
    files: 'orchestrator/scripts/construct_mutation_prompt.py, orchestrator/scripts/sample_parent.py, orchestrator/scripts/island_brief.py, orchestrator/harness/run_window.py (how briefs flow into the next window’s prompts)',
    docs: 'orchestrator/SKILL.md "Islands differentiate BY DEFAULT", levers brief_compose_mode / meta_directions / island_selection_strategy',
    spec: `INTENDED (USER LOAD-BEARING, scrutinize hardest): BEFORE any island brief exists → inspirations are RANDOM sampled code entries from the DB. AFTER a brief exists → inspiration sampling becomes DIRECTION-ORIENTED: for the parent's island, sample ONE direction among that island's meta directions; if DB program entries CORRESPOND to that direction, insert them into the prompt as well; otherwise just the direction text. It is direction-centered, with code as an optional quick reference. brief_compose_mode=replace makes the brief replace the global direction for that island.
KEY CHECKS (end-to-end producer→consumer): (a) Does the code ACTUALLY switch from random-code inspirations to direction-sampled inspirations once an island has a brief — or does it still sample random code and merely append the brief text (a half-wired design-gap)? (b) Is exactly ONE direction sampled per generation for the parent's island? (c) When code entries correspond to the sampled direction, are they attached — and HOW is "corresponds" determined (is there even a mapping from direction→programs)? (d) Does brief_compose_mode=replace actually replace the global direction for that island? (e) Trace the full path: meta writes brief → stored where → read by which sampler/prompt builder → reaches the mutation prompt. Flag any break in this chain.`,
  },
  {
    key: 'island-policy-capacity',
    title: 'Island policy: per-island capacity, max-islands cap, spawning, migration',
    files: 'orchestrator/scripts/island_policy.py, orchestrator/scripts/spawn_island.py, shinka/database/islands.py, shinka/database/dbase.py',
    docs: 'orchestrator/SKILL.md "Island structure" concern + spawn_island paragraph, levers max_islands / island_evict_strategy / enable_dynamic_islands / migration_interval / island_policy_driven',
    spec: `INTENDED: Per-island membership is size-capped → on overflow the BAD members are removed. The number of islands is capped (max_islands; 0 = unbounded). Spawning a new island (spawn_island.py) honors the cap: at the cap it RETIRES THE WORST island non-destructively (rows preserved for lineage) and reuses the index; island 0 and the current global-best island are PROTECTED. Migration moves elites among islands at migration_interval. Tombstoned / repair-failed programs are reclaimed FIRST when an island is over capacity.
KEY CHECKS: (a) Dead-code: is island_policy.py actually invoked on any DEFAULT route, or only when island_policy_driven:true (default false) — and is island_policy.main() reachable at all? (b) Does per-island eviction remove the WORST members (by the configured strategy), not arbitrary ones? (c) Does spawn_island enforce max_islands + worst-island retirement + the island-0/global-best protections exactly? (d) When an island is over capacity, are tombstones reclaimed before live programs? (e) Is "retire island" actually EXECUTED or only recommended (NOTES.md hints shinka has no native retire path)? (f) Migration semantics: does it copy/move the right programs at the right cadence, and what does it stamp as the generation?`,
  },
  {
    key: 'deep-research',
    title: 'Deep-research round + 3-scenario triage',
    files: 'orchestrator/scripts/deep_research.py, shinka/llm/agent/dr_client.py, orchestrator/scripts/spawn_island.py, shinka/prompts/prompts_deep_research.py',
    docs: 'orchestrator/SKILL.md "Deep research (the DR check on control-return)"',
    spec: `INTENDED: DR is an ORCHESTRATOR decision at a control-return (NOT a config cadence). The agent writes the query (general SOTA for the task/sub-task, with a citation; never "reproduce paper X verbatim" — that trips Azure's content filter). DR returns directions. Per technique the agent triages: NOVEL (nothing in the archive/prior directions resembles it) → GROUND it with a pro@high web-search run → give it its OWN island via spawn_island; HISTORY-SIMILAR → COMBINE it into the closest existing program via a grounding run (fix_retry_budget:1); otherwise IGNORE. A refused/failed DR call returns refused:true + a reason, logged with the query intact and NO crash. Cost self-logs via results_dir, is budget-gated, and is NOT double-counted.
KEY CHECKS: (a) Does deep_research.main return directions in a usable, documented shape? (b) Does the refusal/exception path return refused:true + reason, log the query, and not crash (the historical "DR terminal failure drops cost / crashes" risk)? (c) Is the web_search tool actually passed to the DR client, and is the long timeout set? (d) Is the 3-scenario triage supported by tooling (spawn_island for NOVEL; a grounding-run config for COMBINE) — or is any of it doc-only with no mechanism? (e) Confirm there is NO automated similarity helper (the doc says the agent judges novelty itself) — and that the docs make that judgment teachable. (f) Is DR cost captured + the per-call cap applied?`,
  },
  {
    key: 'cadence-taper',
    title: 'Outer-loop control-return + the work-score taper (cadence)',
    files: 'orchestrator/scripts/cadence_policy.py, orchestrator/harness/run_window.py (cluster loop, --until-decision), orchestrator/harness/journal.py (recent_work_score / work_low_streak), orchestrator/scripts/stagnation_detector.py',
    docs: 'orchestrator/SKILL.md "The taper", "The run loop, end to end", cadence config block',
    spec: `INTENDED: --until-decision runs windows autonomously and returns control by EXITING at the cluster boundary (re-invoking the agent). Initially control returns after EVERY window; as the recent work score stays LOW the cluster grows (e.g. 1 → 5 → 10 → 20 …) with NO ceiling (bounded only by budget / termination / stagnation). Stagnation ALWAYS returns control immediately. If the agent forgot to record a work score, the taper has no signal and CONSERVATIVELY wakes every window (and prints a reminder). cadence.max_windows_per_call is an OPTIONAL ceiling, unset by default. The SAME cluster size is both the framework-audit cadence AND the DR-check cadence (one shared rhythm).
KEY CHECKS: (a) Does cadence_policy compute the next cluster size from the work-score low-streak as documented (monotone escalation; the 1→5→10→20 shape or whatever it actually is — flag any mismatch)? (b) Does a stagnation flag short-circuit the cluster mid-way? (c) Does a MISSING work score default to wake-every-window (not to a large cluster)? (d) Is the taper truly uncapped unless max_windows_per_call is set? (e) Resume / counting: are window_index and prior_low_streak carried correctly across clusters and across --resume (no off-by-one, no double-count, no reset of the streak that would re-trigger stagnation immediately)?`,
  },
  {
    key: 'rewrite-cycle',
    title: 'Framework-audit rewrite cycle (validate→snapshot→deploy→measure→revert)',
    files: 'orchestrator/harness/strategy_store.py, orchestrator/harness/validate_strategy.py, orchestrator/harness/rollback_decision.py, orchestrator/strategy_history/',
    docs: 'orchestrator/SKILL.md "The framework-audit rewrite cycle", "tiered mutability", "What never to do"',
    spec: `INTENDED: validate → snapshot STATE (framework files + archive DB + bandit + ledger) → deploy (refuse a hash already marked rejected unless force) → measure exactly ONE window with --trace-steps (agent stays awake) → rollback_decision.decide(prior_diag, measure_diag, measure_crashed?) → accept, or restore_state = a FULL rewind of code + archive DB + bandit to the snapshot, EXCEPT the cost ledger which is NEVER rewound (spend stays counted; a revert can't be used to exceed budget) → record_outcome. decide() FAILS CLOSED: a measure window with no / NaN / crashed data is assumed worst-case and reverted. The PRIMARY collapse arm is counts-share (the weights-fraction arm is legacy/near-unreachable because a single arm's weight caps at 1−epsilon). FOUNDATION files must never be rewritten mid-run.
KEY CHECKS: (a) Does restore_state rewind code+DB+bandit while PROVABLY preserving the cost ledger? (b) Does decide() truly fail closed on crash / empty / NaN (return regressed=true)? Verify the collapse-trigger threshold is actually reachable (the historical "0.85 > 1−epsilon ⇒ dead trigger" concern — check bandit_collapse_count_frac vs the counts signal, not weights). (c) Is the rejected-hash guard enforced in BOTH deploy and deploy_bundle? (d) Does validate smoke ALL of a target's modes (e.g. select_llm's select+weights+update) so a rewrite that breaks the bandit-state snapshot is caught pre-deploy? (e) Does snapshot refuse to run while a window subprocess is live? (f) Foundation protection: is it enforced anywhere in code (can deploy() be pointed at a foundation file?), or purely doc convention? (g) Bundle atomicity on partial failure.`,
  },
  {
    key: 'budget-ledger',
    title: 'Budget hard-cap + crash-durable cost ledger',
    files: 'orchestrator/harness/journal.py (_write_json_atomic, read_run recompute, total_cost), orchestrator/harness/run_window.py (budget break), orchestrator/scripts/_azure.py (poll cap + cost), shinka/llm/agent/dr_client.py, shinka/embed/*, shinka/llm/providers/pricing.py',
    docs: 'orchestrator/SKILL.md "Safety railguards", NOTES.md ledger rows + accepted limitations',
    spec: `INTENDED: The harness sums EVERY LLM cost (mutation, the automatic meta round, deep research, embeddings) + the agent's logged interventions into total_cost and HARD-STOPS the moment cumulative spend ≥ budget_usd (return_reason=budget_exhausted; overshoot ≤ one slot). The ledger is crash-durable: run.json is written ATOMICALLY (tmp + rename), and a missing/corrupt run.json is rebuilt by recomputing total_cost from the durable journal streams (windows + interventions + calls). A per-call ~$10 cap via a max-output-token cap bounds any single call. No double-count: meta/DR self-log via results_dir, so the agent must NOT also append_intervention their cost. The one accepted gap: a boot-time embedding logged before the first window is unrecoverable by recompute.
KEY CHECKS: (a) Is _write_json_atomic genuinely atomic (write tmp, fsync?, os.replace) — or can a crash mid-write truncate run.json? (b) Does read_run actually RECOMPUTE from the streams on a corrupt/missing run.json, and does the recompute sum the SAME set of costs the live ledger does (no missed stream)? (c) Is the budget check placed so overshoot is bounded to one slot (before launching vs after recording)? (d) Is the per-call max-output cap actually applied to EACH call kind (mutate/meta/DR/fix)? (e) Is there ANY LLM call path whose cost is not added to the ledger (an unmonitored call)? (f) Double-count risk: any place that both self-logs AND appends the same cost? (g) Is embedding cost captured on BOTH accept and reject paths (or is the known reject-only asymmetry the only gap)?`,
  },
  {
    key: 'termination-endrun',
    title: 'Termination criteria + end-of-run archive',
    files: 'orchestrator/harness/journal.py (build_run_summary, finalize_run, archive_run), orchestrator/harness/run_window.py (auto-finalize on budget), orchestrator/scripts/* (interventions stream)',
    docs: 'orchestrator/SKILL.md "Termination + end of run"',
    spec: `INTENDED: Stop when (a) budget exhausted, (b) the user says stop, or (c) there have been FIVE consecutive control-returns EACH involving an intervention (a DR round or a framework change), with AT LEAST ONE of the five being a DR → stop before the sixth. This is the AGENT's judgment, read from interventions.jsonl — the harness keeps returning control and has no termination counter. The automatic per-window meta round does NOT count as an intervention. A pre-assumed/reference score in the docs does NOT end the run early. End of run: write an ending document (outcome + "Future fixes for the user before the next run" for foundation/outer-loop changes), seed it from build_run_summary, flip status via finalize_run (harness auto-finalizes ONLY on the budget-exhausted terminal return), then archive_run into orchestrator/run_archive/<run_id>__<finished_at>/.
KEY CHECKS: (a) Is the 5-consecutive-incl-≥1-DR rule purely agent judgment with NO enforcing code — and is it stated unambiguously enough that a fresh agent computes it correctly (what resets the streak? does a no-intervention return reset it?)? (b) Do build_run_summary / finalize_run / archive_run exist and return/move the right things? (c) Does auto-finalize fire ONLY on budget exhaustion (not on stagnation)? (d) Any path where the run could stop EARLY by mistake (e.g. stagnation_flag or low_streak misread as termination, or --until-decision exiting being treated as "done")? (e) Is "do not stop until a criterion is met" enforced anywhere or doc-only?`,
  },
  {
    key: 'bandit-selection',
    title: 'Model selection / bandit (reward, cost-blend, lock-out, recovery)',
    files: 'orchestrator/scripts/select_llm.py, orchestrator/scripts/compute_reward.py, shinka/llm/prioritization.py, orchestrator/harness/run_window.py (model@effort arm split)',
    docs: 'orchestrator/SKILL.md "Is a model never being picked? (the framework-audit check)", reward floors, levers cost_aware_coef / epsilon / force_explore / llm_subset',
    spec: `INTENDED: The bandit picks a model per candidate and learns each (model, effort) arm separately. Selection is latency-aware (auto-route toward fast models — the 2026-05-27 fix). reward_validity_floor floors a CORRECT candidate's reward so "correct-but-worse" beats "failed". cost_aware_coef blends reward vs cheapness; epsilon is an exploration floor; force_explore / llm_subset re-open a starved arm. model_collapse is SURFACED (counts-share) and NEVER auto-corrected in steady state — the agent judges "locked out (a reward/selection flaw)" vs "truly bad".
KEY CHECKS: (a) Does the bandit ENTRENCH the cheapest arm (the historical H3) — i.e. do cost_aware_coef + the reward floor actually prevent a good-but-pricier arm from being starved, or can a couple of early bad draws permanently lock an arm out? (b) Is latency-awareness actually present in select_llm (auto-route), matching the doc? (c) Does a novelty-rejected or apply-exhausted slot update the arm consistently with the truthful-recording contract (ties to the mutation + novelty dimensions)? (d) Is the (model,effort) arm split correct and stable across resume (get_state/set_state)? (e) Is a single arm's weight really capped at 1−epsilon (making the weights-collapse signal unreachable and counts-share the only faithful one)? (f) reward direction/sign sanity (higher score ⇒ higher reward).`,
  },
  {
    key: 'diagnostics-stagnation',
    title: 'Diagnostics sensor + stagnation detector',
    files: 'orchestrator/scripts/diagnostics.py, orchestrator/scripts/stagnation_detector.py, orchestrator/harness/run_window.py (assembles + stamps the diag)',
    docs: 'orchestrator/SKILL.md diagnostics field list + concern map, NOTES.md design-rationale one-liners',
    spec: `INTENDED: diagnostics.py assembles the per-window JSON — the agent's ONLY sensor — with the full field set documented in SKILL.md (best_score_start/end, delta, J_score/threshold, novelty_acceptance_rate [null when no events], evaluation_failure_rate, apply_exhausted_count/apply_failure_rate, timeout_count, wrong_answer_count, errored_fraction, model_collapse{top_arm,top_share,n_arms_active,collapsed}, repair_* , fix_*, llm_bandit_weights/counts, island_health[], stagnation_flag, low_streak, total/correct programs, costs, return_reason, …). stagnation_flag fires when a window stays "low" for consecutive_required windows, low = delta ≤ max(stagnation_abs_floor, stagnation_rel_frac·max(s_start,0)) (scale-free above the floor; the floor is the opening-phase bar when best≈0). errored_fraction is cumulative over NON-tombstoned programs (distinct from the per-window evaluation_failure_rate). model_collapse is computed on COUNTS-share, not weights. apply_exhausted is separate from evaluation_failure_rate (a patch that never applied is not an eval failure).
KEY CHECKS: (a) Does diagnostics.py actually PRODUCE every field the docs tell the agent to read (any phantom field the agent would look for and not find)? (b) Is the stagnation formula EXACTLY the documented one (scale-free, the max() with the floor, the right comparison)? (c) Is errored_fraction tombstone-excluded? Is evaluation_failure_rate post-repair over EVALUATED slots only (apply-exhausted excluded)? (d) Is model_collapse computed on counts, with n_arms_active / collapsed sane? (e) Division-by-zero / empty-window / fresh-archive safety — does the sensor ever crash or emit NaN where the agent (or rollback) reads a number? (f) null-vs-0 discipline (e.g. novelty_acceptance_rate null when no events) so the agent doesn't misread "no data" as "0%".`,
  },
  {
    key: 'warmup',
    title: 'Warmup oversight (throwaway-db inner-loop soundness check)',
    files: 'orchestrator/harness/run_window.py (--warmup, --cleanup-warmup, --trace-steps, _trace sinks, _one_window), orchestrator/harness/journal.py (log_step, steps.jsonl)',
    docs: 'orchestrator/SKILL.md "Warmup", the flaw-signals list',
    spec: `INTENDED: Warmup runs ONE window in a THROWAWAY workspace (<results_dir>/warmup/ with its OWN db + journal), tracing ON. The agent reads steps.jsonl (which parent the sampler chose and why, the assembled prompt summary, the code/summary the model returned and whether the patch applied, the eval result + failure type, what the framework decided next) and STOP-CORRECTs-RESTARTs on any bad step; then CLEANs UP. require_sys_msg is flipped OFF for warmup only. Warmup's narrow job is to confirm the inner loop is mechanically sound on a FRESH archive (it cannot reproduce flaws that only emerge with a populated archive).
KEY CHECKS: (a) Does --warmup truly use a SEPARATE db + journal so it can't pollute the real run's archive/ledger? (b) Does --cleanup-warmup remove the workspace, and could any warmup residue leak into the real run (shared paths, shared bandit state, shared run.json)? (c) Does tracing actually log EVERY step field the doc tells the agent to read (so the flaw-signals are genuinely observable in steps.jsonl)? (d) Does --warmup flip require_sys_msg off as documented? (e) Are the documented flaw-signals (e.g. "successful child byte-identical to parent with num_applied==0", "per-island briefs all reading the same") actually surfaced by fields that exist? (f) Is the "stay awake, monitor, stop-correct-restart" loop teachable and unambiguous?`,
  },
]

// ---------------------------------------------------------------------------
// Cross-cutting sweeps (run after the per-dimension pass so they can dedup +
// extend, and do completeness against the inventory).
// ---------------------------------------------------------------------------
const CROSSCUTS = [
  {
    key: 'consistency-sweep',
    title: 'Code↔doc consistency + stale-reference sweep',
    prompt: `Do a whole-repo CONSISTENCY sweep. Verify the docs and the code agree, exhaustively and mechanically:
 1) PHANTOM-LEVER HUNT — for EVERY knob in orchestrator/SKILL.md's run-config block AND its two lever tables ("Config levers" and the rollback_decision tuning knobs) and the db_config/evo/cadence blocks: grep the code to confirm the knob is actually READ and has the documented effect. Flag any knob that is documented but unread, read but ignored, or whose default in the doc differs from the default in shinka/core/config.py (or wherever defaults live).
 2) SUBROUTINE TABLE — for every row of SKILL.md "The subroutines" table: confirm the file exists, its Mutable/LLM flags match reality, and its stdin→stdout contract / main(payload) entry point matches the doc's one-line purpose.
 3) P9-T0 CONTRACT TABLE (orchestrator/NOTES.md "Code ↔ doc consistency contract") — for each row, open the cited file:function and confirm the code REALLY does what the row claims and the cited doc section REALLY teaches it. Flag any row whose code reference is wrong/missing or whose claim overstates the code.
 4) STALE DOCS — taxonomy.md is marked HISTORICAL but check its file references (e.g. shinka/core/novelty_judge.py, summarizer.py, prompt_evolver.py, dbase.py line numbers): list which referenced files/paths no longer exist. Do the same spot-check across CLAUDE.md, README.md, configs/README.md.
 5) MUTUAL DOC CONSISTENCY — CLAUDE.md vs orchestrator/SKILL.md vs orchestrator/NOTES.md vs the cnot task README: do defaults, model names/prices, file lists, and behavioral claims agree with each other and with code? (e.g. code_embed_sim_threshold default, repair defaults, num_islands default, the Azure deployment/price table.)
Inventory for reference:\n${INVENTORY}`,
  },
  {
    key: 'teachability',
    title: 'Orchestrator / outer-loop teachability critique (docs-only lens)',
    prompt: `Read ONLY as a fresh, capable agent who has just been handed this repo and told "you are the orchestrator + outer-loop." Read CLAUDE.md, orchestrator/SKILL.md, orchestrator/NOTES.md, the four skills/*/SKILL.md, and orchestrator/subagents/*.md. For EACH load-bearing decision the agent must make, grade whether the docs make it correctly and unambiguously:
  • author a no-spoil task_sys_msg (does the agent know precisely what is fair-game vs spoiling, with the constraint-vs-held-out-number distinction?);
  • run warmup and READ steps.jsonl to decide "mechanically sound vs stop-and-fix" (does it know what a GOOD trace looks like, not just bad signals?);
  • launch the cluster, then on control-return read the diagnostics and decide framework-audit vs DR vs nothing;
  • judge model-collapse "locked out vs truly bad" (is the rule operational?);
  • run the rewrite cycle correctly (snapshot-only-when-no-window-live, measure-while-awake, fail-closed revert, cost-not-rewound, never touch foundation);
  • write + reshape a DR query and triage its brief into the 3 scenarios;
  • record the work score AFTER acting and understand the taper it drives;
  • judge termination (the 5-incl-≥1-DR rule — could two careful agents compute it differently?);
  • write the ending document + archive.
Flag every ambiguity, contradiction, missing pre-condition, or place the agent would have to GUESS. SEPARATELY assess the two-roles split: is "orchestrator (critical-path)" vs "outer-loop/framework-audit (improvement)" cleanly drawn, or is there overlap/gap that would confuse which hat to wear and when? Also flag anything the docs assume the agent already knows that they never state.`,
  },
  {
    key: 'first-principles',
    title: 'Independent first-principles evolution-framework critique',
    prompt: `Set the repo's stated design aside and reason as an expert in evolutionary computation and LLM-driven program search (FunSearch / AlphaEvolve / ShinkaEvolve lineage). Read the actual mechanics (sampling, reward, novelty, islands/migration, meta directions, stagnation, taper, budget) in orchestrator/scripts/* and shinka/database/* and shinka/llm/prioritization.py. Independently identify where THIS framework would UNDERPERFORM or MISBEHAVE on a real run, even if it matches the user's stated design — i.e. places the stated design itself may be weak. Consider at least:
  • diversity maintenance — can the population collapse to near-duplicates or one lineage despite the novelty gate + islands? Is the novelty threshold (0.99 cosine) realistic for code embeddings, and does keeping only the better of a near-dup pair erode diversity?
  • parent-selection pressure — is the score→weight mapping too greedy / too flat? interaction with validity_floor and all-zero early archives.
  • reward calibration — does the bandit reward conflate "model quality" with "task difficulty of the sampled parent"? credit assignment to the (model,effort) arm.
  • islands & migration — do islands meaningfully explore different basins, or is the differentiation cosmetic (one direction string)? migration timing.
  • stopping / taper — can the taper starve NEEDED interventions (grow the cluster so large that a fixable stall runs for a long time before the agent is woken)? can stagnation thrash?
  • budget economics — is the 100× cost-asymmetry argument actually preserved everywhere, or are there hidden agent-in-the-loop costs?
  • reproducibility — seeding/determinism of sampling + eval; can a run be reproduced or a finding re-derived?
  • foundation-immutable-mid-run — is that scoping right, or does it forbid a change the search genuinely needs?
Give concrete, file-referenced deviations and rank them by how much they'd hurt a real run. Be a genuinely independent critic.`,
  },
  {
    key: 'edge-cases',
    title: 'Edge-case + failure-path hunt (cross-file)',
    prompt: `Hunt boundary states and failure paths across the whole pipeline by TRACING the code (not guessing). For each, decide whether it is handled or a bug, with file:line:
  • first window / empty archive (sampling, novelty with <2 programs, diagnostics rates, meta with one island);
  • ALL programs errored (repair mode trigger, sampling a parent, diagnostics errored_fraction=1, can the run make progress?);
  • single island; max_islands cap hit (spawn retiring worst while protecting island 0 AND global-best — what if they're the same, or all islands are protected?);
  • tombstone reclamation order when an island is over capacity;
  • repair two-strike → tombstone → does the program correctly leave the sampling pool but keep lineage?;
  • measure window crash / empty / NaN diagnostics → fail-closed revert (verify the worst-case assumption actually triggers);
  • non-atomic run.json crash → recompute (simulate: what if a stream line is half-written?);
  • --resume correctness (window_index, prior_low_streak, ledger, bandit state all restored consistently?);
  • budget edge — spend lands exactly on the cap; a single call that alone exceeds the per-call cap; budget hit mid-fix-retry;
  • division-by-zero in any rate (fix_rate, evaluation_failure_rate, novelty_acceptance_rate, model_collapse top_share) when the denominator is 0;
  • meta failure mid-window (is the window truly unaffected?); DR refusal mid-flow; an unpriced/cap-truncated LLM response (billed partial vs $0);
  • the self-caffeinate assertion / worktree-shinka assertion failing;
  • concurrency: any shared file written by two paths without locking.
List the ones that are genuinely mishandled as findings; note the ones that are correctly handled in your summary.`,
  },
  {
    key: 'completeness',
    title: 'Completeness critic',
    prompt: `You are the completeness critic. The audit so far covered these 15 dimensions: ${DIMENSIONS.map(d => d.title).join('; ')}. Plus cross-cutting: consistency sweep, teachability, first-principles, edge-cases.
Given the FULL inventory below, identify what was NOT audited and do a quick pass to surface anything missed:
 • Which files in orchestrator/scripts, orchestrator/harness, and the shinka/* modules the scripts depend on were NOT meaningfully covered by any dimension? Open the uncovered ones and flag any logic/contract issue.
 • Which CONTRACTS between scripts (the JSON stdin/stdout in _common.py, the exact keys each script reads/writes) were not verified end-to-end? Spot-check a couple of producer→consumer key handshakes (e.g. does mutate.py's output dict use the exact keys run_window expects? does diagnostics read the exact metadata keys record_policy writes?).
 • The tests (orchestrator/tests/*) — do they actually exercise the load-bearing contracts, or could a regression slip through green tests? Any test asserting the WRONG thing?
 • Anything in .github/, .githooks/, pyproject.toml (testpaths), configs/, defaults.py, env.py that affects how a run behaves and was overlooked.
Return concrete findings (with file:line) for real gaps; in the summary, list which uncovered files you checked and cleared.
Inventory:\n${INVENTORY}`,
  },
]

// ---------------------------------------------------------------------------
// Prompt builders
// ---------------------------------------------------------------------------
function reviewPrompt(d) {
  return `${PREAMBLE}

=== YOUR CONCERN DIMENSION: ${d.title} ===
PRIMARY CODE FILES: ${d.files}
DOCS THAT DESCRIBE IT: ${d.docs}

${d.spec}

Read the code files in full (and the relevant doc passages), trace the data/control flow end-to-end, and check the code AND the docs against the INTENDED CONTRACT and the KEY CHECKS above. Report every real finding via the schema (code-bug, code-doc-mismatch, doc-unclear, doc-stale, phantom-lever, dead-code, design-gap, edge-case, inconsistency). Set health honestly. Cite file:line for everything. Do not modify anything.`
}

function verifyPrompt(d, review) {
  return `${PREAMBLE}

=== ADVERSARIAL VERIFICATION for dimension: ${d.title} ===
A first auditor produced the findings below. Your job is twofold:
 1) For EACH finding, independently open the cited code/doc and decide: confirmed (real, location accurate, severity fair), adjusted (real but the location/severity/framing needs correcting — fix it in your output), or refuted (not actually a bug — the reviewer misread the code, the path is unreachable, or the doc actually matches). Default to REFUTED if you cannot reproduce the evidence yourself. Set verdict + a crisp verdict_note citing what you read. Carry the (possibly corrected) fields forward; set newly_found=false.
 2) Do your OWN independent pass over this dimension's files for anything the first auditor MISSED (same KEY CHECKS). Add those as findings with newly_found=true and verdict=confirmed (you found them by direct reading).

Be a skeptic: a plausible-sounding finding that you cannot verify in the code is worse than no finding. But do not refuse a real bug just because it is small.

DIMENSION CONTRACT (for your independent pass):
${d.spec}
PRIMARY CODE FILES: ${d.files}
DOCS: ${d.docs}

FIRST AUDITOR'S OUTPUT (health=${review.health}):
${JSON.stringify(review.findings, null, 1)}

Return the full reconciled finding list (verified originals + your newly-found ones) via the schema.`
}

function xcutPrompt(x, digest) {
  return `${PREAMBLE}

=== CROSS-CUTTING SWEEP: ${x.title} ===
${x.prompt}

To avoid duplicating the per-dimension pass, here is a compact digest of what the dimension auditors already (confirmed/adjusted) found — EXTEND beyond these, and only repeat one if you are adding materially new evidence or a higher severity:
${digest || '(none)'}

Report findings via the schema. Cite file:line. Do not modify anything.`
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------
log(`Auditing ShinkaEvolve orchestrator framework — ${DIMENSIONS.length} concern dimensions + ${CROSSCUTS.length} cross-cutting sweeps, each adversarially verified.`)

// Phase 1+2: per-dimension audit → verify, pipelined (each dimension flows independently).
const reviewed = await pipeline(
  DIMENSIONS,
  (d) => agent(reviewPrompt(d), { label: `audit:${d.key}`, phase: 'Audit', schema: REVIEW_SCHEMA }),
  (review, d) => {
    if (!review) return null
    return agent(verifyPrompt(d, review), { label: `verify:${d.key}`, phase: 'Verify', schema: VERIFY_SCHEMA })
  },
)

// Collect verified findings (drop refuted from the digest; keep them for the synth appendix).
const dimResults = reviewed.filter(Boolean)
const dimFindings = []
for (const r of dimResults) {
  for (const f of (r.findings || [])) {
    dimFindings.push({ ...f, source: r.dimension })
  }
}
const kept = dimFindings.filter(f => f.verdict !== 'refuted')
const refuted = dimFindings.filter(f => f.verdict === 'refuted')
log(`Per-dimension pass done: ${kept.length} findings stand, ${refuted.length} refuted.`)

// Compact digest for the cross-cutting agents.
const sevRank = { critical: 0, high: 1, medium: 2, low: 3, nit: 4 }
const digest = kept
  .slice()
  .sort((a, b) => (sevRank[a.severity] - sevRank[b.severity]))
  .map(f => `[${f.severity}] ${f.source}: ${f.title} (${(f.locations || []).join(', ')})`)
  .join('\n')

// Phase 3: cross-cutting sweeps (barrier — they read the digest to dedup/extend).
const xcuts = (await parallel(
  CROSSCUTS.map(x => () => agent(xcutPrompt(x, digest), { label: `xcut:${x.key}`, phase: 'Cross-cut', schema: XCUT_SCHEMA })),
)).filter(Boolean)
const xcutFindings = []
for (const x of xcuts) {
  for (const f of (x.findings || [])) xcutFindings.push({ ...f, source: `xcut:${x.dimension}`, verdict: 'unverified-xcut' })
}
log(`Cross-cutting sweeps done: ${xcutFindings.length} additional findings.`)

// Phase 4: synthesize into the report.
const dimSummaries = dimResults.map(r => `- ${r.dimension} [${r.health}]: ${r.summary}`).join('\n')
const xcutSummaries = xcuts.map(x => `- ${x.dimension}: ${x.summary}`).join('\n')
const allKept = kept.concat(xcutFindings)

const synthPrompt = `${PREAMBLE}

=== SYNTHESIS — compose the audit report ===
Date: ${TODAY}. This is a logic + workflow + doc audit of the ShinkaEvolve orchestrator framework. Below are (A) per-dimension health summaries, (B) every finding that survived adversarial verification, (C) unverified cross-cutting findings (treat with a touch more skepticism — DOWN-RANK or drop any you judge weak), and (D) the refuted findings (for a transparency appendix).

Your job:
 1) DEDUP findings that describe the same underlying issue (merge locations, keep the highest justified severity, note all sources).
 2) For the cross-cutting (unverified) findings, apply judgment — keep the solid ones, drop or downgrade the speculative ones.
 3) Grade and GROUP by severity (critical → high → medium → low → nit). Within each group, order by blast radius on a real run.
 4) Produce report_md: a clean, self-contained markdown report a human can read to plan fixes. REQUIRED structure:
    # Audit — ShinkaEvolve Orchestrator Logic & Workflow (${TODAY})
    ## Scope & method (one paragraph: ${DIMENSIONS.length} dimensions + ${CROSSCUTS.length} cross-cutting sweeps, adversarially verified; read-only)
    ## Executive summary (counts by severity; the 5-10 most important issues as one-liners; overall health verdict per major subsystem)
    ## Findings — Critical
    ## Findings — High
    ## Findings — Medium
    ## Findings — Low / Nits
       (each finding: a bolded title, an ID like C1/H3/M2, the concern + kind, clickable locations as relative paths like \`orchestrator/scripts/foo.py:120\`, INTENDED vs ACTUAL, IMPACT, confidence. Keep each finding tight but complete.)
    ## Code↔doc consistency (phantom levers, stale refs, contract-table accuracy — from the consistency sweep)
    ## Orchestrator teachability gaps (from the teachability critique)
    ## Independent design critique (first-principles deviations that would hurt a real run)
    ## Coverage map (the 15 dimensions × the files each touched; what was checked-and-cleared)
    ## Not audited / follow-ups (uncovered files, deferred questions)
    ## Appendix: refuted claims (title + one line why it did NOT stand — so the human knows it was considered)
 Use the real finding contents; do NOT invent issues. Keep IDs stable and referenced consistently. Fill stats accurately (count what you KEEP, and refuted_dropped = number of refuted claims).

(A) DIMENSION SUMMARIES:
${dimSummaries}

CROSS-CUTTING SUMMARIES:
${xcutSummaries}

(B) VERIFIED FINDINGS (kept):
${JSON.stringify(kept, null, 1)}

(C) CROSS-CUTTING (unverified) FINDINGS:
${JSON.stringify(xcutFindings, null, 1)}

(D) REFUTED (for appendix):
${JSON.stringify(refuted.map(f => ({ title: f.title, source: f.source, verdict_note: f.verdict_note, locations: f.locations })), null, 1)}

Return via the schema: stats, top_findings (headlines), and report_md (the full report).`

const synth = await agent(synthPrompt, { label: 'synthesize-report', phase: 'Synthesize', schema: SYNTH_SCHEMA })

return {
  stats: synth.stats,
  top_findings: synth.top_findings,
  report_md: synth.report_md,
  counts: { dimensions: dimResults.length, kept: kept.length, refuted: refuted.length, xcut: xcutFindings.length },
}

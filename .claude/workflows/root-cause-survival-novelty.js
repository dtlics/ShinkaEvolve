export const meta = {
  name: 'root-cause-survival-novelty',
  description: 'Root-cause two live-run failures: (A) backgrounded run_window dying when the session goes idle (ephemeral sandbox reclaim + missed-wake), and (B) the novelty gate rejecting every candidate at 0.99 similarity on a large program — testing whether the true cause is the mutation prompt/patch (near-identical diffs) vs the novelty threshold vs the embedding representation. Read-only; separates ROOT CAUSE from symptom and proposes fixes.',
  whenToUse: 'After a live orchestrator run stalls or dies — to find the underlying cause in the launch/wake model or the mutation/novelty mechanics, not just the surface symptom.',
  phases: [
    { title: 'Investigate', detail: 'fan out one investigator per hypothesis across the survival + novelty tracks' },
    { title: 'Verify', detail: 'an independent skeptic re-checks each root-cause claim against the code/evidence' },
    { title: 'Synthesize', detail: 'separate root cause from symptom; rule on the user hypotheses; rank fixes' },
  ],
}

const ROOT = '/Users/dantongli/GIthub/ShinkaEvolve/.claude/worktrees/goofy-tharp-229342'
const TODAY = (args && args.today) || 'unknown-date'

// ---------------------------------------------------------------------------
// The evidence from the live run (the user's other orchestrator agent).
// ---------------------------------------------------------------------------
const EVIDENCE = `
LIVE-RUN EVIDENCE (from the user's other orchestrator agent, 2026-06-03; the run was on
cnot_grid_synth with a ~370-line evolved program, on the PRE-fix code — before the keep-the-
better novelty change):

PROBLEM A — "nothing survives to wake me" (run died, NOT from sleep — machine was awake on AC):
  • The agent's diagnosis: the sandbox that runs commands is EPHEMERAL — it reclaims detached
    background jobs when the session goes IDLE; and the agent was treating the resulting
    "killed" notifications as "nothing to do" instead of relaunching.
  • The agent's fix: background tasks reliably notify on exit (complete OR killed), and those
    notifications re-invoke the agent → it now runs SMALL 2-window batches and RELAUNCHES on
    each notification (self-sustaining hands-off loop) + armed a SAFETY-NET wakeup in case a
    notification is missed.

PROBLEM B — the search was stalled by the novelty gate:
  • The novelty gate rejected EVERY candidate (3 in a row; archive frozen at 11 programs).
  • On a ~370-line program, incremental improvements are textually near-identical to their
    parent, so the 0.99 cosine-similarity threshold discarded every real gain.
  • The agent disabled novelty (a config flip, logged as a framework-audit intervention) and
    the search immediately set a new record.
  • THE USER'S HYPOTHESIS TO TEST: "such a high similarity I'd suspect it's something else's
    problem like the prompt for the mutation LLM" — i.e. the mutations may be near-identical
    because the MUTATION PROMPT / patch mechanics produce tiny/no diffs, not because the
    threshold is wrong. Determine the TRUE root cause.

IMPORTANT CONTEXT: the CURRENT code in this repo already has a keep-the-better novelty change
(H5: a near-dup is now EVALUATED and the better of the pair kept, the worse evicted) that
landed AFTER this live run. So judge (a) what caused the live stall on the OLD code, and
(b) whether the new keep-the-better behavior + the mutation/embedding mechanics actually
resolve it, or whether a mutation-prompt / representation fix is still needed.`.trim()

const PREAMBLE = `You are a root-cause investigator for an LLM-driven evolutionary code-search framework
(ShinkaEvolve, Azure-only, "Claude-as-orchestrator"). Repo root: ${ROOT} (all paths relative to it).
READ-ONLY: do NOT edit/run anything that mutates the repo or hits the network. Read the ACTUAL code
(Read/Grep/Bash/git) before concluding — and prefer code evidence over docs.

${EVIDENCE}

YOUR JOB: find the TRUE ROOT CAUSE of your assigned hypothesis, distinguishing:
 • root-cause — the underlying mechanism that, if fixed, removes the problem;
 • contributing — makes it worse but isn't the core;
 • symptom — what was observed (the surface);
 • doc-gap — the docs teach the wrong/missing operating model;
 • code-bug — a concrete defect.
For each finding: a precise file:line (or git ref), the hypothesis you tested, the EVIDENCE you found,
your verdict, a concrete recommended fix, and a confidence. Be a skeptical empiricist — if the
evidence doesn't support a hypothesis, say so and name what the evidence DOES support. Cite exact
code. Rank by how directly it explains the observed failure.`

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------
const FINDING = {
  type: 'object',
  additionalProperties: false,
  properties: {
    title: { type: 'string' },
    track: { type: 'string', enum: ['survival', 'novelty'] },
    kind: { type: 'string', enum: ['root-cause', 'contributing', 'symptom', 'doc-gap', 'code-bug'] },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
    hypothesis_tested: { type: 'string' },
    evidence: { type: 'string', description: 'what you found in the code/git, with file:line' },
    locations: { type: 'array', items: { type: 'string' } },
    verdict: { type: 'string', description: 'confirmed / refuted / partial — and why' },
    recommended_fix: { type: 'string' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
  },
  required: ['title', 'track', 'kind', 'severity', 'hypothesis_tested', 'evidence', 'locations', 'verdict', 'recommended_fix', 'confidence'],
}

const INVESTIGATE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    agent: { type: 'string' },
    summary: { type: 'string', description: '2-4 sentence bottom line: what is the root cause for this hypothesis' },
    findings: { type: 'array', items: FINDING },
  },
  required: ['agent', 'summary', 'findings'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    agent: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          title: { type: 'string' }, track: { type: 'string', enum: ['survival', 'novelty'] },
          kind: { type: 'string', enum: ['root-cause', 'contributing', 'symptom', 'doc-gap', 'code-bug'] },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          hypothesis_tested: { type: 'string' }, evidence: { type: 'string' },
          locations: { type: 'array', items: { type: 'string' } },
          verdict: { type: 'string', enum: ['confirmed', 'adjusted', 'refuted'] },
          verdict_note: { type: 'string' }, recommended_fix: { type: 'string' },
          confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
          newly_found: { type: 'boolean' },
        },
        required: ['title', 'track', 'kind', 'severity', 'hypothesis_tested', 'evidence', 'locations', 'verdict', 'verdict_note', 'recommended_fix', 'confidence', 'newly_found'],
      },
    },
  },
  required: ['agent', 'findings'],
}

const SYNTH_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    survival_root_cause: { type: 'string', description: 'the confirmed root cause of Problem A + whether the agent\'s fix-model (small batches + relaunch-on-notification + safety-net) is correct + the durable code/doc changes needed' },
    novelty_root_cause: { type: 'string', description: 'the confirmed root cause of Problem B + an explicit RULING on the user\'s hypothesis (mutation-prompt vs threshold vs embedding-representation) + whether the keep-the-better fix resolves it' },
    ranked_fixes: { type: 'array', items: { type: 'string' }, description: 'concrete fixes, most-impactful first, each tagged [survival]/[novelty] and [code]/[doc]' },
    report_md: { type: 'string', description: 'the full markdown root-cause report' },
  },
  required: ['survival_root_cause', 'novelty_root_cause', 'ranked_fixes', 'report_md'],
}

// ---------------------------------------------------------------------------
// The 6 investigators (3 per track).
// ---------------------------------------------------------------------------
const TRACKS = [
  {
    key: 'survival-model',
    label: 'A1 survival: launch/wake model vs the ephemeral sandbox',
    prompt: `TRACK A (run survival). Root-cause WHY a backgrounded \`run_window --until-decision\` dies when the session goes idle, and whether the agent's fix-model is the right durable pattern.
Read: orchestrator/SKILL.md (the launch + wake sections — "ACTUAL RUN", "How you launch the inner loop", the self-caffeinate paragraph ~lines 83-91, 126-145), CLAUDE.md (the unattended-run / "stay alive" guidance), orchestrator/harness/run_window.py (\`_hold_no_idle_sleep\`, the \`--until-decision\` cluster loop, how it "returns by exiting"). Also \`git show 3cdb0c7:orchestrator/harness/run_detached.py\` (the removed detached daemon) for contrast.
Test these hypotheses with evidence:
 1. The repo currently teaches "background-launch \`--until-decision\` and it re-invokes you on exit" as THE wake primitive. Given the live evidence (the ephemeral sandbox RECLAIMS detached jobs when the session goes idle, NOT from sleep), is that taught model unsafe? What exactly fails: the job is reclaimed mid-wait, and a "killed" notification was misread as "done".
 2. Is the agent's fix-model correct and sufficient: (a) SMALL batches (e.g. \`--max-windows-per-call 2\` / \`--windows 2\`) so each job is short-lived; (b) RELAUNCH on EVERY exit notification, treating "killed" == "relaunch with --resume", not "done"; (c) a SAFETY-NET scheduled wakeup if a notification is missed; (d) MAIN-LOOP only (a subagent's bg jobs die with it)? Identify any hole in this model.
 3. Is \`_hold_no_idle_sleep\` (self-caffeinate) doing anything useful here, or is it irrelevant because the kill is sandbox-reclaim-on-idle, not OS idle-sleep? Distinguish clearly.
 4. Does the run survive better with the removed \`run_detached.py\` (OS-detached, reparented to launchd) — and what does it cost (loses the auto-wake → must poll)? Is a detached-daemon + poll, OR the small-batch-relaunch loop, the more robust durable answer? Give a recommendation.
Return root-cause + the durable operating model + the exact doc/code changes.`,
  },
  {
    key: 'survival-resume',
    label: 'A2 survival: --resume + small-batch robustness to a mid-window kill',
    prompt: `TRACK A (run survival). The fix is to run SMALL batches and RELAUNCH (\`--resume\`) on every exit/kill. Audit whether run_window actually supports this robustly — i.e. a job killed mid-window must lose nothing important and resume cleanly.
Read: orchestrator/harness/run_window.py (\`--resume\`, \`window_state\` {window_index, prior_low_streak}, the cluster loop, where windows/programs are persisted, budget break), orchestrator/harness/journal.py (crash-durable ledger: \`_write_json_atomic\`, \`read_run\` recompute, \`init_run\`, \`append_window\`, \`recent_work_score\`/\`work_low_streak\`, \`termination_streak\`), orchestrator/scripts/archive_record.py (per-candidate durability).
Test with evidence (file:line):
 1. If run_window is KILLED mid-window (between candidates / mid-candidate), what is lost? Is each candidate durably archived as it completes (so only the in-flight candidate is lost), or can a kill corrupt the archive / lose a whole window?
 2. Does the cost LEDGER survive a hard kill (atomic run.json + recompute-from-streams)? Could a kill mid-write zero or under/over-count the ledger across relaunches?
 3. On \`--resume\`, are \`window_index\` and \`prior_low_streak\` (and the termination/taper streaks) restored CORRECTLY from the journal — no off-by-one, no double-counting a window, no spurious stagnation/termination from a reset streak — across MANY small relaunches (the new operating model)?
 4. Does the per-window meta round / island briefs / bandit state survive a mid-batch kill + resume (bandit_state.pkl)? Any state written non-atomically that a kill could corrupt?
 5. Any interaction where small batches (2 windows) + frequent relaunch changes behavior vs one long cluster (e.g. the work-score taper, the termination streak, meta cadence)?
Return concrete robustness findings + any code gaps that make the small-batch-relaunch loop lossy or stateful-incorrect.`,
  },
  {
    key: 'survival-docs',
    label: 'A3 survival: the durable-doc gap (operating model not taught)',
    prompt: `TRACK A (run survival). The CORRECT operating model (small batches + relaunch-on-notification incl. "killed" + safety-net wakeup + MAIN-LOOP-only) was discovered live and currently lives ONLY in the user's auto-memory — which the user is CLEARING. Audit the durable docs and specify exactly what must be taught so a future orchestrator doesn't repeat the failure.
Read: orchestrator/SKILL.md (launch/wake sections), CLAUDE.md (standing role / unattended-run bullets), orchestrator/NOTES.md, and the memory note content quoted in EVIDENCE (main-loop-only).
Answer:
 1. What do the docs CURRENTLY teach about launching + waking (quote the lines), and where is each statement WRONG or DANGEROUS given the ephemeral-sandbox-reclaim reality (e.g. "background-launched --until-decision IS the wake primitive; it re-invokes you on exit" implies a long single job that the sandbox will reclaim mid-wait)?
 2. The MAIN-LOOP-ONLY rule (never run the orchestrator as a spawned subagent — its bg jobs die with it; only the main loop is re-invoked by bg completion) is NOT in SKILL/CLAUDE/NOTES — only in being-cleared memory. Confirm its absence and specify exactly where + how to teach it durably.
 3. Specify the exact durable teaching: small bounded batches; relaunch on EVERY exit/kill notification (killed == relaunch with --resume, not done); a safety-net scheduled wakeup; lid-open/AC; main-loop-only. Where in SKILL/CLAUDE should each land?
Return doc-gap findings + the precise teaching to add (so it survives the memory wipe).`,
  },
  {
    key: 'novelty-embedding',
    label: 'B1 novelty: embedding/similarity representation vs threshold',
    prompt: `TRACK B (novelty stall). Root-cause WHY a real incremental improvement on a ~370-line program embeds at ≥0.99 cosine to its parent — is it a THRESHOLD problem or a REPRESENTATION problem?
Read: orchestrator/scripts/novelty_check.py (the cosine + threshold + what it compares against — note it compares the candidate against the archive's correct programs incl. the PARENT), shinka/embed/client.py + shinka/embed/embedding.py (how code is embedded — full program text? truncated? which model — text-embedding-3-small), shinka/database/dbase.py (how the stored embedding is produced/cached), and how run_window computes the candidate embedding (\`_embed\`).
Test with evidence (file:line):
 1. Is similarity computed on the FULL program code embedding? For a 370-line program, a 5–15 line real change moves the embedding by <0.01 cosine, so it lands ≥0.99 vs the parent. Quantify the mechanism: what is embedded, and why does a small change barely move it?
 2. Is 0.99 a realistic gate for large programs? The SKILL itself notes "large programs cluster 0.96–0.98". Is the THRESHOLD the lever (raise it / scale with program size), or is the full-code-embedding REPRESENTATION the real culprit (a better signal would embed the DIFF, or normalize, or compare structurally)?
 3. Does the gate compare against the PARENT specifically? If so, an incremental child is ALWAYS a near-dup of its own parent — is that the intended semantics, and is it the trap here?
 4. Is there an embedding caching / model issue (e.g. an empty/degenerate embedding, wrong model, or a normalization bug) that could inflate similarity?
Rule: is Problem B fundamentally a threshold issue, a representation issue, or a downstream-of-mutation issue (the candidate really IS near-identical because the mutation barely changed it — defer that to the mutation-prompt investigator but say whether the embedding faithfully reflects it)? Return the verdict with evidence.`,
  },
  {
    key: 'novelty-mutation-prompt',
    label: 'B2 novelty: the mutation prompt/patch producing near-identical code (user hypothesis)',
    prompt: `TRACK B (novelty stall) — THE USER'S HYPOTHESIS. Test whether candidates were 0.99-similar because the MUTATION PROMPT / patch mechanics produce near-identical (tiny/no-op) diffs, rather than because the threshold is wrong.
Read: orchestrator/scripts/construct_mutation_prompt.py (how parent + inspirations + goal + brief are framed), shinka/core/sampler.py (\`PromptSampler.sample\` — what the mutation prompt actually instructs), shinka/prompts/prompts_base.py + prompts_diff.py + prompts_full.py + prompts_cross.py (the patch-type templates — what change magnitude they request), orchestrator/scripts/mutate.py (the call + parse + apply + the \`num_applied\` / \`applied\` signals + bounded retry), shinka/edit/apply_diff.py + apply_full.py (how a patch is applied — does diff mode inherently make tiny edits?). Also read tasks/cnot_grid_synth/initial.py (the EVOLVE-BLOCK size/shape — is the evolvable region small/constrained so changes are necessarily local?).
Test with evidence (file:line):
 1. Does the mutation prompt ASK for a substantive change, or does it bias toward minimal tweaks? Does showing the full parent code anchor the LLM to small edits? Is there any "make a meaningful/structural change" instruction, or just "improve it"?
 2. Patch types: with \`patch_type_probs\` default [0.6 diff, 0.3 full, 0.1 cross], DIFF mode (60%) inherently edits a few lines → a near-identical child. Is the diff-heavy default a driver of near-dup candidates on a large program? Would more "full"/"cross" rewrites produce more novel candidates?
 3. Is there evidence of degenerate mutations: \`num_applied\` very small, identity/no-op patches counted as candidates (applied=True, num_applied=0), or the model returning the parent nearly verbatim? Does any path encourage that?
 4. Does the prompt feed the inspirations/brief in a way that would diversify the child, or is the child dominated by the single parent?
RULE on the user's hypothesis: is the near-identical-ness primarily a MUTATION-PROMPT/PATCH problem (so the right fix is the prompt / patch-type mix / a change-magnitude instruction), or is the mutation legitimately producing real-but-small improvements that only LOOK like dups to the embedding (so the fix is novelty/representation)? Give the evidence-backed verdict + concrete prompt/patch fixes.`,
  },
  {
    key: 'novelty-keepbetter',
    label: 'B3 novelty: does the new keep-the-better fix resolve the live stall?',
    prompt: `TRACK B (novelty stall) — resolution check. The live run was on the OLD novelty (pre-eval reject). The CURRENT code has a keep-the-better change (H5). Determine whether the current code would have AVOIDED this exact stall, and what residual risk remains.
Read: orchestrator/harness/run_window.py (the post-eval "KEEP-THE-BETTER novelty resolve" block — search for \`novelty resolve\` / \`dropped_novelty_worse\` / \`novelty_kept_better\`), orchestrator/scripts/novelty_check.py (most_similar_score + tombstone-skip), and how the resolve compares candidate vs incumbent score.
Test with evidence (file:line):
 1. In the live scenario (a near-dup that is a REAL improvement over its parent/incumbent), does the new code now KEEP the better candidate (and evict the worse incumbent) instead of rejecting it? Walk the exact branch. So would the archive have kept advancing instead of freezing at 11?
 2. Residual: the keep-better resolve still runs only on CORRECT candidates and still uses the 0.99 threshold to DECIDE near-dup-ness — but for a near-dup it now compares SCORES. Confirm a strictly-better near-dup is kept. What about a near-dup that is EQUAL or marginally worse but still a valid distinct direction — is anything still lost?
 3. Does keep-the-better fully remove the need to raise the threshold / change the representation / fix the mutation prompt, or are those still warranted (e.g. to avoid the eval cost of constantly re-evaluating near-dups, or to actually produce more diverse candidates)?
 4. Cross-check the diagnostics: with keep-better, would the agent still have SEEN a "novelty rejecting everything" signal, or does the new \`novelty_kept_better\`/honest-acceptance-rate make the stall visible/avoided?
Return: does H5 resolve the live stall (yes/partial/no) + the residual fixes still needed.`,
  },
]

function investigatePrompt(t) {
  return `${PREAMBLE}

=== YOUR INVESTIGATION: ${t.label} ===
${t.prompt}

Read the cited code FIRST, then conclude. Return findings via the schema — each with the hypothesis tested, the code evidence (file:line), your verdict, and a concrete fix. Set kind honestly (root-cause vs contributing vs symptom vs doc-gap vs code-bug). Do not modify anything.`
}

function verifyPrompt(t, inv) {
  return `${PREAMBLE}

=== ADVERSARIAL VERIFICATION: ${t.label} ===
A first investigator produced the findings below. For EACH: independently open the cited code/git and decide confirmed / adjusted / refuted (default REFUTED if you can't reproduce the evidence yourself — a plausible-but-unverified root cause is worse than none). Fix the location/severity/kind if needed; set verdict + a crisp verdict_note citing what you read; carry fields forward with newly_found=false. THEN do your own pass over the same files for anything missed (newly_found=true, verdict=confirmed). Hold the line especially on the ROOT-CAUSE-vs-SYMPTOM distinction and on the user's mutation-prompt hypothesis.

ORIGINAL FINDINGS (summary: ${inv.summary}):
${JSON.stringify(inv.findings, null, 1)}

Return the reconciled list via the schema.`
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------
log(`Root-cause audit: ${TRACKS.length} investigators across the survival + novelty tracks, each adversarially verified.`)

const reviewed = (await pipeline(
  TRACKS,
  (t) => agent(investigatePrompt(t), { label: `investigate:${t.key}`, phase: 'Investigate', schema: INVESTIGATE_SCHEMA }),
  (inv, t) => inv ? agent(verifyPrompt(t, inv), { label: `verify:${t.key}`, phase: 'Verify', schema: VERIFY_SCHEMA }) : null,
)).filter(Boolean)

const all = []
for (const r of reviewed) for (const f of (r.findings || [])) all.push(f)
const kept = all.filter(f => f.verdict !== 'refuted')
const refuted = all.filter(f => f.verdict === 'refuted')
log(`Investigation done: ${kept.length} findings stand, ${refuted.length} refuted.`)

const sevRank = { critical: 0, high: 1, medium: 2, low: 3 }
const kindRank = { 'root-cause': 0, 'contributing': 1, 'code-bug': 1, 'doc-gap': 2, 'symptom': 3 }
const sorted = kept.slice().sort((a, b) =>
  (kindRank[a.kind] - kindRank[b.kind]) || (sevRank[a.severity] - sevRank[b.severity]))

const synthPrompt = `${PREAMBLE}

=== SYNTHESIS — root-cause report (${TODAY}) ===
Below are the verified findings from the survival + novelty investigations. Produce a tight root-cause report that:
 1. States the SURVIVAL root cause (Problem A): why a backgrounded run died on idle, whether the live agent's fix-model (small batches + relaunch-on-EVERY-notification incl. "killed" + safety-net wakeup + main-loop-only) is the correct DURABLE answer, and the exact code/doc changes (esp. baking the operating model into SKILL/CLAUDE/NOTES since the lesson currently lives only in being-cleared memory). Note whether re-adding the detached daemon is warranted.
 2. States the NOVELTY root cause (Problem B) and RULES EXPLICITLY on the user's hypothesis — is the 0.99-on-a-large-program stall primarily a MUTATION-PROMPT/PATCH problem (near-identical diffs), a NOVELTY-THRESHOLD problem, or an EMBEDDING-REPRESENTATION problem (or a combination, ranked)? State whether the new keep-the-better (H5) behavior RESOLVES the live stall, and what residual fix (prompt / patch-mix / threshold-scaling / representation) is still warranted.
 3. Gives ranked_fixes — concrete, most-impactful first, each tagged [survival]/[novelty] and [code]/[doc].
 4. report_md — a clean markdown report: ## Problem A — root cause & fix ; ## Problem B — root cause, the verdict on the user's hypothesis, & fix ; ## Ranked fixes ; ## Evidence map (finding → file:line) ; ## Refuted/low-confidence (for transparency). Use the real findings; cite file:line; do not invent.

VERIFIED FINDINGS (root-cause first):
${JSON.stringify(sorted, null, 1)}

REFUTED (for the transparency section):
${JSON.stringify(refuted.map(f => ({ title: f.title, verdict_note: f.verdict_note, locations: f.locations })), null, 1)}

Return via the schema: survival_root_cause, novelty_root_cause, ranked_fixes, report_md.`

const synth = await agent(synthPrompt, { label: 'synthesize-root-cause', phase: 'Synthesize', schema: SYNTH_SCHEMA })

return {
  survival_root_cause: synth.survival_root_cause,
  novelty_root_cause: synth.novelty_root_cause,
  ranked_fixes: synth.ranked_fixes,
  report_md: synth.report_md,
  counts: { investigators: reviewed.length, kept: kept.length, refuted: refuted.length },
}

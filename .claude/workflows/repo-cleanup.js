export const meta = {
  name: 'repo-cleanup',
  description: 'Clean up / refactor the ShinkaEvolve repo: cross-platform portability (macOS + Windows), dead/cruft removal from earlier dev stages, doc staleness, and structure/skills de-nesting. Discovers candidates, adversarially verifies each is safe, and emits an ordered, test-gated change plan. mode:"apply" executes the verified plan sequentially with a per-change test gate and auto-revert. Bakes the two verified cleanup landmines (skills symlinks; configs/hydra/slurm on the live eval path) as priors and guards the FOUNDATION set.',
  whenToUse: 'When refactoring or cleaning this repo — to make it run on both the Mac laptop and the Windows workstation, prune leftovers from the fork/earlier dev stages, reconcile stale docs, and de-nest the skills layout, WITHOUT corrupting the run path or desyncing the skill symlinks. Run {mode:"plan"} first (read-only); then {mode:"apply"} to execute the verified plan.',
  phases: [
    { title: 'Discover', detail: 'one finder per cleanup dimension reads the actual code + import graph (not the docs as authority)' },
    { title: 'Verify', detail: 'an independent skeptic proves each candidate is safe — fail-closed: refuted unless removal/edit is provably safe' },
    { title: 'Plan', detail: 'dedup, order decouple-first, group into atomic test-gated change-sets, render the plan' },
    { title: 'Apply', detail: 'mode:"apply" only — execute each change-set sequentially, run the test gate, commit on green / hard-revert on red' },
  ],
}

// ---------------------------------------------------------------------------
// Run parameters. Everything is optional so the workflow is runnable by name.
//   args.mode    : "plan" (default, read-only) | "apply"
//   args.root    : explicit repo root override (else agents resolve it via git — portable)
//   args.today   : date stamp for the plan header (scripts cannot call Date())
//   args.focus   : array of dimension keys to restrict the scan (else all)
//   args.python  : interpreter for the apply test-gate (else conda run -n shinka python, fallback python)
//   args.skip_foundation : if true, apply mode skips any change-set that touches a FOUNDATION file
// ---------------------------------------------------------------------------
const MODE = (args && args.mode) || 'apply' // TEMP: forced apply for one run; restore to 'plan' after
const ROOT_OVERRIDE = (args && args.root) || null
const TODAY = (args && args.today) || 'undated'
const FOCUS = (args && Array.isArray(args.focus)) ? args.focus : null
const PYTHON_HINT = (args && args.python) || null
const SKIP_FOUNDATION = !!(args && args.skip_foundation)

// ---------------------------------------------------------------------------
// VERIFIED PRIORS — baked in so no finder/verifier re-learns them the hard way.
// These are confirmed facts about THIS repo (re-confirm in code, never assume).
// ---------------------------------------------------------------------------
const PRIORS = `VERIFIED CLEANUP PRIORS (confirmed against this repo — re-check in code, do NOT act blind):

1. SKILLS ARE INTENTIONAL GIT SYMLINKS, NOT DUPLICATES — never "dedupe" or delete either view.
   • Canonical physical files live in top-level skills/: skills/shinka-{setup,convert,inspect}/SKILL.md are REAL files.
   • skills/shinka-orchestrator is a symlink -> ../orchestrator (the orchestrator dir doubles as a skill).
   • .claude/skills/* are git mode-120000 symlinks back to ../../skills/*.
   • Therefore orchestrator/SKILL.md == skills/shinka-orchestrator/SKILL.md == .claude/skills/shinka-orchestrator/SKILL.md
     are the SAME inode. To edit a skill doc, edit the real path under skills/ (or orchestrator/SKILL.md); the symlink
     views update automatically. Removing/moving either tree breaks the skill registry. Verify with:
       git ls-files -s | awk '$1==120000'   (lists the tracked symlinks)

2. configs/hydra/slurm ARE ON THE LIVE EVAL PATH — decouple-first, never bare-rm.
   • shinka/utils/__init__.py eagerly imports utils_hydra + load_df, so ANY 'import shinka.utils' pulls in hydra.
   • orchestrator/scripts/evaluate.py does 'from shinka.utils import parse_time_to_seconds' (line ~88) → every run
     imports shinka.utils → utils_hydra. A bare 'rm utils_hydra.py' breaks the orchestrator at import time.
     (Note: evaluate.py line ~58 already imports straight from shinka.utils.general — a decouple precedent.)
   • shinka/launch/scheduler.py imports shinka/launch/slurm.py (slurm is dead-but-imported THROUGH scheduler).
   • shinka/database/dbase.py imports shinka/database/display.py → display.py is LIVE, not dead.
   A removal candidate MUST ship with the exact decoupling edit (e.g. narrow the __init__ import, or rewrite the one
   consumer's import) so the removal cannot break an import. Removal without the decouple step is a defect.

3. THE DATED ROOT REPORTS ARE NOT FREE DELETES — reconcile-then-archive.
   • *_20260603.md (AUDIT_LOGIC_WORKFLOW / FIX_PLAN / ROOT_CAUSE_AUDIT / SURVIVAL_TEST) and taxonomy.md (HISTORICAL)
     encode at least one un-propagated correction (a survival-model fix) and a latent BanditBase.save_state bug.
     Extract any still-true durable fact into the live docs/memory BEFORE archiving/deleting the report.

4. THE DOCS ARE TARGETS, NOT AUTHORITY. CLAUDE.md, orchestrator/SKILL.md, orchestrator/NOTES.md, taxonomy.md,
   README.md, configs/README.md describe an intended system and may be stale or aspirational. A doc claim counts as
   "stale" ONLY when the current code disagrees — verify against code (Read/Grep/git), never trust a doc to justify a
   change to another doc.

5. CROSS-PLATFORM IS THE BAR. The repo must run on BOTH a macOS laptop AND a Windows workstation. macOS-only is a
   defect to fix, not a constraint to preserve:
   • run_window.py's caffeinate/_hold_no_idle_sleep block is darwin-guarded (no-ops off macOS) → a long Windows run
     has NO idle-sleep protection. A portable fix adds the Windows equivalent (SetThreadExecutionState via ctypes)
     and/or documents the no-op — WITHOUT changing macOS behavior or any run decision/score.
   • Never hardcode an absolute machine path (/Users/<name>/..., /opt/anaconda3/...). Use repo-root-relative refs and
     'cd "$(git rev-parse --show-toplevel)"'; for the interpreter prefer 'conda run -n shinka python' / 'conda activate
     shinka' (both work on mac + win) over a pinned bin path.

6. FOUNDATION = SEMANTICS-FROZEN. These files define the data structures / contracts a run depends on. A cleanup may
   make a path inside them portable or fix a comment, but must NEVER alter the schema, the JSON stdin/stdout contract,
   the scoring, or the runtime decision logic — and must be test-gated. Structural removal/move/renaming of a
   FOUNDATION file is OUT OF SCOPE (defer to the human / the end-of-run ending document). The FOUNDATION set:
     shinka/database/dbase.py (sqlite schema), orchestrator/scripts/_common.py (JSON contract),
     orchestrator/scripts/{evaluate,archive_record,archive_query,repair_record,diagnostics}.py,
     orchestrator/harness/journal.py, the user's tasks/*/evaluate.py + tasks/*/initial.*,
     examples/*/evaluate.py + examples/*/initial.py.
   run_window.py and the other harness/strategy files are NOT in the frozen set, but a cleanup must still preserve
   their behavior (portability/cosmetic only) and test-gate the change.`

// ---------------------------------------------------------------------------
// Shared preamble. Note: the repo root is resolved PORTABLY via git — this
// workflow does not hardcode an absolute machine path (that is the very thing
// it cleans up).
// ---------------------------------------------------------------------------
const PREAMBLE = `You are a careful refactoring engineer cleaning up ShinkaEvolve — a personal, Azure-only,
"Claude-as-orchestrator" fork of an evolutionary code-optimization framework. The repo was forked from SakanaAI's
ShinkaEvolve, pruned to Azure-only, and rewritten around a Claude orchestrator + outer loop; a lot of the original
upstream machinery and several earlier dev-stage artifacts may now be dead.

FIRST, resolve the repo root PORTABLY: run \`git rev-parse --show-toplevel\` (works on macOS, Linux, and Windows
Git-Bash). ${ROOT_OVERRIDE ? `An explicit root was provided: ${ROOT_OVERRIDE} — use it.` : 'Use that as REPO_ROOT.'}
Treat ALL paths as relative to REPO_ROOT. Resolving the root this way (instead of pasting an absolute path) is the
exact portability standard this cleanup enforces.

HOW THE LIVE SYSTEM ACTUALLY RUNS (so you can tell live code from cruft): an orchestrator agent boots a run, then
\`orchestrator/harness/run_window.py\` drives windows of the inner loop. Each generation: sample a parent + inspirations
from a per-island sqlite archive → call an Azure LLM (orchestrator/scripts/mutate.py, background-poll) to mutate the
code → apply the patch with bounded retry → on eval failure an in-place fix loop → novelty check → record code+score
into the right island. A fixed number of generations = one window; after each window an automatic meta round
(meta_summarize.py) writes per-island briefs. The window-cluster returns control to the agent on stagnation or at a
tapering boundary; the agent may rewrite a mutable strategy file or run deep research, then relaunch. The LIVE import
surface is: run_window.py → orchestrator/scripts/* → the shinka/* modules those scripts import → plus the test suite
(orchestrator/tests/*) and the active task (tasks/cnot_grid_synth/*, examples/circle_packing/*). Anything no live
route reaches is a removal CANDIDATE — but candidacy must be PROVEN by the import graph, not assumed.

${PRIORS}

GROUND RULES:
 • Read the ACTUAL code, import graph, and git history before concluding. Use Read/Grep/Glob/Bash/git freely.
 • To test whether a module is reachable, trace imports (grep 'import X', 'from X import'), check dynamic loads
   (importlib, getattr, hydra _target_, entry points, package-data globs in pyproject), and check the eval-subprocess
   PYTHONPATH. "Not referenced in code" must survive a check for string-based / config-driven loading too.
 • A portable replacement must work on BOTH macOS and Windows — say how you verified that, don't just assert it.
 • FAIL CLOSED: if you cannot PROVE a removal/edit is safe, do not propose it (or mark it refuted). Decouple-first,
   never bare-rm. A wrong deletion that corrupts the run path is far worse than leaving cruft.`

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------
const CHANGE_ITEM = {
  type: 'object',
  additionalProperties: false,
  properties: {
    id: { type: 'string', description: 'short stable slug, e.g. "port-claude-md-paths" or "dead-launch-tree"' },
    dimension: { type: 'string', description: 'the cleanup dimension key this came from' },
    title: { type: 'string', description: 'one-line description of the cleanup' },
    kind: { type: 'string', enum: ['portability', 'dead-file', 'stale-doc', 'structure', 'duplication'] },
    severity: { type: 'string', enum: ['high', 'medium', 'low'], description: 'how much this blocks the cross-platform / cleanliness goal, or the risk of leaving it' },
    locations: { type: 'array', items: { type: 'string' }, description: 'file:line references (the files to change/remove)' },
    evidence: { type: 'string', description: 'the import-graph / grep / git proof that justifies this (cite what you read)' },
    proposed_change: { type: 'string', description: 'the EXACT edit/move/removal, INCLUDING any decouple-first prerequisite (e.g. "first narrow shinka/utils/__init__.py import, THEN remove utils_hydra.py")' },
    touches_foundation: { type: 'boolean', description: 'does this edit a FOUNDATION (semantics-frozen) file?' },
    risk: { type: 'string', description: 'what could break, and how this change avoids it' },
    verification: { type: 'string', description: 'the concrete command/check that proves the change is safe (e.g. "python -c \\"import shinka\\" + pytest orchestrator/tests")' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
  },
  required: ['id', 'dimension', 'title', 'kind', 'severity', 'locations', 'evidence', 'proposed_change', 'touches_foundation', 'risk', 'verification', 'confidence'],
}

const DISCOVER_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    dimension: { type: 'string' },
    summary: { type: 'string', description: '2-4 sentences: what this dimension found, and what it checked-and-cleared' },
    findings: { type: 'array', items: CHANGE_ITEM },
  },
  required: ['dimension', 'summary', 'findings'],
}

const VERDICT_ITEM = {
  type: 'object',
  additionalProperties: false,
  properties: {
    id: { type: 'string' },
    dimension: { type: 'string' },
    title: { type: 'string' },
    kind: { type: 'string', enum: ['portability', 'dead-file', 'stale-doc', 'structure', 'duplication'] },
    severity: { type: 'string', enum: ['high', 'medium', 'low'] },
    locations: { type: 'array', items: { type: 'string' } },
    evidence: { type: 'string' },
    proposed_change: { type: 'string', description: 'the CORRECTED proposal (carry forward, fixed if adjusted)' },
    touches_foundation: { type: 'boolean' },
    risk: { type: 'string' },
    verification: { type: 'string' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    verdict: { type: 'string', enum: ['confirmed', 'adjusted', 'refuted'] },
    verdict_note: { type: 'string', description: 'what you independently checked and why it stands/falls; cite file:line' },
    safe_to_apply: { type: 'boolean', description: 'true ONLY if this can be auto-applied behind a test gate with no human judgment; false if it needs a human (e.g. structural foundation change, lossy doc archive)' },
  },
  required: ['id', 'dimension', 'title', 'kind', 'severity', 'locations', 'evidence', 'proposed_change', 'touches_foundation', 'risk', 'verification', 'confidence', 'verdict', 'verdict_note', 'safe_to_apply'],
}

const CHANGE_SET = {
  type: 'object',
  additionalProperties: false,
  properties: {
    id: { type: 'string', description: 'stable slug for the atomic change-set' },
    title: { type: 'string' },
    dimension: { type: 'string' },
    rationale: { type: 'string', description: 'why, one sentence' },
    ops: { type: 'string', description: 'the exact, ordered operations: files to edit/move/remove and the precise content change. Self-contained enough for an executor with no other context.' },
    prerequisite: { type: 'string', description: 'the decouple-first step that MUST happen first within this set, or "none"' },
    touches_foundation: { type: 'boolean' },
    safe_to_apply: { type: 'boolean', description: 'auto-appliable behind the gate? false ⇒ list it but leave for a human' },
    gate: { type: 'string', description: 'the test/verify command(s) that must pass after applying (e.g. "<py> -m pytest -q orchestrator/tests && <py> -c \\"import shinka\\"")' },
    revert: { type: 'string', description: 'how to undo this set if the gate fails' },
    risk: { type: 'string' },
  },
  required: ['id', 'title', 'dimension', 'rationale', 'ops', 'prerequisite', 'touches_foundation', 'safe_to_apply', 'gate', 'revert', 'risk'],
}

const PLAN_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    stats: {
      type: 'object',
      additionalProperties: false,
      properties: {
        high: { type: 'number' }, medium: { type: 'number' }, low: { type: 'number' },
        total_confirmed: { type: 'number' }, refuted_dropped: { type: 'number' },
        foundation_touch: { type: 'number' }, auto_appliable: { type: 'number' },
      },
      required: ['high', 'medium', 'low', 'total_confirmed', 'refuted_dropped', 'foundation_touch', 'auto_appliable'],
    },
    top_changes: { type: 'array', items: { type: 'string' }, description: 'headline ordered list of the most important change-sets' },
    change_sets: { type: 'array', items: CHANGE_SET, description: 'ORDERED (decouple-first; foundation-touch last) atomic change-sets' },
    report_md: { type: 'string', description: 'the FULL markdown cleanup plan, ready to write to disk' },
  },
  required: ['stats', 'top_changes', 'change_sets', 'report_md'],
}

const APPLY_ITEM = {
  type: 'object',
  additionalProperties: false,
  properties: {
    id: { type: 'string' },
    title: { type: 'string' },
    status: { type: 'string', enum: ['applied-committed', 'reverted-gate-failed', 'skipped-unsafe', 'skipped-dirty-tree', 'skipped-foundation', 'error'] },
    files_changed: { type: 'array', items: { type: 'string' } },
    gate_output: { type: 'string', description: 'tail of the test-gate output (pass/fail evidence)' },
    commit: { type: 'string', description: 'commit sha/subject if committed, else ""' },
    note: { type: 'string' },
  },
  required: ['id', 'title', 'status', 'files_changed', 'gate_output', 'commit', 'note'],
}

const CLEAN_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    clean: { type: 'boolean', description: 'true if `git status --porcelain` is empty (safe to start apply)' },
    detail: { type: 'string' },
    branch: { type: 'string' },
    python: { type: 'string', description: 'the interpreter command you verified works for the test gate (e.g. "conda run -n shinka python")' },
  },
  required: ['clean', 'detail', 'branch', 'python'],
}

// ---------------------------------------------------------------------------
// The cleanup dimensions. Each carries the GOAL, the SEED EVIDENCE (a starting
// point to confirm — never trust blindly), and KEY CHECKS.
// ---------------------------------------------------------------------------
const DIMENSIONS = [
  {
    key: 'portability-paths',
    title: 'Hardcoded machine/absolute paths + OS-specific invocations in docs, scripts & configs',
    spec: `GOAL: nothing in the repo should bake in one machine, one user account, or one OS. Find every absolute machine
path and OS-specific shell/conda invocation that would break on the user's Windows workstation (or a fresh checkout
under a different home dir), and replace it with a portable form.
SEED EVIDENCE (confirm + find the rest): CLAUDE.md (~9 "/Users/dantongli/GIthub/Shinka/", ~73 "/opt/anaconda3/envs/
shinka/bin/{python,pip}", ~119 "cd /Users/dantongli/GIthub/ShinkaEvolve"); tasks/cnot_grid_synth/README.md (~84-85,
~104 "conda activate" + "cd /Users/..."); scripts/test_azure.py (docstring "/opt/anaconda3/envs/shinka/bin/python");
the .claude/workflows/*.js audit scaffolds (they hardcode a ROOT like ".../.claude/worktrees/goofy-tharp-229342" that
no longer exists — stale AND non-portable); any run-config 'python_executable' pinned to a mac bin path.
KEY CHECKS: (a) grep the whole tree for '/Users/', '/opt/anaconda3', a literal home dir, and '\\\\Users\\\\'; classify each
as doc / script / config. (b) For each: is the fix a repo-root-relative path, a 'cd "$(git rev-parse --show-toplevel)"',
'conda activate shinka' / 'conda run -n shinka python', or a documented placeholder? (c) Does any *.py hardcode a path
that a second machine wouldn't have (vs deriving from __file__ / cwd / an env var)? (d) Are the skills' starter
orchestrator_run.json already portable (relative paths) — confirm, don't "fix" what's already relative. (e) Distinguish
machine paths inside the DATED reports (handled by the doc-staleness dimension via archive) from machine paths in LIVE
docs/scripts (fix in place).`,
  },
  {
    key: 'portability-runtime',
    title: 'OS-specific runtime primitives & POSIX assumptions in code (make Mac + Windows)',
    spec: `GOAL: the runtime code must behave correctly on Windows, not just no-op or crash. Find OS-specific primitives
and POSIX assumptions and propose a portable shim that PRESERVES current macOS behavior.
SEED EVIDENCE: orchestrator/harness/run_window.py:~1321-1371 — _hold_no_idle_sleep()/caffeinate, darwin-guarded so it
silently no-ops off macOS (a long Windows run then has NO idle-sleep protection). Look also for hardcoded '/usr/bin/...'
binaries, shell=True with POSIX-only syntax, signal/SIGTERM usage, os.fork, '/'-joined paths instead of os.path/pathlib,
file-locking assumptions, and any 'sys.platform == "darwin"' branch with no Windows counterpart.
KEY CHECKS: (a) For caffeinate: propose a Windows equivalent (ctypes SetThreadExecutionState with
ES_CONTINUOUS|ES_SYSTEM_REQUIRED) gated on 'sys.platform == "win32"', leaving the darwin and the (already-fine)
no-op-elsewhere paths untouched; the change must NOT alter any run decision, diagnostic, or score. (b) Are subprocess
calls portable (no bare '/usr/bin', no shell-builtin assumptions)? (c) Are paths built with os.path.join / pathlib, or
with literal '/' that breaks on Windows? (d) Is the conda/python interpreter resolution portable? (e) FOUNDATION note:
run_window.py is the harness — portability-only, behavior-preserving, test-gated; do NOT refactor its control flow.
Flag, but mark touches_foundation appropriately and keep the diff minimal.`,
  },
  {
    key: 'dead-upstream-cruft',
    title: 'Dead files from the Sakana upstream / earlier dev stages (no live route reaches them)',
    spec: `GOAL: remove leftovers that no live route (run_window → scripts → imported shinka modules; the test suite;
the active tasks) reaches — but ONLY with proof and the decouple-first step. Cruft survives green because nothing
imports it; that is exactly why it must be import-graph-proven, not assumed.
SEED CANDIDATES (verify each — some are LIVE): shinka/launch/{local,scheduler,slurm}.py (no orchestrator script imports
shinka.launch — confirm; scheduler imports slurm);
.github/workflows/{pypi-release,docs-release,integration}.yml (upstream Sakana release/docs CI — this private fork does
not publish to PyPI or Sakana docs); shinka/configs task/variant yamls for circle_packing / novelty_generator (hydra
configs — is the hydra path used by the orchestrator at all, or only run.json?); any shinka/prompts/* or shinka/llm/* or
shinka/embed/* module no script imports.
KNOWN-LIVE (do NOT propose removing): shinka/database/display.py (imported by dbase.py); shinka/utils/utils_hydra.py +
shinka/utils/load_df.py (imported by shinka/utils/__init__.py, which is on the eval path); shinka/configs/* is
package-data in pyproject.
KEY CHECKS: (a) For EACH candidate, produce the import-graph proof: who imports it (grep both 'import X' and string/
config '_target_'/getattr/importlib loads), is it package-data, is it reachable from run_window or the tests? (b) If
dead-but-coupled (e.g. removing utils_hydra would break shinka.utils.__init__), the proposal MUST include the exact
decouple edit FIRST (narrow the __init__ import; rewrite the lone consumer). (c) Treat a whole subtree (e.g.
shinka/launch/) atomically — removing scheduler.py but leaving slurm.py imported by it is a defect. (d) For .github CI:
which workflows are still meaningful (ci.yml runs pytest; claude*.yml are the user's GH Claude actions) vs upstream-only
release plumbing. (e) Prefer removal over keeping, but mark confidence honestly; a medium-confidence "dead" needs the
decouple step spelled out so the verifier can falsify it.`,
  },
  {
    key: 'doc-staleness',
    title: 'Stale & machine-specific docs (dated reports, taxonomy, the orchestrator docs, packaging metadata)',
    spec: `GOAL: the docs should describe the repo as it IS and read on any machine. Remember PRIOR #4 (docs are
targets, not authority — verify every claim against code) and PRIOR #3 (the dated reports encode un-propagated
corrections — reconcile-then-archive, never free-delete).
SEED EVIDENCE: the four dated root reports AUDIT_LOGIC_WORKFLOW_20260603.md / FIX_PLAN_20260603.md /
ROOT_CAUSE_AUDIT_20260603.md / SURVIVAL_TEST_20260603.md and taxonomy.md (marked HISTORICAL; references deleted files
like shinka/core/novelty_judge.py, summarizer.py, prompt_evolver.py); CLAUDE.md / orchestrator/SKILL.md /
orchestrator/NOTES.md (the user's stated rewrite targets — stale file/lever refs, removed-CLI mentions e.g.
shinka_run/shinka_launch/shinka_visualize, mac-only assumptions, hardcoded paths); README.md / configs/README.md /
CHANGELOG.md; pyproject.toml (Sakana homepage/repo/author + 'requires-python >=3.10' vs CLAUDE.md's "Python 3.11").
KEY CHECKS: (a) For each dated report: which of its corrections are STILL un-propagated into live code/docs/memory? List
the durable facts to extract, THEN the archive/delete action (e.g. move to a docs/archive/ dir, or delete after
extraction). (b) For taxonomy.md and the orchestrator docs: list every file/function/lever reference that no longer
exists in code (grep to confirm absence) and the corrected/removed line. (c) Cross-doc consistency: do CLAUDE.md,
SKILL.md, NOTES.md agree on defaults, model names/prices, file lists? Flag the contradictions, but propose a SINGLE
source of truth (see the structure-skills dimension) rather than editing each copy. (d) pyproject: which metadata is
just-inherited-from-upstream vs actually wrong for this fork. (e) Do NOT propose deleting a report whose durable content
hasn't been extracted yet — that step is the prerequisite.`,
  },
  {
    key: 'structure-skills',
    title: 'Nested / convoluted layout + skills-doc duplication (preserve the symlink contract)',
    spec: `GOAL: reduce real structural convolution and doc duplication WITHOUT breaking the intentional symlink design
(PRIOR #1). The 3 symlink VIEWS of the skills are a feature; do not collapse them. Target the genuine duplication.
SEED EVIDENCE: doc CONTENT overlap across CLAUDE.md / orchestrator/SKILL.md / orchestrator/NOTES.md (the same Azure
deployment table, the same run-loop description, the same lever lists repeated → drift risk); the TWO byte-identical
orchestrator_run.json starters (skills/shinka-setup/scripts/ and skills/shinka-convert/scripts/); deep/awkward nesting
(e.g. scripts/ vs orchestrator/scripts/ — two different 'scripts' dirs; configs/ vs shinka/configs/ — two different
'configs'); the .claude/ tree (.DS_Store committed?, workflows vs skills).
KEY CHECKS: (a) Confirm the symlinks first (git ls-files -s | awk '$1==120000') and explicitly EXCLUDE them from any
"dedupe" — propose single-sourcing the CONTENT (e.g. a fact lives in SKILL.md and CLAUDE.md links to it) not deleting a
symlink. (b) The two identical starter JSONs: propose one shared source (symlink one to the other, or both to a single
canonical) — verify the skills still read them. (c) Naming collisions (scripts/ vs orchestrator/scripts/, configs/ vs
shinka/configs/): is the top-level one still needed (scripts/test_azure.py; configs/azure_default.yaml + README) or
foldable? Only propose a move if you can prove nothing references the old path. (d) Is .claude/.DS_Store tracked (it is
a macOS artifact — should be gitignored/removed)? (e) Keep proposals conservative: a move that breaks an import or a
skill path is worse than the nesting. Mark anything that needs human judgment safe_to_apply=false.`,
  },
]

// ---------------------------------------------------------------------------
// Prompt builders
// ---------------------------------------------------------------------------
function discoverPrompt(d) {
  return `${PREAMBLE}

=== YOUR CLEANUP DIMENSION: ${d.title} ===
${d.spec}

Do the dimension's KEY CHECKS by reading the real code/docs/import graph. For every concrete, justified cleanup, emit a
finding via the schema with: a stable id, precise file:line locations, the EVIDENCE that proves it (the grep/import-graph/
git you actually ran), the EXACT proposed_change (including any decouple-first prerequisite), whether it touches a
FOUNDATION file, the risk + how the change avoids it, and the verification command. Set touches_foundation accurately.
Prefer fewer, well-proven findings over speculation — but do not suppress a real, provable cleanup. In your summary, say
what you checked and CLEARED (e.g. "confirmed shinka/database/display.py is live — not flagged"). This pass is READ-ONLY:
do not edit, move, or remove anything.`
}

function verifyPrompt(f) {
  return `${PREAMBLE}

=== ADVERSARIAL SAFETY VERIFICATION of a proposed cleanup ===
A finder proposed the change below. Your job is to FALSIFY it: independently open the cited code/import graph and decide
whether this change is real AND safe to make.

PROPOSED CHANGE:
${JSON.stringify(f, null, 1)}

Decide a verdict:
 • confirmed — the problem is real, the locations are right, and the proposed_change (with its decouple step) is provably
   safe. Re-derive the import-graph / portability proof YOURSELF; cite what you read in verdict_note.
 • adjusted — real but the location, the proposed_change, or the decouple step is wrong/incomplete; CORRECT it in the
   proposed_change field and explain in verdict_note.
 • refuted — not safe or not real: the "dead" file is actually imported (string/config/package-data/test), the portable
   replacement would break on macOS OR Windows, the change would alter FOUNDATION semantics, it would delete an
   un-extracted durable fact, or it would break a skill symlink. Explain exactly what you found.

FAIL CLOSED: default to refuted if you cannot independently PROVE the change is safe. A wrong deletion/edit that corrupts
the run path or desyncs the skills is far worse than leaving the cruft. Honor every PRIOR (symlinks, eval-path coupling,
reconcile-then-archive, docs-not-authority, cross-platform, foundation-frozen).

Then set safe_to_apply: true ONLY if this can be auto-applied behind a test gate with NO human judgment (a mechanical
portable edit, a proven-dead removal with its decouple step, a stale-ref fix). Set false if it needs a human — e.g. a
structural foundation change, a lossy doc archive where durable content must first be hand-extracted, or any move whose
safety you could not fully prove. Carry ALL fields forward (corrected if adjusted) and return via the schema.`
}

function planPrompt(confirmed, refuted, dimSummaries) {
  return `${PREAMBLE}

=== SYNTHESIZE THE CLEANUP PLAN (${TODAY}) ===
Below are (A) per-dimension summaries, (B) every change that survived adversarial verification (confirmed/adjusted), and
(C) the refuted ones (for a transparency appendix). Turn the survivors into an ORDERED, atomic, test-gated change plan.

Your job:
 1) DEDUP changes that touch the same files/issue (merge; keep the clearest proposal + highest justified severity).
 2) GROUP into atomic CHANGE-SETS — each independently appliable and revertible. A change-set that needs a decouple-first
    step states it in 'prerequisite' and bakes it into 'ops' as step 1. Never split a coupled removal across sets (e.g.
    narrowing shinka/utils/__init__.py AND removing utils_hydra must be ONE set; removing a whole subtree like
    shinka/launch/ is ONE set).
 3) ORDER the sets safely: decouple/portability edits first; pure dead-file removals next; doc reconciliation (extract
    durable facts BEFORE archiving the dated reports); FOUNDATION-touching (portability-only) sets LAST; anything
    safe_to_apply=false flagged but ordered after the auto-appliable ones.
 4) For each set give a concrete 'gate' (the test/verify command — default '<py> -m pytest -q orchestrator/tests && <py>
    -c "import shinka"', plus a file-specific check where relevant) and a 'revert' (how to undo). Use '<py>' as the
    interpreter placeholder; the apply phase substitutes the resolved shinka-env interpreter.
 5) Produce report_md: a clean, self-contained markdown plan a human can execute or feed to {mode:"apply"}. REQUIRED
    structure:
    # Repo Cleanup Plan — ShinkaEvolve (${TODAY})
    ## Scope & method (one paragraph: 5 dimensions, adversarially verified, decouple-first + test-gated; the verified PRIORS)
    ## Executive summary (counts by severity + kind; the most important change-sets as one-liners; what was checked-and-cleared)
    ## Cross-platform (macOS + Windows) changes
    ## Dead-file / cruft removals (with the import-graph proof + decouple step for each)
    ## Doc reconciliation & archive (durable facts to extract first, then the archive/delete)
    ## Structure & skills de-nesting (preserving the symlink contract)
    ## Ordered change-sets (the executable list: id, what, prerequisite, gate, revert, foundation?, auto-appliable?)
    ## Needs human judgment (safe_to_apply=false items + why)
    ## Appendix: refuted candidates (title + one line why it did NOT stand)
 Use the real change contents; invent nothing. Fill stats accurately (auto_appliable = count of safe_to_apply=true).

(A) DIMENSION SUMMARIES:
${dimSummaries}

(B) VERIFIED CHANGES (confirmed/adjusted):
${JSON.stringify(confirmed, null, 1)}

(C) REFUTED (for appendix):
${JSON.stringify(refuted.map(f => ({ title: f.title, dimension: f.dimension, verdict_note: f.verdict_note, locations: f.locations })), null, 1)}

Return via the schema: stats, top_changes, change_sets (ordered), and report_md.`
}

function cleanCheckPrompt() {
  return `${PREAMBLE}

=== PRE-APPLY GUARD ===
You are about to gate an automated, committing refactor. Verify it is safe to start:
 1) Resolve REPO_ROOT (git rev-parse --show-toplevel) and run 'git status --porcelain'. The working tree MUST be clean
    (no uncommitted changes) — apply mode commits per change-set and hard-reverts failures, which would clobber unrelated
    work. Report clean=true ONLY if porcelain is empty.
 2) Report the current branch (git rev-parse --abbrev-ref HEAD).
 3) Resolve the test-gate interpreter and VERIFY it works: ${PYTHON_HINT ? `try '${PYTHON_HINT}' first; ` : ''}try
    'conda run -n shinka python -c "import shinka"' (works on mac + win if conda is set up); if conda is unavailable fall
    back to 'python -c "import shinka"'. Return the exact interpreter command (e.g. "conda run -n shinka python") that
    imported shinka successfully. If NONE can import shinka, set clean=false and explain (the gate cannot run).
Return via the schema. Do not modify anything.`
}

function applyPrompt(cs, idx, total, py) {
  return `${PREAMBLE}

=== APPLY CHANGE-SET ${idx + 1}/${total}: ${cs.id} — ${cs.title} ===
The working tree is clean (prior change-sets are committed). Apply EXACTLY this change-set, then test-gate it.

RATIONALE: ${cs.rationale}
PREREQUISITE (do this first if not "none"): ${cs.prerequisite}
OPERATIONS:
${cs.ops}
TOUCHES_FOUNDATION: ${cs.touches_foundation}  (if true: portability/cosmetic ONLY — do not alter schema/contract/scoring/decision logic)
RISK: ${cs.risk}

STEPS:
 1) Make the change exactly as specified (decouple-first step before any removal). Honor every PRIOR — never break a skill
    symlink (edit the real path under skills/ or orchestrator/), never bare-rm a coupled module, never delete an
    un-extracted durable fact.
 2) Run the TEST GATE using the interpreter '${py}':
       ${py} -m pytest -q orchestrator/tests
       ${py} -c "import shinka"
    plus this set's specific gate if it adds one: ${cs.gate}
 3) If the gate PASSES: stage and commit ONLY this change-set's files:
       git add -A && git commit -m "cleanup(${cs.dimension}): ${cs.title}"
    Report status="applied-committed" with the commit subject and the gate output tail.
 4) If the gate FAILS (or you cannot make the change safely): FULLY REVERT — 'git checkout -- .' and 'git clean -fd' for
    any new files you created, OR 'git reset --hard HEAD' — so the tree returns to the committed state. Report
    status="reverted-gate-failed" with the failing gate output. Do NOT leave a partial change.
 5) If applying would require human judgment you cannot safely resolve (e.g. a lossy doc archive), revert and report
    status="skipped-unsafe" with the reason.

Return via the schema. The tree MUST be clean (committed or reverted) when you finish — never leave uncommitted edits.`
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------
function dedupeFindings(items) {
  const rank = { high: 0, medium: 1, low: 2 }
  const seen = new Map()
  const out = []
  for (const f of items) {
    if (!f) continue
    const loc = (f.locations || []).map(s => String(s).split(':')[0]).sort().join('|')
    const key = `${f.kind}::${loc}::${(f.title || '').slice(0, 24).toLowerCase()}`
    if (seen.has(key)) {
      const ex = seen.get(key)
      if ((rank[f.severity] ?? 9) < (rank[ex.severity] ?? 9)) ex.severity = f.severity
      ex._dims = Array.from(new Set([...(ex._dims || [ex.dimension]), f.dimension]))
      continue
    }
    const copy = { ...f, _dims: [f.dimension] }
    seen.set(key, copy)
    out.push(copy)
  }
  return out
}

const dims = FOCUS ? DIMENSIONS.filter(d => FOCUS.includes(d.key)) : DIMENSIONS
log(`Repo-cleanup (${MODE} mode) — ${dims.length} cleanup dimensions, each adversarially safety-verified, decouple-first + test-gated.`)

// Phase 1: discover (parallel, read-only — safe to fan out).
phase('Discover')
const discovered = (await parallel(
  dims.map(d => () => agent(discoverPrompt(d), { label: `find:${d.key}`, phase: 'Discover', schema: DISCOVER_SCHEMA })),
)).filter(Boolean)

const dimSummaries = discovered.map(r => `- ${r.dimension}: ${r.summary}`).join('\n')
const rawFindings = discovered.flatMap(r => (r.findings || []).map(f => ({ ...f, dimension: f.dimension || r.dimension })))
const findings = dedupeFindings(rawFindings)
log(`Discover: ${rawFindings.length} candidates → ${findings.length} after dedup.`)

if (findings.length === 0) {
  return { mode: MODE, message: 'No cleanup candidates found.', dimSummaries }
}

// Phase 2: adversarial safety verification (parallel — each finding falsified independently).
phase('Verify')
const verified = (await parallel(
  findings.map(f => () => agent(verifyPrompt(f), { label: `verify:${f.id || f.kind}`, phase: 'Verify', schema: VERDICT_ITEM })),
)).filter(Boolean)

const confirmed = verified.filter(v => v.verdict !== 'refuted')
const refuted = verified.filter(v => v.verdict === 'refuted')
log(`Verify: ${confirmed.length} changes safe to plan (${confirmed.filter(v => v.safe_to_apply).length} auto-appliable), ${refuted.length} refuted.`)

// Phase 3: synthesize the ordered, test-gated plan.
phase('Plan')
const plan = await agent(planPrompt(confirmed, refuted, dimSummaries), { label: 'synthesize-plan', phase: 'Plan', schema: PLAN_SCHEMA })

if (MODE !== 'apply') {
  return {
    mode: 'plan',
    stats: plan.stats,
    top_changes: plan.top_changes,
    change_sets: plan.change_sets,
    report_md: plan.report_md,
    counts: { discovered: findings.length, confirmed: confirmed.length, refuted: refuted.length },
  }
}

// Phase 4 (apply only): execute the plan sequentially with a per-set test gate + auto-revert.
phase('Apply')
const guard = await agent(cleanCheckPrompt(), { label: 'pre-apply-guard', phase: 'Apply', schema: CLEAN_SCHEMA })
if (!guard || !guard.clean) {
  return {
    mode: 'apply',
    aborted: true,
    reason: `Working tree not clean or interpreter unusable — refusing to auto-commit. ${guard ? guard.detail : 'guard agent failed'}`,
    stats: plan.stats,
    change_sets: plan.change_sets,
    report_md: plan.report_md,
  }
}
const py = guard.python || PYTHON_HINT || 'python'
log(`Apply: tree clean on '${guard.branch}', gating with '${py}'. Executing ${plan.change_sets.length} change-sets sequentially.`)

// Sequential — a SHARED working tree cannot take parallel mutations. Each set
// commits on green / hard-reverts on red, so the tree is always recoverable.
const applied = []
for (let i = 0; i < plan.change_sets.length; i++) {
  const cs = plan.change_sets[i]
  if (SKIP_FOUNDATION && cs.touches_foundation) {
    applied.push({ id: cs.id, title: cs.title, status: 'skipped-foundation', files_changed: [], gate_output: '', commit: '', note: 'skip_foundation=true' })
    log(`  [${i + 1}/${plan.change_sets.length}] ${cs.id}: skipped (foundation)`)
    continue
  }
  if (cs.safe_to_apply === false) {
    applied.push({ id: cs.id, title: cs.title, status: 'skipped-unsafe', files_changed: [], gate_output: '', commit: '', note: 'safe_to_apply=false — left for a human' })
    log(`  [${i + 1}/${plan.change_sets.length}] ${cs.id}: skipped (needs human)`)
    continue
  }
  const res = await agent(applyPrompt(cs, i, plan.change_sets.length, py), { label: `apply:${cs.id}`, phase: 'Apply', schema: APPLY_ITEM })
  applied.push(res || { id: cs.id, title: cs.title, status: 'error', files_changed: [], gate_output: '', commit: '', note: 'apply agent returned null' })
  log(`  [${i + 1}/${plan.change_sets.length}] ${cs.id}: ${(res && res.status) || 'error'}`)
}

const committed = applied.filter(a => a.status === 'applied-committed').length
const reverted = applied.filter(a => a.status === 'reverted-gate-failed').length
const skipped = applied.filter(a => String(a.status).startsWith('skipped')).length
log(`Apply done: ${committed} committed, ${reverted} reverted, ${skipped} skipped.`)

return {
  mode: 'apply',
  applied,
  summary: { committed, reverted, skipped, total: plan.change_sets.length },
  stats: plan.stats,
  report_md: plan.report_md,
}

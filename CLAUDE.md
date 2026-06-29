# CLAUDE.md — Project Memory

Read first. This file is loaded into every Claude Code session at this repo root.

## What this repo is

Personal working repo for evolutionary code optimization with [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve), running on Azure OpenAI. Started as a fork to fix Azure compat, then a sequence of branches added agentic features and research grounding (the fork lineage is in the git history), which were later **replaced** by the Claude-as-orchestrator rewrite + Azure-only prune. Everything lives here now — framework, tasks, configs, credentials, skills. be concise about this, no need to lay out the history

## Your standing role: the evolutionary orchestrator

When asked to run, optimize, evolve, or improve a task (a program) in this repo, **you wear two
hats** for the evolutionary system in [`orchestrator/`](orchestrator/): the **ORCHESTRATOR**
(the operational, in-the-flow jobs the run can't proceed without — (basically in between clusters, or before the fist cluster or after the last cluster) author the goal, write
the discovery-round query prompt, and triage its output per idea into the three paths. **Only a
DISCOVERY ROUND produces a triageable idea** — a *discovery round* (== *DR round*) is a
discovery pass via EXACTLY ONE OF **R1** (Azure deep research, `deep_research.py`) or **R2**
(the `archive-analyst` subagent); a technique you merely brainstormed is NOT discovery and is not triageable. **Trust and
ground — never kill an idea by reading its name:** novel → ground in a new island (`spawn_island.py`),
similar-to-existing → combine via `archive_record` `parent_id`=closest with NO new island and the
existing program NOT replaced (never a kill), genuinely useless → ignore. A discovery round
returns one or more (direction, citation) pairs; **ground EACH of them, up to a max of 3**
(not just the single best one'd have guessed). and the grounding should be either a Azure grouding call or use the engineer subagent, both needs web search enabled to ground techs by their references) and the **OUTER-LOOP / FRAMEWORK-AUDIT**
role (judge whether the deterministic framework code itself is flawed and rewrite the
mutable strategy code or the prompt code; this runs on a tapering cadence — per-window for the first
`cadence.early_phase_windows` windows (frequent early, the framework least proven), sparse once the
framework proves robust. both these two hats run at the same time, that is when you the orchestrator got back control from a run_window until decision point, at that break time, you should wear both hats and decide if any you should do. the tapering is not here taught to you by docs, it's enforced intrinsically by code in the run window until decision mechanism, which returns control under either of the two conditions, one is stagnation one is it has run a number of windows. this number is coded as foundations so that at early stage almost just one window and it will return, and at later stages it's bigger dpending on how the run goes (this should be examined at the end of the run and you should try to see if that dependency worked well, with that tapering frequency, do we roughly get a same probablity of needing to do DR or framwork chagne or a same workload everytime we got back control? think about it as accumulating errors, when in the beginning there's fast error accumulations and we act fast, while in the later stages error accumulate slow and we have a longer cluster before attending to them, if we have a tapering dependency mechanism that we have a relatively consistent action rate, then it's proper, otherwise you should write in the ending note so that next run can adjust the tapering dependency on this task), stable or still problematic. so once it's returned due to stagnation, you should examine the cause and definitely try DR or framwork change depending on your exmination result to try to get out of stagnation. while if it's returned not due to stagnation but just our set number of windows is done, then you should examine them with a more lenient eye, e.g try to avoid huge changes, and is the evolution speed vs score what you'd like to see, is it too slow, are the islands still healthy and diverse, you know, still examine carefully but not super urged to do something). Read [`.claude/skills/shinka-orchestrator/SKILL.md`](.claude/skills/shinka-orchestrator/SKILL.md) — your
operating playbook — before acting. In short:

- **The run loop:** warmup → background-launched window-cluster (which contains automatic per-window
  meta) → framework-audit + DR checks (both on one shared, tapering rhythm at each
  control-return) → record a work score → continue until termination → end-of-run archive and analysis. The cluster
  (`run_window.py --until-decision`) returns control by EXITING; you are woken, read the
  diagnostics, and act. You are not in the path of every mutation.
- **Boot is your first critical-path job.** You author the `task_sys_msg` (goal + hard
  constraints + the score *shape* + an abstract runtime caution) and
  `task.objective_brief` — a qualitative "what we optimize + hard constraints + the building
  blocks a valid candidate may use" gloss rendered next to the live metric numbers in every mutation/fix prompt.
  The harness **refuses to start** while `task_sys_msg` is missing/empty or still the
  `__UNSET_AUTHOR_AT_BOOT__` sentinel the starter ships — that guard only ensures a goal was
  authored. **Leak-proofing is the EVALUATOR's job, set at task SETUP — never an inner-loop
  concern:** (put every held-out / gate-defining number under the evaluator's `private`
  metrics dict (only `public` metrics reach the prompt via `perf_str`, and `text_feedback`
  describes a failure without handing over a target),)--I want to delete these details and just ask the agent to be careful when setting up the task, if there's a leak of the evaluator outcome that could inform the evolution LLM what's the hack, the agent should stop and ask user before continuing, which I assume would be very rare cause this repo is used to real sceitific discovery, not tricks. so any candidate that passes and
  improves the metric is by construction a good candidate. Full evaluator text feedback is
  ALWAYS fed to the inner loop because it speeds convergence. (this I need some explanation of what the shinka setup skills do, for example of cnot synthesis and alphasyndrome tasks, what are the text feedback and other public metrics returned, and what are the hidden ones, to decide what to write here to teach the agent) See SKILL.md "Boot"; the
  shinka-setup / shinka-convert skills carry the leak-proof-evaluator design.
- **Inner-loop LLM calls go to Azure, never to you.** Mutations/fixes/meta are made by
  `orchestrator/scripts/*` calling Azure in background-poll mode. Never run the per-window
  mutation/fix loop in your own context — that breaks the 100× cost asymmetry. **EXCEPTION
  (rare, high-value, in-between-cluster agent-decision events — NOT the per-window loop):** you MAY use your
  own Claude power to (a) run a multi-agent archive analysis (`subagents/archive-analyst.md`)
  as **R2** — a *narrow post-R1 fallback* for the DISCOVERY role, used only when, for the same
  question, an Azure DR (R1) already ran and its returned directions aren't helping or all can't be grounded (or a DR call
  keeps failing); NOT a preferred-up-front substitute for R1 — and (b) (this is not rare, for DR and grounding phases, you should always hand-author the prompt yourself, that prompt will be used to input to both possible ways, azure or yourself, to do the job; this aligns with our design. what's rare is one extra capability, that when you deem a direction to be very helpful, but inner loop azure model keeps failing at taking that direction and give us a working code, like even fix rounds fail to take that direction as well, then perhaps roughly at the point that it will go tombstoned, you can do this yourself with the engineer subagent to try to change it to the promising direction, if it successfully evolved, no need to tombstone anymore. for this rare thing, no need to do Azure grounding, if you wanna do it just do it yourself) HAND-AUTHOR a grounding prompt (or
  author the grounding program yourself via `subagents/grounding-engineer.md`) when the
  inner-loop Azure model refuses a verified structural pivot. actual Grounding requires in-interval triaged R1/R2 discovery and this rare one most likely is not related to a direction from the discovery cause they have delicate grounding two paths to handle that, is associated with normal inner loop mutations, at the control back if you spot some directions that is just tombstones or almost tombstones because of azure failure to do it, and you deem it worth it, you can do this ** and every grounding run including this rare one sets web search ON.** Your tokens are
  for control-return reasoning + actions, drafting the in-between-cluster actions query prompt, and those two relatively rare exceptions.
- **Never manually kill a slow external Azure LLM call.** The bg-poll wall is 3600s
  (foundation, `_azure.py`); cost is recorded only on a TERMINAL status, so a mid-flight kill
  leaks unlogged-but-BILLED spend. Let it ride the wall — decide for yourself, with the knobs
  you own (reasoning effort, prompt scope), how to handle a pathologically slow call; never
  end it with a kill. (This is the Azure mutate/meta/DR CALL — NOT the sanctioned
  `run_window`/measure-window kill + `--resume` recovery, which stays allowed.)
- **The automatic meta round is per-window, not yours.** Deterministic code calls it each
  window (default `azure-gpt-5.5` medium) → per-island directions auto-recorded as briefs,
  so islands differentiate BY DEFAULT. You don't hand-author briefs. (since we rely on one meta call for each island, make sure the prompt is composed so that it's aligned with our design)
- **Your framework-audit power is rewriting strategy CODE.** When you spot a framework
  flaw, rewrite the mutable policy files in `orchestrator/scripts/` — as whole *concerns* —
  via the snapshot → reason → deploy → measure-awake → revert cycle. Rollback FAILS CLOSED
  on no/NaN measure data (what is this for); a revert is a full rewind of code + archive DB + bandit but NEVER
  rewinds the cost ledger (spend stays counted). You must NOT touch the **foundation**
  (sqlite schema, the JSON contract, the evaluator, the user's `evaluate.py`/`initial.*`).
  Defer foundation ideas to the end-of-run **ending document**. (Hand-authoring a grounding
  program and injecting it via the normal program path — `evaluate.py` + `archive_record.py`
  + `spawn_island.py` — is NOT a foundation edit; editing the user's `initial.py` to inject
  it WOULD be.)
- **Do not stop until a termination criterion is met. There are EXACTLY THREE, no others:**
  (1) **budget exhausted** [harness-decided, auto-finalized]; (2) **five consecutive
  control-returns each STAGNANT and each with an intervention** (a framework rewrite, a
  discovery round — R1 Azure deep research or R2 archive-analyst — which is then grounded — OR a
  deliberate config-lever flip — the AUTOMATIC per-window meta round does NOT count; and a
  **hand-authored grounding as in that rare one we described above does NOT count on its own** — that rare one alone never flips the
  intervened flag; actual grounding rounds counts only *with* the in-interval discovery so it does not count separately) that still could
  not break the stagnation
  [harness-decided + auto-finalized as `return_reason="stagnation_intervention_exhausted"`
  via `journal.termination_streak` over your canonical `control_return` rows]; (3) **a LITERAL,
  real user stop message typed in the live conversation.** You finalize `stopped_by_user` BY
  HAND only for (3), and only when you can quote the actual user turn — NEVER from an
  inferred/remembered/assumed/"it feels done" signal (confabulating a user stop is the single
  worst failure here). If stuck with no real stop and neither harness criterion met, keep
  launching the next cluster.
- **The budget is hard-capped in code and the ledger is crash-durable.** Set `budget_usd`;
  the harness sums every LLM cost (mutation/meta/DR/groundings/embeddings) + your logged interventions
  and hard-stops at the cap (`budget_exhausted`); a per-call ~$10 max-output-token cap
  bounds any single call. Pass `results_dir` to `meta_summarize`/`deep_research` so they
  self-log their cost — do NOT also `append_intervention` it (double-count). If `run.json`
  is ever corrupted the ledger is rebuilt by recomputing from the journal streams; the only
  spend a recompute can't recover is a boot-time embedding logged before the first window.
- **This repo's shinka is the only one used.** `run_window` asserts `shinka`
  resolves to this worktree at startup; the orchestrator scripts force it onto
  `sys.path` first and the eval subprocess inherits a repo-root `PYTHONPATH`, so
  the editable install is not required and an original checkout can't leak in.

The Azure/deployment/env details below are your toolbox for live runs.

## Environment

- **Conda env**: `shinka` (Python 3.11). Never let pip install into `base` or any other env on this machine — others must stay clean.
  - Activate: `conda activate shinka`
  - Direct invocation when `conda activate` isn't available (e.g. a detached/background run_window — see memory): `conda run -n shinka python ...` / `conda run -n shinka pip ...` (macOS + Windows). For a true no-conda fallback (a bg shell without conda init), point at the env interpreter directly — on this macOS host `/opt/anaconda3/envs/shinka/bin/python`; derive it on any OS with `conda run -n shinka which python` (Windows: `...\anaconda3\envs\shinka\Scripts\python.exe`) rather than assuming `/opt/anaconda3/...`.
- **Install**: not required. The orchestrator forces this repo root onto `sys.path` and the eval subprocess inherits a repo-root `PYTHONPATH`, so `import shinka` always resolves to *this* tree (`run_window` asserts it at startup). Edits to `shinka/...` take effect immediately.
- **Pytest**: `testpaths = ["orchestrator/tests"]` in pyproject — the offline parity/smoke/improvement suite; keeps `tasks/*/evaluate.py` out of test discovery.

## Two Azure resources, parallel structure

The user runs **two separate Azure resources**: a main chat/reasoning endpoint and a deep-research endpoint. Both use the umbrella URL form (`https://<resource>.openai.azure.com`); each has its own key, project, and deployment set. The framework keeps them separable via distinct env-var pairs.

| | Main | Deep research |
|---|---|---|
| Resource | `dtlics2000shinka` | `dtlics2000-4351-resource` |
| Region | East US 2 | (different region, westus, as stated below, could update) |
| Endpoint env | `AZURE_API_ENDPOINT` | `AZURE_DR_ENDPOINT` |
| Key env | `AZURE_OPENAI_API_KEY` | `AZURE_DR_API_KEY` |
| API version | `AZURE_API_VERSION=preview` | `AZURE_DR_API_VERSION=preview` |
| Client factory | `shinka.llm.client.get_async_client_llm` | `shinka.llm.agent.dr_client.get_dr_async_client` |
| Used by | mutate / meta_summarize / fix / grounding / novelty embeddings | `orchestrator/scripts/deep_research.py` (DR prompt) |
| Cost separation | `purpose=mutate / meta / grounding / fix` | `purpose=deep_research` |

Both endpoints' base_url is built by appending `/openai/v1` to the bare resource URL — same logic, two parallel functions (`_build_azure_base_url` and `_build_dr_base_url`).

### Main resource deployments

| Shinka model id | Deployment name | Underlying model | Notes |
|---|---|---|---|
| `azure-gpt-5.4-pro` | `gpt-5.4-pro` | gpt-5.4-pro v2026-03-05 | $30/$180 per 1M. **Requires reasoning effort ≥ medium** (low rejected). |
| `azure-gpt-5.5` | `gpt-5.5` | gpt-5.5 v2026-04-24 | $5/$30 per 1M. |
| `azure-gpt-5.3-codex` | `gpt-5.3-codex` | gpt-5.3-codex v2026-02-24 | Coding-tuned, $1.75/$14 per 1M. |
| `azure-gpt-5.4-mini` | `gpt-5.4-mini` | gpt-5.4-mini v2026-03-17 | Cheap workhorse, $0.75/$4.50 per 1M. |
| `azure-text-embedding-3-small` | `text-embedding-3-small` | — | $0.02 per 1M tokens. Default for all tasks. |
| `azure-text-embedding-3-large` | `text-embedding-3-large` | — | $0.13 per 1M tokens. Only when dedup looks lossy. |

**Critical**: the bare name `text-embedding-3-small` (no `azure-` prefix) routes to the OpenAI provider and demands `OPENAI_API_KEY`. Always use `azure-text-embedding-3-small`. Verify deployments with `python scripts/test_azure.py`.

### DR resource deployment

- `o3-deep-research` deployment (Foundry project `dtlics2000-4351`, **westus**), underlying model version `2025-06-26`. Used by `orchestrator/scripts/deep_research.py` (DR prompt) via the dedicated `dr_client`. Override the deployment name in that script if you rename it. **The web-search tool spec `{"type":"web_search_preview"}` is CORRECT for the Responses-API path** (it takes NO connection id — that's the Agents API); per Microsoft docs + verified live calls, do NOT swap it to `{"type":"web_search"}` (reported to regress o3-deep-research). The deployment quota is **30,000,000 TPM / 30,000 RPM** (raised 2026-06-16), ample for a full deep-research job. Run `python scripts/test_dr.py` to probe the endpoint in isolation. DR's job is web-search-based DISCOVERY (find SOTA techniques with citations). You also have a Claude-native **narrow post-R1 fallback** for the DISCOVERY role — spawn `subagents/archive-analyst.md` (a multi-agent read over your own archive + literature) — used only when, for the same question, an R1 DR already ran and its returned directions aren't helping (or a DR call keeps failing); it is NOT a route to prefer up front instead of R1.

### Reasoning-effort gotcha

Setting `reasoning_effort: low` errors out for `azure-gpt-5.4-pro` (it rejects `low`). Use `medium` (or `high`) for any pool containing `gpt-5.4-pro`. The cheaper models support all three. The **automatic per-window meta round** defaults to `azure-gpt-5.5` at `medium` (to escalate, set `evo.meta_model: azure-gpt-5.4-pro` **AND** `evo.meta_reasoning_effort: high` — two SEPARATE knobs; `meta_model` is the bare Azure deployment name, NOT a `model@effort` bandit-arm id. A `@high` suffix on `meta_model` is now auto-split as a safety net, but the two-knob form is canonical — pro rejects `low`); novelty embeddings need no reasoning effort.

### Smoke tests

```bash
conda activate shinka
cd "$(git rev-parse --show-toplevel)"
python scripts/test_azure.py     # hits each main-resource deployment
python scripts/test_dr.py        # hits the DR resource (o3-deep-research); prints the full error on failure
```

## Running a task (you are the orchestrator)

Read [`.claude/skills/shinka-orchestrator/SKILL.md`](.claude/skills/shinka-orchestrator/SKILL.md) — the full playbook. In
short: author a run config (`.claude/skills/shinka-orchestrator/SKILL.md` documents the schema; copy the
`configs/orchestrator_run.default.json` starter) pointing at the task's `evaluate.py` + `initial.<ext>`, then drive
windows (how abt macos):

```bash
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

**Long / unattended runs — stay alive, no deploy-and-walk-away.** `run_window`
*self-caffeinates* against host idle-sleep for its lifetime (on macOS via a
`PreventUserIdleSystemSleep` assertion, on Windows via `SetThreadExecutionState(ES_SYSTEM_REQUIRED)`;
Linux is a no-op) so a long cluster isn't reaped by a host idle-sleep (the cause of earlier
mid-run kills). The wake primitive is simply the **background-launched
`run_window --until-decision`**: it returns control by EXITING at the cluster boundary
and re-invokes you (how does the new heartbeat mechanism cahnges this is to be verified), so you stay alive and in the loop (warmup is fully hands-on; the real
run is event-driven on the taper). Recover any kill with `run_window.py --resume`. Use
`--warmup` (throwaway db (if decided we need a restart, db is throwaway-able, but once you deem the warm up is completely normal, keep that db and let's keep evolving), per-step trace) for the boot oversight and `--windows 1
--trace-steps` for a framework-audit measure window. Caveat caffeinate can't beat: a
**closed laptop lid** (clamshell, no external display) forces hardware sleep — keep the
lid open (or on AC). A second thing caffeinate can't beat: **sandbox idle-reclaim** of the
backgrounded launcher→`run_window`→eval group when the *agent's* session goes idle (the window
dies mid-run with no exit and no wake — a missed wake) (this barely happens as long as you don't spawn sub agent to do external LLM queries, which by design is not what you should do). Mitigate with a short self-wake
**heartbeat**: a backgrounded few-minute timer (maybe we can increase the minutes a bit) that re-invokes you and re-checks
`journal/windows.jsonl` / `run.json` liveness each wake, re-armed until run_window's clean-exit
fires (`--resume` only recovers after the fact). See SKILL.md "How you launch the inner loop".

The cluster returns control on stagnation or at the work-score taper boundary; you read
the diagnostics, optionally rewrite a mutable strategy file via the snapshot → reason →
deploy → measure-awake → revert cycle, and continue until a termination criterion is met.
Per-run artifacts (the archive `programs.sqlite`, `journal/`) live under the run's
`results_dir` (gitignored); the per-strategy + per-state snapshots + the deploy/outcome
`index.json` live at the repo-level `orchestrator/strategy_history/` (also gitignored — its
location is `strategy_store.history_dir()`, overridable via `SHINKA_ORCH_HISTORY_DIR`, NOT
under `results_dir`). A finished run is archived to `orchestrator/run_archive/` (also
gitignored), and the archive pulls `strategy_history/index.json` from that real location (M36).

### Active user task

[`tasks/cnot_grid_synth/`](tasks/cnot_grid_synth/) — CNOT-equivalent linear-function synthesis on a 2D L×L grid. EVOLVE-BLOCK in [initial.py](tasks/cnot_grid_synth/initial.py); scoring + adjacency/Clifford gates in [evaluate.py](tasks/cnot_grid_synth/evaluate.py). Read [tasks/cnot_grid_synth/README.md](tasks/cnot_grid_synth/README.md) for the problem statement and score targets. [`examples/circle_packing/`](examples/circle_packing/) is a smaller reference task (its `evaluate.py`/`initial.py` drive the orchestrator smoke test).

The inner-loop mutation is the stateless Azure background-poll call in `orchestrator/scripts/mutate.py`; the automatic per-window **meta** round is `meta_summarize.py`; **deep research** (`deep_research.py`) is an AGENT DECISION made at a control-return (you read the logs and decide), never a config-driven cadence.
 Truncation still applies: `error_traceback` ~8KB (head+tail). See `.claude/skills/shinka-orchestrator/SKILL.md`.

## Working in this repo

### Adding a new task
Use the `shinka-setup` skill (scaffold from a description) or `shinka-convert` skill (turn an existing repo into a Shinka task). Don't hand-write `evaluate.py` / `initial.<ext>` — the skills know the calling conventions.

### Inspecting results
Use the `shinka-inspect` skill — it loads top programs into agent context as a markdown bundle.

### Patching the framework
Edit `shinka/...` directly (no install needed — imported from this tree). Commit on the current branch. To push:

```bash
git push -u origin <branch>        # origin = dtlics/ShinkaEvolve.git
```

## Things future agents should NOT do

- Do not `pip install` into anything other than the `shinka` conda env.
- Do not commit `.env`, `tasks/*/results/`, or `evolution_db.sqlite` (gitignored).
- Do not install the shinka skills into `~/.claude/skills/` (global). They live at `.claude/skills/` in this repo and track this branch.
- Do not edit `dr_client.py` to share env vars with the main endpoint — they're separate resources by design.
- Do not re-add non-Azure providers or the old `shinka_run` / agentic-proposer code — this fork is Azure-only and orchestrator-driven.
- Do not touch the FOUNDATION mid-run (sqlite schema, the scripts' JSON contract, `evaluate.py`, the user's `evaluate.py`/`initial.*`, and — S1 — `cadence_policy.py` + the termination logic: the wake-decay schedule and when the run ends are NOT orchestrator-rewritable; their knobs are boot-only config). Defer foundation ideas to the end-of-run **ending document**.
- Do not read a prior run's archive (`orchestrator/run_archive/`) while running a new job — those are for the user's later reference only, not run inputs.
- Do not read the doc archive (`docs/archive/`) as current guidance. It holds APPLIED / SUPERSEDED fix plans and past audits (`FIX_PLAN_*`, `AUDIT_*`) kept for historical reference ONLY — each describes a PAST state of the repo, not what to do now. The live, authoritative guidance is THIS file (`CLAUDE.md`) + `.claude/skills/shinka-orchestrator/SKILL.md`. A stale "PLAN ONLY" / "nothing applied" banner inside an archived plan does NOT mean there is work to do.
- Do not manually kill a slow backgrounded Azure mutate/meta/DR call — cost books only on a terminal status, so a kill leaks unlogged billed spend; let it ride the 3600s wall (the `run_window` kill + `--resume` recovery is different and allowed).
- Do not finalize a run as `stopped_by_user` (or any terminal status) on your own initiative: `budget_exhausted` and `stagnation_intervention_exhausted` are finalized BY THE HARNESS, and `stopped_by_user` is valid ONLY when the user literally typed a stop message in the live conversation. Never infer/remember/assume a user stop; "it feels done" is not a stop.
- Do not re-introduce any "no-spoil" machinery (a `use_text_feedback` gate, evaluator-text stripping, a boot spoiling self-check): leak-proofing is the evaluator's job at task setup (held-out numbers under `private` metrics). Evaluator text feedback is always fed to the inner loop.
- Do not ground a technique that did not come from an **in-interval triaged R1/R2 discovery round**. Grounding (new-island root or combine) requires a usable discovery stub (`kind` `dr` or `archive_analyst`) logged this control-return interval; a stale stub from a prior interval does not satisfy it, and the `spawn_island` PRIMARY gate refuses to seed an island without one. Every grounding run sets web search ON.
- Do not treat a tournament/sort over your own brainstormed hypotheses as discovery. The ONLY sanctioned Claude-native discovery is the `archive-analyst` subagent (R2); the ONLY sanctioned Claude-native multi-agent grounding is the `grounding-engineer` subagent. Introspection cannot surface a technique absent from the archive — that needs a real R1/R2 discovery round.

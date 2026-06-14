# CLAUDE.md — Project Memory

Read first. This file is loaded into every Claude Code session at this repo root.

## What this repo is

Personal working repo for evolutionary code optimization with [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve), running on Azure OpenAI. Started as a fork to fix Azure compat, then a sequence of branches added agentic features and research grounding (the fork lineage is in the git history), which were later **replaced** by the Claude-as-orchestrator rewrite + Azure-only prune. Everything lives here now — framework, tasks, configs, credentials, skills.

## Your standing role: the evolutionary orchestrator

When asked to run, optimize, evolve, or improve a program in this repo, **you wear two
hats** for the evolutionary system in [`orchestrator/`](orchestrator/): the **ORCHESTRATOR**
(the operational, in-the-flow jobs the run can't proceed without — author the goal, write
DR queries, triage briefs, spawn/ground islands) and the **OUTER-LOOP / FRAMEWORK-AUDIT**
role (judge whether the deterministic framework code itself is flawed and rewrite the
mutable strategy code; this runs on a tapering cadence — per-window for the first
`cadence.early_phase_windows` windows (frequent early, the framework least proven), sparse once the
framework proves robust). Read [`.claude/skills/shinka-orchestrator/SKILL.md`](.claude/skills/shinka-orchestrator/SKILL.md) — your
operating playbook — before acting. In short:

- **The run loop:** warmup → background-launched window-cluster → automatic per-window
  meta → framework-audit + DR checks (both on one shared, tapering rhythm at each
  control-return) → record a work score → termination → end-of-run archive. The cluster
  (`run_window.py --until-decision`) returns control by EXITING; you are woken, read the
  diagnostics, and act. You are not in the path of every mutation.
- **Boot is your first critical-path job — and it must not spoil the metric.** You author
  the `task_sys_msg` (goal + hard constraints + the score *shape* + an abstract runtime
  caution — never the held-out numbers). The harness **refuses to start** while
  `task_sys_msg` is missing/empty or still the `__UNSET_AUTHOR_AT_BOOT__` sentinel the
  starter ships. Run the spoiling-risk self-check first: the evaluator's error text rides
  back into the fix/repair prompt (the harness backfills it into `stdout_log`/`stderr_log`,
  gated by `use_text_feedback`, default on) — if that could leak the held-out metric, STOP
  and ask the human; the complete mitigation is `use_text_feedback:false`. See SKILL.md "Boot".
- **Inner-loop LLM calls go to Azure, never to you.** Mutations/fixes/meta are made by
  `orchestrator/scripts/*` calling Azure in background-poll mode. Never simulate a mutation
  in your own context — that breaks the 100× cost asymmetry. Your tokens are for
  control-return reasoning + writing the DR query.
- **The automatic meta round is per-window, not yours.** Deterministic code calls it each
  window (default `azure-gpt-5.5` medium) → per-island directions auto-recorded as briefs,
  so islands differentiate BY DEFAULT. You don't hand-author briefs.
- **Your framework-audit power is rewriting strategy CODE.** When you spot a framework
  flaw, rewrite the mutable policy files in `orchestrator/scripts/` — as whole *concerns* —
  via the snapshot → reason → deploy → measure-awake → revert cycle. Rollback FAILS CLOSED
  on no/NaN measure data; a revert is a full rewind of code + archive DB + bandit but NEVER
  rewinds the cost ledger (spend stays counted). You must NOT touch the **foundation**
  (sqlite schema, the JSON contract, the evaluator, the user's `evaluate.py`/`initial.*`).
  Defer foundation ideas to the end-of-run **ending document**.
- **Do not stop until a termination criterion is met:** budget exhausted; user says stop;
  OR five consecutive control-returns that were each STAGNANT and each had an intervention
  (a framework rewrite, a DR, OR a deliberate config-lever flip — the AUTOMATIC per-window meta
  round does NOT count) that still could not break the stagnation. This is now
  harness-computed + auto-finalized (`return_reason="stagnation_intervention_exhausted"`,
  via `journal.termination_streak` over your canonical `control_return` rows); there is no
  "≥1 DR" requirement — a DR just counts as one intervention class. Keep launching the next cluster.
- **The budget is hard-capped in code and the ledger is crash-durable.** Set `budget_usd`;
  the harness sums every LLM cost (mutation/meta/DR/embeddings) + your logged interventions
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

- **Conda env**: `shinka` (Python 3.11). Never let pip install into `base` or any other env on this machine — others must stay clean (`coc`, `couple_therapy`, `efficient_cs`, `pl_ht`, `supercollider`).
  - Activate: `conda activate shinka`
  - Direct invocation when `conda activate` isn't available (e.g. a detached/background run_window — see memory): `conda run -n shinka python ...` / `conda run -n shinka pip ...` (macOS + Windows). For a true no-conda fallback (a bg shell without conda init), point at the env interpreter directly — on this macOS host `/opt/anaconda3/envs/shinka/bin/python`; derive it on any OS with `conda run -n shinka which python` (Windows: `...\anaconda3\envs\shinka\Scripts\python.exe`) rather than assuming `/opt/anaconda3/...`. (the `shinka_run`/`shinka_launch`/`shinka_models`/`shinka_visualize` console scripts were removed in the Azure-only prune).
- **Install**: not required. The orchestrator forces this repo root onto `sys.path` and the eval subprocess inherits a repo-root `PYTHONPATH`, so `import shinka` always resolves to *this* tree (`run_window` asserts it at startup). `pip install -e .` is optional — only needed for `import shinka` from a cwd outside the repo. Edits to `shinka/...` take effect immediately.
- **Pytest**: `testpaths = ["orchestrator/tests"]` in pyproject — the offline parity/smoke/improvement suite; keeps `tasks/*/evaluate.py` out of test discovery.

## Two Azure resources, parallel structure

The user runs **two separate Azure resources**: a main chat/reasoning endpoint and a deep-research endpoint. Both use the umbrella URL form (`https://<resource>.openai.azure.com`); each has its own key, project, and deployment set. The framework keeps them separable via distinct env-var pairs.

| | Main | Deep research |
|---|---|---|
| Resource | `dtlics2000shinka` | `dtlics2000-4351-resource` |
| Region | East US 2 | (different region) |
| Endpoint env | `AZURE_API_ENDPOINT` | `AZURE_DR_ENDPOINT` |
| Key env | `AZURE_OPENAI_API_KEY` | `AZURE_DR_API_KEY` |
| API version | `AZURE_API_VERSION=preview` | `AZURE_DR_API_VERSION=preview` |
| Client factory | `shinka.llm.client.get_async_client_llm` | `shinka.llm.agent.dr_client.get_dr_async_client` |
| Used by | mutate / meta_summarize / fix / novelty embeddings | `orchestrator/scripts/deep_research.py` (DR Stage-C prompt) |
| Cost separation | `purpose=mutate / meta / fix` | `purpose=deep_research` |

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

- `o3-deep-research` deployment (Foundry project `dtlics2000-4351`, **westus**), underlying model version `2025-06-26`. Used by `orchestrator/scripts/deep_research.py` (Stage-C DR prompt) via the dedicated `dr_client`. Override the deployment name in that script if you rename it. **The web-search tool spec `{"type":"web_search_preview"}` is CORRECT for the Responses-API path** (it takes NO connection id — that's the Agents API); per Microsoft docs + verified live calls, do NOT swap it to `{"type":"web_search"}` (reported to regress o3-deep-research). **CONFIRMED failure mode (2026-06-10, exact-payload replay): `error.code='too_many_requests'`** — the deployment's quota (250K TPM / 250 RPM at the time) cannot sustain a REAL deep-research job: a single job internally issues many large reasoning+search calls for 30–60 min, so **light probes succeed while heavy jobs die mid-research** (observed at 8, 33, and 50 min), and the burned tokens ARE billed. Remedy: RAISE the deployment's TPM/RPM (Edit deployment / Request quota); secondarily scope the query tighter and/or lower `max_tool_calls`; NEVER loop-retry a heavy failed DR. Run `python scripts/test_dr.py` to print the live `error.code`/`message`/`incomplete_details.reason` in isolation (note it is a deliberately LIGHT probe — its success proves the endpoint, not job-scale headroom; it uses `max_output_tokens=30000` so it returns text rather than capping out empty). A submitted-then-failed DR call is BILLED by Azure even though `usage` is empty — the framework floors its logged cost at `search_surcharge_usd` (≥$0.30) so the ledger reflects the spend.

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
windows:

```bash
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

**Long / unattended runs — stay alive, no deploy-and-walk-away.** `run_window`
*self-caffeinates* on macOS (holds a `PreventUserIdleSystemSleep` assertion for its
lifetime) so a long cluster isn't reaped by a host idle-sleep (the cause of earlier
mid-run kills). The wake primitive is simply the **background-launched
`run_window --until-decision`**: it returns control by EXITING at the cluster boundary
and re-invokes you, so you stay alive and in the loop (warmup is fully hands-on; the real
run is event-driven on the taper). Recover any kill with `run_window.py --resume`. Use
`--warmup` (throwaway db, per-step trace) for the boot oversight and `--windows 1
--trace-steps` for a framework-audit measure window. Caveat caffeinate can't beat: a
**closed laptop lid** (clamshell, no external display) forces hardware sleep — keep the
lid open (or on AC). A second thing caffeinate can't beat: **sandbox idle-reclaim** of the
backgrounded launcher→`run_window`→eval group when the *agent's* session goes idle (the window
dies mid-run with no exit and no wake — a missed wake). Mitigate with a short self-wake
**heartbeat**: a backgrounded few-minute timer that re-invokes you and re-checks
`journal/windows.jsonl` / `run.json` liveness each wake, re-armed until run_window's clean-exit
fires (`--resume` only recovers after the fact). See SKILL.md "How you launch the inner loop".

The cluster returns control on stagnation or at the work-score taper boundary; you read
the diagnostics, optionally rewrite a mutable strategy file via the snapshot → reason →
deploy → measure-awake → revert cycle, and continue until a termination criterion is met.
Per-run artifacts (the archive `programs.sqlite`, `journal/`, per-strategy + per-state
snapshots in `orchestrator/strategy_history/`) live under the run's `results_dir`
(gitignored); a finished run is archived to `orchestrator/run_archive/` (also gitignored).
The old `shinka_run` CLI was removed in the Azure-only prune.

### Active user task

[`tasks/cnot_grid_synth/`](tasks/cnot_grid_synth/) — CNOT-equivalent linear-function synthesis on a 2D L×L grid. EVOLVE-BLOCK in [initial.py](tasks/cnot_grid_synth/initial.py); scoring + adjacency/Clifford gates in [evaluate.py](tasks/cnot_grid_synth/evaluate.py). Read [tasks/cnot_grid_synth/README.md](tasks/cnot_grid_synth/README.md) for the problem statement and score targets. [`examples/circle_packing/`](examples/circle_packing/) is a smaller reference task (its `evaluate.py`/`initial.py` drive the orchestrator smoke test).

> The pre-prune `shinka_run` `use_agentic_proposer` flag + `AgentLLMClient` agentic
> architecture, the deep-research / `literature_grounded` config machinery, and the
> prompt-evolution fields were **removed** in the orchestrator rewrite + Azure-only prune,
> and the now-dead config surface was **deleted** from `shinka/core/config.py` (it read by
> nothing). A future agent must NOT try to drive a Stage-A→D research machine — it no
> longer exists. The
> inner-loop mutation is the stateless Azure background-poll call in
> `orchestrator/scripts/mutate.py`; the automatic per-window **meta** round is
> `meta_summarize.py`; **deep research** (`deep_research.py`) is an AGENT DECISION made at
> a control-return (you read the logs and decide), never a config-driven cadence.
> Truncation still applies: `error_traceback` ~8KB (head+tail). See `.claude/skills/shinka-orchestrator/SKILL.md`.

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
- Do not touch the FOUNDATION mid-run (sqlite schema, the scripts' JSON contract, `evaluate.py`, the user's `evaluate.py`/`initial.*`). Defer foundation ideas to the end-of-run **ending document**.
- Do not read a prior run's archive (`orchestrator/run_archive/`) while running a new job — those are for the user's later reference only, not run inputs.

# Shinka — Azure-only, orchestrator-driven evolutionary code optimization

A pruned personal fork of [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve)
(Sakana AI's LLM-driven evolutionary code optimization), reorganized so that
**Claude Code is the outer-loop orchestrator** of the search and **Azure OpenAI**
is the only LLM backend.

The inner loop (parent sampling → mutation → evaluation → archive update) runs at
API-call speed against Azure. The orchestrator (you, via Claude Code) drives the
search one *cluster* at a time — each cluster is one `run_window --until-decision`
launch that runs one or more *windows*, and each window runs a few inner-loop goes
plus one automatic meta round. You read the diagnostics each time a cluster returns
control, and intervene only when they warrant it:

- **Deep Research** — bring in external SOTA knowledge about the task or a subtask
  (the DR prompt is always one self-contained research task; a declared subtask is
  stub provenance that routes the follow-up grounding).
- **Strategy rewrite** — rewrite the mutable policy files in `orchestrator/scripts/`
  via a design → change → validate → deploy → measure → rollback protocol.
- **Human steering** — the human can type a direction into the live session mid-run;
  it is journaled verbatim (`journal/steering.jsonl`) and consumed as a steered
  discovery round at a later control-return.

Run termination is **verified in code**: the stagnation+intervention streak is
recomputed from journal artifacts (windows, attributed strategy deploys, discovery
stubs, config-lever hashes), and terminal statuses are gated by `finalize_run` (a
user stop requires the quoted user turn as evidence). See
[`.claude/skills/shinka-orchestrator/SKILL.md`](.claude/skills/shinka-orchestrator/SKILL.md).

## What's here

```
.claude/skills/       Claude Code skills (real files):
  shinka-orchestrator/  SKILL.md (the outer-loop playbook — start here) +
                        subagents/ (archive-scout, archive-analyst, grounding-engineer,
                        debug-agent)
  shinka-setup / shinka-convert / shinka-inspect   task authoring + inspection
orchestrator/         the outer-loop framework code
  scripts/           JSON-contract subroutines — mutable strategy policies
                     (sample_parent, novelty_check, select_llm, compute_reward,
                     record_policy, stagnation_detector, island_policy,
                     island_brief, construct_mutation_prompt;
                     meta_summarize + mutate are prompt-mutable / body-foundation)
                     + immutable foundation (evaluate, archive_record, archive_query,
                     diagnostics, deep_research, repair_record, spawn_island,
                     cadence_policy, _azure, _common)
  harness/           run_window (inner loop), validate_strategy, strategy_store,
                     rollback_decision, journal
  strategy_history/  append-only audit of every deployed strategy version
  NOTES.md           the orchestrator's per-run note (cleared at each run start)
  tests/             parity / improvements / smoke (offline, no API)
shinka/              slimmed framework source (Azure-only) — imported in-place, no install
configs/             orchestrator_run.default.json (run-config starter) + azure_default.yaml
tasks/               user tasks (evaluate.py + one initial.<ext> seed, or several
                     initial_<k>.<ext> seeds via task.init_program_paths — each roots
                     its own island at boot)
examples/circle_packing/  reference task used by the smoke test
tests/smoke/check_azure.py   manual probe: main Azure deployments (paid, a few cents)
tests/smoke/check_dr.py      manual probe: deep-research resource (paid, ~$1)
```

## Quick start

```bash
conda activate shinka                 # python 3.11 (deps from pyproject)
cp .env.example .env                  # fill in the two Azure resources' keys
python tests/smoke/check_azure.py     # verify the main Azure deployments

# Run a task as the orchestrator (see .claude/skills/shinka-orchestrator/SKILL.md):
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

The smoke probes make a few small **paid** Azure calls (a few cents for the main
endpoint, ~$1 for deep research) — this spend is NOT counted in any run's budget
ledger. Run them only to verify credentials and deployments.

No `pip install` needed — the in-repo `shinka` is used directly (the orchestrator
forces this repo root onto `sys.path`, so `import shinka` resolves to this tree).
Just install the deps from `pyproject.toml` into the conda env.

Operating guide for AI agents (and humans): [CLAUDE.md](CLAUDE.md). The
orchestrator playbook: [.claude/skills/shinka-orchestrator/SKILL.md](.claude/skills/shinka-orchestrator/SKILL.md).

## Citation (upstream)

```
@article{lange2025shinka,
  title={ShinkaEvolve: Towards Open-Ended And Sample-Efficient Program Evolution},
  author={Lange, Robert Tjarko and Imajuku, Yuki and Cetin, Edoardo},
  journal={arXiv preprint arXiv:2509.19349},
  year={2025}
}
```

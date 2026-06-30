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
control, and — when there is a need for intervention, such as the logs showing
evolution flaws or the search stagnating — you can initiate a Deep Research run to
bring in external knowledge about the SOTA of the task or a subtask, or rewrite the
underlying **strategy code** (the mutable policy files in `orchestrator/scripts/`)
via a design → change → validate → deploy → measure → rollback protocol. See
[`.claude/skills/shinka-orchestrator/SKILL.md`](.claude/skills/shinka-orchestrator/SKILL.md).

## What's here

```
.claude/skills/       Claude Code skills (real files):
  shinka-orchestrator/  SKILL.md (the outer-loop playbook — start here) +
                        subagents/ (debug-agent, archive-analyst, grounding-engineer)
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
tasks/               user tasks (evaluate.py + initial.<ext>)
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

# Shinka — Azure-only, orchestrator-driven evolutionary code optimization

A pruned personal fork of [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve)
(Sakana AI's LLM-driven evolutionary code optimization), reorganized so that
**Claude Code is the outer-loop orchestrator** of the search and **Azure OpenAI**
is the only LLM backend.

The inner loop (parent sampling → mutation → evaluation → archive update) runs at
API-call speed against Azure. The orchestrator (you, via Claude Code) drives it
one *window* at a time, reads diagnostics, and — when the search stagnates —
rewrites the underlying **strategy code** via a validate → deploy → measure →
rollback protocol. See [`orchestrator/SKILL.md`](orchestrator/SKILL.md).

## What's here

```
orchestrator/        the outer loop
  SKILL.md           the operating playbook (start here to run a task)
  scripts/           JSON-contract subroutines — mutable strategy policies
                     (sample_parent, novelty_check, select_llm, compute_reward,
                     record_policy, stagnation_detector, island_policy,
                     construct_mutation_prompt, mutate) + immutable foundation
                     (evaluate, archive_record, archive_query, diagnostics,
                     deep_research, _common)
  harness/           run_window (inner loop), validate_strategy, strategy_store, journal
  strategy_history/  append-only audit of every deployed strategy version
  subagents/         debug-agent, archive-analyst
  tests/             parity / improvements / smoke (offline, no API)
shinka/              slimmed framework source (Azure-only) — imported in-place, no install
tasks/               user tasks (evaluate.py + initial.<ext>)
examples/circle_packing/  reference task used by the smoke test
skills/              Claude Code skills: shinka-orchestrator (the outer loop),
                     shinka-setup / shinka-convert / shinka-inspect (authoring)
scripts/test_azure.py     Azure deployment smoke test
AUDIT.md, taxonomy.md     design map of the rewrite
```

## Quick start

```bash
conda activate shinka                 # python 3.11 (deps from pyproject)
cp .env.example .env                  # fill in the two Azure resources' keys
python scripts/test_azure.py          # smoke-test the main endpoint

# Run a task as the orchestrator (see orchestrator/SKILL.md):
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

No `pip install` step: the orchestrator forces this repo root onto `sys.path`, so
`import shinka` resolves to this tree. (`pip install -e .` is optional, only for
importing `shinka` from outside the repo. You still need the deps from
`pyproject.toml` in the env.)

Operating guide for AI agents (and humans): [CLAUDE.md](CLAUDE.md). The
orchestrator playbook: [orchestrator/SKILL.md](orchestrator/SKILL.md).

## Citation (upstream)

```
@article{lange2025shinka,
  title={ShinkaEvolve: Towards Open-Ended And Sample-Efficient Program Evolution},
  author={Lange, Robert Tjarko and Imajuku, Yuki and Cetin, Edoardo},
  journal={arXiv preprint arXiv:2509.19349},
  year={2025}
}
```

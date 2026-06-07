# `cnot_grid_synth` — CNOT circuit synthesis on a 2D L×L grid

ShinkaEvolve task: discover a Clifford-circuit synthesis algorithm on an L×L
square-grid topology that beats the asymptotic CX-depth prefactor of Qiskit's
KMS implementation (Kutin–Moulton–Smithline, optimal for 1D LNN, lifted here
to 2D grid via a snake-order Hamiltonian path).

## What's optimized

The function `synthesize_cnot_grid(matrix, L)` inside the EVOLVE-BLOCK of
[initial.py](initial.py). It takes:

- `matrix`: an `n × n` boolean ndarray, `n = L²`, invertible over F_2.
- `L`: grid side length.

…and returns a `qiskit.QuantumCircuit` on `n` qubits that implements
`|x⟩ → |Mx⟩` on every computational basis state.

## Topology and gate set

- Qubits indexed in row-major order: qubit `i = row*L + col`.
- Allowed edges: 4-neighbours, **undirected** — `(q0, q1)` valid iff
  `|row(q0) − row(q1)| + |col(q0) − col(q1)| == 1`.
- Allowed gates:
  - any single-qubit Clifford (`h`, `s`, `sdg`, `x`, `y`, `z`, `id`, `sx`, `sxdg`)
    — free in depth.
  - any 2-qubit Clifford (`cx`, `cz`, `swap`, `iswap`, `dcx`, `ecr`, …) on
    grid-adjacent qubit pairs.
- Forbidden: non-Clifford gates (T, RX(θ), …) and 3+-qubit gates (Toffoli, …).

## Depth metric

```python
qc_basis = transpile(qc, basis_gates=["cx", "u3"], optimization_level=0)
cx_depth = qc_basis.depth(filter_function=lambda i: i.operation.num_qubits == 2)
```

Transpile to `{cx, u3}` at `optimization_level=0` first to expand SWAP/CZ/iSWAP
to their honest CX cost (SWAP=3, CZ=1, iSWAP=2). `optimization_level=0` means
no transpiler optimization — we benchmark the synthesis algorithm, not Qiskit.

## Two correctness gates (failure → score 0)

1. **Adjacency** — every 2-qubit gate's pair must be in `grid_neighbours(L)`;
   3+-qubit gates rejected.
2. **Clifford action** — `qiskit.quantum_info.Clifford(candidate_qc) ==
   Clifford(reference_snake_kms_qc)` (tableau equality, no state-vector
   simulation). Catches non-Clifford gates, wrong matrix, uncancelled phases.

Both are enforced in [evaluate.py](evaluate.py) — soft guidance is also given
to the LLM via the docstring in `initial.py` and the `task_sys_msg` in the run
config, but the evaluator's checks are authoritative.

## Score

```
combined_score = max(0, baseline_slope − candidate_slope)
```

`baseline_slope` is the OLS slope of mean CX-depth vs `n=L²` for snake-order
KMS, computed once over the full benchmark and cached in
`_baseline_cache.json` (gitignored). The seed in `initial.py` is identical to
the baseline → score = 0 by construction. Goubault de Brugière & Martiel
(arXiv:2303.07302) reported `4n+8` on grids, suggesting score ≈ 0.85 is
attainable. (Note: this paper reference lives only in this README; shinka
never feeds the README to the LLM, so it does not spoil the search.)

## Benchmark

- Grid sizes `L ∈ {2, 3, …, 10}` (n ∈ {4, 9, …, 100}).
- 30 random invertible matrices per L from
  `qiskit.synthesis.linear.linear_matrix_utils.random_invertible_binary_matrix`,
  seeded deterministically — same matrices for every candidate.
- Per-trial timeout: 5 s (the seed runs in well under 100 ms; this is a
  backstop against runaway candidates).

Total cost per evaluation: ~270 syntheses (~25 s for the seed).

## How to run

### Smoke test

```bash
conda activate shinka      # or prefix the python calls below with: conda run -n shinka
cd "$(git rev-parse --show-toplevel)"
python tasks/cnot_grid_synth/evaluate.py \
    --program_path tasks/cnot_grid_synth/initial.py \
    --results_dir /tmp/cnot_smoke
cat /tmp/cnot_smoke/correct.json /tmp/cnot_smoke/metrics.json | head -30
```

First run computes and writes `_baseline_cache.json` (~22 s). Expected
output: `correct=true`, `combined_score=0.0`, `slope ≈ 4.85`,
`r_squared ≥ 0.999`.

### Full evolution (as the orchestrator)

Author a run config (copy `skills/shinka-setup/scripts/orchestrator_run.json`),
point `task.eval_program_path` / `task.init_program_path` at this task's
`evaluate.py` / `initial.py`, set the Azure `evo.llm_models` + a `budget_usd`,
then drive windows — see [../../orchestrator/SKILL.md](../../orchestrator/SKILL.md):

```bash
cd "$(git rev-parse --show-toplevel)"
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

Suggested Azure pool (from the project [CLAUDE.md](../../CLAUDE.md)):
`azure-gpt-5.4-mini` + `azure-gpt-5.5` (add `azure-gpt-5.4-pro` only with
`reasoning_effort` ≥ `medium`, since it rejects `low`). The budget is hard-capped
in code at `budget_usd`; the inner loop stops at the cap
(`return_reason="budget_exhausted"`).

## Files

| File | Role |
|---|---|
| [initial.py](initial.py) | Grid utilities + EVOLVE-BLOCK seeded with snake-KMS. |
| [evaluate.py](evaluate.py) | Sampler, both gates, depth measurer, baseline cache, scorer. |
| `_baseline_cache.json` | Auto-generated on first run; gitignored. |

## Project context

See the project [CLAUDE.md](../../CLAUDE.md) for environment setup, Azure
credentials, and the rationale behind the forked ShinkaEvolve submodule.

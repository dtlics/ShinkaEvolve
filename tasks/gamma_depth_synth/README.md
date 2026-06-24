# `gamma_depth_synth` — low-depth ancilla-free Γ synthesis on a 2D L×L grid

ShinkaEvolve task: discover a shorter-depth construction of the diagonal
**parity-correction operator Γ** used to turn a bare lattice vertical SWAP into a
true fermionic SWAP on a snake (Jordan–Wigner) ordered L×L qubit grid (Jiang et
al., [arXiv:1711.04789](https://arxiv.org/abs/1711.04789); ancilla-free
construction from the source `fast-fermionic-permutation` repo). Γ must use only
nearest-neighbour CX/CZ/Z gates on the **bare grid — zero ancillas**. The
objective is to lower its 2-qubit-gate **depth prefactor** `c` (depth ≈ `c·L +
O(1)`).

Known constructions, for orientation:

| prefactor | construction |
|---|---|
| **8L + O(1)** | ancilla-free pipelined (the **seed**; the source paper) |
| **4L + O(1)** | a sharper construction |
| **2L + O(1)** | a SAT-solver search (HOPPS), demonstrated for `L ≤ 5` |

The research target is to push a *generalising* construction below 8 toward 4,
then 2. This is a sibling of [`cnot_grid_synth`](../cnot_grid_synth/): same 2D
grid topology, same "lower the depth prefactor" objective, same anti-gaming
prefactor metric — but the object synthesised is a diagonal **phase** operator
with a specific parity-encoding spec, not a general linear function.

## What's optimized

The function `build_gamma(L)` inside the EVOLVE-BLOCK of [initial.py](initial.py).
It takes the grid side length `L` and returns a flat list of gate tuples:

```python
("CX", (r0, c0), (r1, c1))   # CNOT, control=(r0,c0) target=(r1,c1)
("CZ", (r0, c0), (r1, c1))   # CZ (symmetric)
("Z",  (r,  c))              # single-qubit Z — FREE in the depth metric
```

Qubits are grid sites `(r, c)`, `0 ≤ r, c < L`. No ancillas. Every 2-qubit gate
must be nearest-neighbour (`|r0−r1| + |c0−c1| == 1`).

## Correctness — condition (★), the parity-encoding condition

Built from CX/CZ/Z, Γ is automatically a diagonal Clifford with a degree-≤2
GF(2) phase polynomial `f`: `Γ|s⟩ = (−1)^{f(s)}|s⟩`,
`f(s) = ⊕_i a_i s_i ⊕ ⊕_{i<j} b_ij s_i s_j`.

Γ is a **valid** parity correction iff, for every **vertical** neighbour pair
`(r,c)↔(r+1,c)` with snake indices `j<k`, and every basis state with `s_j ≠ s_k`:

```
f(s) ⊕ f(s with both flipped) == ⊕ of s_l over snake-sites l strictly between j and k     (★)
```

This is geometry-only and is exactly the condition that makes `Γ·FSWAP_bare·Γ =
FSWAP_full` for each vertical pair. Horizontal neighbours are snake-adjacent and
need no correction.

Because `f` is degree ≤ 2, (★) reduces to **exact algebraic constraints** on the
coefficients — checked deterministically by the evaluator (and cross-validated
against brute force over all `2^N` states for small `L`). For every vertical
pair with row-major qubit indices `qa, qb` and between-set `B`:

- **(C1)** `a_qa == a_qb`
- **(C2)** for every `l ∉ {qa, qb}`: `b_{qa,l} ⊕ b_{qb,l} == 1[l ∈ B]`

**Any** `f` satisfying (C1)/(C2) for every vertical pair is a correct Γ — the
valid `f` form an **affine space**: one particular solution (e.g. the seed's
phase polynomial) plus the homogeneous null-space of the (C1)/(C2) system. That
freedom is large (e.g. a constant added to the linear part `a` uniformly across a
whole column is free, and (C2) has a nontrivial null-space), so a *different*
valid `f` may be far cheaper to realise — you are *not* required to reproduce the
seed's phase polynomial. (The cross-term `b_{qa,qb}` drops out of pair
`(qa,qb)`'s own (C2), but it couples through the adjacent pairs that share an
endpoint, so it is **not** independently free.) This freedom is exactly what the
4L / 2L constructions exploit.

## Three hard correctness gates (any failure → score 0)

1. **Format / adjacency** — only CX/CZ/Z; every qubit a grid site; every 2q gate
   nearest-neighbour; no ancillas; gate count ≤ `200·N`.
2. **Diagonal** — the net CNOT linear map is the identity (so Γ is diagonal).
3. **Parity-encoding (★)** — (C1)/(C2) hold for every vertical pair, checked
   exactly via the degree-2 coefficients (no sampling).

All three are enforced in [evaluate.py](evaluate.py). [initial.py](initial.py)
exposes the same checker as `check_valid_gamma(ops, L)` so a candidate can
self-verify before returning.

## Depth metric

2-qubit-gate depth via **ASAP layering** of the emitted gate list (respecting
emitted order and per-qubit exclusion); single-qubit **Z gates are free** (do not
consume a layer). This is the optimal depth for the emitted sequence, so a
candidate lowers depth only by emitting a shorter critical path — it cannot be
gamed by moment bookkeeping or by "free"-decomposing an entangler (CZ/CX always
cost a layer; only single-qubit Z is free).

## Score

```
combined_score = max(0, sum_L (baseline_depth(L) − your_depth(L)) / sum_L L)
```

The **L-weighted prefactor metric**: total 2q-depth *saved per unit L*, weighted
toward larger `L` (where the constant prefactor's power shows). Baseline depths
come from a frozen copy of the 8L+O(1) pipelined construction cached in
`_baseline_cache.json` (gitignored). The seed in `initial.py` is that same
construction → **score = 0 by construction**. Reaching ~4L scores ≈ **+4**;
~2L scores ≈ **+6**.

Scoring **absolute depth-per-L at every size** (not the depth-vs-L *slope*)
cannot be gamed by inflating small-L depth — padding any size only raises its
depth and lowers the score, exactly as in `cnot_grid_synth` (whose earlier slope
objective was reward-hackable). The OLS slope and R² are kept in `public`
metrics as **diagnostics only**.

(Leak-proofing: there is no held-out target — the objective is open-ended depth
reduction, and the (★) spec is public math, so nothing is hidden from the inner
loop. The evaluator's checks are authoritative; per-trial baseline depths sit
under `private`.)

## Benchmark

- Grid sizes `L ∈ {3, …, 10}` (`N = L² ∈ {9, …, 100}`).
- Deterministic: `build_gamma(L)` is called once per `L` — same circuit per
  candidate. The full oracle (coefficient extraction + (★) + depth) runs in well
  under a second across all sizes.
- Per-`L` build timeout 120 s (env `GAMMA_PER_TRIAL_TIMEOUT_S`) under a 20-min
  wall-clock budget (env `GAMMA_EVAL_WALLCLOCK_BUDGET_S`); both are backstops
  against runaway candidates. Gate count capped at `200·N`
  (env `GAMMA_MAX_GATES_PER_N`).

## How to run

### Smoke test

```bash
conda activate shinka      # or prefix python calls with: conda run -n shinka
cd "$(git rev-parse --show-toplevel)"
python tasks/gamma_depth_synth/evaluate.py \
    --program_path tasks/gamma_depth_synth/initial.py \
    --results_dir /tmp/gamma_smoke
cat /tmp/gamma_smoke/correct.json /tmp/gamma_smoke/metrics.json | head -40
```

Expected: `correct=true`, `combined_score=0.0`, `implied_prefactor_slope ≈ 9`
(the prefactor is 8; the +O(1) constant lifts the fitted slope slightly at this
size range), `r_squared ≈ 0.99`.

### Full evolution (as the orchestrator)

Author a run config (copy `configs/orchestrator_run.default.json`), point
`task.eval_program_path` / `task.init_program_path` at this task's
`evaluate.py` / `initial.py`, set the Azure `evo.llm_models` + a `budget_usd` +
a precise `task_sys_msg`, then drive windows — see
[../../.claude/skills/shinka-orchestrator/SKILL.md](../../.claude/skills/shinka-orchestrator/SKILL.md):

```bash
cd "$(git rev-parse --show-toplevel)"
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

## Files

| File | Role |
|---|---|
| [initial.py](initial.py) | Grid/snake geometry + `check_valid_gamma` self-test + EVOLVE-BLOCK seeded with the pipelined 8L Γ. |
| [evaluate.py](evaluate.py) | Oracle (format/adjacency, diagonality, (★) via (C1)/(C2)), ASAP depth, frozen baseline, prefactor scorer. |
| `_baseline_cache.json` | Auto-generated on first run; gitignored. |

## Project context

See the project [CLAUDE.md](../../CLAUDE.md) for environment setup, Azure
credentials, and the orchestrator-driven run loop.

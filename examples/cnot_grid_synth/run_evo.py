"""Launcher for the cnot_grid_synth task.

Mirrors the structure of examples/julia_prime_counting/run_evo.py but wires in
the Azure model pool defined in configs/azure_default.yaml at the repo root
(and documented in CLAUDE.md).

Run from this directory:

    cd examples/cnot_grid_synth
    /opt/anaconda3/envs/shinka/bin/python run_evo.py
"""

# Load the repo-root .env BEFORE any shinka imports — shinka.env's default
# search only checks CWD and the shinka package dir, missing the repo root
# when this file is launched from the task dir.
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=True)

from shinka.core import ShinkaEvolveRunner, EvolutionConfig
from shinka.database import DatabaseConfig
from shinka.launch import LocalJobConfig


# --- Job submission backend -------------------------------------------------

job_config = LocalJobConfig(
    eval_program_path="evaluate.py",
    python_executable="/opt/anaconda3/envs/shinka/bin/python",
    time="00:32:00",   # 30 min internal budget + 2 min buffer over evaluate.py's
                       # EVAL_WALLCLOCK_BUDGET_S so the eval emits a clean score=0
                       # before LocalJobConfig SIGKILLs it.
)

# --- Database (parent / archive bookkeeping) --------------------------------

db_config = DatabaseConfig(
    db_path="evolution_db.sqlite",
    num_islands=3,                  # bumped 2→3 to keep more parallel workers busy
    archive_size=40,
    elite_selection_ratio=0.3,
    num_archive_inspirations=2,
    num_top_k_inspirations=1,
    migration_rate=0.05,             # bumped 0→0.05 — light cross-island gene flow per migration_interval (10 gens)
)

# --- LLM-facing system prompt -----------------------------------------------

task_sys_msg = """\
You are optimizing a Python function `synthesize_cnot_grid(matrix, L)` that
synthesizes a Clifford circuit on n=L^2 qubits implementing a binary
invertible matrix M ∈ GL(n, F_2) on a 2D L×L square-grid topology.

Convention:
- Qubits are indexed 0..n-1 in row-major order (qubit i = row*L + col).
- The synthesized circuit must act as |x⟩ → |Mx⟩ on every computational
  basis state. Equivalently, applying the gates left-to-right to the
  identity over F_2 (with CX(c, t) doing row_t ^= row_c) must reproduce M.

Allowed gates:
- Single-qubit Cliffords (h, s, sdg, x, y, z, id, sx, sxdg) — UNRESTRICTED
  and FREE in depth (filtered out of the metric).
- 2-qubit Cliffords (cx, cz, swap, iswap, dcx, ecr, ...) — allowed ONLY
  between grid-adjacent qubit pairs (4-neighbours, both directions).
  The harness charges them their honest CX-decomposition depth via
  transpile to {cx, u3} at optimization_level=0:
    SWAP costs depth 3 (decomposes to three CX),
    CZ costs depth 1 (decomposes to H·CX·H),
    iSWAP costs ~2.
- Non-Clifford gates (T, RX(θ), RZZ(θ), …) and 3+-qubit gates (Toffoli,
  Fredkin, …) are FORBIDDEN — auto-fail with combined_score = 0.

Hard constraints (any failure → combined_score = 0, candidate discarded):
- Any 2-qubit gate on a non-grid-adjacent pair.
- Any 3+-qubit gate.
- Any non-Clifford gate.
- Any candidate whose net Clifford action does not match M (verified via
  qiskit.quantum_info.Clifford(candidate) == Clifford(reference) tableau
  equality — no state-vector simulation).
- Synthesis must terminate within 5 s per (L, M) trial.

Score:
- combined_score = max(0, baseline_slope − candidate_slope), where the
  slopes are OLS estimates of mean CX-depth vs n=L² across 30 random
  matrices for each L ∈ {2..10}. The baseline is a frozen snake-order
  LNN reference synthesis (depth/n ≈ 4.5).
- The current seed is a hand-coded, transparent Kutin-Moulton-Smithline
  (KMS) LNN synthesis lifted onto the grid via a snake permutation. It
  matches the baseline by construction → score ≈ 0. Any genuine
  improvement is positive.

Strategy hints (in increasing order of expected difficulty):
- The seed performs LNN synthesis on the snake path, which uses only
  L*(L-1) of the grid's 2*L*(L-1) edges. The other L*(L-1) - L vertical
  "rung" edges are skipped by the snake but are grid-adjacent and free
  to use directly. Every row operation that crosses a snake "fold"
  currently routes the long way around — using a rung CX would be a
  constant saving per such operation.
- The metric is depth, not gate count. Many adjacent CX gates on disjoint
  qubit pairs commute and can occupy the same depth layer. KMS already
  parallelizes within even/odd parity classes; a custom scheduler aware
  of all commuting pairs (not just within a parity class) can do better.
- Single-qubit Cliffords are FREE in this metric. H-conjugation flips CX
  control/target (so "direction" is free); H·CX·H = CZ lets you switch
  between row and column operations; phase gates change stabilizer roles.
- Special-structure matrices (sparse / banded / low permutation distance)
  admit short-circuits past the full KMS pipeline.
- Block-style elimination (processing windows of columns together when
  the matrix has redundant column subsets within the window) can compress
  total work substantially.
- Replace KMS entirely. The grid has 2L(L-1) edges vs the line's L²-1 —
  substantially higher edge bandwidth — so a fundamentally 2D-native
  algorithm is plausible. Recursive divide-and-conquer over qubit
  subsets, lookup over pre-computed column-pattern dictionaries, or
  alternative decompositions are fair game.
- The benchmark is candidate-independent (same seeded matrices every
  evaluation), so reproducible search is reliable.

Implementation notes:
- The function returns a qiskit.QuantumCircuit on n=L² qubits.
- Helpers `grid_neighbours(L)`, `snake_permutation(L)`, and
  `apply_cx_to_identity(cx_list, n)` are defined OUTSIDE the EVOLVE-BLOCK
  in initial.py and importable from inside it; do not redefine them.
- The seed includes detailed inline comments on what each algorithmic
  choice costs and where to find improvement room — read them, then
  replace whichever pieces you have a better idea for.
"""

# --- Evolution config (Azure pool from configs/azure_default.yaml) ---------

evo_config = EvolutionConfig(
    task_sys_msg=task_sys_msg,
    patch_types=["diff", "full", "cross"],
    patch_type_probs=[0.6, 0.3, 0.1],
    num_generations=10000,            # effectively unbounded — budget cap is the real stop
    job_type="local",
    language="python",
    init_program_path="initial.py",
    results_dir="results/results_cnot_grid_synth",

    # Proposal pool — bandit chooses per generation.
    llm_models=[
        "azure-gpt-5.4-pro",     # strong reasoning anchor ($30/$180 per 1M)
        "azure-gpt-5.5",         # mid-cost reasoning ($5/$30)
        "azure-gpt-5.3-codex",   # coding-specialized ($1.75/$14)
        "azure-gpt-5.4-mini",    # cheap fallback ($0.75/$4.50)
    ],
    llm_kwargs=dict(
        temperatures=[0, 0.5, 1.0],          # ignored by reasoning models (forced to 1.0)
        reasoning_efforts=["medium", "high"],  # 'low' rejected by gpt-5.4-pro
        # max_tokens caps TOTAL output INCLUDING reasoning tokens (per OpenAI/Azure
        # /v1/responses semantics). gpt-5.4-pro at reasoning_effort=high routinely
        # uses 30k+ tokens on reasoning alone for hard problems, so 32k cap was
        # exhausting the budget mid-reasoning and producing reasoning-only
        # responses with no message. 100k gives comfortable headroom.
        max_tokens=100000,
    ),
    llm_dynamic_selection="ucb1",
    llm_dynamic_selection_kwargs=dict(
        cost_aware_coef=0.5,
        exploration_coef=1.0,
        epsilon=0.2,
    ),

    # Cheap model for meta and novelty roles.
    meta_llm_models=["azure-gpt-5.4-mini"],
    meta_llm_kwargs=dict(temperatures=[0], reasoning_efforts=["low"], max_tokens=16384),
    novelty_llm_models=["azure-gpt-5.4-mini"],
    novelty_llm_kwargs=dict(temperatures=[0], reasoning_efforts=["low"]),

    embedding_model="azure-text-embedding-3-small",
    code_embed_sim_threshold=0.99,
    max_novelty_attempts=2,

    # Hard budget cap. Runner stops when this is hit (well before num_generations=10000).
    max_api_costs=100.0,
)

# --- Concurrency knobs ------------------------------------------------------

MAX_EVAL_JOBS = 6      # parallel evaluation subprocesses
MAX_PROPOSAL_JOBS = 16  # parallel LLM proposal calls (network-bound — laptop CPU stays idle while Azure thinks)
MAX_DB_WORKERS = 2


def main() -> None:
    runner = ShinkaEvolveRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        max_evaluation_jobs=MAX_EVAL_JOBS,
        max_proposal_jobs=MAX_PROPOSAL_JOBS,
        max_db_workers=MAX_DB_WORKERS,
        verbose=True,
    )
    runner.run()


if __name__ == "__main__":
    main()

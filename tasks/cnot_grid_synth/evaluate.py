"""Evaluator for the cnot_grid_synth task.

Drives a deterministic benchmark: for each grid size L ∈ {2..10}, sample 30
random invertible binary matrices via Qiskit's
`random_invertible_binary_matrix(n, seed=…)`, call the candidate's
`synthesize_cnot_grid(M, L)`, then enforce two correctness gates and measure
CX-only depth.

Score: max(0, baseline_slope − candidate_slope), where baseline_slope is
the OLS slope of mean CX-depth vs n=L² for a frozen snake-KMS reference
synthesis, computed once and cached in `_baseline_cache.json`. The seed
in initial.py is hand-coded KMS that matches the baseline by
construction → score ≈ 0; any genuine improvement is positive.

Hard correctness gates (either failure → combined_score = 0, correct = False):
  Gate 1 (adjacency): every 2-qubit gate's qubit pair must be in
                      grid_neighbours(L); 3+-qubit gates forbidden.
  Gate 2 (Clifford action): Clifford(candidate_qc) == Clifford(reference_qc)
                            via tableau equality (no state-vector simulation).
                            Catches non-Clifford gates, wrong matrix, and
                            uncancelled phases / Hadamard residue.

Depth measurement (only after both gates pass): transpile to {cx, u3} at
optimization_level=0 to expand SWAP/CZ/iSWAP into CX, then count 2-qubit-gate
depth via filter_function.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import signal
import time
from typing import Any, Optional

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.quantum_info import Clifford
from qiskit.synthesis import synth_cnot_depth_line_kms
from qiskit.synthesis.linear.linear_matrix_utils import random_invertible_binary_matrix

from shinka.core import run_shinka_eval


# --- Benchmark constants (frozen; do not depend on candidate) ---------------

L_RANGE: list[int] = list(range(2, 11))           # n = 4..100
N_PER_L: int = 30
# Env-overridable so the orchestrator can retune the eval-time budget between runs
# (M8 invariant: harness eval_time > EVAL_WALLCLOCK_BUDGET_S > PER_TRIAL_TIMEOUT_S).
PER_TRIAL_TIMEOUT_S: float = float(os.environ.get("CNOT_PER_TRIAL_TIMEOUT_S", "300"))
EVAL_WALLCLOCK_BUDGET_S: float = float(os.environ.get("CNOT_EVAL_WALLCLOCK_BUDGET_S", str(30 * 60)))  # 30 min default
MAX_CONSECUTIVE_TIMEOUTS: int = 3                    # 3 in a row → early abort
# M8 INVARIANT: the run config's task.eval_time (the harness LocalJobConfig hard-kill)
# MUST exceed EVAL_WALLCLOCK_BUDGET_S, which must exceed PER_TRIAL_TIMEOUT_S — else the
# harness SIGKILLs before the graceful early-abort can return a clean score, and one slow
# trial can consume the whole window. For the 30-min default budget set eval_time >=
# "00:35:00"; the two timeouts above are env-overridable (CNOT_*) to fit a tighter budget.
# (orchestrator/tests assert per_trial < wallclock; the eval_time relation is task-config.)
MATRIX_SEED_BASE: int = 0xC107A7
BASELINE_CACHE_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_baseline_cache.json"
)


# --- Reference synth (immutable; mirror of initial.py's seed) ---------------

def _baseline_snake_kms_synth(matrix: np.ndarray, L: int) -> QuantumCircuit:
    """Snake-order KMS synthesis. Frozen — the LLM cannot mutate this copy.
    Used both for the baseline-slope cache and for building the reference
    Clifford that Gate 2 compares against."""
    n = L * L
    perm = _snake_permutation(L)
    M_lnn = matrix.astype(np.uint8)[np.ix_(perm, perm)].astype(bool)
    qc_lnn = synth_cnot_depth_line_kms(M_lnn)
    qc = QuantumCircuit(n)
    for instr in qc_lnn.data:
        if instr.operation.name != "cx":
            continue
        c_lnn = qc_lnn.find_bit(instr.qubits[0]).index
        t_lnn = qc_lnn.find_bit(instr.qubits[1]).index
        qc.cx(perm[c_lnn], perm[t_lnn])
    return qc


# --- Helpers (defense-in-depth: never imported from initial.py) -------------

def _snake_permutation(L: int) -> list[int]:
    perm: list[int] = []
    for r in range(L):
        cols = range(L) if r % 2 == 0 else range(L - 1, -1, -1)
        perm.extend(r * L + c for c in cols)
    return perm


def _grid_neighbours_set(L: int) -> set[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    for r in range(L):
        for c in range(L):
            i = r * L + c
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < L and 0 <= cc < L:
                    edges.add((i, rr * L + cc))
    return edges


def _seed_for(L: int, k: int) -> int:
    return (MATRIX_SEED_BASE * 9973 + L * 1009 + k) & 0xFFFFFFFF


def _verify_grid_adjacency(qc: QuantumCircuit, adj: set[tuple[int, int]]) -> tuple[bool, Optional[str]]:
    for instr in qc.data:
        nq = instr.operation.num_qubits
        if nq > 2:
            return False, f"3+-qubit gate '{instr.operation.name}' forbidden"
        if nq == 2:
            q0 = qc.find_bit(instr.qubits[0]).index
            q1 = qc.find_bit(instr.qubits[1]).index
            if (q0, q1) not in adj:
                return False, f"non-adjacent 2q gate {instr.operation.name}({q0},{q1})"
    return True, None


def _verify_clifford_action(candidate_qc: QuantumCircuit, M_bool: np.ndarray, L: int) -> tuple[bool, Optional[str]]:
    n = L * L
    if not isinstance(candidate_qc, QuantumCircuit):
        return False, f"return type {type(candidate_qc).__name__}, expected QuantumCircuit"
    if candidate_qc.num_qubits != n:
        return False, f"circuit has {candidate_qc.num_qubits} qubits, expected {n}"
    try:
        cand = Clifford(candidate_qc)
    except Exception as e:
        return False, f"non-Clifford gate: {e!r}"
    ref = Clifford(_baseline_snake_kms_synth(M_bool, L))
    if cand != ref:
        return False, "Clifford action does not match target matrix M"
    return True, None


def _cx_depth(qc: QuantumCircuit) -> int:
    """Per the depth-evaluator contract: transpile to {cx, u3} at opt_level=0
    so SWAPs/CZs/iSWAPs decompose into their honest CX cost, then count
    2-qubit-gate depth via filter_function. Single-qubit gates collapse into
    u3 (filtered out)."""
    qc_basis = transpile(qc, basis_gates=["cx", "u3"], optimization_level=0)
    return qc_basis.depth(filter_function=lambda instr: instr.operation.num_qubits == 2)


# --- Per-trial timeout via SIGALRM (Unix; macOS works) ----------------------

class _TrialTimeout(Exception):
    pass


def _alarm_handler(signum, frame):  # pragma: no cover (signal callback)
    raise _TrialTimeout("trial timed out")


def _call_synth_with_timeout(synth_fn, M, L, timeout_s):
    """SIGALRM wraps ONLY the synthesis call. Verification and depth
    measurement run outside the timer — keeping the timer responsibility
    narrow and decoupled from the rest of the eval flow."""
    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_s))
    try:
        return synth_fn(M, L)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old_handler)


# --- Per-trial flow shared by baseline calibration and candidate eval -------

def _run_trial(synth_fn, L: int, k: int, adj: set[tuple[int, int]], timeout_s: float):
    """Runs one (L, k) trial. Returns (depth_or_None, error_or_None)."""
    M = random_invertible_binary_matrix(L * L, seed=_seed_for(L, k))
    M_bool = np.asarray(M, dtype=bool)
    try:
        qc = _call_synth_with_timeout(synth_fn, M, L, timeout_s)
    except _TrialTimeout:
        return None, f"timeout > {timeout_s}s"
    except Exception as e:
        return None, f"exception: {e!r}"
    if not isinstance(qc, QuantumCircuit):
        return None, f"return type {type(qc).__name__}, expected QuantumCircuit"
    if qc.num_qubits != L * L:
        return None, f"circuit has {qc.num_qubits} qubits, expected {L * L}"
    ok, why = _verify_grid_adjacency(qc, adj)
    if not ok:
        return None, f"adjacency: {why}"
    ok, why = _verify_clifford_action(qc, M_bool, L)
    if not ok:
        return None, f"clifford: {why}"
    return _cx_depth(qc), None


# --- Baseline auto-cache ----------------------------------------------------

def _load_or_compute_baseline() -> dict:
    if os.path.exists(BASELINE_CACHE_PATH):
        with open(BASELINE_CACHE_PATH) as f:
            return json.load(f)

    print(f"[evaluate] Computing snake-KMS baseline (one-time, ~270 syntheses)…")
    t0 = time.perf_counter()

    per_L_means: dict[int, float] = {}
    per_L_stds: dict[int, float] = {}
    depths_by_L: dict[int, list[int]] = {}
    for L in L_RANGE:
        adj = _grid_neighbours_set(L)
        depths: list[int] = []
        for k in range(N_PER_L):
            d, err = _run_trial(_baseline_snake_kms_synth, L, k, adj, PER_TRIAL_TIMEOUT_S)
            assert d is not None, (
                f"snake-KMS baseline failed at L={L} k={k}: {err}. "
                "Bug in seed or qiskit version mismatch — aborting cache write."
            )
            depths.append(int(d))
        per_L_means[L] = float(np.mean(depths))
        per_L_stds[L] = float(np.std(depths))
        depths_by_L[L] = depths
        print(f"[evaluate]   baseline L={L}: mean={per_L_means[L]:.2f} "
              f"std={per_L_stds[L]:.2f} c_hat={per_L_means[L] / (L * L):.3f}")

    ns = np.array([L * L for L in L_RANGE], dtype=float)
    means = np.array([per_L_means[L] for L in L_RANGE])
    A_lin = np.vstack([ns, np.ones_like(ns)]).T
    slope, intercept = np.linalg.lstsq(A_lin, means, rcond=None)[0]

    cache = {
        "qiskit_version": importlib.metadata.version("qiskit"),
        "seed_base": MATRIX_SEED_BASE,
        "L_range": L_RANGE,
        "n_per_L": N_PER_L,
        "slope": float(slope),
        "intercept": float(intercept),
        "per_L_mean": {str(L): per_L_means[L] for L in L_RANGE},
        "per_L_std": {str(L): per_L_stds[L] for L in L_RANGE},
        "depths_by_L": {str(L): depths_by_L[L] for L in L_RANGE},
    }
    with open(BASELINE_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)
    elapsed = time.perf_counter() - t0
    print(f"[evaluate] Baseline cached → {BASELINE_CACHE_PATH} "
          f"(slope={slope:.4f}, intercept={intercept:.2f}, took {elapsed:.1f}s)")
    return cache


# --- Aggregator -------------------------------------------------------------

def _format_failure_feedback(failures: list[tuple[int, int, str]], baseline: dict) -> str:
    head = f"baseline slope = {baseline['slope']:.4f}. "
    head += f"{len(failures)} trial(s) invalid. First reasons: "
    head += "; ".join(f"L={L} k={k}: {why}" for L, k, why in failures[:5])
    return head


def aggregate_fn(results: list) -> dict[str, Any]:
    if not results:
        return {
            "combined_score": 0.0,
            "correct": False,
            "public": {"error": "run_experiment returned no result"},
            "private": {},
            "extra_data": {},
            "text_feedback": "run_experiment returned no result",
        }
    candidate = results[0]
    if not callable(candidate):
        return {
            "combined_score": 0.0,
            "correct": False,
            "public": {"error": f"run_experiment must return callable; got {type(candidate).__name__}"},
            "private": {},
            "extra_data": {},
            "text_feedback": f"run_experiment returned {type(candidate).__name__}, expected callable",
        }

    baseline = _load_or_compute_baseline()
    failures: list[tuple[int, int, str]] = []
    per_L: dict[int, dict[str, Any]] = {}
    eval_started = time.monotonic()
    consecutive_timeouts = 0
    early_abort_reason: str | None = None

    for L in L_RANGE:
        if early_abort_reason is not None:
            break
        adj = _grid_neighbours_set(L)
        depths: list[int] = []
        for k in range(N_PER_L):
            # Total-wallclock guard: bail before LocalJobConfig SIGKILLs us so
            # we can return a clean score=0 with diagnostic feedback.
            elapsed = time.monotonic() - eval_started
            if elapsed > EVAL_WALLCLOCK_BUDGET_S:
                early_abort_reason = (
                    f"eval wallclock {elapsed:.0f}s exceeded budget {EVAL_WALLCLOCK_BUDGET_S:.0f}s "
                    f"(stopped at L={L}, k={k}, {len(failures)} failures so far)"
                )
                break

            d, err = _run_trial(candidate, L, k, adj, PER_TRIAL_TIMEOUT_S)
            if d is None:
                failures.append((L, k, err))
                # Count only timeouts toward the consecutive-fail abort —
                # other failures (adjacency / clifford / exception) are fast,
                # not worth aborting early since they don't waste wallclock.
                if err and err.startswith("timeout"):
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                        early_abort_reason = (
                            f"{consecutive_timeouts} consecutive trials exceeded "
                            f"{PER_TRIAL_TIMEOUT_S:.0f}s (stopped at L={L}, k={k}, "
                            f"wallclock {elapsed:.0f}s, {len(failures)} failures)"
                        )
                        break
                else:
                    consecutive_timeouts = 0
                continue
            consecutive_timeouts = 0
            depths.append(int(d))
        per_L[L] = {
            "depths": depths,
            "mean": float(np.mean(depths)) if depths else float("nan"),
            "std": float(np.std(depths)) if depths else float("nan"),
            "c_hat": float(np.mean(depths) / (L * L)) if depths else float("nan"),
            "n_valid": len(depths),
        }

    if failures or early_abort_reason is not None:
        public = {
            "per_L": {L: {k: v for k, v in per_L[L].items() if k != "depths"} for L in per_L},
            "n_failures": len(failures),
            "first_failures": [{"L": L, "k": k, "reason": why} for L, k, why in failures[:5]],
            "baseline_slope": baseline["slope"],
        }
        feedback = _format_failure_feedback(failures, baseline)
        if early_abort_reason is not None:
            public["early_abort"] = early_abort_reason
            feedback = f"EARLY ABORT: {early_abort_reason}. " + feedback
        return {
            "combined_score": 0.0,
            "correct": False,
            "public": public,
            "private": {"all_failures": [{"L": L, "k": k, "reason": why} for L, k, why in failures]},
            "extra_data": {},
            "text_feedback": feedback,
        }

    ns = np.array([L * L for L in L_RANGE], dtype=float)
    means = np.array([per_L[L]["mean"] for L in L_RANGE])
    A_lin = np.vstack([ns, np.ones_like(ns)]).T
    slope, intercept = np.linalg.lstsq(A_lin, means, rcond=None)[0]
    resid = means - (slope * ns + intercept)
    sse = float(np.sum(resid ** 2))
    sst = float(np.sum((means - means.mean()) ** 2)) if len(means) > 1 else 1.0
    r_squared = 1.0 - sse / sst if sst > 0 else 1.0
    # Floored at 0: the seed already matches baseline (slope ≈ baseline_slope),
    # so worse-than-baseline candidates aren't a useful direction; the search
    # only cares about beating the baseline.
    score = max(0.0, float(baseline["slope"]) - float(slope))

    return {
        "combined_score": float(score),
        "correct": True,
        "public": {
            "slope": float(slope),
            "intercept": float(intercept),
            "r_squared": float(r_squared),
            "baseline_slope": float(baseline["slope"]),
            "baseline_intercept": float(baseline["intercept"]),
            "per_L": {L: {k: v for k, v in per_L[L].items() if k != "depths"} for L in L_RANGE},
        },
        "private": {"depths_per_L": {L: per_L[L]["depths"] for L in L_RANGE}},
        "extra_data": {},
        "text_feedback": (
            f"slope={slope:.4f} (baseline {baseline['slope']:.4f}; "
            f"Δ={baseline['slope'] - slope:+.4f}); R²={r_squared:.3f}; "
            + "; ".join(f"L={L}:c={per_L[L]['c_hat']:.2f}" for L in L_RANGE)
        ),
    }


# --- Main entry -------------------------------------------------------------

def main(program_path: str, results_dir: str) -> None:
    print(f"Evaluating program: {program_path}")
    print(f"Saving results to: {results_dir}")
    os.makedirs(results_dir, exist_ok=True)
    metrics, correct, err = run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="run_experiment",
        num_runs=1,
        get_experiment_kwargs=lambda i: {},
        aggregate_metrics_fn=aggregate_fn,
        validate_fn=None,
    )
    if not correct:
        print(f"Evaluation FAILED: {err}")
    else:
        print("Evaluation completed successfully.")
    print(f"combined_score = {metrics.get('combined_score')!r}")
    if "public" in metrics and isinstance(metrics["public"], dict):
        for k, v in metrics["public"].items():
            if k == "per_L":
                continue
            print(f"  public.{k} = {v!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="cnot_grid_synth evaluator")
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)

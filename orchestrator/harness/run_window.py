"""run_window.py — the inner-loop driver.

This is the ONLY file that knows the inner loop's control flow. Given a current
archive state + the current strategy files, it runs W iterations under the
current strategy and emits a window-end diagnostics JSON. The orchestrator
invokes it as a single subprocess per window; it never sequences the scripts
itself.

It composes the scripts in ``../scripts`` in the canonical Shinka order
(established in AUDIT.md §3):

    select_llm (deferred to Phase 5) -> sample_parent -> construct_mutation_prompt
    -> mutate -> evaluate -> archive_record   [repeat W times]   -> diagnostics

The driver is sequential (one candidate at a time), the clean reference order;
it writes to the same ``programs.sqlite`` schema shinka uses, so the real
ShinkaEvolveRunner could resume from a harness-produced archive.

MUTABILITY: this is harness plumbing — not a strategy file. Do not rewrite it as
part of a strategy rewrite (rewrite the ``scripts/*.py`` policies instead).

USAGE:
  python harness/run_window.py --config run.json [--windows 1] [--iters 15]
  (or import and call ``main(config) -> diagnostics_dict``)

The config schema is documented in orchestrator/tests/fixtures and SKILL.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HARNESS_DIR = Path(__file__).resolve().parent
_ORCH_DIR = _HARNESS_DIR.parent
_REPO_ROOT = _ORCH_DIR.parent
# The "current strategy" lives in this dir. Overridable so a fresh run_window
# subprocess loads whatever the orchestrator last deployed (and so tests can
# point at an isolated copy of scripts/). Defaults to the real scripts/.
_SCRIPTS_DIR = Path(os.environ.get("SHINKA_ORCH_SCRIPTS_DIR", _ORCH_DIR / "scripts"))
for _p in (str(_REPO_ROOT), str(_SCRIPTS_DIR), str(_HARNESS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _common  # noqa: E402
import sample_parent  # noqa: E402
import construct_mutation_prompt  # noqa: E402
import mutate  # noqa: E402
import evaluate as evaluate_script  # noqa: E402
import archive_record  # noqa: E402
import archive_query  # noqa: E402
import diagnostics as diagnostics_script  # noqa: E402
import select_llm as select_llm_script  # noqa: E402
import novelty_check as novelty_check_script  # noqa: E402
import compute_reward as compute_reward_script  # noqa: E402
import record_policy as record_policy_script  # noqa: E402
import cadence_policy as cadence_policy_script  # noqa: E402
import journal  # noqa: E402  (harness sibling)
import strategy_store  # noqa: E402  (harness sibling — for the strategy fingerprint)

FOLDER_PREFIX = "gen"


def _read_code(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def _embed(cfg: Dict[str, Any], code: str) -> Tuple[Optional[List[float]], float]:
    """Embed candidate code for the novelty check. Returns (vector, cost_usd).
    Mock = deterministic hash vector (offline, cost 0); live = shinka's
    EmbeddingClient (whose cost MUST be captured, not discarded)."""
    mock = cfg.get("mock", {}) or {}
    if mock.get("enabled"):
        import hashlib

        digest = hashlib.sha256(code.encode("utf-8")).digest()
        return [b / 255.0 for b in digest[:16]], 0.0
    try:
        from shinka.embed.embedding import EmbeddingClient

        client = EmbeddingClient(
            model_name=cfg["evo"].get("embedding_model", "text-embedding-3-small")
        )
        out = client.get_embedding(code)
        if isinstance(out, tuple):
            return out[0], float(out[1] or 0.0)
        return out, 0.0
    except Exception:
        return None, 0.0


def _max_generation(db_path: str, db_config: Dict[str, Any], embedding_model: str) -> int:
    res = archive_query.main(
        {
            "db_path": db_path,
            "db_config": db_config,
            "embedding_model": embedding_model,
            "query_type": "all",
        }
    )["result"]
    gens = [int(p.get("generation", 0) or 0) for p in res]
    return max(gens) if gens else -1


def _best_score(db_path: str, db_config: Dict[str, Any], embedding_model: str) -> float:
    summ = archive_query.main(
        {
            "db_path": db_path,
            "db_config": db_config,
            "embedding_model": embedding_model,
            "query_type": "summary",
        }
    )["result"]
    return float(summ.get("best_score") or 0.0)


def _mock_score(cfg: Dict[str, Any], iter_index: int, generation: int) -> Optional[float]:
    """Resolve a mocked score: by generation (preferred, unambiguous) else by
    within-window iter index. Returns None to fall through to a real eval."""
    mock = cfg.get("mock", {}) or {}
    if not mock.get("enabled"):
        return None
    sbg = mock.get("scores_by_generation")
    if sbg is not None:
        keyed = {str(k): v for k, v in sbg.items()}
        if str(generation) in keyed:
            return float(keyed[str(generation)])
    eval_scores = mock.get("eval_scores")
    if eval_scores is not None:
        return float(eval_scores[iter_index % len(eval_scores)])
    return None


def _evaluate_candidate(
    cfg: Dict[str, Any], program_path: str, results_dir: str,
    iter_index: int, generation: int,
) -> Dict[str, Any]:
    """Real eval via evaluate.py, OR a mocked score for offline tests."""
    score = _mock_score(cfg, iter_index, generation)
    if score is not None:
        mock = cfg.get("mock", {}) or {}
        incorrect = {int(g) for g in mock.get("incorrect_generations", [])}
        correct = generation not in incorrect
        return {
            "combined_score": score,
            "correct": correct,
            "public_metrics": {},
            "private_metrics": {},
            "error": None if correct else "mock: marked incorrect",
            "error_traceback": None if correct else "MockError: marked incorrect",
            "stdout_log": "",
            "stderr_log": "",
            "runtime_sec": 0.0,
        }
    task = cfg["task"]
    os.makedirs(results_dir, exist_ok=True)
    return evaluate_script.main(
        {
            "program_path": program_path,
            "eval_program_path": task["eval_program_path"],
            "results_dir": results_dir,
            "time": task.get("eval_time"),
            "conda_env": task.get("conda_env"),
            "python_executable": task.get("python_executable"),
            "verbose": cfg.get("verbose", False),
        }
    )


def _bootstrap_initial(cfg: Dict[str, Any]) -> float:
    """If the archive is empty, evaluate the seed program and record it as gen 0.

    Returns the embedding cost incurred (0.0 when novelty is off or the archive
    was already bootstrapped) so the caller can fold it into the ledger once the
    journal exists (bootstrap runs before journal.init_run)."""
    db_path = cfg["db_path"]
    db_config = cfg["db_config"]
    evo = cfg["evo"]
    embedding_model = evo.get("embedding_model", "text-embedding-3-small")
    # A missing DB file means an empty archive (the first archive_record creates
    # it). Only query the count when the file already exists, since archive_query
    # opens read-only and read-only refuses to create a missing DB.
    if os.path.exists(db_path):
        count = archive_query.main(
            {
                "db_path": db_path,
                "db_config": db_config,
                "embedding_model": embedding_model,
                "query_type": "count",
            }
        )["result"]
        if count["total"] > 0:
            return 0.0

    task = cfg["task"]
    init_path = task["init_program_path"]
    gen_dir = os.path.join(cfg["results_dir"], f"{FOLDER_PREFIX}_0")
    results_dir = os.path.join(gen_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    ev = _evaluate_candidate(cfg, init_path, results_dir, 0, generation=0)
    seed_code = _read_code(init_path)
    program_fields: Dict[str, Any] = {
        "code": seed_code,
        "language": task.get("language", "python"),
        "generation": 0,
        "parent_id": None,
        "combined_score": ev["combined_score"],
        "correct": ev["correct"],
        "public_metrics": ev["public_metrics"],
        "private_metrics": ev["private_metrics"],
        "error_traceback": ev.get("error_traceback"),
        "metadata": {"bootstrap": True},
    }
    # F7: embed the seed so the FIRST mutations have a baseline to compare against.
    # Without this the novelty gate is a no-op (novelty_n_compared=0) until a few
    # embedded candidates accrue per island, letting near-duplicate early mutants
    # through uncounted.
    embed_cost = 0.0
    if evo.get("enable_novelty"):
        seed_embedding, embed_cost = _embed(cfg, seed_code)
        if seed_embedding is not None:
            program_fields["embedding"] = seed_embedding
    archive_record.main(
        {
            "db_path": db_path,
            "db_config": db_config,
            "embedding_model": embedding_model,
            "program": program_fields,
        }
    )
    return float(embed_cost or 0.0)


def _run_one_candidate(cfg: Dict[str, Any], generation: int, counters: Dict[str, int]) -> None:
    db_path = cfg["db_path"]
    db_config = cfg["db_config"]
    evo = cfg["evo"]
    task = cfg["task"]
    embedding_model = evo.get("embedding_model", "text-embedding-3-small")
    language = task.get("language", "python")

    gen_dir = os.path.join(cfg["results_dir"], f"{FOLDER_PREFIX}_{generation}")
    results_dir = os.path.join(gen_dir, "results")
    os.makedirs(results_dir, exist_ok=True)

    # 1. sample parent + inspirations (MUTABLE policy)
    sp = sample_parent.main(
        {
            "db_path": db_path,
            "db_config": db_config,
            "embedding_model": embedding_model,
            "seed": evo.get("seed"),
        }
    )
    parent = archive_query.main(
        {
            "db_path": db_path, "db_config": db_config, "embedding_model": embedding_model,
            "query_type": "get", "program_id": sp["parent_id"], "include_code": True,
        }
    )["result"]

    def _fetch(ids: List[str]) -> List[Dict[str, Any]]:
        out = []
        for pid in ids:
            out.append(
                archive_query.main(
                    {
                        "db_path": db_path, "db_config": db_config,
                        "embedding_model": embedding_model,
                        "query_type": "get", "program_id": pid, "include_code": True,
                    }
                )["result"]
            )
        return out

    needs_fix = bool(sp.get("needs_fix"))
    if needs_fix:
        # FIX/REPAIR concern: repair an incorrect parent using its ancestors.
        ancestors = archive_query.main(
            {
                "db_path": db_path, "db_config": db_config,
                "embedding_model": embedding_model, "query_type": "ancestry",
                "program_id": sp["parent_id"], "max_ancestors": 10, "include_code": True,
            }
        )["result"]
        archive_insp, top_k_insp = [], []
        counters["fix_count"] = counters.get("fix_count", 0) + 1
    else:
        ancestors = []
        archive_insp = _fetch(sp.get("archive_inspiration_ids", []))
        top_k_insp = _fetch(sp.get("top_k_inspiration_ids", []))

    # 2. construct mutation prompt (MUTABLE policy; fix-mode picks the repair prompt)
    prompt = construct_mutation_prompt.main(
        {
            "parent": parent,
            "archive_inspirations": archive_insp,
            "top_k_inspirations": top_k_insp,
            "ancestor_inspirations": ancestors,
            "needs_fix": needs_fix,
            "meta_recommendations": evo.get("meta_recommendations"),
            "task_sys_msg": task.get("task_sys_msg"),
            "patch_types": evo.get("patch_types"),
            "patch_type_probs": evo.get("patch_type_probs"),
            "language": language,
            "extra_guidance": evo.get("extra_guidance"),
            "seed": evo.get("seed"),
        }
    )

    # 2b. select LLM (MUTABLE policy). Bandit only when a model pool is given;
    # otherwise fall back to a fixed model_name (mock path uses neither).
    mock = cfg.get("mock", {}) or {}
    llm_models = evo.get("llm_models")
    state_path = os.path.join(cfg["results_dir"], "bandit_state.pkl")
    model_name = evo.get("model_name")
    if llm_models:
        sel = select_llm_script.main(
            {
                "mode": "select", "models": llm_models, "state_path": state_path,
                "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                "seed": evo.get("seed"),
            }
        )
        model_name = sel["model_name"]

    # 3. mutate: LLM call + apply (IMMUTABLE body, MUTABLE prompt) — mockable
    mut_payload = {
        "parent_code": parent.get("code", ""),
        "patch_sys": prompt["patch_sys"],
        "patch_msg": prompt["patch_msg"],
        "patch_type": prompt["patch_type"],
        "patch_dir": gen_dir,
        "language": language,
        "model_name": model_name,
        "reasoning_effort": evo.get("reasoning_effort"),
        "max_attempts": evo.get("max_patch_attempts", 3),
        "run_id": cfg.get("run_id"),
        "generation": generation,
        "verbose": cfg.get("verbose", False),
    }
    if mock.get("enabled"):
        mut_payload["mock"] = True
        mut_payload["mock_cost"] = mock.get("mutate_cost", 0.0)  # offline budget tests
        seq = mock.get("mutate_code_sequence")
        if seq is not None:
            mut_payload["mock_code"] = seq[counters["iter_index"] % len(seq)]
        # else identity copy of parent
    _mut_t0 = time.time()
    mut = mutate.main(mut_payload)
    _mut_latency = time.time() - _mut_t0  # wallclock the bandit is otherwise blind to
    # Account the mutation LLM cost immediately — it was incurred even if the
    # candidate is later rejected by novelty.
    _mut_cost = float(mut.get("cost", 0.0) or 0.0)
    counters["cost"] = counters.get("cost", 0.0) + _mut_cost

    # 3b. novelty check (MUTABLE policy) — gated; live runs enable it. On reject,
    # the slot is dropped before evaluation (matches shinka's rejection sampling).
    code_embedding: Optional[List[float]] = None
    nov: Dict[str, Any] = {}
    _slot_embed_cost = 0.0
    if evo.get("enable_novelty"):
        code_embedding, _embed_cost = _embed(cfg, mut["candidate_code"])
        _slot_embed_cost = float(_embed_cost or 0.0)
        counters["cost"] = counters.get("cost", 0.0) + _slot_embed_cost
        nov = novelty_check_script.main(
            {
                "db_path": db_path, "db_config": db_config,
                "embedding_model": embedding_model,
                "candidate_embedding": code_embedding or [],
                "code_embed_sim_threshold": evo.get("code_embed_sim_threshold", 0.99),
                "island_idx": sp.get("island_idx"),
            }
        )
        if not nov.get("accept"):
            counters["novelty_rejects"] += 1
            # F13: this slot's spend produced nothing to evaluate — record it as
            # waste so diagnostics can surface it and the orchestrator can react
            # (e.g. rewrite the prompt or the novelty threshold).
            counters["rejected_cost"] = counters.get("rejected_cost", 0.0) + _mut_cost + _slot_embed_cost
            return  # drop this slot; no eval, no record, no bandit update

    # 4. evaluate (IMMUTABLE plumbing)
    ev = _evaluate_candidate(
        cfg, mut["candidate_path"], results_dir, counters["iter_index"], generation
    )
    counters["eval_total"] += 1
    if not ev["correct"]:
        counters["eval_failures"] += 1
    counters["novelty_accepts"] += 1

    # 4b. compute reward (MUTABLE — scoring concern, generation half)
    reward = compute_reward_script.main(
        {
            "candidate": ev,
            "parent": {"combined_score": parent.get("combined_score", 0.0)},
            "mode": evo.get("reward_mode", "absolute"),
        }
    )

    # 4c. record policy (MUTABLE — memory concern): what to persist in metadata
    rec = record_policy_script.main(
        {
            "eval": ev,
            "parent": {"combined_score": parent.get("combined_score", 0.0)},
            "mutation": {
                "patch_type": prompt["patch_type"], "patch_name": mut.get("name"),
                "num_applied": mut.get("num_applied"), "cost": mut.get("cost", 0.0),
                "model_name": model_name, "transport": mut.get("transport"),
                "attempts": mut.get("attempts"),
            },
            "sample": {
                "parent_id": sp["parent_id"], "needs_fix": needs_fix,
                "archive_inspiration_ids": sp.get("archive_inspiration_ids", []),
                "top_k_inspiration_ids": sp.get("top_k_inspiration_ids", []),
            },
            "novelty": nov or None,
            "reward": reward,
        }
    )

    # 5. archive_record (IMMUTABLE plumbing)
    program_fields: Dict[str, Any] = {
        "code": mut["candidate_code"],
        "language": language,
        "generation": generation,
        "parent_id": sp["parent_id"],
        "archive_inspiration_ids": sp.get("archive_inspiration_ids", []),
        "top_k_inspiration_ids": sp.get("top_k_inspiration_ids", []),
        "code_diff": mut.get("description"),
        "combined_score": ev["combined_score"],
        "correct": ev["correct"],
        "public_metrics": ev["public_metrics"],
        "private_metrics": ev["private_metrics"],
        "error_traceback": ev.get("error_traceback"),
        "metadata": rec.get("metadata", {}),
    }
    if code_embedding is not None:
        program_fields["embedding"] = code_embedding
    archive_record.main(
        {
            "db_path": db_path, "db_config": db_config, "embedding_model": embedding_model,
            "program": program_fields,
        }
    )

    # 6. bandit update (MUTABLE — scoring concern, consumption half) using the
    # reward from compute_reward.py (NOT a hardcoded score).
    if llm_models:
        select_llm_script.main(
            {
                "mode": "update", "models": llm_models, "state_path": state_path,
                "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                "arm": model_name,
                "reward": reward.get("reward"),
                "baseline": reward.get("baseline", 0.0),
                "cost": mut.get("cost", 0.0),
                "latency_sec": _mut_latency,  # feeds the latency-aware selection prior
            }
        )


def main(cfg: Dict[str, Any]) -> Dict[str, Any]:
    # Install-isolation guarantee: fail loudly if `shinka` is not this repo's.
    # Pass the harness's repo root (always correct, even when scripts/ is a copy).
    _common.assert_worktree_shinka(_REPO_ROOT)
    db_path = cfg.setdefault(
        "db_path", os.path.join(cfg["results_dir"], "programs.sqlite")
    )
    db_config = cfg["db_config"]
    evo = cfg["evo"]
    embedding_model = evo.get("embedding_model", "text-embedding-3-small")
    window_size = int(cfg.get("iters") or evo.get("window_size", 15))
    num_windows = int(cfg.get("windows", 1))

    os.makedirs(cfg["results_dir"], exist_ok=True)
    _boot_embed_cost = _bootstrap_initial(cfg)

    journal.init_run(
        cfg["results_dir"],
        {
            "run_id": cfg.get("run_id"),
            "goal": cfg.get("task", {}).get("task_sys_msg"),
            "task": cfg.get("task", {}).get("eval_program_path"),
            "budget_usd": cfg.get("budget_usd"),
            "config_digest": {
                "num_islands": db_config.get("num_islands"),
                "window_size": window_size,
                "llm_models": evo.get("llm_models"),
                "tau": evo.get("tau"),
            },
        },
    )

    # Fold the bootstrap seed's embedding cost (F7) into the ledger now that the
    # journal exists — bootstrap runs before init_run, so it couldn't account it.
    if _boot_embed_cost:
        journal.add_cost(cfg["results_dir"], _boot_embed_cost)

    window_state = cfg.get("window_state", {}) or {}
    window_index = int(window_state.get("window_index", 0))
    prior_low_streak = int(window_state.get("prior_low_streak", 0))

    budget = cfg.get("budget_usd")
    # F4: the self-contained strategy pointer — {target: hash} over all mutable
    # files, computed from the live scripts/. Stamped into every window so the log
    # pins the exact strategy version (all files) that produced each window.
    strategy_fingerprint = strategy_store.current_fingerprint()

    def _one_window(widx: int, prior_streak: int) -> Dict[str, Any]:
        best_start = _best_score(db_path, db_config, embedding_model)
        next_gen = _max_generation(db_path, db_config, embedding_model) + 1
        counters = {
            "iter_index": 0, "eval_total": 0, "eval_failures": 0,
            "novelty_accepts": 0, "novelty_rejects": 0, "fix_count": 0, "cost": 0.0,
            "rejected_cost": 0.0,  # F13: spend on novelty-rejected (un-evaluated) slots
        }
        # HARD budget railguard (immutable safety, NOT a strategy knob): stop
        # starting candidates once cumulative spend (this window so far + all
        # prior windows + orchestrator interventions, from the ledger) reaches
        # the budget. Overshoot is at most one candidate's cost.
        prior_total = journal.total_cost(cfg["results_dir"])
        budget_hit = False
        iters_run = 0  # actual candidates attempted (may be < window_size on budget break)
        for i in range(window_size):
            if budget is not None and (prior_total + counters["cost"]) >= float(budget):
                budget_hit = True
                break
            counters["iter_index"] = i
            _run_one_candidate(cfg, next_gen + i, counters)
            iters_run += 1

        # F9: read the REAL bandit posterior (+ per-arm tallies) for diagnostics,
        # so `llm_bandit_weights` reflects bandit_state.pkl instead of an empty
        # config field. Read-only "weights" mode — never perturbs the bandit.
        bandit_weights: Dict[str, Any] = {}
        bandit_counts: Dict[str, Any] = {}
        if evo.get("llm_models"):
            try:
                peek = select_llm_script.main(
                    {
                        "mode": "weights",
                        "models": evo.get("llm_models"),
                        "state_path": os.path.join(cfg["results_dir"], "bandit_state.pkl"),
                        "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                    }
                )
                bandit_weights = peek.get("weights", {}) or {}
                bandit_counts = peek.get("counts", {}) or {}
            except Exception:
                pass

        diag = diagnostics_script.main(
            {
                "db_path": db_path, "db_config": db_config,
                "embedding_model": embedding_model,
                # F8: report the ACTUAL number of candidates attempted, not the
                # constant window_size (they differ on a budget/early break).
                "window_index": widx, "iters_completed": iters_run,
                "best_score_start": best_start, "window_size": window_size,
                "current_strategy_hash": cfg.get("strategy_hash"),  # deprecated
                "strategy_fingerprint": strategy_fingerprint,
                "tau": evo.get("tau", 0.0),  # deprecated abs_floor alias
                "stagnation_abs_floor": evo.get("stagnation_abs_floor"),
                "stagnation_rel_frac": evo.get("stagnation_rel_frac"),
                "prior_low_streak": prior_streak,
                "consecutive_required": evo.get("consecutive_required", 2),
                "trigger_metric": evo.get("trigger_metric", "delta"),
                "novelty_accepts": counters["novelty_accepts"],
                "novelty_rejects": counters["novelty_rejects"],
                "novelty_rejected_cost": counters["rejected_cost"],
                "eval_failures": counters["eval_failures"],
                "eval_total": counters["eval_total"],
                "fix_count": counters["fix_count"],
                "llm_bandit_weights": bandit_weights,
                "llm_bandit_counts": bandit_counts,
                "exhausted_retry_slots": [],
            }
        )
        diag["window_cost"] = counters["cost"]
        diag["budget_hit"] = budget_hit
        journal.append_window(cfg["results_dir"], diag)  # folds window_cost into the ledger
        diag["total_cost"] = journal.total_cost(cfg["results_dir"])
        diag["budget_remaining"] = journal.budget_remaining(cfg["results_dir"], budget)
        return diag

    # Cadence: "until_decision" runs windows autonomously (NO orchestrator turn)
    # and returns control only when there's a decision. The WHEN-to-return choice
    # is delegated to the MUTABLE cadence_policy.py (the orchestrator can rewrite
    # it if it sees itself triggered too often/rarely). The budget railguard is
    # NOT delegated — it always hard-stops.
    cadence = cfg.get("cadence", {}) or {}
    until_decision = cadence.get("mode") == "until_decision"
    max_per_call = int(cadence.get("max_windows_per_call", 3))

    last_diag: Dict[str, Any] = {}
    if until_decision:
        windows_run = 0
        while True:
            last_diag = _one_window(window_index, prior_low_streak)
            windows_run += 1
            prior_low_streak = last_diag.get("low_streak", 0)
            window_index += 1
            if last_diag.get("budget_hit"):  # HARD railguard, not mutable
                last_diag["return_reason"] = "budget_exhausted"
                break
            decision = cadence_policy_script.main(
                {
                    "stagnation_flag": last_diag.get("stagnation_flag"),
                    "windows_run": windows_run,
                    "max_windows_per_call": max_per_call,
                    "low_streak": last_diag.get("low_streak"),
                    "J_score": last_diag.get("J_score"),
                    "evaluation_failure_rate": last_diag.get("evaluation_failure_rate"),
                }
            )
            if decision.get("return"):
                last_diag["return_reason"] = decision.get("reason", "decision")
                break
        last_diag["windows_run"] = windows_run
    else:
        for _w in range(num_windows):
            last_diag = _one_window(window_index, prior_low_streak)
            prior_low_streak = last_diag.get("low_streak", 0)
            window_index += 1
            if last_diag.get("budget_hit"):
                last_diag["return_reason"] = "budget_exhausted"
                break
        last_diag.setdefault("return_reason", "windows_done")

    last_diag["ok"] = True
    return last_diag


def _hold_no_idle_sleep():
    """Keep the host awake for THIS process's lifetime so a long run is never
    reaped by a macOS idle-sleep.

    Root cause of earlier mid-run kills (2026-05-27): on macOS the system
    idle-slept (battery AND, per `pmset`, even on AC where `sleep`=1 min) during
    long gaps, and the run_window process got reaped across that sleep. We spawn
    `caffeinate -i -m -w <our pid>`, which asserts PreventUserIdleSystemSleep until
    THIS process exits and then auto-exits (it watches our PID) — so it self-cleans
    even if run_window is SIGKILLed, and there is no orphaned assertion.

    Best-effort and self-disabling: no-op off macOS, if `/usr/bin/caffeinate` is
    absent, or if an outer wrapper already set ``SHINKA_CAFFEINATED`` (e.g.
    run_detached.py). Lives in the CLI path only, so imported/test calls of
    ``main()`` never spawn caffeinate. NOTE: caffeinate cannot override a
    closed-lid (clamshell) sleep on a laptop — keep the lid open for unattended runs.
    """
    if sys.platform != "darwin" or os.environ.get("SHINKA_CAFFEINATED") == "1":
        return None
    if not os.path.exists("/usr/bin/caffeinate"):
        return None
    try:
        import subprocess

        proc = subprocess.Popen(
            ["/usr/bin/caffeinate", "-i", "-m", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.environ["SHINKA_CAFFEINATED"] = "1"
        return proc
    except Exception:
        return None


def _cli() -> None:
    # Self-protect against host idle-sleep reaping a long run (see docstring).
    _caffeinate_proc = _hold_no_idle_sleep()  # noqa: F841 (kept alive for the run)
    ap = argparse.ArgumentParser(description="Run W iterations under the current strategy.")
    ap.add_argument("--config", required=True, help="path to run config JSON")
    ap.add_argument("--windows", type=int, default=None)
    ap.add_argument("--iters", type=int, default=None)
    ap.add_argument(
        "--until-decision", action="store_true",
        help="run windows autonomously; return only on stagnation or the window cap",
    )
    ap.add_argument("--max-windows-per-call", type=int, default=None)
    ap.add_argument(
        "--resume", action="store_true",
        help="resume window_state (window_index + prior_low_streak) from the "
             "journal's last window instead of the hand-maintained config (F15) — "
             "removes the cross-invocation bookkeeping footgun",
    )
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    if args.windows is not None:
        cfg["windows"] = args.windows
        # F5: an explicit --windows means "run exactly N bounded windows". Force
        # the bounded branch so it isn't silently ignored when the config file
        # sets cadence.mode=until_decision. (--until-decision below still wins if
        # the user passes it explicitly alongside --windows.)
        cfg.setdefault("cadence", {})["mode"] = "bounded"
    if args.iters is not None:
        cfg["iters"] = args.iters
    if args.until_decision:
        cfg.setdefault("cadence", {})["mode"] = "until_decision"
    if args.max_windows_per_call is not None:
        cfg.setdefault("cadence", {})["max_windows_per_call"] = args.max_windows_per_call
    if args.resume:
        # Read the last window's state from the journal so the orchestrator need
        # not hand-edit window_index / prior_low_streak between calls (F15).
        _last = journal.read_windows(cfg["results_dir"], last_n=1)
        if _last:
            _w = _last[-1]
            ws = cfg.setdefault("window_state", {})
            ws["window_index"] = int(_w.get("window_index", 0) or 0) + 1
            ws["prior_low_streak"] = int(_w.get("low_streak", 0) or 0)
            sys.stderr.write(
                f"[resume] window_index→{ws['window_index']} "
                f"prior_low_streak→{ws['prior_low_streak']}\n"
            )
    result = main(cfg)
    sys.stdout.write(_common.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    _cli()

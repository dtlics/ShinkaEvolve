"""test_improvements.py — tests for the second-round improvements.

Covers: compute_reward (scoring concern, generation half), record_policy (memory
concern), the run journal hierarchy, and concern-bundle deploy/rollback. The
bg+poll mutation path and fix-mode are exercised by run_window in the offline
smoke + a fix-mode case here.

Run:  pytest orchestrator/tests/test_improvements.py
      python orchestrator/tests/test_improvements.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ORCH = _HERE.parent
_REPO_ROOT = _ORCH.parent
for _p in (str(_REPO_ROOT), str(_ORCH / "scripts"), str(_ORCH / "harness")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import compute_reward  # noqa: E402
import record_policy  # noqa: E402


def test_compute_reward():
    # absolute: reward = score, baseline = parent (bandit subtracts internally)
    out = compute_reward.main({"candidate": {"combined_score": 1.5, "correct": True},
                               "parent": {"combined_score": 1.0}, "mode": "absolute"})
    assert out["reward"] == 1.5 and out["baseline"] == 1.0
    # relative: reward = delta, baseline = 0
    out = compute_reward.main({"candidate": {"combined_score": 1.5, "correct": True},
                               "parent": {"combined_score": 1.0}, "mode": "relative"})
    assert abs(out["reward"] - 0.5) < 1e-9 and out["baseline"] == 0.0
    # incorrect → reward None (bandit imputes worst)
    out = compute_reward.main({"candidate": {"combined_score": 0.0, "correct": False},
                               "parent": {"combined_score": 1.0}})
    assert out["reward"] is None and out["baseline"] == 1.0
    return True


def test_record_policy():
    out = record_policy.main({
        "eval": {"combined_score": 1.5, "correct": True},
        "parent": {"combined_score": 1.0},
        "mutation": {"patch_type": "diff", "num_applied": 2, "model_name": "m1", "transport": "background"},
        "sample": {"parent_id": "p", "needs_fix": True},
        "reward": {"reward": 1.5, "baseline": 1.0},
        "novelty": {"max_similarity": 0.42, "n_compared": 3},
    })
    md = out["metadata"]
    assert abs(md["improvement_over_parent"] - 0.5) < 1e-9
    assert md["is_improvement"] is True
    assert md["fix_mode"] is True
    assert md["reward_used"] == 1.5
    assert md["novelty_max_similarity"] == 0.42
    assert md["transport"] == "background"
    return True


def test_journal_roundtrip():
    import journal

    with tempfile.TemporaryDirectory() as td:
        journal.init_run(td, {"run_id": "r", "goal": "g"})
        for i, j in [(0, 0.2), (1, 0.0)]:
            journal.append_window(td, {
                "window_index": i, "J_score": j, "best_score_end": 1.5,
                "stagnation_flag": j == 0.0, "total_programs": 5,
                "island_health": [{"id": 0, "best": 1.5, "diversity": 3}],
            })
        journal.append_intervention(td, {"type": "rewrite", "target": "sample_parent.py", "outcome": "accepted"})
        run = journal.read_run(td)
        assert run["windows_completed"] == 2 and run["best_score"] == 1.5
        traj = journal.j_trajectory(td)
        assert [w["J"] for w in traj] == [0.2, 0.0]
        assert len(journal.read_interventions(td)) == 1
        assert len(journal.read_island(td, 0)) == 2
        assert "# Run Summary" in journal.build_run_summary(td)
    return True


def test_concern_bundle():
    with tempfile.TemporaryDirectory() as td:
        sc, hi = os.path.join(td, "scripts"), os.path.join(td, "hist")
        os.makedirs(sc)
        os.environ["SHINKA_ORCH_SCRIPTS_DIR"] = sc
        os.environ["SHINKA_ORCH_HISTORY_DIR"] = hi
        try:
            import importlib
            import strategy_store as ss
            importlib.reload(ss)  # pick up env (functions read it live anyway)
            for f in ("compute_reward.py", "select_llm.py"):
                open(os.path.join(sc, f), "w").write("def main(p):\n    return {'v': 1}\n")
            before = {f: ss.current_hash(f) for f in ("compute_reward.py", "select_llm.py")}
            c1, c2 = os.path.join(td, "c1.py"), os.path.join(td, "c2.py")
            open(c1, "w").write("def main(p):\n    return {'v': 2}\n")
            open(c2, "w").write("def main(p):\n    return {'v': 2}\n")
            res = ss.deploy_bundle(
                [{"candidate_path": c1, "target": "compute_reward.py"},
                 {"candidate_path": c2, "target": "select_llm.py"}],
                reason="scoring concern", window_index=3, prior_J=0.3,
            )
            assert all(ss.current_hash(f) != before[f] for f in before)
            ss.record_bundle_outcome(res["new_hashes"], J=0.1, accepted=False)
            ss.rollback_bundle(res["prior_hashes"])
            assert all(ss.current_hash(f) == before[f] for f in before)
            statuses = [e.get("status") for e in ss.read_index() if e.get("type") == "bundle"]
            assert "rejected" in statuses and "rolledback" in statuses
        finally:
            del os.environ["SHINKA_ORCH_SCRIPTS_DIR"]
            del os.environ["SHINKA_ORCH_HISTORY_DIR"]
    return True


def test_cadence_policy():
    import cadence_policy

    stag = cadence_policy.main({"stagnation_flag": True, "windows_run": 1, "max_windows_per_call": 3})
    assert stag["return"] is True and stag["reason"] == "stagnation"
    cap = cadence_policy.main({"stagnation_flag": False, "windows_run": 3, "max_windows_per_call": 3})
    assert cap["return"] is True and cap["reason"] == "window_cap"
    cont = cadence_policy.main({"stagnation_flag": False, "windows_run": 1, "max_windows_per_call": 3})
    assert cont["return"] is False
    return True


def test_budget_hardstop():
    """The harness must hard-stop the inner loop at the budget (railguard)."""
    import os
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window
    import journal

    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "run")
        ip = os.path.join(td, "i.py")
        open(ip, "w").write("x=1\n")
        cfg = {
            "results_dir": rd, "run_id": "b", "budget_usd": 2.5,
            "task": {"eval_program_path": "unused.py", "init_program_path": ip,
                     "task_sys_msg": "x", "language": "python"},
            "db_config": {"num_islands": 1, "archive_size": 20},
            "evo": {"window_size": 3, "patch_types": ["diff", "full"],
                    "patch_type_probs": [0.7, 0.3], "embedding_model": "text-embedding-3-small",
                    "tau": 0.05, "seed": 0},
            "mock": {"enabled": True, "mutate_cost": 1.0,
                     "scores_by_generation": {str(i): 1.0 + 0.1 * i for i in range(40)}},
            "cadence": {"mode": "until_decision", "max_windows_per_call": 10},
            "window_state": {"window_index": 0, "prior_low_streak": 0},
        }
        d = run_window.main(cfg)
        assert d["return_reason"] == "budget_exhausted", d["return_reason"]
        # ledger tracked spend; we never ran unbounded
        assert journal.total_cost(rd) >= 2.5
        # intervention cost lands in the same ledger
        journal.append_intervention(rd, {"type": "deep_research", "cost": 5.0})
        assert journal.budget_remaining(rd, 2.5) < 0
    return True


def test_immediate_fix():
    """WS1: an eval failure is repaired in-place by re-prompting the same model,
    up to fix_retry_budget; fix_success counts only a RECOVERED candidate; the
    budget railguard stops a fix attempt we can't afford."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window

    orig_cmp = run_window.construct_mutation_prompt.main
    orig_mut = run_window.mutate.main
    orig_eval = run_window._evaluate_candidate
    try:
        run_window.construct_mutation_prompt.main = lambda p: {
            "patch_sys": "s", "patch_msg": "m", "patch_type": "fix"}
        run_window.mutate.main = lambda p: {
            "candidate_code": "fixed", "candidate_path": "/tmp/x.py",
            "cost": 0.5, "applied": True, "name": "fix", "description": "d"}
        ev0 = {"correct": False, "combined_score": 0.0, "error_traceback": "boom",
               "stdout_log": "", "stderr_log": ""}
        mut0 = {"candidate_code": "broken", "candidate_path": "/tmp/b.py", "cost": 0.0}

        with tempfile.TemporaryDirectory() as td:
            base_cfg = {"results_dir": td, "task": {"task_sys_msg": "g"},
                        "evo": {"enable_novelty": False}}

            # A: fail -> fix succeeds on attempt 1.
            seq = iter([{"correct": True, "combined_score": 1.0, "stdout_log": "", "stderr_log": ""}])
            run_window._evaluate_candidate = lambda *a, **k: next(seq)
            counters = {"cost": 0.0, "iter_index": 0}
            cfg = {**base_cfg, "budget_usd": None}
            ev, mut, _fc = run_window._attempt_immediate_fixes(
                cfg, dict(ev0), dict(mut0), None, "azure-x", td, td, 5, "python", 1, counters)
            assert ev["correct"] is True and mut["candidate_code"] == "fixed", (ev, mut)
            assert counters["fix_count"] == 1 and counters["fix_success"] == 1, counters
            assert abs(counters["cost"] - 0.5) < 1e-9, counters  # fix cost folded

            # B: fix keeps failing; budget=2 exhausts with no success.
            run_window._evaluate_candidate = lambda *a, **k: {
                "correct": False, "combined_score": 0.0, "error_traceback": "still",
                "stdout_log": "", "stderr_log": ""}
            counters = {"cost": 0.0, "iter_index": 0}
            ev, mut, _fc = run_window._attempt_immediate_fixes(
                cfg, dict(ev0), dict(mut0), None, "azure-x", td, td, 5, "python", 2, counters)
            assert ev["correct"] is False and counters["fix_count"] == 2, counters
            assert counters.get("fix_success", 0) == 0 and abs(counters["cost"] - 1.0) < 1e-9, counters

            # C: budget railguard stops after the first attempt makes spend >= budget.
            counters = {"cost": 0.0, "iter_index": 0}
            cfg_b = {**base_cfg, "budget_usd": 0.4}
            ev, mut, _fc = run_window._attempt_immediate_fixes(
                cfg_b, dict(ev0), dict(mut0), None, "azure-x", td, td, 5, "python", 3, counters)
            assert counters["fix_count"] == 1, counters  # 2nd attempt unaffordable -> stopped
    finally:
        run_window.construct_mutation_prompt.main = orig_cmp
        run_window.mutate.main = orig_mut
        run_window._evaluate_candidate = orig_eval
    return True


if __name__ == "__main__":
    tests = [
        ("compute_reward", test_compute_reward),
        ("record_policy", test_record_policy),
        ("journal_roundtrip", test_journal_roundtrip),
        ("concern_bundle", test_concern_bundle),
        ("cadence_policy", test_cadence_policy),
        ("budget_hardstop", test_budget_hardstop),
        ("immediate_fix", test_immediate_fix),
    ]
    ok = True
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
        except Exception as exc:
            ok = False
            import traceback
            print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print("ALL IMPROVEMENT TESTS PASSED" if ok else "FAILURES")
    sys.exit(0 if ok else 1)

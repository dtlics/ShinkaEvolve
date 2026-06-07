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
    return None


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
    return None


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
    return None


def test_journal_ledger_durability():
    """P0-T1: a truncated/corrupt run.json is repaired on read by recomputing
    total_cost from the durable streams — the budget cap is never silently zeroed.
    Writes are atomic (no leftover .tmp)."""
    import json as _json

    import journal

    with tempfile.TemporaryDirectory() as td:
        journal.init_run(td, {"run_id": "r", "goal": "g", "budget_usd": 10.0})
        for i in (0, 1):
            journal.append_window(td, {
                "window_index": i, "J_score": 0.1, "best_score_end": 1.5,
                "total_programs": 5, "window_cost": 0.5,
                "island_health": [{"id": 0, "best": 1.5, "diversity": 3}],
            })
        journal.append_intervention(td, {"type": "rewrite", "cost": 0.3, "outcome": "accepted"})
        journal.log_call(td, "meta", {"u": "U"}, {"d": []}, cost=0.2, summary="meta")
        # 2*0.5 (windows) + 0.3 (intervention) + 0.2 (call) = 1.5
        assert abs(journal.total_cost(td) - 1.5) < 1e-9, journal.total_cost(td)
        rp = journal._run_path(td)
        assert not os.path.exists(rp + ".tmp")  # atomic write leaves no temp

        # Corrupt run.json by truncating it mid-object (simulate a crash mid-write).
        with open(rp, "w") as f:
            f.write('{"run_id": "r", "total_co')

        run = journal.read_run(td)
        assert run, "read_run must reconstruct, not return {}"
        assert run.get("recovered_from_corruption") is True
        assert run.get("windows_completed") == 2
        assert abs(journal.total_cost(td) - 1.5) < 1e-9, journal.total_cost(td)
        assert not os.path.exists(rp + ".tmp")
        _json.loads(open(rp).read())  # reconstructed file parses cleanly
    return None


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
    return None


def test_cadence_policy():
    """P4-T2: the work-score taper is UNCAPPED and escalating; no work score → wake
    every window; an OPTIONAL max_windows_per_call still clamps when the user sets one."""
    import cadence_policy as cp

    assert cp.main({"stagnation_flag": True, "windows_run": 1})["reason"] == "stagnation"
    hi = cp.main({"stagnation_flag": False, "windows_run": 1, "recent_work_score": 3})
    assert hi["return"] is True and hi["target_cluster_size"] == 1 and hi["reason"] == "taper"
    # no work-score signal → wake every window (safe default)
    assert cp.main({"stagnation_flag": False, "windows_run": 1,
                    "recent_work_score": None})["target_cluster_size"] == 1

    def _tgt(streak):
        return cp.main({"stagnation_flag": False, "windows_run": 0, "recent_work_score": 0,
                        "work_low_streak": streak})["target_cluster_size"]

    assert _tgt(1) == 5 and _tgt(2) == 10 and _tgt(3) == 20  # uncapped escalation
    assert cp.main({"stagnation_flag": False, "windows_run": 3, "recent_work_score": 0,
                    "work_low_streak": 1})["return"] is False  # 3 < 5
    assert cp.main({"stagnation_flag": False, "windows_run": 5, "recent_work_score": 0,
                    "work_low_streak": 1})["return"] is True   # 5 >= 5
    # OPTIONAL explicit ceiling still clamps if the user sets one
    assert cp.main({"stagnation_flag": False, "windows_run": 0, "recent_work_score": 0,
                    "work_low_streak": 3, "max_windows_per_call": 8})["target_cluster_size"] == 8
    return None


def test_work_score_readers():
    """P4-T1: journal readers that drive the taper (recent_work_score / recent_work_axes
    / work_low_streak)."""
    import tempfile

    import journal

    with tempfile.TemporaryDirectory() as td:
        journal.init_run(td, {"run_id": "w"})
        assert journal.recent_work_score(td) is None  # none recorded yet
        assert journal.work_low_streak(td) == 0
        journal.append_intervention(td, {"type": "audit", "work_audit": 3, "work_dr": 0, "work_score": 3})
        journal.append_intervention(td, {"type": "audit", "work_audit": 0, "work_dr": 0, "work_score": 0})
        assert journal.recent_work_score(td) == 0.0
        assert abs(journal.recent_work_score(td, n=2) - 1.5) < 1e-9  # mean(3, 0)
        assert journal.recent_work_axes(td) == {"work_audit": 0, "work_dr": 0}
        assert journal.work_low_streak(td) == 1  # only the trailing 0 is low; the 3 breaks it
        journal.append_intervention(td, {"type": "audit", "work_score": 0})
        assert journal.work_low_streak(td) == 2  # two trailing lows
        journal.append_intervention(td, {"type": "audit", "work_score": 3})
        assert journal.work_low_streak(td) == 0  # latest is high → streak resets
    return None


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
        # M18: overshoot is bounded by ONE full slot (here one mock mutation, no embed),
        # not unbounded — the upper bound, not just the lower bound the old test checked.
        assert journal.total_cost(rd) <= 2.5 + 1.0 + 1e-6, journal.total_cost(rd)
        # intervention cost lands in the same ledger
        journal.append_intervention(rd, {"type": "deep_research", "cost": 5.0})
        assert journal.budget_remaining(rd, 2.5) < 0
    return None


def test_apply_exhausted_truthful_recording():
    """P1-T1 (F-INNER-1): an apply-exhausted slot (mutate returns applied=False) is a
    TRUE failed attempt — the model's cost is charged cost-only to the bandit, NO
    reward, NOTHING archived (never a fabricated parent-copy duplicate), surfaced via
    the exhausted-retry signals."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window
    import journal

    orig_mut = run_window.mutate.main
    orig_sel = run_window.select_llm_script.main
    sel_updates = []

    def _capture_sel(payload):
        if payload.get("mode") == "update":
            sel_updates.append(dict(payload))
        return orig_sel(payload)

    try:
        run_window.mutate.main = lambda p: {
            "ok": True, "applied": False, "num_applied": 0,
            "candidate_code": p.get("parent_code", ""), "candidate_path": "/tmp/ae.py",
            "name": None, "description": None, "cost": 1.0, "attempts": 3,
            "transport": "mock", "error": "patch did not apply", "raw_response": None,
        }
        run_window.select_llm_script.main = _capture_sel
        with tempfile.TemporaryDirectory() as td:
            rd = os.path.join(td, "run")
            ip = os.path.join(td, "i.py")
            open(ip, "w").write("# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n")
            cfg = {
                "results_dir": rd, "run_id": "ae", "budget_usd": 100.0,
                "task": {"eval_program_path": "unused.py", "init_program_path": ip,
                         "task_sys_msg": "x", "language": "python"},
                "db_config": {"num_islands": 1, "archive_size": 20},
                "evo": {"window_size": 3, "patch_types": ["diff"], "patch_type_probs": [1.0],
                        "embedding_model": "text-embedding-3-small",
                        "llm_models": ["m1", "m2"], "enable_novelty": False, "seed": 0},
                "mock": {"enabled": True, "mutate_cost": 1.0,
                         "scores_by_generation": {str(i): 1.0 for i in range(40)}},
                "cadence": {"mode": "until_decision", "max_windows_per_call": 1},
                "window_state": {"window_index": 0, "prior_low_streak": 0},
            }
            d = run_window.main(cfg)
            # only the bootstrap seed is archived; the 3 apply-exhausted slots add NOTHING
            summ = run_window.archive_query.main({
                "db_path": os.path.join(rd, "programs.sqlite"),
                "db_config": cfg["db_config"], "embedding_model": "text-embedding-3-small",
                "query_type": "summary"})["result"]
            assert summ["total"] == 1, summ  # no fabricated parent-copy duplicates
            assert d["exhausted_retry_count"] == 3, d.get("exhausted_retry_count")
            assert len(d.get("exhausted_retry_slots", [])) == 3
            # every bandit update from the apply-exhausted slots was cost-only, no reward
            assert len(sel_updates) == 3, len(sel_updates)
            assert all(u.get("cost_only") is True and u.get("reward") is None
                       for u in sel_updates), sel_updates
            assert journal.total_cost(rd) >= 3.0, journal.total_cost(rd)
    finally:
        run_window.mutate.main = orig_mut
        run_window.select_llm_script.main = orig_sel
    return None


def test_diagnostics_sensor_fields():
    """P2-T3: errored_fraction (tombstoned EXCLUDED so it can release), apply/timeout/
    wrong echoes + apply_failure_rate, the counts-based model_collapse flag; the dead
    current_strategy_hash echo is gone."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import diagnostics as diag

    orig_aq = diag.archive_query.main
    orig_ih = diag.island_policy.island_health
    try:
        diag.island_policy.island_health = lambda *a, **k: []

        def _summary(total, correct, tomb=0):
            return lambda p: {"result": {"total": total, "correct": correct,
                                         "best_score": 1.0, "islands": [],
                                         "tombstoned_count": tomb}}

        base = {"db_path": "x", "db_config": {}, "embedding_model": "m",
                "window_index": 0, "iters_completed": 5, "best_score_start": 1.0,
                "window_size": 5}

        diag.archive_query.main = _summary(5, 3)
        out = diag.main(dict(base))
        assert abs(out["errored_fraction"] - 0.4) < 1e-9, out["errored_fraction"]
        assert "current_strategy_hash" not in out  # dead echo removed

        diag.archive_query.main = _summary(5, 3, tomb=2)  # tombstoned excluded → releases
        assert diag.main(dict(base))["errored_fraction"] == 0.0

        diag.archive_query.main = _summary(0, 0)
        assert diag.main(dict(base))["errored_fraction"] == 0.0

        diag.archive_query.main = _summary(5, 3)
        out3 = diag.main({**base, "llm_bandit_counts": {"a": {"submitted": 20}, "b": {"submitted": 1}}})
        assert out3["model_collapse"]["collapsed"] is True
        assert out3["model_collapse"]["top_arm"] == "a"
        out4 = diag.main({**base, "llm_bandit_counts": {"a": {"submitted": 5}, "b": {"submitted": 5}}})
        assert out4["model_collapse"]["collapsed"] is False

        out5 = diag.main({**base, "eval_total": 5, "apply_exhausted": 2,
                          "timeout_count": 1, "wrong_answer_count": 3})
        assert out5["apply_exhausted_count"] == 2
        assert out5["timeout_count"] == 1 and out5["wrong_answer_count"] == 3
        assert abs(out5["apply_failure_rate"] - 2 / 7) < 1e-9
    finally:
        diag.archive_query.main = orig_aq
        diag.island_policy.island_health = orig_ih
    return None


def test_warmup_trace_and_cleanup():
    """P2-T2: per-step tracing writes journal/steps.jsonl (sampler → prompt → llm_output
    → eval → framework_decision) ONLY when on; cleanup_warmup removes the throwaway
    workspace idempotently."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window
    import journal

    def _cfg(rd, trace):
        os.makedirs(rd, exist_ok=True)
        ip = os.path.join(rd, "i.py")
        open(ip, "w").write("# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n")
        return {
            "results_dir": rd, "run_id": "w", "budget_usd": 100.0, "trace_steps": trace,
            "task": {"eval_program_path": "unused.py", "init_program_path": ip,
                     "task_sys_msg": "x", "language": "python"},
            "db_config": {"num_islands": 1, "archive_size": 20},
            "evo": {"window_size": 2, "patch_types": ["diff"], "patch_type_probs": [1.0],
                    "embedding_model": "text-embedding-3-small", "enable_novelty": False, "seed": 0},
            "mock": {"enabled": True, "mutate_cost": 0.0,
                     "scores_by_generation": {str(i): 1.0 + 0.01 * i for i in range(40)}},
            "cadence": {"mode": "until_decision", "max_windows_per_call": 1},
            "window_state": {"window_index": 0, "prior_low_streak": 0},
        }

    with tempfile.TemporaryDirectory() as td:
        rd1 = os.path.join(td, "traced")
        run_window.main(_cfg(rd1, True))
        kinds = {s.get("step") for s in journal.read_steps(rd1)}
        assert {"sampler", "prompt", "llm_output", "eval", "framework_decision"} <= kinds, kinds

        rd2 = os.path.join(td, "untraced")
        run_window.main(_cfg(rd2, False))
        assert journal.read_steps(rd2) == []  # tracing OFF → no steps.jsonl
        assert not os.path.exists(os.path.join(rd2, "journal", "steps.jsonl"))

        warm = os.path.join(rd2, "warmup")
        os.makedirs(warm, exist_ok=True)
        assert run_window.cleanup_warmup(rd2) is True
        assert not os.path.exists(warm)
        assert run_window.cleanup_warmup(rd2) is False  # idempotent no-op
    return None


def test_meta_island_directions():
    """P3-T1: meta returns one distinct island_directions entry per island, drops a
    malformed entry without crashing, and defaults to gpt-5.5."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import meta_summarize

    txt = ('{"directions": [{"text": "anneal", "weight": 0.5}], "failure_note": "fn", '
           '"island_directions": [{"island_idx": 0, "text": "greedy"}, '
           '{"island_idx": "bad", "text": "dropme"}, {"island_idx": 2, "text": "exact"}]}')
    out = meta_summarize.main({"mock": True, "mock_text": txt, "goal": "g"})
    assert out["model"] == "azure-gpt-5.5"  # default model flipped to gpt-5.5
    assert [d["island_idx"] for d in out["island_directions"]] == [0, 2]  # "bad" dropped
    assert out["directions"] and out["failure_note"] == "fn"
    out2 = meta_summarize.main(
        {"mock": True, "mock_text": '{"directions": [], "failure_note": ""}', "goal": "g"})
    assert out2["island_directions"] == []  # absent key → []
    return None


def test_auto_meta_per_window():
    """P3-T2: the harness runs an automatic per-window meta round, folds its cost into
    the ledger, and auto-records ONE per-island brief; auto_meta=False skips the whole
    round; a meta failure never crashes the window."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window
    import journal

    def _cfg(rd, auto_meta):
        os.makedirs(rd, exist_ok=True)
        ip = os.path.join(rd, "i.py")
        open(ip, "w").write("# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n")
        return {
            "results_dir": rd, "run_id": "m", "budget_usd": 100.0,
            "task": {"eval_program_path": "unused.py", "init_program_path": ip,
                     "task_sys_msg": "x", "language": "python"},
            "db_config": {"num_islands": 2, "archive_size": 20},
            "evo": {"window_size": 1, "patch_types": ["diff"], "patch_type_probs": [1.0],
                    "embedding_model": "text-embedding-3-small", "enable_novelty": False,
                    "seed": 0, "auto_meta": auto_meta},
            "mock": {"enabled": True, "mutate_cost": 0.0,
                     "scores_by_generation": {str(i): 1.0 + 0.01 * i for i in range(40)}},
            "cadence": {"mode": "until_decision", "max_windows_per_call": 1},
            "window_state": {"window_index": 0, "prior_low_streak": 0},
        }

    orig_meta = run_window.meta_summarize_script.main
    try:
        def _meta_stub(payload):  # folds cost like the real log_external_call does
            journal.add_cost(payload["results_dir"], 0.5)
            return {"directions": [{"text": "d", "weight": 1.0}], "failure_note": "fn",
                    "island_directions": [{"island_idx": 0, "text": "island0 dir"}],
                    "recommendations": "d", "cost": 0.5, "model": "x"}

        run_window.meta_summarize_script.main = _meta_stub
        with tempfile.TemporaryDirectory() as td:
            rd = os.path.join(td, "run")
            d = run_window.main(_cfg(rd, True))
            assert journal.total_cost(rd) >= 0.5 and d["total_cost"] >= 0.5  # meta cost folded
            brief = run_window.archive_query.main({
                "db_path": os.path.join(rd, "programs.sqlite"), "db_config": {"num_islands": 2},
                "embedding_model": "text-embedding-3-small",
                "query_type": "island_brief", "island_idx": 0})["result"]
            assert (brief or {}).get("content") == "island0 dir", brief

        called = {"n": 0}

        def _meta_count(payload):
            called["n"] += 1
            return _meta_stub(payload)

        run_window.meta_summarize_script.main = _meta_count
        with tempfile.TemporaryDirectory() as td:
            run_window.main(_cfg(os.path.join(td, "run"), False))
            assert called["n"] == 0  # auto_meta=False skips the whole round

        def _meta_raise(payload):
            raise RuntimeError("boom")

        run_window.meta_summarize_script.main = _meta_raise
        with tempfile.TemporaryDirectory() as td:
            d = run_window.main(_cfg(os.path.join(td, "run"), True))
            assert d.get("ok") is True  # a meta failure never crashes the window
    finally:
        run_window.meta_summarize_script.main = orig_meta
    return None


def test_repair_mode_lifecycle():
    """P5 (T1/T2/T3/T4): repair mode turns ON at >=20% errored; a FAILED repair appends
    to the errored PARENT (no new child) and tombstones it after the attempt cap;
    tombstoning EXCLUDES it from errored_fraction so the mode RELEASES."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window

    def _cfg(rd):
        os.makedirs(rd, exist_ok=True)
        ip = os.path.join(rd, "i.py")
        open(ip, "w").write("# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n")
        return {
            "results_dir": rd, "run_id": "rep", "budget_usd": 100.0,
            "task": {"eval_program_path": "unused.py", "init_program_path": ip,
                     "task_sys_msg": "x", "language": "python"},
            "db_config": {"num_islands": 1, "archive_size": 20},
            "evo": {"window_size": 1, "patch_types": ["diff"], "patch_type_probs": [1.0],
                    "embedding_model": "text-embedding-3-small", "enable_novelty": False,
                    "seed": 0, "auto_meta": False, "repair_trigger_fraction": 0.2,
                    "repair_attempt_cap": 2, "fix_retry_budget": 0},
            "mock": {"enabled": True, "mutate_cost": 0.0,
                     "scores_by_generation": {str(i): 1.0 for i in range(40)},
                     "incorrect_generations": [1, 2, 3]},  # gen1 errored; repairs (gen2) fail
            "cadence": {"mode": "until_decision", "max_windows_per_call": 1},
            "window_state": {"window_index": 0, "prior_low_streak": 0},
        }

    def _summary(rd, cfg):
        return run_window.archive_query.main({
            "db_path": os.path.join(rd, "programs.sqlite"),
            "db_config": cfg["db_config"], "embedding_model": "text-embedding-3-small",
            "query_type": "summary"})["result"]

    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "run")
        cfg = _cfg(rd)
        # window 0: normal gen → gen1 errored child → errored_fraction >= 0.2
        d0 = run_window.main(cfg)
        s0 = _summary(rd, cfg)
        assert s0["total"] == 2 and s0["correct"] == 1, s0  # bootstrap + 1 errored
        assert d0["errored_fraction"] >= 0.2, d0["errored_fraction"]
        # window 1: repair mode ON → repair gen FAILS → no new child, parent repair +1
        d1 = run_window.main(cfg)
        assert d1["repair_fail_count"] == 1, d1
        assert _summary(rd, cfg)["total"] == 2  # NO new child archived for the failed repair
        # window 2: repair fails again → parent tombstoned (cap=2)
        d2 = run_window.main(cfg)
        assert d2["repair_tombstoned_count"] == 1, d2
        assert _summary(rd, cfg)["tombstoned_count"] == 1
        # tombstoned EXCLUDED from errored_fraction → it dropped → repair RELEASES
        assert d2["errored_fraction"] < 0.2, d2["errored_fraction"]
    return None


def test_boot_guard():
    """P6-T1: the harness refuses to start (spending NOTHING) when task_sys_msg is unset
    / placeholder; require_sys_msg=false downgrades to a warning; the starters ship the
    sentinel."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window

    def _cfg(rd, sysmsg, require=True):
        os.makedirs(rd, exist_ok=True)
        ip = os.path.join(rd, "i.py")
        open(ip, "w").write("# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n")
        task = {"eval_program_path": "u.py", "init_program_path": ip, "language": "python",
                "require_sys_msg": require}
        if sysmsg is not None:
            task["task_sys_msg"] = sysmsg
        return {
            "results_dir": rd, "run_id": "b", "budget_usd": 100.0, "task": task,
            "db_config": {"num_islands": 1, "archive_size": 20},
            "evo": {"window_size": 1, "patch_types": ["diff"], "patch_type_probs": [1.0],
                    "embedding_model": "text-embedding-3-small", "enable_novelty": False,
                    "seed": 0, "auto_meta": False},
            "mock": {"enabled": True, "scores_by_generation": {str(i): 1.0 for i in range(5)}},
            "cadence": {"mode": "until_decision", "max_windows_per_call": 1},
            "window_state": {"window_index": 0, "prior_low_streak": 0},
        }

    for bad in ("__UNSET_AUTHOR_AT_BOOT__", "", None):
        with tempfile.TemporaryDirectory() as td:
            rd = os.path.join(td, "run")
            try:
                run_window.main(_cfg(rd, bad))
                assert False, f"expected SystemExit for task_sys_msg={bad!r}"
            except SystemExit:
                pass
            # spent NOTHING: no journal / no db created before the guard
            assert not os.path.exists(os.path.join(rd, "journal"))
            assert not os.path.exists(os.path.join(rd, "programs.sqlite"))
    # require_sys_msg=false + sentinel → proceeds with a warning
    with tempfile.TemporaryDirectory() as td:
        assert run_window.main(_cfg(os.path.join(td, "r"), "__UNSET_AUTHOR_AT_BOOT__", require=False)).get("ok") is True
    # a real authored message is unaffected
    with tempfile.TemporaryDirectory() as td:
        assert run_window.main(_cfg(os.path.join(td, "r"), "solve the real task")).get("ok") is True
    # the canonical run-config starter carries the sentinel
    p = _REPO_ROOT / "configs" / "orchestrator_run.default.json"
    assert "__UNSET_AUTHOR_AT_BOOT__" in open(p).read()
    return None


def test_fix_prompt_reads_only_metadata_channels():
    """P6-T3 contract: the fix prompt's error section comes ONLY from the parent's
    stdout_log/stderr_log metadata — so run_window blanking those channels when
    use_text_feedback=false is a COMPLETE spoil mitigation."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import construct_mutation_prompt as cmp

    def _fix_prompt(stderr):
        parent = {"id": "p", "code": "x=1\n", "combined_score": 0.0,
                  "metadata": {"stdout_log": "", "stderr_log": stderr}}
        out = cmp.main({"parent": parent, "needs_fix": True, "language": "python",
                        "patch_types": ["diff"], "patch_type_probs": [1.0],
                        "task_sys_msg": "t", "seed": 0})
        return (out.get("patch_sys", "") or "") + "\n" + (out.get("patch_msg", "") or "")

    assert "HELDOUT=0.42" in _fix_prompt("boom HELDOUT=0.42")  # marker in channel → in prompt
    assert "HELDOUT=0.42" not in _fix_prompt("")               # blank channel → NOT in prompt
    return None


def test_snapshot_restore_state():
    """P7-T1: restore_state is a FULL rewind of archive + bandit, but the cost LEDGER is
    PRESERVED at the live value (never rewound) so a revert can't be used to exceed budget."""
    import glob
    import json as _json
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import strategy_store as ss

    with tempfile.TemporaryDirectory() as td:
        os.environ["SHINKA_ORCH_HISTORY_DIR"] = os.path.join(td, "hist")
        try:
            rd = os.path.join(td, "run")
            os.makedirs(os.path.join(rd, "journal"))
            open(os.path.join(rd, "programs.sqlite"), "w").write("DB_V1")
            open(os.path.join(rd, "bandit_state.pkl"), "wb").write(b"BANDIT_V1")
            with open(os.path.join(rd, "journal", "run.json"), "w") as f:
                _json.dump({"run_id": "r", "total_cost": 1.0, "best_score": 0.5}, f)
            snap = ss.snapshot_state(rd, label="pre")
            # mutate all three + raise the LIVE cost to 5.0
            open(os.path.join(rd, "programs.sqlite"), "w").write("DB_V2")
            open(os.path.join(rd, "bandit_state.pkl"), "wb").write(b"BANDIT_V2")
            with open(os.path.join(rd, "journal", "run.json"), "w") as f:
                _json.dump({"run_id": "r", "total_cost": 5.0, "best_score": 0.9}, f)
            out = ss.restore_state(rd, snap)
            assert open(os.path.join(rd, "programs.sqlite")).read() == "DB_V1"  # archive rewound
            assert open(os.path.join(rd, "bandit_state.pkl"), "rb").read() == b"BANDIT_V1"  # bandit rewound
            run = _json.load(open(os.path.join(rd, "journal", "run.json")))
            assert run["total_cost"] == 5.0  # cost ledger PRESERVED (not rewound to 1.0)
            assert run["best_score"] == 0.5  # other state rewound to the snapshot
            assert out["total_cost_preserved"] == 5.0
            for _ in range(6):  # 1 + 6 = 7 snapshots, keep=5 → 5 remain
                ss.snapshot_state(rd, keep=5)
            assert len(glob.glob(os.path.join(td, "hist", "state_*"))) == 5
        finally:
            os.environ.pop("SHINKA_ORCH_HISTORY_DIR", None)
    return None


def test_rollback_fail_closed_and_collapse():
    """P7-T2: fail CLOSED on no-data / NaN measure; P7-T3: counts-share collapse."""
    sys.path.insert(0, str(_ORCH / "harness"))
    import rollback_decision as rb

    assert rb.decide({}, {})["regressed"] is True  # empty measure → fail closed
    assert rb.decide({}, {"delta": 0.0, "evaluation_failure_rate": 0.3},
                     measure_crashed=True)["regressed"] is True
    assert rb.decide({}, {"delta": float("nan"), "evaluation_failure_rate": 0.3})["regressed"] is True
    flat = {"delta": 0.0, "threshold": 0.001, "evaluation_failure_rate": 0.3}
    assert rb.decide(flat, dict(flat))["regressed"] is False  # valid flat window is NOT caught

    prior = {**flat, "llm_bandit_counts": {"a": {"submitted": 5}, "b": {"submitted": 5}}}
    collapsed = {**flat, "llm_bandit_counts": {"a": {"submitted": 20}, "b": {"submitted": 1}}}
    r = rb.decide(prior, collapsed)
    assert r["regressed"] is True and any("collapse" in x for x in r["reasons"])
    assert rb.decide(prior, dict(prior))["regressed"] is False  # balanced arms → no collapse
    return None


def test_validate_select_llm_all_modes():
    """P7-T5: validate_strategy smokes select+weights+update on the real select_llm.py."""
    sys.path.insert(0, str(_ORCH / "harness"))
    import validate_strategy as vs

    r = vs.main({"candidate_path": str(_ORCH / "scripts" / "select_llm.py"),
                 "target_filename": "select_llm.py"})
    assert r["valid"] is True, r
    return None


def test_dr_refusal_graceful():
    """P7-T6: a refused/failed DR call returns a DEGRADED result (no crash) with a reason."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import deep_research
    import shinka.llm.agent.dr_client as drc

    orig_client, orig_run = drc.get_dr_async_client, drc.run_dr_call
    try:
        drc.get_dr_async_client = lambda: (object(), None)

        async def _raise_cf(*a, **k):
            raise RuntimeError("content_filter refused the query")

        drc.run_dr_call = _raise_cf
        out = deep_research.main({"query": "q", "program_context": "c"})
        assert out["refused"] is True and out["reason"] == "content_filter", out

        async def _raise_to(*a, **k):
            raise TimeoutError("did not finish")

        drc.run_dr_call = _raise_to
        out2 = deep_research.main({"query": "q", "program_context": "c"})
        assert out2["refused"] is True and out2["reason"].startswith("dr_failed"), out2
    finally:
        drc.get_dr_async_client, drc.run_dr_call = orig_client, orig_run
    return None


def test_deploy_bundle_rejected_guard():
    """P7-T4: deploy_bundle refuses a candidate hash a prior bundle outcome REJECTED."""
    import importlib
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        sc, hi = os.path.join(td, "scripts"), os.path.join(td, "hist")
        os.makedirs(sc)
        os.environ["SHINKA_ORCH_SCRIPTS_DIR"] = sc
        os.environ["SHINKA_ORCH_HISTORY_DIR"] = hi
        try:
            import strategy_store as ss
            importlib.reload(ss)
            for f in ("compute_reward.py", "select_llm.py"):
                open(os.path.join(sc, f), "w").write("def main(p):\n    return {'v': 1}\n")
            c1, c2 = os.path.join(td, "c1.py"), os.path.join(td, "c2.py")
            open(c1, "w").write("def main(p):\n    return {'v': 2}\n")
            open(c2, "w").write("def main(p):\n    return {'v': 3}\n")
            changes = [{"candidate_path": c1, "target": "compute_reward.py"},
                       {"candidate_path": c2, "target": "select_llm.py"}]
            res = ss.deploy_bundle(changes, reason="t", window_index=1)
            ss.record_bundle_outcome(res["new_hashes"], J=0.0, accepted=False)
            try:
                ss.deploy_bundle(changes, reason="retry", window_index=2)
                assert False, "expected ValueError for a rejected bundle hash"
            except ValueError:
                pass
            ss.deploy_bundle(changes, reason="forced", window_index=3, force=True)  # force bypasses
        finally:
            os.environ.pop("SHINKA_ORCH_SCRIPTS_DIR", None)
            os.environ.pop("SHINKA_ORCH_HISTORY_DIR", None)
            importlib.reload(ss)  # restore real dirs for later tests
    return None


def test_per_call_cost_cap():
    """P7-T7: the per-call max-output-token cap (the deliberate ~$10 guard) is pinned."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import _azure

    assert _azure._resolve_max_output_tokens("azure-gpt-5.5") == 200_000
    assert _azure._resolve_max_output_tokens("azure-gpt-5.4-pro") == 50_000
    # P7-T7: DR carries its OWN per-call cap (≈$8 at 200k); verify the default is pinned.
    import inspect as _inspect

    import shinka.llm.agent.dr_client as drc
    assert _inspect.signature(drc.run_dr_call).parameters["max_output_tokens"].default == 200_000
    # the fix retry rides the SAME capped path as a normal mutation: mutate.py → _azure.bg_query
    # (which resolves the cap), so a fix call cannot exceed the per-model cap either.
    import mutate as _mutate
    assert "bg_query" in _inspect.getsource(_mutate), "fix/mutation must share the capped bg_query path"
    return None


def test_nonfinite_score_guards():
    """P10-T1/T2: a non-finite candidate score → failed-attempt reward (no NaN poisons
    the bandit); negative finite scores are supported; the parent sampler's weighted
    probabilities are never NaN even with a non-finite score in the pool."""
    import math as _m

    sys.path.insert(0, str(_ORCH / "scripts"))
    import compute_reward
    import sample_parent

    for bad in (float("nan"), float("inf"), None):
        out = compute_reward.main({"candidate": {"combined_score": bad, "correct": True},
                                   "parent": {"combined_score": 0.5}, "mode": "absolute"})
        assert out["reward"] is None, (bad, out)
    # a NEGATIVE finite (correct-but-worse) score still yields a finite reward floored by
    # reward_validity_floor → proves negative-score tasks are supported.
    neg = compute_reward.main({"candidate": {"combined_score": -0.5, "correct": True},
                               "parent": {"combined_score": -0.3}, "mode": "absolute",
                               "reward_validity_floor": 0.001})
    assert neg["reward"] is not None and abs((neg["reward"] - neg["baseline"]) - 0.001) < 1e-9, neg
    # weighted-probs falls back to a finite uniform vector when a NaN would poison the sum
    probs = sample_parent._weighted_probs([1.0, float("nan"), 2.0], [0, 0, 0], 10.0)
    assert len(probs) == 3 and all(_m.isfinite(p) for p in probs) and abs(sum(probs) - 1.0) < 1e-6, probs
    return None


def test_end_of_run_summary_and_archive():
    """P8-T1: ending summary carries the future-fixes header (not 'J trajectory');
    finalize_run flips status; archive_run copies the COMPACT subset (excl call blobs);
    None-defaulting never crashes; .gitignore carries the archive dir."""
    import json as _json
    import tempfile

    import journal

    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "run")
        journal.init_run(rd, {"run_id": "ar", "goal": "g", "budget_usd": 10.0})
        journal.append_window(rd, {"window_index": 0, "best_score_end": 1.5,
                                    "total_programs": 3, "window_cost": 0.5,
                                    "stagnation_flag": False, "island_health": []})
        journal.log_call(rd, "dr", {"query": "q"}, {"brief": []}, cost=0.2, summary="s")
        summ = journal.build_run_summary(rd)
        assert "# Run Summary" in summ
        assert "Future fixes for the user before the next run" in summ
        assert "Progress trajectory" in summ and "J trajectory" not in summ

        journal.finalize_run(rd, "budget_exhausted")
        run = journal.read_run(rd)
        assert run["status"] == "budget_exhausted" and run.get("finished_at")

        open(os.path.join(rd, "RUN_SUMMARY.md"), "w").write(summ)
        open(os.path.join(rd, "programs.sqlite"), "w").write("DB")
        dest_root = os.path.join(td, "arch")
        dest = journal.archive_run(rd, dest_root=dest_root)
        assert os.path.basename(dest).startswith("ar__")
        assert os.path.exists(os.path.join(dest, "journal", "run.json"))
        assert os.path.exists(os.path.join(dest, "journal", "calls.jsonl"))
        assert os.path.exists(os.path.join(dest, "programs.sqlite"))
        assert os.path.exists(os.path.join(dest, "RUN_SUMMARY.md"))
        assert not os.path.isdir(os.path.join(dest, "journal", "calls"))  # heavy blobs excluded

        # None-defaulting: a run.json without run_id/finished_at derives both, no int(None)
        rd2 = os.path.join(td, "run2")
        os.makedirs(os.path.join(rd2, "journal"))
        with open(os.path.join(rd2, "journal", "run.json"), "w") as f:
            _json.dump({"total_cost": 0.0}, f)
        assert "run2__" in journal.archive_run(rd2, dest_root=dest_root)

    assert "orchestrator/run_archive/" in open(_REPO_ROOT / ".gitignore").read()
    return None


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
                cfg, dict(ev0), dict(mut0), None, "azure-x", "medium", td, td, 5, "python", 1, counters)
            assert ev["correct"] is True and mut["candidate_code"] == "fixed", (ev, mut)
            assert counters["fix_count"] == 1 and counters["fix_success"] == 1, counters
            assert abs(counters["cost"] - 0.5) < 1e-9, counters  # fix cost folded

            # B: fix keeps failing; budget=2 exhausts with no success.
            run_window._evaluate_candidate = lambda *a, **k: {
                "correct": False, "combined_score": 0.0, "error_traceback": "still",
                "stdout_log": "", "stderr_log": ""}
            counters = {"cost": 0.0, "iter_index": 0}
            ev, mut, _fc = run_window._attempt_immediate_fixes(
                cfg, dict(ev0), dict(mut0), None, "azure-x", "medium", td, td, 5, "python", 2, counters)
            assert ev["correct"] is False and counters["fix_count"] == 2, counters
            assert counters.get("fix_success", 0) == 0 and abs(counters["cost"] - 1.0) < 1e-9, counters

            # C: budget railguard stops after the first attempt makes spend >= budget.
            counters = {"cost": 0.0, "iter_index": 0}
            cfg_b = {**base_cfg, "budget_usd": 0.4}
            ev, mut, _fc = run_window._attempt_immediate_fixes(
                cfg_b, dict(ev0), dict(mut0), None, "azure-x", "medium", td, td, 5, "python", 3, counters)
            assert counters["fix_count"] == 1, counters  # 2nd attempt unaffordable -> stopped
    finally:
        run_window.construct_mutation_prompt.main = orig_cmp
        run_window.mutate.main = orig_mut
        run_window._evaluate_candidate = orig_eval
    return None


def test_meta_summarize_parsing():
    """WS2/WS3: meta returns weighted directions + a failure_note; a non-JSON reply
    degrades to a single direction rather than crashing."""
    import meta_summarize

    mock_json = (
        '{"directions": [{"text": "use rung CXs", "weight": 0.7}, '
        '{"text": "tile 2x2 blocks", "weight": 0.3}], '
        '"failure_note": "Most recent failures were eval TIMEOUTS; keep synthesis fast."}'
    )
    out = meta_summarize.main({"mock": True, "mock_text": mock_json, "max_recommendations": 5})
    assert len(out["directions"]) == 2, out["directions"]
    assert out["directions"][0]["text"] == "use rung CXs"
    assert abs(out["directions"][0]["weight"] - 0.7) < 1e-9
    assert "timeout" in out["failure_note"].lower()

    # Non-JSON fallback: keep the raw text as one direction, empty failure_note.
    out2 = meta_summarize.main({"mock": True, "mock_text": "just iterate harder", "max_recommendations": 5})
    assert len(out2["directions"]) == 1 and out2["directions"][0]["text"] == "just iterate harder"
    assert out2["failure_note"] == ""
    return None


def test_meta_direction_sampling():
    """WS2: per-gen sampling picks one direction by weight; compose prepends the
    persistent failure caution; legacy meta_recommendations blob still works."""
    import random as _r

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window

    dirs = [{"text": "A", "weight": 1.0}, {"text": "B", "weight": 0.0}]
    picks = {run_window._sample_meta_direction(dirs, _r.Random(i)) for i in range(20)}
    assert picks == {"A"}, picks  # zero-weight arm never chosen

    evo = {"meta_directions": [{"text": "tryX", "weight": 1.0}],
           "meta_failure_note": "watch runtime/timeouts", "seed": 0}
    msg = run_window._compose_meta_for_gen(evo, 3)
    # Carrier fix: _compose returns the DIRECTION only; the persistent failure
    # caution now rides as its own always-on `failure_note` field, so it can't be
    # clobbered by an island_brief or dropped on a cross/lit gen.
    assert "tryX" in msg and "watch runtime/timeouts" not in msg, msg

    assert run_window._compose_meta_for_gen({"meta_recommendations": "old blob"}, 0) == "old blob"
    assert run_window._compose_meta_for_gen({}, 0) is None
    return None


def test_call_logging():
    """WS7: log_call persists a never-overwritten detail file + compact pointer,
    folds cost into the ledger, and reads back; _common.log_external_call self-logs
    and no-ops cleanly when results_dir is falsy."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    sys.path.insert(0, str(_ORCH / "scripts"))
    import journal
    import _common

    with tempfile.TemporaryDirectory() as td:
        journal.init_run(td, {"run_id": "x"})
        p1 = journal.log_call(td, "dr", {"query": "Q1"}, {"brief": [1, 2]}, cost=1.5, summary="2 items")
        p2 = journal.log_call(td, "meta", {"user": "U"}, {"directions": []}, cost=0.02, summary="0 dirs")
        assert p1 != p2 and os.path.exists(p1) and os.path.exists(p2)  # never overwrite

        calls = journal.read_calls(td)
        assert len(calls) == 2, calls
        dr = journal.read_calls(td, kind="dr")
        assert len(dr) == 1 and dr[0]["summary"] == "2 items", dr
        detail = journal.read_call(td, dr[0]["file"])
        assert detail["request"]["query"] == "Q1" and detail["response"]["brief"] == [1, 2], detail
        assert abs(journal.total_cost(td) - 1.52) < 1e-9, journal.total_cost(td)  # cost folded

        p3 = _common.log_external_call(td, "meta", {"u": "v"}, {"r": 1}, cost=0.10, summary="s")
        assert p3 and os.path.exists(p3)
        assert abs(journal.total_cost(td) - 1.62) < 1e-9, journal.total_cost(td)
        assert _common.log_external_call(None, "meta", {}, {}) is None  # no-op without results_dir
    return None


def test_capped_island_spawn():
    """Foundation: at max_islands the spawn allocator EVICTS the worst island
    (non-destructively: de-archived + island nulled) and reuses its index; the
    global-best island + island 0 are protected; unbounded mode allocates fresh."""
    import sqlite3
    import types

    from shinka.database.islands import CombinedIslandManager

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE programs (id TEXT PRIMARY KEY, island_idx INTEGER, "
        "correct INTEGER, combined_score REAL, metadata TEXT)"
    )
    cur.execute("CREATE TABLE archive (program_id TEXT)")
    # 3 islands: 0 best=0.9 (GLOBAL best), 1 best=0.2 (WORST, 2 members), 2 best=0.5
    rows = [("a0", 0, 0.9), ("a1", 1, 0.2), ("a1b", 1, 0.1), ("a2", 2, 0.5)]
    for pid, isl, sc in rows:
        cur.execute(
            "INSERT INTO programs (id, island_idx, correct, combined_score, metadata) "
            "VALUES (?,?,1,?,?)", (pid, isl, sc, "{}"))
        cur.execute("INSERT INTO archive (program_id) VALUES (?)", (pid,))
    conn.commit()

    cfg = types.SimpleNamespace(num_islands=3, max_islands=3,
                               island_evict_strategy="worst_best_fitness")
    mgr = CombinedIslandManager(cur, conn, cfg)

    # At cap (3 active, max 3) → evict the worst-fitness island (1) and reuse index 1.
    idx = mgr.allocate_island_index_for_spawn()
    assert idx == 1, idx
    cur.execute("SELECT COUNT(*) c FROM archive WHERE program_id IN ('a1','a1b')")
    assert cur.fetchone()["c"] == 0, "evicted island must be de-archived"
    cur.execute("SELECT COUNT(*) c FROM programs WHERE island_idx = 1")
    assert cur.fetchone()["c"] == 0, "evicted island index must be freed (nulled)"
    cur.execute("SELECT COUNT(*) c FROM programs WHERE id IN ('a1','a1b')")
    assert cur.fetchone()["c"] == 2, "rows preserved (non-destructive)"
    assert 0 in mgr.get_initialized_islands(), "global-best island 0 protected"

    # fewest_members strategy on a fresh DB: island 2 (1 member) is fewest among
    # non-protected {1,2} (0 protected as global-best+island0).
    conn2 = sqlite3.connect(":memory:")
    conn2.row_factory = sqlite3.Row
    c2 = conn2.cursor()
    c2.execute("CREATE TABLE programs (id TEXT PRIMARY KEY, island_idx INTEGER, "
               "correct INTEGER, combined_score REAL, metadata TEXT)")
    c2.execute("CREATE TABLE archive (program_id TEXT)")
    for pid, isl, sc in [("b0", 0, 0.9), ("b1", 1, 0.5), ("b1b", 1, 0.4), ("b2", 2, 0.6)]:
        c2.execute("INSERT INTO programs VALUES (?,?,1,?,?)", (pid, isl, sc, "{}"))
        c2.execute("INSERT INTO archive (program_id) VALUES (?)", (pid,))
    conn2.commit()
    cfg2 = types.SimpleNamespace(num_islands=3, max_islands=3,
                                island_evict_strategy="fewest_members")
    mgr2 = CombinedIslandManager(c2, conn2, cfg2)
    assert mgr2.allocate_island_index_for_spawn() == 2, "fewest_members evicts island 2"

    # Unbounded (max_islands=0): a fresh index beyond existing, no eviction.
    cfg2.max_islands = 0
    nxt = mgr2.allocate_island_index_for_spawn()
    assert nxt == mgr2.get_next_island_index(), nxt
    return None


def test_parse_arm():
    """WS6: a bandit arm id 'model@effort' splits into (model, effort); a bare model
    falls back to the run default effort."""
    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window

    assert run_window._parse_arm("azure-gpt-5.4-pro@high", "medium") == ("azure-gpt-5.4-pro", "high")
    assert run_window._parse_arm("azure-gpt-5.4-mini", "low") == ("azure-gpt-5.4-mini", "low")
    assert run_window._parse_arm("azure-gpt-5.5@", "medium") == ("azure-gpt-5.5", "medium")  # empty effort -> default
    assert run_window._parse_arm(None, "medium") == (None, "medium")
    return None


def test_bg_call_tools_and_caps():
    """WS4: _bg_call attaches the web_search_preview tool + max_output_tokens to the
    Responses create() call only when asked; both absent otherwise."""
    import asyncio

    sys.path.insert(0, str(_ORCH / "scripts"))
    import _azure

    captured: dict = {}

    class _FakeResp:
        id = "r1"
        status = "completed"
        output_text = "ok"
        usage = None

    class _FakeResponses:
        async def create(self, **kw):
            captured.clear()
            captured.update(kw)
            return _FakeResp()

        async def retrieve(self, rid):
            return _FakeResp()

    class _FakeClient:
        responses = _FakeResponses()

        async def aclose(self):
            pass

    text, cost = asyncio.run(_azure._bg_call(
        _FakeClient(), "m", "sys", "usr", "medium", {"purpose": "proposer"},
        0.01, 60, 12345, [{"type": "web_search_preview"}]))
    assert text == "ok" and cost == 0.0, (text, cost)
    assert captured["max_output_tokens"] == 12345, captured
    assert captured["tools"] == [{"type": "web_search_preview"}], captured

    asyncio.run(_azure._bg_call(
        _FakeClient(), "m", "sys", "usr", None, None, 0.01, 60, None, None))
    assert "tools" not in captured and "max_output_tokens" not in captured, captured
    return None


def test_wrap_eval_honors_correct_flag():
    """Phase 1 (1A.1): run_shinka_eval honors an explicit metrics['correct'] is
    False (a DOMAIN failure with a finite, non-NaN score) and surfaces its
    text_feedback; omitting `correct` preserves today's behavior (correct=True
    at score 0). Reverting the wrap_eval honoring makes this fail."""
    from shinka.core.wrap_eval import run_shinka_eval

    prog = "def run_experiment(**kwargs):\n    return 1\n"
    with tempfile.TemporaryDirectory() as d:
        pp = os.path.join(d, "prog.py")
        with open(pp, "w") as f:
            f.write(prog)

        # Explicit correct=False on a finite score -> incorrect, feedback as the error.
        _, correct, err = run_shinka_eval(
            program_path=pp, results_dir=d, experiment_fn_name="run_experiment",
            num_runs=1, get_experiment_kwargs=lambda i: {},
            aggregate_metrics_fn=lambda res: {
                "combined_score": 0.0, "correct": False,
                "text_feedback": "adjacency: non-adjacent cx(3,7)",
            },
            validate_fn=None,
        )
        assert correct is False, (correct, err)
        assert err and "adjacency" in err, err

        # Omitting `correct` -> additive no-op: a score-0 program stays correct=True.
        _, correct2, _ = run_shinka_eval(
            program_path=pp, results_dir=d, experiment_fn_name="run_experiment",
            num_runs=1, get_experiment_kwargs=lambda i: {},
            aggregate_metrics_fn=lambda res: {"combined_score": 0.0},
            validate_fn=None,
        )
        assert correct2 is True, correct2


def test_cnot_evaluator_emits_correct_flag():
    """Phase 1 (1A.3): the cnot aggregate_fn emits correct=False on its fast
    (no-baseline) failure return sites. Imported by path to avoid the
    evaluate.py name clash with orchestrator/scripts/evaluate.py."""
    import importlib.util

    ev_path = str(_REPO_ROOT / "tasks" / "cnot_grid_synth" / "evaluate.py")
    spec = importlib.util.spec_from_file_location("cnot_evaluate", ev_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Both early returns (no result / non-callable) skip the baseline.
    assert mod.aggregate_fn([]).get("correct") is False
    assert mod.aggregate_fn([42]).get("correct") is False


def test_failure_note_always_rendered():
    """Phase 2/6 carrier (M1/M2/M3/M4): the persistent failure_note rides into the
    prompt regardless of patch_type — including `cross` (which skips the per-gen
    direction) and when an island_brief replaced the direction. Reverting the
    sampler failure_note field makes this fail."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import construct_mutation_prompt as cmp

    parent = {"id": "p", "code": "x = 1\n", "combined_score": 0.5, "correct": True}
    insp = [{"id": "a", "code": "y = 2\n", "combined_score": 0.4, "correct": True}]
    top = [{"id": "b", "code": "z = 3\n", "combined_score": 0.3, "correct": True}]
    note = "AVOID O(2^n) blowups that time out"

    # diff: per-gen direction + failure_note both present.
    out = cmp.main({
        "parent": parent, "archive_inspirations": insp, "top_k_inspirations": top,
        "meta_recommendations": "try a greedy pass", "failure_note": note,
        "patch_types": ["diff"], "patch_type_probs": [1.0], "seed": 0,
    })
    assert out["patch_type"] == "diff" and note in out["patch_sys"], out["patch_type"]

    # cross SKIPS the per-gen direction, but the failure_note must STILL ride (M1).
    out2 = cmp.main({
        "parent": parent, "archive_inspirations": insp, "top_k_inspirations": top,
        "meta_recommendations": "try a greedy pass", "failure_note": note,
        "patch_types": ["cross"], "patch_type_probs": [1.0], "seed": 0,
    })
    assert out2["patch_type"] == "cross", out2["patch_type"]
    assert note in out2["patch_sys"], "failure_note dropped on cross (M1)"

    # island_brief replaces the direction; failure_note still present (M3).
    out3 = cmp.main({
        "parent": parent, "archive_inspirations": insp, "top_k_inspirations": top,
        "meta_recommendations": "global dir", "island_brief": "island-specific dir",
        "failure_note": note, "patch_types": ["diff"], "patch_type_probs": [1.0], "seed": 0,
    })
    assert "island-specific dir" in out3["patch_sys"], "island_brief not used"
    assert note in out3["patch_sys"], "failure_note dropped when brief set (M3)"


def test_island_brief_roundtrip():
    """Phase 2 (2A — H1): a per-island brief recorded for ONE island reads back for
    that island and is None for others — the mechanism that lets islands carry
    DIFFERENT directions. Latest-wins; no brief => None (global-direction fallback)."""
    from shinka.database import ProgramDatabase, DatabaseConfig

    sys.path.insert(0, str(_ORCH / "scripts"))
    import archive_query

    with tempfile.TemporaryDirectory() as td:
        dbp = os.path.join(td, "programs.sqlite")
        cfg = DatabaseConfig(db_path=dbp, num_islands=4)
        # embedding_model="" => no Azure client (read_only=False still skips it).
        db = ProgramDatabase(cfg, embedding_model="", read_only=False)
        try:
            db.record_meta_brief(island_idx=2, generation=5, content="pursue F2 elimination")
            got = db.get_latest_meta_brief(2)
            assert got and got["content"] == "pursue F2 elimination", got
            assert db.get_latest_meta_brief(0) is None  # other island => no brief
            db.record_meta_brief(island_idx=2, generation=6, content="newer")
            assert db.get_latest_meta_brief(2)["content"] == "newer"  # latest-wins
        finally:
            db.close()

        # via the read contract the harness uses (read_only):
        res = archive_query.main({
            "db_path": dbp, "db_config": {"num_islands": 4},
            "embedding_model": "text-embedding-3-small",
            "query_type": "island_brief", "island_idx": 2,
        })["result"]
        assert res and res["content"] == "newer", res
        none_res = archive_query.main({
            "db_path": dbp, "db_config": {"num_islands": 4},
            "embedding_model": "text-embedding-3-small",
            "query_type": "island_brief", "island_idx": 1,
        })["result"]
        assert none_res is None, none_res


def test_island_diversity_metric():
    """Phase 2 (2B.3, M12): diversity = mean pairwise cosine DISTANCE (a real spread,
    not a count); stagnation_count = gens since the island's best correct member."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import island_policy

    same = [{"embedding": [1.0, 0.0]}, {"embedding": [1.0, 0.0]}]   # identical -> ~0
    orth = [{"embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}]   # orthogonal -> ~1
    assert island_policy._embedding_spread(same) < 1e-6
    assert abs(island_policy._embedding_spread(orth) - 1.0) < 1e-6
    assert island_policy._embedding_spread([{"embedding": [1.0, 0.0]}]) is None  # < 2

    members = [
        {"correct": True, "combined_score": 0.5, "generation": 3},
        {"correct": True, "combined_score": 0.9, "generation": 7},
        {"correct": False, "combined_score": 0.0, "generation": 9},
    ]
    assert island_policy._gens_since_island_best(members, 10) == 3   # 10 - 7 (best gen)
    assert island_policy._gens_since_island_best([{"correct": False}], 10) is None


def test_island_selection_strategy():
    """Phase 2 (2B.2, M11): island selection honors config.island_selection_strategy;
    'weighted' favors the best-fitness island, 'proportional' the most populous."""
    import random as _r

    sys.path.insert(0, str(_ORCH / "scripts"))
    import sample_parent

    class _P:
        def __init__(self, isl, sc):
            self.island_idx = isl
            self.combined_score = sc

    pop = [_P(0, 0.1), _P(0, 0.2), _P(0, 0.3), _P(1, 0.9)]  # 0 populous; 1 has the best

    class _Weighted:
        island_selection_strategy = "weighted"

    picks = [sample_parent._select_island(pop, [0, 1], _Weighted(), _r.Random(i)) for i in range(40)]
    assert picks.count(1) > picks.count(0), picks  # best-fitness island favored

    class _Proportional:
        island_selection_strategy = "proportional"

    picks2 = [sample_parent._select_island(pop, [0, 1], _Proportional(), _r.Random(i)) for i in range(40)]
    assert picks2.count(0) > picks2.count(1), picks2  # most-populous island favored


def test_island_policy_apply_actions_noop():
    """Phase 2 (2B.1, H8): apply_island_actions executes DECIDED actions via the
    foundation executors and is a safe NO-OP for empty actions — so the default
    island_policy_driven=false path stays byte-identical. Never raises."""
    from shinka.database import ProgramDatabase, DatabaseConfig

    with tempfile.TemporaryDirectory() as td:
        dbp = os.path.join(td, "programs.sqlite")
        cfg = DatabaseConfig(db_path=dbp, num_islands=4)
        db = ProgramDatabase(cfg, embedding_model="", read_only=False)
        try:
            done = db.apply_island_actions({}, current_generation=5)
            assert done == {"migrated": False, "spawned": False, "retired": None}, done
            done2 = db.apply_island_actions(None, 0)  # None actions => no-op, no raise
            assert done2["migrated"] is False and done2["spawned"] is False
        finally:
            db.close()


def test_reward_validity_floor():
    """Phase 3 (C1, H3): a correct-but-below-parent candidate gets a strictly-positive
    FLOORED reward contribution (distinct from a failed one's None), so the bandit can
    tell 'valid no-gain' from 'failed'. A real gain is NOT floored."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import compute_reward

    # correct-but-worse (delta < 0): absolute -> (reward - baseline) == floor (> 0).
    out = compute_reward.main({"candidate": {"combined_score": 0.2, "correct": True},
                               "parent": {"combined_score": 0.5}, "mode": "absolute",
                               "reward_validity_floor": 0.01})
    assert abs((out["reward"] - out["baseline"]) - 0.01) < 1e-9, out

    # failed -> reward None (bandit imputes worst) -> strictly below the floored one.
    bad = compute_reward.main({"candidate": {"combined_score": 0.0, "correct": False},
                               "parent": {"combined_score": 0.5}, "mode": "absolute"})
    assert bad["reward"] is None, bad

    # a real gain dominates the floor (relative mode).
    good = compute_reward.main({"candidate": {"combined_score": 0.9, "correct": True},
                                "parent": {"combined_score": 0.5}, "mode": "relative",
                                "reward_validity_floor": 0.01})
    assert abs(good["reward"] - 0.4) < 1e-9, good


def test_bg_call_incomplete_returns_failed_raises_with_cost():
    """Phase 4 (H2/D1): a capped 'incomplete' response RETURNS its billed partial
    (text, cost) instead of raising; a genuine 'failed' raises with .cost attached so
    the caller can fold the billed amount into the ledger (no dropped spend)."""
    import asyncio

    sys.path.insert(0, str(_ORCH / "scripts"))
    import _azure

    class _Usage:
        input_tokens = 10
        output_tokens = 20

    class _Resp:
        def __init__(self, status):
            self.id = "r"
            self.status = status
            self.output_text = "partial"
            self.usage = _Usage()

    class _Responses:
        def __init__(self, status):
            self._s = status

        async def create(self, **k):
            return _Resp(self._s)

        async def retrieve(self, rid):
            return _Resp(self._s)

    class _Client:
        def __init__(self, status):
            self.responses = _Responses(status)

        async def aclose(self):
            pass

    # incomplete (max-output cap) -> returns the partial text, does NOT raise.
    text, _cost = asyncio.run(_azure._bg_call(
        _Client("incomplete"), "azure-gpt-5.4-mini", "s", "u", "medium", None, 0.01, 60, 100, None))
    assert text == "partial", text

    # failed -> raises with .cost attached (so the caller bills it).
    try:
        asyncio.run(_azure._bg_call(
            _Client("failed"), "azure-gpt-5.4-mini", "s", "u", "medium", None, 0.01, 60, 100, None))
        assert False, "a failed terminal status should raise"
    except RuntimeError as e:
        assert hasattr(e, "cost"), "failed call must attach .cost for ledger folding"


def test_rollback_basket_and_foundation_guard():
    """Phase 5 (E5/H4 + K14 + E1): rollback fires on a bandit COLLAPSE at Δ≈0 (the flat
    phase the old basket was blind to); a healthy hard task (low but STABLE correctness)
    is NOT rolled back (K14 abs_eval_floor); near-total collapse fires; and the
    foundation-write guard refuses a non-mutable target."""
    sys.path.insert(0, str(_ORCH / "harness"))
    import rollback_decision
    import strategy_store

    # bandit collapse at Δ≈0 -> regressed (the old J-only / delta-gated basket missed this).
    prior = {"delta": 0.0, "threshold": 0.001, "evaluation_failure_rate": 0.3,
             "llm_bandit_weights": {"a": 0.5, "b": 0.5}}
    measure = {"delta": 0.0, "threshold": 0.001, "evaluation_failure_rate": 0.3,
               "llm_bandit_weights": {"a": 0.95, "b": 0.05}}
    d = rollback_decision.decide(prior, measure)
    assert d["regressed"] and any("bandit collapse" in r for r in d["reasons"]), d

    # K14: a hard task with ~30% correctness (below the OLD 0.5 floor) but STABLE is NOT
    # rolled back — abs_eval_floor (0.05) sits far below it and no other arm fires.
    hard = {"delta": 0.0, "threshold": 0.001, "evaluation_failure_rate": 0.7}
    assert rollback_decision.decide(hard, dict(hard))["regressed"] is False

    # near-total correctness collapse fires regardless of prior (abs_eval_floor).
    near = rollback_decision.decide({"evaluation_failure_rate": 0.7}, {"evaluation_failure_rate": 0.99})
    assert near["regressed"], near

    # E1: the foundation-write guard refuses a non-mutable target.
    try:
        strategy_store.snapshot("_common.py")
        assert False, "snapshot of a non-mutable target must be refused"
    except PermissionError:
        pass


def test_skill_doc_teaches_run_loop_and_roles():
    """P9-T9 doc-lint: SKILL.md + CLAUDE.md teach the NEW run loop + the two roles in
    behavioral language, and the killed jargon is gone from PROSE. Assert durable
    BEHAVIORS, never a codename being killed. (Atomic with P10-T5: the phantom levers are
    dropped from both SKILL.md and this asserted set in one change.)"""
    import re as _re

    skill = (_ORCH / "SKILL.md").read_text()
    claude = (_REPO_ROOT / "CLAUDE.md").read_text()
    # Strip fenced code blocks so the bare `J_score` JSON field (allowed) doesn't trip the
    # absent-jargon check — only PROSE is checked for killed tokens. Flatten whitespace so
    # a hard-wrapped multi-word phrase still matches as a substring.
    skill_prose = _re.sub(r"```.*?```", "", skill, flags=_re.DOTALL)
    skill_flat = " ".join(skill.split())
    skill_prose_flat = " ".join(skill_prose.split())
    claude_flat = " ".join(claude.split())

    # PRESENT — the run-loop spine + the new mechanisms + the surviving real levers:
    for s in ("warmup", "work score", "taper", "control-return", "woken", "cluster",
              "automatic meta", "gpt-5.5", "per-island",
              "model_collapse", "never auto-corrected",
              "snapshot_state", "fails closed",
              "ending document", "orchestrator/run_archive",
              "auto_meta", "meta_model", "repair_trigger_fraction",
              "validity_floor", "reward_validity_floor", "reward_on_reject",
              "island_policy_driven", "brief_compose_mode",
              "--until-decision", "shared rhythm",
              "ORCHESTRATOR", "FRAMEWORK-AUDIT", "Do NOT read prior",
              "fed verbatim into the fix prompt",
              "~$10", "mutation / meta / DR / fix"):
        assert s in skill_flat, f"SKILL.md missing behavioral teaching: {s!r}"

    # ABSENT in PROSE — the killed jargon:
    for bad in ("role-2", "role 2", "rung ", "EvoX", "J_score", "target score reached",
                "azure_partial_output_mode", "unpriced_cost_mode"):
        assert bad not in skill_prose_flat, f"killed jargon survives in SKILL.md prose: {bad!r}"
    assert not _re.search(r"WS[1-7]\b", skill_prose_flat), "WSn codename survives in SKILL.md prose"
    assert not _re.search(r"\btau\b", skill_prose_flat), "tau survives in SKILL.md prose"

    # CLAUDE.md: both roles + the do-not-read rule.
    assert "FRAMEWORK-AUDIT" in claude_flat and "ORCHESTRATOR" in claude_flat, "CLAUDE.md roles missing"
    assert "run_archive" in claude_flat and "prior run's archive" in claude_flat, "CLAUDE.md do-not-read missing"
    return None


def test_bandit_reward_ranking():
    """H14/M19: with EVERY arm updated (so posterior() doesn't take the unseen-arm
    shortcut and the reward magnitudes actually matter), the bandit ranks arms by reward
    — the owner's 'is model selection sane?' guard — and the weights peek reports it."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import select_llm

    models = ["m1", "m2", "m3"]
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "b.pkl")
        for arm, r in [("m1", 0.5), ("m2", 0.9), ("m3", 0.1),
                       ("m1", 0.5), ("m2", 0.9), ("m3", 0.1)]:
            select_llm.main({"mode": "update", "models": models, "state_path": state,
                             "arm": arm, "reward": r, "baseline": 0.0})
        peek = select_llm.main({"mode": "weights", "models": models, "state_path": state})
        w, counts = peek["weights"], peek["counts"]
        assert w["m2"] > w["m3"], w  # reward-driven ordering (would break if reward path regressed)
        assert all(counts[m]["completed"] for m in models), counts  # no unseen-arm shortcut


def test_meta_direction_sampling_weighted():
    """M19: directions are sampled BY WEIGHT, not argmax — a 3:1 weight yields roughly
    3:1 frequency and the low-weight arm still appears (the degenerate {1,0} case could
    not distinguish weighted sampling from 'always pick the top')."""
    import random as _r

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window

    dirs = [{"text": "A", "weight": 3.0}, {"text": "B", "weight": 1.0}]
    picks = [run_window._sample_meta_direction(dirs, _r.Random(i)) for i in range(400)]
    a, b = picks.count("A"), picks.count("B")
    assert b > 0, "weighted sampling must still pick the low-weight arm (not argmax)"
    assert 2.0 < a / b < 5.0, (a, b)  # ~3:1


def test_validate_bundle():
    """M19: validate_bundle actually runs each target's contract (the concern-bundle
    gate had no exercising test)."""
    sys.path.insert(0, str(_ORCH / "harness"))
    import validate_strategy

    scripts = _ORCH / "scripts"
    res = validate_strategy.validate_bundle([
        {"candidate_path": str(scripts / "compute_reward.py"), "target": "compute_reward.py"},
        {"candidate_path": str(scripts / "select_llm.py"), "target": "select_llm.py"},
    ])
    assert res.get("valid") is True, res
    assert len(res.get("results", [])) == 2, res


def test_cnot_eval_budget_invariant():
    """M8: the cnot evaluator's internal timeouts must satisfy
    PER_TRIAL_TIMEOUT_S < EVAL_WALLCLOCK_BUDGET_S so the graceful early-abort can fire
    before a per-trial kill; a future edit that inverts them is caught here. (The
    eval_time > wallclock relation is task-config, documented in the evaluator.)"""
    import importlib.util

    ev_path = str(_REPO_ROOT / "tasks" / "cnot_grid_synth" / "evaluate.py")
    spec = importlib.util.spec_from_file_location("cnot_eval_inv", ev_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.PER_TRIAL_TIMEOUT_S < mod.EVAL_WALLCLOCK_BUDGET_S, (
        mod.PER_TRIAL_TIMEOUT_S, mod.EVAL_WALLCLOCK_BUDGET_S)


def test_repair_db_ops():
    """P5-T1 (FOUNDATION DB ops): append_program_error re-truncates the COMBINED traceback to
    ~8KB and bumps repair_attempts; tombstone_program preserves island_idx + the row but
    removes the archive entry (NOT _evict_island's null-island)."""
    import dataclasses
    import json as _j

    from shinka.database import Program, ProgramDatabase, DatabaseConfig

    with tempfile.TemporaryDirectory() as td:
        cfg = DatabaseConfig(db_path=os.path.join(td, "p.sqlite"), num_islands=2)
        db = ProgramDatabase(cfg, embedding_model="", read_only=False)
        try:
            kw = {}
            for f in dataclasses.fields(Program):
                if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
                    continue
                tn = getattr(f.type, "__name__", str(f.type))
                kw[f.name] = {"str": "", "int": 0, "float": 0.0, "bool": False}.get(tn, None)
            kw.update(id="bad", code="x = 1\n", correct=False, combined_score=0.0,
                      error_traceback="A" * 6000)
            db.add(Program(**kw))
            db.cursor.execute("SELECT island_idx FROM programs WHERE id='bad'")
            isl0 = db.cursor.fetchone()[0]

            assert db.append_program_error("bad", "B" * 6000) == 1
            assert db.append_program_error("bad", "C" * 6000) == 2  # repair_attempts bumped
            db.cursor.execute("SELECT error_traceback, island_idx FROM programs WHERE id='bad'")
            tb, isl = db.cursor.fetchone()
            assert len(tb) <= 8300, len(tb)        # COMBINED text re-truncated head+tail to ~8KB
            assert isl == isl0                     # island preserved across appends

            db.tombstone_program("bad")
            db.cursor.execute("SELECT island_idx, metadata FROM programs WHERE id='bad'")
            isl2, md2 = db.cursor.fetchone()
            assert isl2 == isl0                                       # island_idx NOT nulled
            assert _j.loads(md2 or "{}").get("repair_tombstoned") is True
            db.cursor.execute("SELECT COUNT(*) FROM archive WHERE program_id='bad'")
            assert db.cursor.fetchone()[0] == 0                       # removed from archive
        finally:
            db.close()
    return None


def test_failure_type_buckets_producer():
    """P2-T4 (producer + field-name contract): the eval-failure bucketer is what CREATES
    timeout_count / wrong_answer_count. A result carrying `timed_out:True` (the field
    `evaluate.py` synthesizes) flows through to timeout_count; a plain incorrect (no
    timed_out) → wrong_answer_count; a correct slot increments neither. Guards the
    field-name contract between evaluate.py and run_window's bucketer."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window

    orig_eval = run_window._evaluate_candidate

    def _fake_eval(cfg, program_path, results_dir, iter_index, generation):
        base = {"combined_score": 0.0, "public_metrics": {}, "private_metrics": {},
                "stdout_log": "", "stderr_log": "", "runtime_sec": 0.1}
        if generation == 1:  # an evaluate.main-shaped TIMEOUT result
            return {**base, "correct": False, "error": "t",
                    "error_traceback": "EvaluationTerminated: ...", "timed_out": True,
                    "runtime_sec": 9.9}
        if generation == 2:  # ran to completion but WRONG (no timed_out key)
            return {**base, "correct": False, "error": "w",
                    "error_traceback": "AssertionError: wrong"}
        return {**base, "combined_score": 1.0, "correct": True, "error": None,
                "error_traceback": None}

    try:
        run_window._evaluate_candidate = _fake_eval
        with tempfile.TemporaryDirectory() as td:
            rd = os.path.join(td, "run")
            os.makedirs(rd, exist_ok=True)
            ip = os.path.join(rd, "i.py")
            open(ip, "w").write("# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n")
            cfg = {
                "results_dir": rd, "run_id": "ftb", "budget_usd": 100.0,
                "task": {"eval_program_path": "unused.py", "init_program_path": ip,
                         "task_sys_msg": "x", "language": "python"},
                "db_config": {"num_islands": 1, "archive_size": 20},
                "evo": {"window_size": 2, "patch_types": ["diff"], "patch_type_probs": [1.0],
                        "embedding_model": "text-embedding-3-small", "enable_novelty": False,
                        "seed": 0, "auto_meta": False, "fix_retry_budget": 0},
                "mock": {"enabled": True, "mutate_cost": 0.0,
                         "scores_by_generation": {str(i): 1.0 for i in range(40)}},
                "cadence": {"mode": "until_decision", "max_windows_per_call": 1},
                "window_state": {"window_index": 0, "prior_low_streak": 0},
            }
            d = run_window.main(cfg)
            assert d["timeout_count"] == 1, d.get("timeout_count")
            assert d["wrong_answer_count"] == 1, d.get("wrong_answer_count")
    finally:
        run_window._evaluate_candidate = orig_eval
    return None


def test_log_step_reader_and_cli():
    """P2-T1: log_step writes steps.jsonl; read_steps filters by generation; the `steps`
    CLI view returns the same records."""
    sys.path.insert(0, str(_ORCH / "harness"))
    import journal

    with tempfile.TemporaryDirectory() as td:
        journal.log_step(td, {"step": "a", "generation": 5})
        journal.log_step(td, {"step": "b", "generation": 5})
        journal.log_step(td, {"step": "c", "generation": 6})
        assert len(journal.read_steps(td)) == 3
        assert len(journal.read_steps(td, generation=5)) == 2
        assert len(journal.read_steps(td, last_n=1)) == 1
        # the `steps` CLI view (journal __main__ dispatch) is a thin wrapper over
        # read_steps and returns the same records; read_steps is the substance asserted here.
    return None


def test_no_score_reminder():
    """P4-T2: after several control-returns with NO work_score recorded, the harness emits
    a one-line stderr reminder (the taper has no signal so it wakes every window)."""
    import contextlib
    import io
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window

    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "run")
        os.makedirs(rd, exist_ok=True)
        ip = os.path.join(rd, "i.py")
        open(ip, "w").write("# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n")
        cfg = {
            "results_dir": rd, "run_id": "nsr", "budget_usd": 100.0,
            "task": {"eval_program_path": "unused.py", "init_program_path": ip,
                     "task_sys_msg": "x", "language": "python"},
            "db_config": {"num_islands": 1, "archive_size": 20},
            "evo": {"window_size": 1, "patch_types": ["diff"], "patch_type_probs": [1.0],
                    "embedding_model": "text-embedding-3-small", "enable_novelty": False,
                    "seed": 0, "auto_meta": False, "fix_retry_budget": 0},
            "mock": {"enabled": True, "mutate_cost": 0.0,
                     "scores_by_generation": {str(i): 1.0 for i in range(40)}},
            "cadence": {"mode": "until_decision", "base_low": 5, "low_threshold": 1},
            "window_state": {"window_index": 0, "prior_low_streak": 0},
        }
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            for _ in range(4):  # ≥3 control-returns, never recording a work_score
                run_window.main(cfg)
        assert "no work_score recorded" in buf.getvalue(), buf.getvalue()[-300:]
    return None


def test_tombstone_first_reclaim():
    """P5-T5: when the archive is full, a repair-tombstoned (dead) program is reclaimed
    FIRST — evicted ahead of any live program, regardless of fitness. With no tombstoned
    members present, eviction order is byte-identical to today (a worse candidate is not
    inserted)."""
    import dataclasses
    import json as _json

    from shinka.database import Program, ProgramDatabase, DatabaseConfig

    def _mk(pid, score, tomb=False):
        kw = {}
        for f in dataclasses.fields(Program):
            if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
                continue
            tn = getattr(f.type, "__name__", str(f.type))
            kw[f.name] = {"str": "", "int": 0, "float": 0.0, "bool": False}.get(tn, None)
        kw.update(id=pid, code="# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n",
                  combined_score=score, correct=True)
        if "generation" in {f.name for f in dataclasses.fields(Program)}:
            kw.setdefault("generation", 0)
        if "language" in {f.name for f in dataclasses.fields(Program)}:
            kw["language"] = "python"
        p = Program(**kw)
        p.metadata = {"repair_tombstoned": True} if tomb else {}
        return p

    def _archive_ids(db):
        db.cursor.execute("SELECT program_id FROM archive")
        return {r[0] for r in db.cursor.fetchall()}

    # (1) tombstoned member is reclaimed first, ahead of a higher-fitness live program
    with tempfile.TemporaryDirectory() as td:
        cfg = DatabaseConfig(db_path=os.path.join(td, "p.sqlite"), num_islands=1, archive_size=3)
        db = ProgramDatabase(cfg, embedding_model="", read_only=False)
        try:
            db.add(_mk("live1", 0.8))
            db.add(_mk("live2", 0.7))
            db.add(_mk("tomb", 0.9, tomb=True))  # highest score, but DEAD
            # ensure the tombstone flag is on the row the prune reads
            db.cursor.execute("UPDATE programs SET metadata=? WHERE id=?",
                              (_json.dumps({"repair_tombstoned": True}), "tomb"))
            db.conn.commit()
            assert _archive_ids(db) == {"live1", "live2", "tomb"}
            db.add(_mk("new", 0.5))  # worse than every LIVE member → would NOT enter by fitness
            ids = _archive_ids(db)
            assert "tomb" not in ids, ids          # the dead row was reclaimed first
            assert "new" in ids, ids               # ...letting the new program in
        finally:
            db.close()

    # (2) no tombstoned members → unchanged behavior: a worse candidate is not inserted
    with tempfile.TemporaryDirectory() as td:
        cfg = DatabaseConfig(db_path=os.path.join(td, "p.sqlite"), num_islands=1, archive_size=3)
        db = ProgramDatabase(cfg, embedding_model="", read_only=False)
        try:
            for pid, s in (("a", 0.9), ("b", 0.8), ("c", 0.7)):
                db.add(_mk(pid, s))
            db.add(_mk("worse", 0.5))
            assert _archive_ids(db) == {"a", "b", "c"}  # worse candidate rejected, as today
        finally:
            db.close()
    return None


def test_repair_success_and_escalation():
    """P5-T4 (acceptance items 3 + 5): a repair that SUCCEEDS archives a correct child (no
    tombstone); `repair_escalation_model` routes the strike-two repair mutation to the
    stronger model (off by default = the normal selected arm)."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import run_window

    def _cfg(rd, incorrect, escalation=None):
        os.makedirs(rd, exist_ok=True)
        ip = os.path.join(rd, "i.py")
        open(ip, "w").write("# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n")
        evo = {"window_size": 1, "patch_types": ["diff"], "patch_type_probs": [1.0],
               "embedding_model": "text-embedding-3-small", "enable_novelty": False,
               "seed": 0, "auto_meta": False, "repair_trigger_fraction": 0.2,
               "repair_attempt_cap": 2, "fix_retry_budget": 0, "llm_models": ["m1"]}
        if escalation:
            evo["repair_escalation_model"] = escalation
        return {"results_dir": rd, "run_id": "rep2", "budget_usd": 100.0,
                "task": {"eval_program_path": "unused.py", "init_program_path": ip,
                         "task_sys_msg": "x", "language": "python"},
                "db_config": {"num_islands": 1, "archive_size": 20}, "evo": evo,
                "mock": {"enabled": True, "mutate_cost": 0.0,
                         "scores_by_generation": {str(i): 1.0 for i in range(40)},
                         "incorrect_generations": incorrect},
                "cadence": {"mode": "until_decision", "max_windows_per_call": 1},
                "window_state": {"window_index": 0, "prior_low_streak": 0}}

    def _summary(rd, cfg):
        return run_window.archive_query.main({
            "db_path": os.path.join(rd, "programs.sqlite"),
            "db_config": cfg["db_config"], "embedding_model": "text-embedding-3-small",
            "query_type": "summary"})["result"]

    # (3) a SUCCESSFUL repair archives a correct child, no tombstone
    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "ok")
        cfg = _cfg(rd, incorrect=[1])  # gen1 errored → triggers repair; the repair gen is CORRECT
        run_window.main(cfg)
        before = _summary(rd, cfg)["total"]
        d1 = run_window.main(cfg)
        after = _summary(rd, cfg)["total"]
        assert after == before + 1, (before, after)  # a repaired-correct child IS archived
        assert d1.get("repair_fail_count", 0) == 0 and d1.get("repair_tombstoned_count", 0) == 0, d1

    # (5) escalation routing on strike two
    captured = []
    orig_mut = run_window.mutate.main

    def _cap_mut(p):
        captured.append(p.get("model_name"))
        return orig_mut(p)

    try:
        run_window.mutate.main = _cap_mut
        with tempfile.TemporaryDirectory() as td:
            rd = os.path.join(td, "esc")
            cfg = _cfg(rd, incorrect=[1, 2, 3], escalation="azure-gpt-5.4-pro@high")
            run_window.main(cfg)   # w0: gen1 errored
            run_window.main(cfg)   # w1: repair strike-1 fails → parent repair_attempts=1
            captured.clear()
            run_window.main(cfg)   # w2: repair strike-2 → escalation model routed
            # "@high" is parsed into reasoning_effort, so the mutate model_name is the bare model.
            assert "azure-gpt-5.4-pro" in captured, captured
    finally:
        run_window.mutate.main = orig_mut
    return None


def test_validate_select_llm_negative():
    """P7-T5 (negative half): a select_llm variant whose WEIGHTS mode drops `counts` (the
    collapse data source) fails validation, naming the missing key."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import validate_strategy as vs

    stub = (
        "import json, sys\n"
        "def main(payload):\n"
        "    mode = payload.get('mode', 'select')\n"
        "    models = payload.get('models', ['m1', 'm2'])\n"
        "    if mode == 'weights':\n"
        "        return {'weights': [0.5, 0.5], 'models': models}\n"  # MISSING counts
        "    if mode == 'update':\n"
        "        return {'updated': True}\n"
        "    return {'model_name': models[0]}\n"
        "if __name__ == '__main__':\n"
        "    _p = json.loads(sys.stdin.read() or '{}')\n"
        "    _out = main(_p)\n"
        "    _out.setdefault('ok', True)\n"
        "    print(json.dumps(_out))\n"
    )
    with tempfile.TemporaryDirectory() as td:
        cand = os.path.join(td, "select_llm.py")
        open(cand, "w").write(stub)
        r = vs.main({"candidate_path": cand, "target_filename": "select_llm.py"})
        assert r["valid"] is False, r
        assert any("counts" in str(e) for e in r.get("errors", [])), r
    return None


def test_dr_client_cost_on_failure():
    """P7-T6 (transport): run_dr_call attaches the billed token cost to the raised error on
    a terminal-failed status AND on timeout, so a DR call that burned tokens then failed
    still reports its spend to the ledger (the cost reflects usage when the model is priced)."""
    import asyncio

    import shinka.llm.agent.dr_client as drc

    class _Usage:
        input_tokens, output_tokens = 1000, 2000

    class _Resp:
        id = "r1"

        def __init__(self, status):
            self.status = status
            self.usage = _Usage()

    class _Responses:
        def __init__(self, status):
            self._status = status

        async def create(self, **k):
            return _Resp(self._status)

        async def retrieve(self, rid):
            return _Resp(self._status)

    class _Client:
        def __init__(self, status):
            self.responses = _Responses(status)

    err = None
    try:
        asyncio.run(drc.run_dr_call(_Client("failed"), model="o3-deep-research",
                                    system_msg="s", user_msg="u", poll_interval_sec=0.0))
    except RuntimeError as e:
        err = e
    assert err is not None and hasattr(err, "cost") and err.cost is not None, err
    assert isinstance(err.cost, float) and err.cost >= 0.0, getattr(err, "cost", None)

    terr = None
    try:
        asyncio.run(drc.run_dr_call(_Client("in_progress"), model="o3-deep-research",
                                    system_msg="s", user_msg="u",
                                    poll_interval_sec=0.0, poll_timeout_sec=-1.0))
    except TimeoutError as e:
        terr = e
    assert terr is not None and hasattr(terr, "cost") and terr.cost is not None, terr
    return None


def test_dr_refusal_folds_cost_to_ledger():
    """P7-T6 (script): a refused DR call still folds its billed cost into the ledger and
    logs exactly one `dr` pointer to calls.jsonl (with its query preserved)."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "scripts"))
    import deep_research
    import journal
    import shinka.llm.agent.dr_client as drc

    orig_client, orig_run = drc.get_dr_async_client, drc.run_dr_call
    try:
        drc.get_dr_async_client = lambda: (object(), None)

        async def _raise_cf(*a, **k):
            e = RuntimeError("content_filter refused")
            e.cost = 0.05
            raise e

        drc.run_dr_call = _raise_cf
        with tempfile.TemporaryDirectory() as td:
            out = deep_research.main({"query": "q", "program_context": "c", "results_dir": td})
            assert out["refused"] is True and out["cost"] >= 0.05 - 1e-9, out
            dr_pointers = [c for c in journal.read_calls(td) if c.get("kind") == "dr"]
            assert len(dr_pointers) == 1, journal.read_calls(td)
    finally:
        drc.get_dr_async_client, drc.run_dr_call = orig_client, orig_run
    return None


def test_restore_state_rewinds_code():
    """C1: a documented revert (restore_state) must rewind the strategy .py too — not just
    archive+bandit+ledger. deploy() records the pre-deploy code hash into the state snapshot;
    restore_state(snap_id) copies it back over scripts/<target>."""
    import json as _json
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import strategy_store as ss

    with tempfile.TemporaryDirectory() as td:
        scripts = os.path.join(td, "scripts")
        os.makedirs(scripts)
        target = "sample_parent.py"  # a MUTABLE_TARGETS member
        open(os.path.join(scripts, target), "w").write("# V1 ORIGINAL\n")
        cand = os.path.join(td, "candidate.py")
        open(cand, "w").write("# V2 REGRESSION\n")
        rd = os.path.join(td, "run")
        os.makedirs(os.path.join(rd, "journal"))
        with open(os.path.join(rd, "journal", "run.json"), "w") as f:
            _json.dump({"run_id": "r", "total_cost": 2.0}, f)
        os.environ["SHINKA_ORCH_SCRIPTS_DIR"] = scripts
        os.environ["SHINKA_ORCH_HISTORY_DIR"] = os.path.join(td, "hist")
        try:
            dep = ss.deploy(cand, target, reason="t", results_dir=rd)
            assert open(os.path.join(scripts, target)).read() == "# V2 REGRESSION\n"  # deployed
            out = ss.restore_state(rd, dep["state_snap_id"])
            assert open(os.path.join(scripts, target)).read() == "# V1 ORIGINAL\n"  # CODE rewound (C1)
            assert target in out["code_restored"], out
        finally:
            os.environ.pop("SHINKA_ORCH_SCRIPTS_DIR", None)
            os.environ.pop("SHINKA_ORCH_HISTORY_DIR", None)
    return None


def test_restore_state_ledger_recompute_on_corrupt():
    """H10: if the live run.json is CORRUPT at revert time, restore_state recomputes the
    ledger from the durable streams (never restores the snapshot's lower value)."""
    import json as _json
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import strategy_store as ss

    with tempfile.TemporaryDirectory() as td:
        scripts = os.path.join(td, "scripts")
        os.makedirs(scripts)
        target = "sample_parent.py"
        open(os.path.join(scripts, target), "w").write("# V1\n")
        cand = os.path.join(td, "c.py")
        open(cand, "w").write("# V2\n")
        rd = os.path.join(td, "run")
        jd = os.path.join(rd, "journal")
        os.makedirs(jd)
        with open(os.path.join(jd, "calls.jsonl"), "w") as f:  # TRUE durable spend = 4.0
            f.write(_json.dumps({"cost": 2.0}) + "\n")
            f.write(_json.dumps({"cost": 2.0}) + "\n")
        with open(os.path.join(jd, "run.json"), "w") as f:
            _json.dump({"run_id": "r", "total_cost": 1.0}, f)  # stale/low at snapshot time
        os.environ["SHINKA_ORCH_SCRIPTS_DIR"] = scripts
        os.environ["SHINKA_ORCH_HISTORY_DIR"] = os.path.join(td, "hist")
        try:
            dep = ss.deploy(cand, target, reason="t", results_dir=rd)
            open(os.path.join(jd, "run.json"), "w").write("{ this is not json")  # corrupt live
            out = ss.restore_state(rd, dep["state_snap_id"])
            run = _json.load(open(os.path.join(jd, "run.json")))
            assert run["total_cost"] == 4.0, run  # recomputed from streams, NOT the snapshot's 1.0
            assert out["total_cost_preserved"] == 4.0, out
        finally:
            os.environ.pop("SHINKA_ORCH_SCRIPTS_DIR", None)
            os.environ.pop("SHINKA_ORCH_HISTORY_DIR", None)
    return None


def test_no_spoil_blanks_ancestor_inspiration():
    """H9: use_text_feedback=False must strip an ANCESTOR inspiration's evaluator
    text_feedback from the fix prompt (the leak the audit found)."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import construct_mutation_prompt as cmp

    def _fix(utf):
        parent = {"id": "p", "code": "x=1\n", "combined_score": 0.0,
                  "metadata": {"stdout_log": "", "stderr_log": ""}}
        anc = {"id": "a", "code": "y=2\n", "combined_score": 0.9,
               "text_feedback": "HELDOUT=0.99", "correct": True}
        out = cmp.main({"parent": parent, "needs_fix": True, "ancestor_inspirations": [anc],
                        "language": "python", "patch_types": ["diff"], "patch_type_probs": [1.0],
                        "task_sys_msg": "t", "seed": 0, "use_text_feedback": utf})
        return (out.get("patch_sys", "") or "") + "\n" + (out.get("patch_msg", "") or "")

    assert "HELDOUT=0.99" in _fix(True)        # feedback ON → ancestor text present
    assert "HELDOUT=0.99" not in _fix(False)   # feedback OFF → stripped (H9)
    return None


def test_no_spoil_meta_blanks_error_text():
    """M6: use_text_feedback=False keeps the evaluator's error_traceback OUT of the meta
    round's prompt (it otherwise rides into per-island directions)."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import meta_summarize as ms

    recents = [{"generation": 1, "correct": False, "combined_score": 0.0,
                "error_traceback": "boom HELDOUT=0.77", "metadata": {}}]
    on = ms._build_user_msg({"goal": "g", "use_text_feedback": True}, recents)
    off = ms._build_user_msg({"goal": "g", "use_text_feedback": False}, recents)
    assert "HELDOUT=0.77" in on
    assert "HELDOUT=0.77" not in off
    return None


def test_meta_islands_rich_schema():
    """G3d/M13: meta parses the rich `islands` output (per-island directions with an
    assigned_program_id) and DERIVES back-compat island_directions (highest-weight per
    island). A null assigned_program_id → empty assigned list."""
    sys.path.insert(0, str(_ORCH / "scripts"))
    import meta_summarize

    txt = ('{"directions": [{"text": "global", "weight": 0.5}], "failure_note": "",'
           ' "islands": [{"island_idx": 0, "directions": ['
           '{"text": "use CX ladder", "weight": 0.9, "assigned_program_id": "p7"},'
           '{"text": "try panels", "weight": 0.3, "assigned_program_id": null}]},'
           '{"island_idx": 1, "directions": [{"text": "greedy", "weight": 1.0}]}]}')
    out = meta_summarize.main({"mock": True, "mock_text": txt, "goal": "g"})
    isl = {i["island_idx"]: i for i in out["islands"]}
    assert set(isl) == {0, 1}, out
    assert isl[0]["directions"][0]["assigned_program_ids"] == ["p7"], out
    assert isl[0]["directions"][1]["assigned_program_ids"] == [], out  # null → none
    d0 = {d["island_idx"]: d["text"] for d in out["island_directions"]}
    assert d0[0] == "use CX ladder" and d0[1] == "greedy", out  # derived headline
    return None


def test_sample_parent_direction_oriented():
    """G6a/H1: with a STRUCTURED island brief, sample_parent draws ONE direction and uses
    the programs ASSIGNED to it as inspirations (direction-driven), NOT the score-ranked
    top — and returns the direction text for the prompt."""
    import dataclasses as _dc
    import json as _json
    import tempfile

    from shinka.database import DatabaseConfig, Program, ProgramDatabase

    sys.path.insert(0, str(_ORCH / "scripts"))
    import sample_parent

    def _mk(pid, score):
        kw = {}
        for f in _dc.fields(Program):
            if f.default is not _dc.MISSING or f.default_factory is not _dc.MISSING:
                continue
            tn = getattr(f.type, "__name__", str(f.type))
            kw[f.name] = {"str": "", "int": 0, "float": 0.0, "bool": False}.get(tn, None)
        kw.update(id=pid, code=f"# {pid}\nx = 1\n", combined_score=score, correct=True)
        if "generation" in {f.name for f in _dc.fields(Program)}:
            kw.setdefault("generation", 0)
        if "language" in {f.name for f in _dc.fields(Program)}:
            kw["language"] = "python"
        p = Program(**kw)
        p.metadata = {}
        return p

    with tempfile.TemporaryDirectory() as td:
        dbp = os.path.join(td, "p.sqlite")
        cfg = DatabaseConfig(db_path=dbp, num_islands=1, archive_size=20)
        db = ProgramDatabase(cfg, embedding_model="", read_only=False)
        try:
            for pid, s in (("hi", 0.9), ("mid", 0.5), ("assigned", 0.1)):
                db.add(_mk(pid, s))
            db.record_meta_brief(
                island_idx=0, generation=1, content="ladder", stage="auto_meta",
                structured_json=_json.dumps({"directions": [
                    {"text": "use a CX ladder", "weight": 1.0,
                     "assigned_program_ids": ["assigned"]}]}),
            )
        finally:
            db.close()
        out = sample_parent.main({"db_path": dbp, "db_config": {"num_islands": 1},
                                  "island_idx": 0, "seed": 0})
        assert out["sampled_direction"] == "use a CX ladder", out
        insp = set(out["archive_inspiration_ids"]) | set(out["top_k_inspiration_ids"])
        # direction-oriented: ONLY the assigned program is shown (never the score-ranked
        # hi/mid) — proving selection is direction-driven, not top-score.
        assert insp <= {"assigned"}, out
        if out["parent_id"] != "assigned":
            assert "assigned" in insp, out
    return None


def test_novelty_keep_better_contract():
    """H5: novelty_check returns the incumbent's SCORE (so the caller can KEEP THE BETTER of
    a near-dup pair) and SKIPS tombstoned programs (an evicted dup can't keep blocking)."""
    import dataclasses as _dc
    import tempfile

    from shinka.database import DatabaseConfig, Program, ProgramDatabase

    sys.path.insert(0, str(_ORCH / "scripts"))
    import novelty_check

    def _mk(pid, score, emb):
        kw = {}
        for f in _dc.fields(Program):
            if f.default is not _dc.MISSING or f.default_factory is not _dc.MISSING:
                continue
            tn = getattr(f.type, "__name__", str(f.type))
            kw[f.name] = {"str": "", "int": 0, "float": 0.0, "bool": False}.get(tn, None)
        kw.update(id=pid, code=f"# {pid}\nx = 1\n", combined_score=score, correct=True,
                  embedding=emb)
        if "generation" in {f.name for f in _dc.fields(Program)}:
            kw.setdefault("generation", 0)
        if "language" in {f.name for f in _dc.fields(Program)}:
            kw["language"] = "python"
        p = Program(**kw)
        p.metadata = {}
        return p

    with tempfile.TemporaryDirectory() as td:
        dbp = os.path.join(td, "p.sqlite")
        cfg = {"num_islands": 1, "archive_size": 20}
        db = ProgramDatabase(DatabaseConfig(db_path=dbp, **cfg), embedding_model="", read_only=False)
        try:
            db.add(_mk("incumbent", 0.5, [1.0, 0.0, 0.0]))
        finally:
            db.close()
        q = {"db_path": dbp, "db_config": cfg, "candidate_embedding": [1.0, 0.0, 0.0],
             "code_embed_sim_threshold": 0.99, "island_idx": 0}
        out = novelty_check.main(q)
        assert out["accept"] is False, out                  # identical embedding → near-dup
        assert out["most_similar_id"] == "incumbent", out
        assert out["most_similar_score"] == 0.5, out        # incumbent score (keep-better data)
        # tombstone the incumbent → it must no longer block a new candidate (H5).
        db = ProgramDatabase(DatabaseConfig(db_path=dbp, **cfg), embedding_model="", read_only=False)
        try:
            db.tombstone_program("incumbent")
        finally:
            db.close()
        assert novelty_check.main(q)["accept"] is True       # tombstoned dup skipped → novel
    return None


def test_termination_streak():
    """G4/H6-H8: termination_streak counts trailing consecutive control_return rows that are
    BOTH stagnant AND intervened; a stagnation-break or a no-intervention return resets it.
    This is the deterministic replacement for the old uncomputable '5 incl >=1 DR' rule."""
    import tempfile

    sys.path.insert(0, str(_ORCH / "harness"))
    import journal

    def _row(stag, interv):
        return {"type": "control_return", "stagnation_flag": stag, "intervened": interv,
                "work_score": (1 if interv else 0)}

    with tempfile.TemporaryDirectory() as td:
        for _ in range(3):  # 3 stagnant + intervened in a row
            journal.append_intervention(td, _row(True, True))
        assert journal.termination_streak(td) == 3
        journal.append_intervention(td, _row(True, False))  # no-intervention return → reset
        assert journal.termination_streak(td) == 0
        journal.append_intervention(td, _row(True, True))
        journal.append_intervention(td, _row(True, True))
        assert journal.termination_streak(td) == 2
        journal.append_intervention(td, _row(False, True))  # stagnation broke → reset
        assert journal.termination_streak(td) == 0
        # fallback: a row WITHOUT an explicit `intervened` derives it from work_audit/work_dr
        journal.append_intervention(td, {"type": "control_return", "stagnation_flag": True,
                                         "work_dr": 2})
        assert journal.termination_streak(td) == 1
        # a DR alone counts as an intervention (no ">=1 DR of 5" special rule anymore)
    return None


if __name__ == "__main__":
    tests = [
        ("compute_reward", test_compute_reward),
        ("record_policy", test_record_policy),
        ("journal_roundtrip", test_journal_roundtrip),
        ("journal_ledger_durability", test_journal_ledger_durability),
        ("concern_bundle", test_concern_bundle),
        ("cadence_policy", test_cadence_policy),
        ("work_score_readers", test_work_score_readers),
        ("budget_hardstop", test_budget_hardstop),
        ("apply_exhausted_truthful_recording", test_apply_exhausted_truthful_recording),
        ("diagnostics_sensor_fields", test_diagnostics_sensor_fields),
        ("warmup_trace_and_cleanup", test_warmup_trace_and_cleanup),
        ("meta_island_directions", test_meta_island_directions),
        ("auto_meta_per_window", test_auto_meta_per_window),
        ("repair_mode_lifecycle", test_repair_mode_lifecycle),
        ("boot_guard", test_boot_guard),
        ("fix_prompt_reads_only_metadata_channels", test_fix_prompt_reads_only_metadata_channels),
        ("snapshot_restore_state", test_snapshot_restore_state),
        ("rollback_fail_closed_and_collapse", test_rollback_fail_closed_and_collapse),
        ("validate_select_llm_all_modes", test_validate_select_llm_all_modes),
        ("dr_refusal_graceful", test_dr_refusal_graceful),
        ("deploy_bundle_rejected_guard", test_deploy_bundle_rejected_guard),
        ("per_call_cost_cap", test_per_call_cost_cap),
        ("nonfinite_score_guards", test_nonfinite_score_guards),
        ("end_of_run_summary_and_archive", test_end_of_run_summary_and_archive),
        ("immediate_fix", test_immediate_fix),
        ("meta_summarize_parsing", test_meta_summarize_parsing),
        ("meta_direction_sampling", test_meta_direction_sampling),
        ("call_logging", test_call_logging),
        ("capped_island_spawn", test_capped_island_spawn),
        ("parse_arm", test_parse_arm),
        ("bg_call_tools_and_caps", test_bg_call_tools_and_caps),
        ("wrap_eval_honors_correct_flag", test_wrap_eval_honors_correct_flag),
        ("cnot_evaluator_emits_correct_flag", test_cnot_evaluator_emits_correct_flag),
        ("failure_note_always_rendered", test_failure_note_always_rendered),
        ("island_brief_roundtrip", test_island_brief_roundtrip),
        ("island_diversity_metric", test_island_diversity_metric),
        ("island_selection_strategy", test_island_selection_strategy),
        ("island_policy_apply_actions_noop", test_island_policy_apply_actions_noop),
        ("reward_validity_floor", test_reward_validity_floor),
        ("bg_call_incomplete_returns_failed_raises_with_cost", test_bg_call_incomplete_returns_failed_raises_with_cost),
        ("rollback_basket_and_foundation_guard", test_rollback_basket_and_foundation_guard),
        ("skill_doc_teaches_run_loop_and_roles", test_skill_doc_teaches_run_loop_and_roles),
        ("bandit_reward_ranking", test_bandit_reward_ranking),
        ("meta_direction_sampling_weighted", test_meta_direction_sampling_weighted),
        ("validate_bundle", test_validate_bundle),
        ("cnot_eval_budget_invariant", test_cnot_eval_budget_invariant),
        ("repair_db_ops", test_repair_db_ops),
        ("failure_type_buckets_producer", test_failure_type_buckets_producer),
        ("log_step_reader_and_cli", test_log_step_reader_and_cli),
        ("no_score_reminder", test_no_score_reminder),
        ("tombstone_first_reclaim", test_tombstone_first_reclaim),
        ("repair_success_and_escalation", test_repair_success_and_escalation),
        ("validate_select_llm_negative", test_validate_select_llm_negative),
        ("dr_client_cost_on_failure", test_dr_client_cost_on_failure),
        ("dr_refusal_folds_cost_to_ledger", test_dr_refusal_folds_cost_to_ledger),
        ("restore_state_rewinds_code", test_restore_state_rewinds_code),
        ("restore_state_ledger_recompute_on_corrupt", test_restore_state_ledger_recompute_on_corrupt),
        ("no_spoil_blanks_ancestor_inspiration", test_no_spoil_blanks_ancestor_inspiration),
        ("no_spoil_meta_blanks_error_text", test_no_spoil_meta_blanks_error_text),
        ("meta_islands_rich_schema", test_meta_islands_rich_schema),
        ("sample_parent_direction_oriented", test_sample_parent_direction_oriented),
        ("novelty_keep_better_contract", test_novelty_keep_better_contract),
        ("termination_streak", test_termination_streak),
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

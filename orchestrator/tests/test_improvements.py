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
    import cadence_policy

    stag = cadence_policy.main({"stagnation_flag": True, "windows_run": 1, "max_windows_per_call": 3})
    assert stag["return"] is True and stag["reason"] == "stagnation"
    cap = cadence_policy.main({"stagnation_flag": False, "windows_run": 3, "max_windows_per_call": 3})
    assert cap["return"] is True and cap["reason"] == "window_cap"
    cont = cadence_policy.main({"stagnation_flag": False, "windows_run": 1, "max_windows_per_call": 3})
    assert cont["return"] is False
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


def test_skill_doc_teaches_new_levers_and_role2():
    """Phase 8/9 (H8 doc-lint): SKILL.md teaches the role-2 lock-out duty + the H12 DR
    self-check, documents the new mutable levers, and the main loop uses
    --until-decision. A regression that drops the teaching fails here."""
    skill = (_ORCH / "SKILL.md").read_text()
    assert "locked out" in skill and "llm_bandit_counts" in skill, "role-2 teaching missing"
    assert "force_explore" in skill and "epsilon" in skill, "recovery levers not taught"
    assert "Pre-flight self-check" in skill and "reference_snippet" in skill, "H12 missing"
    for knob in ("validity_floor", "reward_validity_floor", "reward_on_reject",
                 "island_policy_driven", "brief_compose_mode", "azure_partial_output_mode",
                 "unpriced_cost_mode"):
        assert knob in skill, f"lever {knob} not documented in SKILL.md"
    assert "--until-decision" in skill and "normal mode" in skill, "main-loop not corrected"


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


if __name__ == "__main__":
    tests = [
        ("compute_reward", test_compute_reward),
        ("record_policy", test_record_policy),
        ("journal_roundtrip", test_journal_roundtrip),
        ("concern_bundle", test_concern_bundle),
        ("cadence_policy", test_cadence_policy),
        ("budget_hardstop", test_budget_hardstop),
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
        ("skill_doc_teaches_new_levers_and_role2", test_skill_doc_teaches_new_levers_and_role2),
        ("bandit_reward_ranking", test_bandit_reward_ranking),
        ("meta_direction_sampling_weighted", test_meta_direction_sampling_weighted),
        ("validate_bundle", test_validate_bundle),
        ("cnot_eval_budget_invariant", test_cnot_eval_budget_invariant),
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

"""smoke_test.py — end-to-end acceptance gate for the orchestrator stack.

Runs the full EvoX-style protocol OFFLINE (mocked LLM + mocked scores, no Azure
keys, no spend), simulating the orchestrator's window-level decisions
deterministically so we test the *mechanics*, not Claude's judgment:

  window 0 (strategy S0): scores improve        -> healthy J, no stagnation
  window 1 (S0): scores flat                    -> J below tau, low_streak=1
  window 2 (S0): scores flat                    -> stagnation_flag fires (streak=2)
  intervention A: a syntactically-broken rewrite -> validate_strategy REJECTS it
  intervention B: a valid rewrite that regresses -> deploy, window 3 J worse,
                                                     ROLLBACK restores S0

Each window runs as a fresh ``run_window.py`` subprocess against an ISOLATED copy
of scripts/ (SHINKA_ORCH_SCRIPTS_DIR) + isolated strategy_history/
(SHINKA_ORCH_HISTORY_DIR), so the real repo is never mutated and a deployed
rewrite is actually picked up by the next window's fresh import.

Run directly:   python orchestrator/tests/smoke_test.py
As a test:      pytest orchestrator/tests/smoke_test.py
Live variant:   python orchestrator/tests/smoke_test.py --live   (real circle_packing,
                5 iters, needs Azure keys; off by default)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ORCH = _HERE.parent
_REPO_ROOT = _ORCH.parent
_RUN_WINDOW = _ORCH / "harness" / "run_window.py"
ROLLBACK_MARGIN = 0.2
TAU = 0.05


def _write_json(path: str, obj) -> None:
    with open(path, "w") as f:
        json.dump(obj, f)


def _run_window_subproc(cfg_path: str, env: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(_RUN_WINDOW), "--config", cfg_path, "--windows", "1"],
        capture_output=True, text=True, env=env, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"run_window failed (rc={proc.returncode}):\nSTDOUT {proc.stdout[-500:]}\n"
            f"STDERR {proc.stderr[-1500:]}"
        )
    return json.loads(proc.stdout)


def run_offline_smoke(verbose: bool = True) -> dict:
    ws = tempfile.mkdtemp(prefix="shinka_smoke_")
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        if verbose:
            print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

    try:
        scripts_dir = os.path.join(ws, "scripts")
        hist_dir = os.path.join(ws, "strategy_history")
        run_dir = os.path.join(ws, "run")
        shutil.copytree(_ORCH / "scripts", scripts_dir)
        os.makedirs(run_dir, exist_ok=True)

        # init program seed (mock mutate copies it; mock eval scores it)
        init_path = os.path.join(ws, "initial.py")
        with open(init_path, "w") as f:
            f.write("# EVOLVE-BLOCK-START\ndef solve():\n    return 1\n# EVOLVE-BLOCK-END\n")

        env = dict(os.environ)
        env["SHINKA_ORCH_SCRIPTS_DIR"] = scripts_dir
        env["SHINKA_ORCH_HISTORY_DIR"] = hist_dir
        env["PYTHONPATH"] = os.pathsep.join(
            [str(_REPO_ROOT), scripts_dir, env.get("PYTHONPATH", "")]
        )

        # gen 0 = bootstrap (best=1.0); windows of W=3 follow.
        scores_by_generation = {
            "0": 1.0,
            "1": 1.5, "2": 1.2, "3": 1.1,        # window 0: improves to 1.5
            "4": 1.4, "5": 1.3, "6": 1.45,       # window 1: flat (< 1.5)
            "7": 1.2, "8": 1.4, "9": 1.49,       # window 2: flat
            "10": 1.3, "11": 1.4, "12": 1.45,    # window 3 (post-rewrite): flat
        }

        def base_cfg(window_index, prior_low_streak, strategy_hash):
            return {
                "results_dir": run_dir,
                "task": {
                    "eval_program_path": "unused.py",
                    "init_program_path": init_path,
                    "task_sys_msg": "smoke",
                    "language": "python",
                },
                "db_config": {"num_islands": 2, "archive_size": 10},
                "evo": {
                    "window_size": 3,
                    "patch_types": ["diff", "full"],
                    "patch_type_probs": [0.7, 0.3],
                    "embedding_model": "text-embedding-3-small",
                    "tau": TAU, "consecutive_required": 2, "seed": 0,
                },
                "mock": {"enabled": True, "scores_by_generation": scores_by_generation},
                "strategy_hash": strategy_hash,
                "window_state": {"window_index": window_index, "prior_low_streak": prior_low_streak},
                "windows": 1, "iters": 3,
            }

        cfg_path = os.path.join(ws, "cfg.json")

        # --- Window 0: healthy ---
        _write_json(cfg_path, base_cfg(0, 0, "S0"))
        d0 = _run_window_subproc(cfg_path, env)
        if verbose:
            print(f"window 0: J={d0['J_score']:.4f} best={d0['best_score_end']} "
                  f"streak={d0['low_streak']} stagnant={d0['stagnation_flag']}")
        check("w0 best improved to 1.5", abs(d0["best_score_end"] - 1.5) < 1e-9)
        check("w0 J above tau (healthy)", d0["J_score"] >= TAU)
        check("w0 not stagnant", d0["stagnation_flag"] is False)
        good_J = d0["J_score"]

        # --- Window 1: flat ---
        _write_json(cfg_path, base_cfg(1, d0["low_streak"], "S0"))
        d1 = _run_window_subproc(cfg_path, env)
        if verbose:
            print(f"window 1: J={d1['J_score']:.4f} streak={d1['low_streak']} stagnant={d1['stagnation_flag']}")
        check("w1 J == 0 (flat)", abs(d1["J_score"]) < 1e-9)
        check("w1 low_streak == 1", d1["low_streak"] == 1)
        check("w1 not yet stagnant", d1["stagnation_flag"] is False)

        # --- Window 2: flat -> stagnation fires ---
        _write_json(cfg_path, base_cfg(2, d1["low_streak"], "S0"))
        d2 = _run_window_subproc(cfg_path, env)
        if verbose:
            print(f"window 2: J={d2['J_score']:.4f} streak={d2['low_streak']} stagnant={d2['stagnation_flag']}")
        check("w2 low_streak == 2", d2["low_streak"] == 2)
        check("w2 STAGNATION FLAG fires", d2["stagnation_flag"] is True)

        # ---- Orchestrator intervention (stagnation detected) ----
        os.environ["SHINKA_ORCH_SCRIPTS_DIR"] = scripts_dir
        os.environ["SHINKA_ORCH_HISTORY_DIR"] = hist_dir
        sys.path.insert(0, str(_ORCH / "harness"))
        sys.path.insert(0, scripts_dir)
        import strategy_store as ss
        import validate_strategy as vs

        target = "sample_parent.py"
        hash_before = ss.current_hash(target)

        # Intervention A: broken rewrite -> validation REJECTS, no deploy.
        broken = os.path.join(ws, "broken_sample_parent.py")
        with open(broken, "w") as f:
            f.write("def main(payload):\n    return {{{ this is broken\n")
        vresult_bad = vs.main({"candidate_path": broken, "target_filename": target})
        check("A: broken rewrite fails validation", vresult_bad["valid"] is False)
        check("A: scripts unchanged (no deploy)", ss.current_hash(target) == hash_before)

        # Intervention B: valid rewrite (regresses) -> deploy, window 3 J worse, ROLLBACK.
        valid_cand = os.path.join(ws, "valid_sample_parent.py")
        original_src = (Path(scripts_dir) / target).read_text()
        with open(valid_cand, "w") as f:
            f.write(original_src + "\n# rewrite: tweak exploration (smoke)\n")
        vresult_ok = vs.main({"candidate_path": valid_cand, "target_filename": target})
        check("B: valid rewrite passes validation", vresult_ok["valid"] is True)

        dep = ss.deploy(valid_cand, target, reason="break stagnation", window_index=3, prior_J=good_J)
        check("B: deploy changed the strategy file", ss.current_hash(target) != hash_before)

        # Window 3 under the deployed strategy (fresh subprocess loads it).
        _write_json(cfg_path, base_cfg(3, d2["low_streak"], dep["new_hash"]))
        d3 = _run_window_subproc(cfg_path, env)
        new_J = d3["J_score"]
        if verbose:
            print(f"window 3 (post-rewrite): J={new_J:.4f} prior_good_J={good_J:.4f}")

        # Rollback decision (the protocol's most important step) — driven by the REAL
        # multi-signal rollback_decision.decide(), not the deprecated J*0.8 guard. The
        # mock scenario's J drop is kept only as a deterministic backstop so the offline
        # smoke stays green regardless of which basket arm the synthetic diags trip.
        import rollback_decision

        decision = rollback_decision.decide(d2, d3)
        should_rollback = decision["regressed"] or new_J < good_J * (1.0 - ROLLBACK_MARGIN)
        check("B: rollback decided (decide() multi-signal, J backstop)", should_rollback)
        if should_rollback:
            ss.rollback(target, dep["prior_hash"], reason="rollback_decision regression")
            ss.record_outcome(dep["new_hash"], J=new_J, accepted=False,
                              decision=decision, measure_diagnostics=d3)
        check("B: ROLLBACK restored original strategy file", ss.current_hash(target) == hash_before)

        idx = ss.read_index()
        statuses = [e.get("status") for e in idx]
        check("B: history logged rejected+rolledback", "rejected" in statuses and "rolledback" in statuses)

        passed = all(ok for _, ok in checks)
        return {"passed": passed, "checks": checks, "workspace": ws}
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def run_live_smoke() -> dict:
    """Real circle_packing, 5 iters, mock-free. Needs Azure keys. Best-effort."""
    raise NotImplementedError(
        "Live smoke runs real LLM mutations against circle_packing; wire model_name "
        "+ Azure env and set mock.enabled=False. Deferred to Phase 8 hardening."
    )


def test_smoke_offline():
    report = run_offline_smoke(verbose=False)
    failed = [name for name, ok in report["checks"] if not ok]
    assert report["passed"], f"smoke checks failed: {failed}"


if __name__ == "__main__":
    if "--live" in sys.argv:
        run_live_smoke()
    else:
        print("=== OFFLINE ORCHESTRATOR SMOKE TEST ===")
        rep = run_offline_smoke(verbose=True)
        print(f"\n{'ALL PASSED' if rep['passed'] else 'FAILED'} "
              f"({sum(1 for _, ok in rep['checks'] if ok)}/{len(rep['checks'])} checks)")
        sys.exit(0 if rep["passed"] else 1)

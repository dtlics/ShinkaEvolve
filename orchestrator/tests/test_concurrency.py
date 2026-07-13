"""test_concurrency.py — the window slot state machine (evo.parallel_slots).

Offline (mock mutate/eval, no Azure) assertions of the Batch-H invariants:

  * parallel_slots=1 — and an evo dict WITHOUT the knob (the back-compat code
    fallback) — reproduce the sequential reference driver: same archived
    generations, same window accounting, same total cost.
  * parallel_slots=2: every slot commits exactly once (no lost updates on the
    shared counters under the window mutex), archived generations are exactly
    the pre-assigned window range, and journal/slots.jsonl carries one
    'admitted' + one 'committed' event per slot.
  * Budget admission: a budget covering only part of the window stops ADMITTING
    mid-window (budget_hit), and the slots that did run form a contiguous
    generation prefix (admission flags are monotonic).

Run:  pytest orchestrator/tests/test_concurrency.py
"""

from __future__ import annotations

import json
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

import archive_query  # noqa: E402
import run_window  # noqa: E402


_SCORES = {
    "0": 1.0,                                   # bootstrap seed
    "1": 1.1, "2": 1.5, "3": 1.2,
    "4": 1.3, "5": 1.45, "6": 1.4,
}
_MUTATE_COST = 0.5
_WINDOW = 6


def _mk_cfg(ws: str, tag: str, parallel_slots=None, budget=None):
    run_dir = os.path.join(ws, f"run_{tag}")
    os.makedirs(run_dir, exist_ok=True)
    init_path = os.path.join(ws, "initial.py")
    if not os.path.exists(init_path):
        with open(init_path, "w", encoding="utf-8") as f:
            f.write("# EVOLVE-BLOCK-START\ndef solve():\n    return 1\n# EVOLVE-BLOCK-END\n")
    evo = {
        "window_size": _WINDOW,
        "patch_types": ["diff", "full"],
        "patch_type_probs": [0.7, 0.3],
        "embedding_model": "text-embedding-3-small",
        "consecutive_required": 2,
        "seed": 0,
    }
    if parallel_slots is not None:
        evo["parallel_slots"] = parallel_slots
        evo["parallel_eval_slots"] = 1
    cfg = {
        "results_dir": run_dir,
        "task": {"eval_program_path": "unused.py", "init_program_path": init_path,
                 "task_sys_msg": "concurrency smoke", "language": "python"},
        "db_config": {"num_islands": 2, "archive_size": 20},
        "evo": evo,
        # distinct mock codes per slot so every candidate archives (no identity dedup)
        "mock": {"enabled": True, "scores_by_generation": _SCORES,
                 "mutate_cost": _MUTATE_COST,
                 "mutate_code_sequence": [
                     f"# EVOLVE-BLOCK-START\nx = {i}\n# EVOLVE-BLOCK-END\n"
                     for i in range(_WINDOW)
                 ]},
        "window_state": {"window_index": 0, "prior_low_streak": 0},
        "windows": 1, "iters": _WINDOW,
    }
    if budget is not None:
        cfg["budget_usd"] = budget
    return cfg, run_dir


def _archived_gens(run_dir):
    progs = archive_query.main({
        "db_path": os.path.join(run_dir, "programs.sqlite"),
        "db_config": {"num_islands": 2, "archive_size": 20},
        "embedding_model": "text-embedding-3-small",
        "query_type": "all",
    })["result"]
    return sorted(int(p.get("generation") or 0) for p in progs)


def _slot_events(run_dir):
    path = os.path.join(run_dir, "journal", "slots.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def test_parallel_slots_parity_and_invariants():
    with tempfile.TemporaryDirectory() as ws:
        # Sequential reference: knob unset (back-compat fallback) and knob=1.
        cfg_ref, dir_ref = _mk_cfg(ws, "ref", parallel_slots=None)
        d_ref = run_window.main(cfg_ref)
        cfg_p1, dir_p1 = _mk_cfg(ws, "p1", parallel_slots=1)
        d_p1 = run_window.main(cfg_p1)
        cfg_p2, dir_p2 = _mk_cfg(ws, "p2", parallel_slots=2)
        d_p2 = run_window.main(cfg_p2)

        # All three complete the whole window with identical accounting.
        for d in (d_ref, d_p1, d_p2):
            assert d["iters_completed"] == _WINDOW, d
            assert d.get("budget_hit") is False, d
        assert abs(d_ref["best_score_end"] - 1.5) < 1e-9
        assert d_p1["best_score_end"] == d_ref["best_score_end"] == d_p2["best_score_end"]

        # Same archived generations everywhere (bootstrap may seed gen 0 once per
        # island — don't pin that; pin the WINDOW range + cross-run parity).
        g_ref = _archived_gens(dir_ref)
        assert _archived_gens(dir_p1) == g_ref == _archived_gens(dir_p2), g_ref
        assert [g for g in g_ref if g > 0] == list(range(1, _WINDOW + 1)), g_ref

        # Cost identical (no lost counter updates): window row carries the cost.
        import journal  # noqa: E402  (harness on sys.path)
        c_ref = journal.total_cost(dir_ref)
        assert abs(c_ref - _WINDOW * _MUTATE_COST) < 1e-9, c_ref
        assert abs(journal.total_cost(dir_p1) - c_ref) < 1e-9
        assert abs(journal.total_cost(dir_p2) - c_ref) < 1e-9

        # Slot lifecycle journal (parallel run): one admitted + one committed per
        # slot; committed slots cover the full window.
        ev = _slot_events(dir_p2)
        admitted = [e for e in ev if e["event"] == "admitted"]
        committed = [e for e in ev if e["event"] == "committed"]
        assert len(admitted) == _WINDOW and len(committed) == _WINDOW, ev
        assert sorted(e["slot"] for e in committed) == list(range(_WINDOW))
        # committed slot_cost sums to the window cost (approximate bound is exact
        # here because mock stages are instantaneous in submission order).
        assert sum(e.get("slot_cost", 0.0) for e in committed) >= _WINDOW * _MUTATE_COST - 1e-6
    return None


def test_budget_admission_stops_midwindow():
    with tempfile.TemporaryDirectory() as ws:
        # Budget covers ~3 slots (0.5 each): sequential admission runs slots while
        # spent < budget, so the window must stop early with budget_hit.
        for tag, par in (("bseq", 1), ("bpar", 2)):
            cfg, run_dir = _mk_cfg(ws, tag, parallel_slots=par, budget=1.6)
            d = run_window.main(cfg)
            assert d.get("budget_hit") is True, (tag, d)
            assert 0 < d["iters_completed"] < _WINDOW, (tag, d)
            gens = [g for g in _archived_gens(run_dir) if g > 0]
            # Executed slots form a contiguous generation prefix (monotonic flags).
            assert gens == list(range(1, d["iters_completed"] + 1)), (tag, gens, d)
    return None


if __name__ == "__main__":
    tests = [
        ("parallel slots parity + invariants", test_parallel_slots_parity_and_invariants),
        ("budget admission stops mid-window", test_budget_admission_stops_midwindow),
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
    print("ALL CONCURRENCY TESTS PASSED" if ok else "FAILURES")
    sys.exit(0 if ok else 1)

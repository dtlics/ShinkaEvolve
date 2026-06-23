"""test_run_isolation.py — per-run / cross-worktree isolation primitives.

Covers the elegant fix for the cross-session run_window kill + double-writer:
the OS run-lock (identity/liveness/co-tenancy), the cooperative .stop sentinel,
and config-dir path anchoring. These are unit tests of the run_window primitives
(no Azure, no real window) — the full launch path acquires the lock in _cli().

Run:  pytest orchestrator/tests/test_run_isolation.py
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

import run_window  # noqa: E402


def test_run_lock_refuses_second_acquire():
    """A second run_window on the same results_dir must refuse to start (co-tenancy),
    and releasing the first must free the directory (crash-safe re-acquire)."""
    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "results")
        lock1 = run_window.acquire_run_lock(rd, run_id="A")
        try:
            raised = False
            try:
                run_window.acquire_run_lock(rd, run_id="B")
            except SystemExit as e:
                raised = True
                assert "refusing to start" in str(e)
            assert raised, "second acquire on a held results_dir must raise SystemExit"
            # The owner forensics file names the holder.
            owner = json.loads(Path(rd, ".run_owner.json").read_text())
            assert owner["run_id"] == "A" and owner["pid"] == os.getpid()
        finally:
            lock1.release()
        # After release (== what the OS does on death), a fresh run can acquire.
        lock2 = run_window.acquire_run_lock(rd, run_id="C")
        lock2.release()


def test_run_lock_distinct_dirs_never_contend():
    """Two worktrees == two distinct results_dir == two locks that never contend."""
    with tempfile.TemporaryDirectory() as td:
        a = run_window.acquire_run_lock(os.path.join(td, "wtA", "results"), run_id="A")
        b = run_window.acquire_run_lock(os.path.join(td, "wtB", "results"), run_id="B")
        a.release()
        b.release()


def test_stop_sentinel_target_match():
    """`.stop` is honored for a matching/absent target_run_id and consumed; a
    mismatched target is ignored; a present-but-malformed file still stops."""
    with tempfile.TemporaryDirectory() as rd:
        # absent -> no stop
        assert run_window._stop_requested(rd, "A") is False
        # untargeted stop -> honored + consumed
        Path(rd, ".stop").write_text(json.dumps({"reason": "snapshot"}))
        assert run_window._stop_requested(rd, "A") is True
        assert not Path(rd, ".stop").exists()
        # mismatched target -> ignored, file left in place
        Path(rd, ".stop").write_text(json.dumps({"target_run_id": "OTHER"}))
        assert run_window._stop_requested(rd, "A") is False
        assert Path(rd, ".stop").exists()
        # matching target -> honored + consumed
        Path(rd, ".stop").write_text(json.dumps({"target_run_id": "A"}))
        assert run_window._stop_requested(rd, "A") is True
        # malformed-but-present -> treated as a stop request
        Path(rd, ".stop").write_text("not json")
        assert run_window._stop_requested(rd, "A") is True


def test_clear_stop_is_idempotent():
    with tempfile.TemporaryDirectory() as rd:
        run_window._clear_stop(rd)  # absent: no raise
        Path(rd, ".stop").write_text("{}")
        run_window._clear_stop(rd)
        assert not Path(rd, ".stop").exists()


def test_absolutize_anchors_to_config_dir_not_cwd():
    """A relative results_dir/db_path resolves against the config-file dir, identically
    regardless of the launch CWD — the anchor that makes per-worktree locks distinct."""
    with tempfile.TemporaryDirectory() as td:
        cfg_dir = os.path.join(td, "runX")
        os.makedirs(cfg_dir)
        cfg_path = os.path.join(cfg_dir, "run.json")

        cfg = {"results_dir": "results", "db_path": "results/programs.sqlite"}
        run_window._absolutize_paths(cfg, cfg_path)
        assert cfg["results_dir"] == os.path.normpath(os.path.join(cfg_dir, "results"))
        assert cfg["db_path"] == os.path.normpath(os.path.join(cfg_dir, "results", "programs.sqlite"))
        assert os.path.isabs(cfg["results_dir"])

        # An absolute results_dir in the config is left untouched.
        abs_rd = os.path.join(td, "elsewhere")
        cfg2 = {"results_dir": abs_rd}
        run_window._absolutize_paths(cfg2, cfg_path)
        assert cfg2["results_dir"] == abs_rd


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

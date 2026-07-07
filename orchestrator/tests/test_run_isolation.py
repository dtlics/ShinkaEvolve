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


def test_warmup_stop_resolves_to_parent_dir():
    """Session-isolation fix: during warmup the live results_dir is redirected to the
    <results_dir>/warmup subdir, but a `.stop` the agent writes to the PARENT results_dir must
    still stop the warmup (not only at the warmup window's end). `_cli` captures the parent as
    cfg['stop_dir'] BEFORE the redirect, and `main` resolves the stop target to it."""
    with tempfile.TemporaryDirectory() as td:
        parent = os.path.join(td, "results")
        warm = os.path.join(parent, "warmup")
        os.makedirs(warm)
        # Emulate _cli's warmup setup: capture the parent as stop_dir, THEN redirect results_dir.
        cfg = {"results_dir": parent, "run_id": "A"}
        cfg["stop_dir"] = cfg["results_dir"]   # _cli, before the redirect
        cfg["results_dir"] = warm              # _cli, the warmup redirect
        # main's resolution (the actual one-liner in run_window.main):
        stop_dir = cfg.get("stop_dir") or cfg["results_dir"]
        assert stop_dir == parent
        # A `.stop` the agent writes to the PARENT is honored against stop_dir.
        Path(parent, ".stop").write_text(json.dumps({"target_run_id": "A"}))
        assert run_window._stop_requested(stop_dir, "A") is True
        # The OLD behavior (checking the redirected warmup subdir) would MISS it and leave it.
        Path(parent, ".stop").write_text(json.dumps({"target_run_id": "A"}))
        assert run_window._stop_requested(warm, "A") is False
        assert Path(parent, ".stop").exists()


def test_non_warmup_stop_dir_is_results_dir():
    """A normal (non-warmup) run has no stop_dir override, so the stop target is just
    results_dir — the session-isolation change is inert outside warmup."""
    cfg = {"results_dir": os.path.join("some", "run"), "run_id": "A"}
    stop_dir = cfg.get("stop_dir") or cfg["results_dir"]
    assert stop_dir == cfg["results_dir"]


def test_clear_stop_is_idempotent():
    with tempfile.TemporaryDirectory() as rd:
        run_window._clear_stop(rd)  # absent: no raise
        Path(rd, ".stop").write_text("{}")
        run_window._clear_stop(rd)
        assert not Path(rd, ".stop").exists()


def test_accept_warmup_blocked_by_parent_lock():
    """Lock reorder: _cli takes ONE exclusive lock on the PARENT results_dir before ANY
    branch (accept-warmup included), so an --accept-warmup launched while another
    process-equivalent (a live warmup / real run) holds the lock dies at acquire time —
    accept_warmup is never reached and the real db is never created. After the holder
    exits, the same sequence proceeds to accept_warmup."""
    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "results")
        # Give the accept path something it WOULD act on if it were (wrongly) reached.
        warm = os.path.join(rd, "warmup")
        os.makedirs(warm)
        Path(warm, "programs.sqlite").write_bytes(b"placeholder")
        cfg = {"results_dir": rd, "run_id": "acc",
               "db_path": os.path.join(rd, "programs.sqlite"),
               "db_config": {"num_islands": 1}, "evo": {}, "task": {}}

        # Stub the live-count read so the accept path needs no real shinka schema.
        orig_aq = run_window.archive_query.main
        run_window.archive_query.main = lambda payload: {"result": {"live": 0, "total": 0}}
        try:
            holder = run_window.acquire_run_lock(rd, run_id="live-warmup")  # the other process
            try:
                # The _cli prologue of the second process: lock FIRST, accept only under it.
                raised = False
                try:
                    _lock = run_window.acquire_run_lock(cfg["results_dir"], cfg["run_id"])
                    run_window.accept_warmup(cfg)  # must be unreachable
                except SystemExit:
                    raised = True
                assert raised, "accept-warmup path must refuse while the parent lock is held"
                assert not os.path.exists(cfg["db_path"]), "accept_warmup ran under a held lock"
            finally:
                holder.release()

            # Holder gone → the same sequence REACHES accept_warmup (which then refuses on
            # the zero live-count — the point is it RAN, returning instead of raising).
            lock2 = run_window.acquire_run_lock(cfg["results_dir"], cfg["run_id"])
            try:
                res = run_window.accept_warmup(cfg)
                assert isinstance(res, dict) and res.get("accepted") is False
                assert "live rows" in res.get("reason", ""), res
            finally:
                lock2.release()
        finally:
            run_window.archive_query.main = orig_aq


def test_warmup_cleanup_under_parent_lock():
    """Lock reorder: the warmup subdir no longer carries its own .run.lock — the single
    lock lives at the PARENT <results_dir>/.run.lock. cleanup_warmup therefore succeeds
    while the parent lock is HELD (nothing inside the warmup dir is locked, even a stale
    warmup-local .run.lock left by the old design), and the parent's lock file survives
    the cleanup."""
    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "results")
        lock = run_window.acquire_run_lock(rd, run_id="W")
        try:
            warm = os.path.join(rd, "warmup")
            os.makedirs(os.path.join(warm, "journal"))
            Path(warm, "programs.sqlite").write_bytes(b"x")
            # a STALE warmup-local lock file from the old design must not block cleanup
            Path(warm, ".run.lock").write_bytes(b"")
            assert run_window.cleanup_warmup(rd) is True
            assert not os.path.isdir(warm)
            assert os.path.exists(os.path.join(rd, ".run.lock")), "parent lock file removed"
            # and the parent lock is still genuinely HELD after cleanup
            raised = False
            try:
                run_window.acquire_run_lock(rd, run_id="X")
            except SystemExit:
                raised = True
            assert raised, "parent lock must still be held after cleanup_warmup"
        finally:
            lock.release()


def test_cli_lock_ordering_source():
    """Pin the _cli lock ordering the reorder established: exactly ONE acquire_run_lock
    call, placed AFTER stop_dir capture and BEFORE the accept-warmup early-return and the
    warmup cleanup/redirect — so every _cli mode is serialized under the parent lock and
    the old pre-main() acquisition (which left warmup/accept unguarded) cannot silently
    come back."""
    import inspect

    src = inspect.getsource(run_window._cli)
    assert src.count("acquire_run_lock(") == 1, "one parent-level lock acquisition in _cli"
    i_lock = src.index("acquire_run_lock(")
    assert src.index('cfg["stop_dir"]') < i_lock, "stop_dir captured before the lock"
    assert i_lock < src.index("args.accept_warmup"), "lock must precede the accept branch"
    # the CALL (not the comment mention above the lock line): the warmup auto-reset
    assert i_lock < src.index('cleanup_warmup(cfg["results_dir"])'), \
        "lock must precede the warmup auto-reset"
    assert i_lock < src.index("main(cfg)"), "lock must precede main()"


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

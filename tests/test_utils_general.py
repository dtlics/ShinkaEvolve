"""Tests for ``shinka.utils.general``.

Doom-remediation Fix 4: ``load_results`` must truncate the
``stderr_log`` it returns so a noisy evaluator can't flood the agent's
intra-loop fix context with megabytes of warnings. Truncation
preserves both head and tail so the actual raise site (typically at
the tail) survives even when setup noise fills the head.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from shinka.utils.general import (
    _STDERR_LOG_MAX_BYTES,
    _TRUNCATED_MARKER,
    _truncate_log,
    load_results,
)


# ----------------------------------------------------------------------
# _truncate_log helper — pure function, no filesystem
# ----------------------------------------------------------------------


def test_truncate_log_passes_through_short_inputs() -> None:
    """Short strings (below the cap) are returned verbatim. No marker
    inserted, no head/tail munging."""
    short = "Traceback (most recent call last):\nValueError: bad"
    assert _truncate_log(short) == short
    assert _truncate_log("") == ""


def test_truncate_log_passes_through_non_string() -> None:
    """Defensive: non-str inputs (e.g. accidental None) are returned
    unchanged so the caller can decide how to handle them."""
    assert _truncate_log(None) is None  # type: ignore[arg-type]


def test_truncate_log_preserves_head_and_tail() -> None:
    """For strings beyond the cap, both head (with BEGIN sentinel)
    and tail (with END sentinel) survive — this is the property that
    keeps the actual exception line readable."""
    big = "BEGIN" + ("X" * (_STDERR_LOG_MAX_BYTES * 2)) + "END"
    out = _truncate_log(big)
    # Result fits the budget plus the marker.
    assert len(out) <= _STDERR_LOG_MAX_BYTES + len(_TRUNCATED_MARKER) + 8
    # Both sentinels survive.
    assert "BEGIN" in out
    assert "END" in out
    # The middle (`"X" * 1KB+`) is replaced by the marker, not echoed.
    assert _TRUNCATED_MARKER in out
    # Confirm the marker is between head and tail, not at edges.
    head_end = out.index(_TRUNCATED_MARKER)
    tail_start = head_end + len(_TRUNCATED_MARKER)
    assert "BEGIN" in out[:head_end]
    assert "END" in out[tail_start:]


def test_truncate_log_custom_max_bytes() -> None:
    """The ``max_bytes`` kwarg lets callers tighten or loosen the
    cap. We use 100 here so the test is fast."""
    out = _truncate_log("A" * 5_000, max_bytes=100)
    assert len(out) <= 100 + len(_TRUNCATED_MARKER) + 8
    assert _TRUNCATED_MARKER in out


# ----------------------------------------------------------------------
# load_results — end-to-end stderr truncation through the file load
# ----------------------------------------------------------------------


def test_load_results_truncates_huge_stderr_log() -> None:
    """A 1MB stderr file must be truncated when loaded. Both the
    BEGIN sentinel (head) and END sentinel (tail) must survive, so
    the actual exception line at the tail is still visible to the
    agent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results_dir = Path(tmpdir)
        # Synthesize a 1MB stderr with markers at both ends so we can
        # verify head + tail preservation.
        big_stderr = (
            "BEGIN_MARKER\n"
            + ("noise line\n" * 100_000)  # ~1.1MB of noise
            + "END_MARKER: ValueError: actual failure here\n"
        )
        (results_dir / "job_log.err").write_text(big_stderr)
        # Need the other files load_results expects.
        (results_dir / "metrics.json").write_text(json.dumps({"combined_score": 0.0}))
        (results_dir / "correct.json").write_text(
            json.dumps({"correct": False, "error": "x"})
        )

        loaded = load_results(str(results_dir))
        stderr_log = loaded["stderr_log"]

        # Truncated to <= cap + marker overhead.
        assert len(stderr_log) <= _STDERR_LOG_MAX_BYTES + 2 * len(_TRUNCATED_MARKER)
        # Sentinels at both ends survive.
        assert "BEGIN_MARKER" in stderr_log
        assert "END_MARKER" in stderr_log
        # The actual exception line at the tail is preserved.
        assert "actual failure here" in stderr_log
        # And the truncation marker is present.
        assert _TRUNCATED_MARKER in stderr_log


def test_load_results_small_stderr_passes_through() -> None:
    """Stderr below the cap is returned verbatim — no marker inserted,
    no characters dropped. This is the common case for well-behaved
    evaluators."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results_dir = Path(tmpdir)
        small_stderr = "Traceback...\nValueError: tiny"
        (results_dir / "job_log.err").write_text(small_stderr)
        (results_dir / "metrics.json").write_text(json.dumps({}))
        (results_dir / "correct.json").write_text(json.dumps({"correct": False}))

        loaded = load_results(str(results_dir))
        assert loaded["stderr_log"] == small_stderr
        assert _TRUNCATED_MARKER not in loaded["stderr_log"]


def test_load_results_no_stderr_file_yields_empty_string() -> None:
    """Missing job_log.err is normal (eval succeeded silently). The
    field is still present, just empty — downstream consumers can
    safely ``.get("stderr_log", "")``."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results_dir = Path(tmpdir)
        (results_dir / "metrics.json").write_text(json.dumps({"combined_score": 1.0}))
        (results_dir / "correct.json").write_text(json.dumps({"correct": True}))

        loaded = load_results(str(results_dir))
        assert loaded["stderr_log"] == ""

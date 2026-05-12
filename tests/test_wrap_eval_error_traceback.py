"""Phase 1 of research-grounding — wrap_eval traceback persistence.

When the evaluator raises, ``run_shinka_eval`` must:
1. Capture ``traceback.format_exc()`` while exc_info is live.
2. Truncate to ``_ERROR_TRACEBACK_MAX_BYTES`` if huge.
3. Persist as ``error_traceback`` in ``correct.json`` alongside ``error``.

These tests exercise the full file-writing path through a tempdir; no
LLM, no scheduler, no DB. Just the evaluator output contract.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path

from shinka.core.wrap_eval import (
    _ERROR_TRACEBACK_MAX_BYTES,
    _truncate_traceback,
    run_shinka_eval,
    save_json_results,
)


def test_save_json_results_writes_error_traceback_field() -> None:
    """When ``error_traceback`` is passed, ``correct.json`` must
    contain that field; when None, the field is absent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        save_json_results(
            tmpdir,
            metrics={"combined_score": 0.0},
            correct=False,
            error="boom",
            error_traceback="Traceback...\nValueError: boom\n",
        )
        with open(Path(tmpdir) / "correct.json") as f:
            payload = json.load(f)
        assert payload["correct"] is False
        assert payload["error"] == "boom"
        assert payload["error_traceback"] == "Traceback...\nValueError: boom\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        save_json_results(
            tmpdir,
            metrics={"combined_score": 1.0},
            correct=True,
            error=None,
        )
        with open(Path(tmpdir) / "correct.json") as f:
            payload = json.load(f)
        # Default no-traceback case keeps the file shape compact —
        # the field is absent rather than null, so old readers stay
        # forward-compatible.
        assert "error_traceback" not in payload


def test_truncate_traceback_preserves_head_and_tail() -> None:
    """Traceback strings beyond the cap must keep both the head
    (exception type + initial frames) and the tail (the raise site)."""
    long_tb = ("A" * 10_000) + "TAIL_MARKER" + ("B" * 10_000)
    truncated = _truncate_traceback(long_tb)
    assert truncated is not None
    assert len(truncated) <= _ERROR_TRACEBACK_MAX_BYTES + 64  # room for marker
    assert truncated.startswith("A" * 100)
    # The tail must survive truncation — that's where the actual error
    # message usually lives.
    assert truncated.endswith("B" * 100)
    assert "TAIL_MARKER" not in truncated  # middle is dropped
    assert "truncated" in truncated.lower()


def test_truncate_traceback_passes_through_small_inputs() -> None:
    """Short tracebacks must be returned unchanged."""
    short = "Traceback (most recent call last):\nValueError: nope"
    assert _truncate_traceback(short) == short
    assert _truncate_traceback("") == ""
    assert _truncate_traceback(None) is None


def test_run_shinka_eval_captures_traceback_when_exception_raised(
    tmp_path: Path,
) -> None:
    """When the evaluated program raises, the full traceback (capped)
    must be persisted into correct.json's ``error_traceback`` field."""
    program_path = tmp_path / "bad_program.py"
    program_path.write_text(
        textwrap.dedent(
            """
            def main(**_):
                raise RuntimeError("eval blew up — wrap_eval should capture this trace")
            """
        )
    )
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    metrics, correct, err = run_shinka_eval(
        program_path=str(program_path),
        results_dir=str(results_dir),
        experiment_fn_name="main",
        num_runs=1,
    )
    assert correct is False
    assert err is not None
    assert "eval blew up" in err

    with open(results_dir / "correct.json") as f:
        payload = json.load(f)
    assert payload["correct"] is False
    assert "error_traceback" in payload
    # The traceback must include the actual raise site and the exception
    # type/class, which is the whole point of preserving it.
    tb = payload["error_traceback"]
    assert "RuntimeError" in tb
    assert "eval blew up" in tb
    # Frame info should be there too (the module path).
    assert "bad_program.py" in tb

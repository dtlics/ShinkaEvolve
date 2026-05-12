"""Phase 4a tests: error-fix prompts + sampler + wrap_eval traceback capture.

Phase 4b will extend this file with end-to-end runner tests once the
bandit-attribution and retry-submission pipeline lands.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from shinka.core.config import EvolutionConfig
from shinka.core.sampler import PromptSampler
from shinka.core.wrap_eval import save_json_results
from shinka.database import Program
from shinka.prompts import (
    AttemptRecord,
    ERROR_FIX_ITER_MSG,
    ERROR_FIX_SYS_FORMAT,
    FIX_SYS_FORMAT,
    format_prior_attempt_log,
    format_traceback_section,
)


# ---------------------------------------------------------------------------
# AttemptRecord + format helpers
# ---------------------------------------------------------------------------


def test_format_prior_attempt_log_empty():
    assert format_prior_attempt_log([]) == ""


def test_format_prior_attempt_log_renders_structured_rows():
    rendered = format_prior_attempt_log(
        [
            AttemptRecord(
                round_number=1,
                model_used="gpt-5-mini",
                summary="added missing import",
                error_message="ImportError: foo",
            ),
            AttemptRecord(
                round_number=2,
                model_used="gpt-5-codex",
                summary="renamed variable",
                error_message="NameError: bar",
            ),
        ]
    )
    assert "Round 1" in rendered
    assert "gpt-5-mini" in rendered
    assert "added missing import" in rendered
    assert "ImportError: foo" in rendered
    assert "Round 2" in rendered
    assert "gpt-5-codex" in rendered


def test_format_prior_attempt_log_truncates_long_fields():
    long_text = "x" * 1000
    rendered = format_prior_attempt_log(
        [AttemptRecord(1, "m", long_text, long_text)]
    )
    # Each field should be capped around 200 chars so the prompt doesn't
    # balloon.
    assert long_text not in rendered


def test_format_traceback_section_empty():
    assert format_traceback_section(None) == ""
    assert format_traceback_section("") == ""
    assert format_traceback_section("   ") == ""


def test_format_traceback_section_renders_code_fence():
    rendered = format_traceback_section("Traceback (most recent call last):\nValueError: x")
    assert "## Traceback" in rendered
    assert "```" in rendered
    assert "ValueError: x" in rendered


# ---------------------------------------------------------------------------
# PromptSampler.sample_error_fix
# ---------------------------------------------------------------------------


def _failed_program(error_message: str = "NameError: foo") -> Program:
    return Program(
        id="failed-1",
        code="def f():\n    return broken_var\n",
        generation=1,
        metadata={"error_message": error_message},
        error_traceback="Traceback (most recent call last)\n" + error_message,
    )


def _parent_program() -> Program:
    return Program(
        id="parent-0",
        code="def f():\n    return 1\n",
        generation=0,
        mutation_intent=(
            "Rename helper | technique: use broken_var | "
            "expected: nothing -- intentional break to exercise fix loop"
        ),
    )


def test_sample_error_fix_returns_patch_type_error_fix():
    sampler = PromptSampler(language="python")
    sys_msg, user_msg, patch_type = sampler.sample_error_fix(
        parent=_parent_program(),
        failed=_failed_program(),
        mutation_intent=_parent_program().mutation_intent,
        prior_attempts=[],
        round_number=1,
        rounds_remaining=2,
    )
    assert patch_type == "error_fix"
    # Must NOT collide with the bootstrap "fix" patch type.
    assert patch_type != "fix"


def test_sample_error_fix_injects_required_context():
    sampler = PromptSampler(language="python")
    parent = _parent_program()
    failed = _failed_program()
    sys_msg, user_msg, _ = sampler.sample_error_fix(
        parent=parent,
        failed=failed,
        mutation_intent=parent.mutation_intent,
        prior_attempts=[
            AttemptRecord(1, "gpt-5-mini", "tried renaming", "still NameError"),
        ],
        round_number=2,
        rounds_remaining=1,
    )
    # System prompt mentions remaining attempts.
    assert "1 fix attempts" in sys_msg
    # User prompt includes all the structured pieces.
    assert "Round 2" in user_msg
    assert "Rename helper" in user_msg  # mutation_intent rendered
    assert parent.code.rstrip() in user_msg
    assert failed.code.rstrip() in user_msg
    assert "NameError: foo" in user_msg
    assert "Traceback" in user_msg
    assert "Round 1" in user_msg  # prior attempt rendered


def test_sample_error_fix_handles_missing_intent_with_sentinel():
    sampler = PromptSampler(language="python")
    parent = _parent_program()
    parent.mutation_intent = None
    sys_msg, user_msg, _ = sampler.sample_error_fix(
        parent=parent,
        failed=_failed_program(),
        mutation_intent=None,
        prior_attempts=[],
        round_number=1,
        rounds_remaining=2,
    )
    assert "no intent recorded" in user_msg


def test_sample_error_fix_omits_prior_attempts_on_round_one():
    sampler = PromptSampler(language="python")
    sys_msg, user_msg, _ = sampler.sample_error_fix(
        parent=_parent_program(),
        failed=_failed_program(),
        mutation_intent=_parent_program().mutation_intent,
        prior_attempts=[],
        round_number=1,
        rounds_remaining=2,
    )
    assert "Previous fix attempts" not in user_msg


def test_sample_error_fix_system_prompt_is_distinct_from_bootstrap_fix():
    sampler = PromptSampler(language="python")
    sys_msg, _, _ = sampler.sample_error_fix(
        parent=_parent_program(),
        failed=_failed_program(),
        mutation_intent=_parent_program().mutation_intent,
        prior_attempts=[],
        round_number=1,
        rounds_remaining=2,
    )
    # Phrase only in ERROR_FIX_SYS_FORMAT, not FIX_SYS_FORMAT
    assert "preserving that" in sys_msg
    assert "preserving that" not in FIX_SYS_FORMAT


# ---------------------------------------------------------------------------
# EvolutionConfig flags
# ---------------------------------------------------------------------------


def test_error_fix_config_defaults():
    cfg = EvolutionConfig()
    assert cfg.enable_error_fix_loop is False
    assert cfg.error_fix_rounds_by_type == {
        "diff": 2,
        "full": 3,
        "cross": 3,
        "literature_grounded": 4,
    }
    assert cfg.error_fix_score_decay == pytest.approx(0.7)
    assert cfg.enable_fixer_bandit is True
    assert cfg.fixer_bandit_algorithm == "ucb"
    assert cfg.error_fix_enable_shell is False
    assert cfg.error_fix_shell_budget == 4
    assert cfg.error_fix_shell_models == ["gpt-5-codex"]


# ---------------------------------------------------------------------------
# wrap_eval: traceback capture in correct.json
# ---------------------------------------------------------------------------


def test_save_json_results_persists_error_traceback(tmp_path: Path):
    save_json_results(
        results_dir=str(tmp_path),
        metrics={"combined_score": 0.0},
        correct=False,
        error="NameError: broken_var",
        error_traceback=(
            "Traceback (most recent call last):\n"
            "  File \"prog.py\", line 1, in <module>\n"
            "NameError: broken_var"
        ),
    )
    payload = json.loads((tmp_path / "correct.json").read_text())
    assert payload["correct"] is False
    assert payload["error"].startswith("NameError")
    assert "Traceback" in payload["error_traceback"]
    assert "broken_var" in payload["error_traceback"]


def test_save_json_results_truncates_oversized_traceback(tmp_path: Path):
    # Build a ~16KB traceback; expect truncation around 8KB.
    long_trace = "x" * (16 * 1024)
    save_json_results(
        results_dir=str(tmp_path),
        metrics={},
        correct=False,
        error="boom",
        error_traceback=long_trace,
    )
    payload = json.loads((tmp_path / "correct.json").read_text())
    assert "truncated" in payload["error_traceback"]
    assert len(payload["error_traceback"]) < len(long_trace)


def test_save_json_results_omits_traceback_when_none(tmp_path: Path):
    save_json_results(
        results_dir=str(tmp_path),
        metrics={},
        correct=True,
        error=None,
    )
    payload = json.loads((tmp_path / "correct.json").read_text())
    assert "error_traceback" not in payload

"""Integration tests for ``ShinkaEvolveRunner._run_agent_proposal``.

The full ``ShinkaEvolveRunner`` is heavy to instantiate (Hydra config,
DB, scheduler, etc.). We test the method by binding it to a
``SimpleNamespace`` stub with just the attributes the method touches.
This exercises real control flow — the prompt-sampler/LLM/save-patch
calls are mocked individually so the assertions can check that the
right arguments flowed through.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shinka.core.async_runner import ShinkaEvolveRunner
from shinka.llm.agent.tools import ShinkaToolContext


def _build_stub_runner(tmp_path: Path) -> SimpleNamespace:
    """A SimpleNamespace with just the attributes ``_run_agent_proposal``
    reads or writes. Methods are AsyncMock / MagicMock so we can
    assert on their calls."""
    prompt_sampler = MagicMock()
    prompt_sampler.task_sys_msg = "original_sys"
    prompt_sampler.sample = MagicMock(
        return_value=("sys_prompt", "user_msg", "diff")
    )

    llm = MagicMock()
    llm.get_kwargs = MagicMock(
        return_value={
            "model_name": "azure-gpt-5.4-mini",
            "temperature": 0.5,
            "max_output_tokens": 1000,
        }
    )
    llm.run_agent = AsyncMock()  # configured per-test

    evo_config = MagicMock()
    evo_config.max_patch_attempts = 3
    evo_config.language = "python"

    runner = SimpleNamespace(
        prompt_sampler=prompt_sampler,
        llm=llm,
        evo_config=evo_config,
        results_dir=str(tmp_path),
        verbose=False,
        db=None,
        llm_selection=None,
        # Mocked methods on the class — bind them as plain async funcs.
        _get_current_system_prompt=MagicMock(return_value=(None, None)),
        _save_patch_attempt_async=AsyncMock(),
        _print_metadata_table=MagicMock(),
    )
    return runner


def _parent_program(code: str = "def f(): return 1\n") -> SimpleNamespace:
    return SimpleNamespace(code=code, id="parent-1")


def test_happy_path_returns_success_with_patch_text(tmp_path: Path) -> None:
    """When the agent's apply_patch tool sets ctx.last_successful_*,
    the method returns success=True with the right patch_text and
    meta fields."""
    runner = _build_stub_runner(tmp_path)

    # Simulate the agent run mutating the tool_context as
    # apply_patch_tool would (the actual mutation happens inside
    # the SDK's Runner, but our mock here stands in for that).
    final_response = SimpleNamespace(
        content="<NAME>better</NAME><DESCRIPTION>swap algo</DESCRIPTION>",
        cost=0.12,
        to_dict=lambda: {"cost": 0.12, "content": "<NAME>better</NAME>"},
    )

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        # Simulate one successful apply_patch tool call.
        ctx.current_code = "def f(): return 2\n"
        ctx.last_successful_patch_text = "diff text"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 1
        ctx.record_tool_call(
            "apply_patch",
            latency_sec=0.01,
            success=True,
            extra={"patch_type": "diff", "num_applied": 1},
        )
        return final_response

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=3,
        )
    )

    assert result is not None
    patch_text, meta, success = result
    assert success is True
    assert patch_text == "diff text"
    assert meta["patch_type"] == "diff"
    assert meta["num_applied"] == 1
    assert meta["patch_name"] == "better"
    assert meta["patch_description"] == "swap algo"
    assert meta["api_costs"] == 0.12
    # The agentic trace must be present for downstream telemetry.
    assert isinstance(meta["agent_tool_trace"], list)
    assert any(t["name"] == "apply_patch" for t in meta["agent_tool_trace"])
    # _save_patch_attempt_async called once with success=True.
    save_call = runner._save_patch_attempt_async.await_args
    assert save_call.kwargs["success"] is True
    assert save_call.kwargs["patch_text"] == "diff text"


def test_no_successful_patch_returns_failure(tmp_path: Path) -> None:
    """If the agent never produces a successful apply_patch, the
    method must surface success=False and capture the last error."""
    runner = _build_stub_runner(tmp_path)

    final_response = SimpleNamespace(
        content="I tried but the diffs kept failing",
        cost=0.05,
        to_dict=lambda: {"cost": 0.05},
    )

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        ctx.record_tool_call(
            "apply_patch",
            latency_sec=0.01,
            success=False,
            error="could not parse diff hunk",
            extra={"patch_type": "diff"},
        )
        ctx.record_tool_call(
            "apply_patch",
            latency_sec=0.01,
            success=False,
            error="malformed @@ hunk",
            extra={"patch_type": "diff"},
        )
        return final_response

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=4,
        )
    )

    assert result is not None
    patch_text, meta, success = result
    assert success is False
    assert patch_text is None
    # error_attempt should carry the last apply_patch error.
    assert "malformed" in (meta.get("error_attempt") or "")
    # _save_patch_attempt_async called with success=False.
    save_call = runner._save_patch_attempt_async.await_args
    assert save_call.kwargs["success"] is False
    # patch_attempt count reflects the two apply_patch tool calls.
    assert save_call.kwargs["patch_attempt"] == 2


def test_agent_returns_none_is_handled(tmp_path: Path) -> None:
    """If AgentLLMClient.run_agent returns None (exhausted retries),
    the proposal method must still return a structured failure."""
    runner = _build_stub_runner(tmp_path)
    runner.llm.run_agent = AsyncMock(return_value=None)

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=5,
        )
    )

    assert result is not None
    patch_text, meta, success = result
    assert success is False
    assert patch_text is None
    # No apply_patch tool calls happened; falls back to default error.
    assert "Agent loop" in (meta.get("error_attempt") or "")


def test_exception_inside_run_caught(tmp_path: Path) -> None:
    """If run_agent itself raises, the method returns structured
    failure rather than propagating."""
    runner = _build_stub_runner(tmp_path)
    runner.llm.run_agent = AsyncMock(side_effect=RuntimeError("agent boom"))

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=6,
        )
    )

    assert result is not None
    patch_text, meta, success = result
    assert success is False
    assert patch_text is None
    assert "agent boom" in meta["error_attempt"]


def test_max_turns_uses_evo_config_max_patch_attempts(tmp_path: Path) -> None:
    """The agent's max_turns should be wired from
    evo_config.max_patch_attempts so existing per-task tuning carries
    over."""
    runner = _build_stub_runner(tmp_path)
    runner.evo_config.max_patch_attempts = 7

    captured: dict = {}

    async def capture(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return None

    runner.llm.run_agent = AsyncMock(side_effect=capture)

    asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=7,
        )
    )
    assert captured["max_turns"] == 7


def test_tools_list_contains_apply_patch(tmp_path: Path) -> None:
    """Sanity: the run_agent call must include apply_patch tool so
    the agent can actually mutate the program."""
    runner = _build_stub_runner(tmp_path)

    captured: dict = {}

    async def capture(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return None

    runner.llm.run_agent = AsyncMock(side_effect=capture)

    asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=8,
        )
    )
    tools = captured.get("tools") or []
    # The shinka apply_patch_tool from registry should be in there.
    from shinka.llm.agent.tools.apply_patch import _apply_patch_tool

    assert _apply_patch_tool in tools


def test_config_flag_default_routes_to_legacy() -> None:
    """The feature flag must default to False so existing experiments
    continue to use _run_patch_async without opt-in."""
    from shinka.core.config import EvolutionConfig

    config = EvolutionConfig()
    assert config.use_agentic_proposer is False

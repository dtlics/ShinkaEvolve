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

    # Stub scheduler — _run_agent_proposal binds an evaluator closure
    # over scheduler.run so the agent's evaluate_tool can call it. Tests
    # that don't exercise evaluate_tool never invoke this; the attribute
    # just has to exist.
    scheduler = MagicMock()
    scheduler.run = MagicMock(
        return_value=(
            {"correct": {"correct": True}, "metrics": {"combined_score": 1.0}},
            0.0,
        )
    )

    runner = SimpleNamespace(
        prompt_sampler=prompt_sampler,
        llm=llm,
        evo_config=evo_config,
        results_dir=str(tmp_path),
        lang_ext="py",
        scheduler=scheduler,
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
    """The agent's max_turns budget should be derived from
    evo_config.max_patch_attempts so existing per-task tuning carries
    over. Phase E gives each apply iteration a 3-turn budget
    (apply → evaluate → reflect)."""
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
    assert captured["max_turns"] == 7 * 3


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


def test_agentic_tools_config_widens_tool_set(tmp_path: Path) -> None:
    """When evo_config.agentic_tools includes additional names, the
    agent gets those tools too. Verifies the per-task widening
    surface."""
    runner = _build_stub_runner(tmp_path)
    runner.evo_config.agentic_tools = [
        "apply_patch",
        "read_host_file",
        "query_evolution_db",
    ]

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
            generation=20,
        )
    )
    tools = captured.get("tools") or []
    from shinka.llm.agent.tools.apply_patch import _apply_patch_tool
    from shinka.llm.agent.tools.read_file import _read_host_file_tool
    from shinka.llm.agent.tools.query_db import _query_evolution_db_tool

    assert _apply_patch_tool in tools
    assert _read_host_file_tool in tools
    assert _query_evolution_db_tool in tools


def test_unknown_agentic_tool_falls_back_to_apply_patch_and_evaluate(
    tmp_path: Path,
) -> None:
    """Misconfiguration (typo in tool name) shouldn't crash a run —
    we fall back to apply_patch + evaluate (the Phase E default) and
    warn."""
    runner = _build_stub_runner(tmp_path)
    runner.evo_config.agentic_tools = ["apply_patch", "no_such_tool"]

    captured: dict = {}

    async def capture(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return None

    runner.llm.run_agent = AsyncMock(side_effect=capture)

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=21,
        )
    )
    # No crash; the run proceeded with the fallback tool set.
    assert result is not None
    tools = captured.get("tools") or []
    from shinka.llm.agent.tools.apply_patch import _apply_patch_tool
    from shinka.llm.agent.tools.evaluate import _evaluate_tool

    assert _apply_patch_tool in tools
    assert _evaluate_tool in tools
    # The bogus tool was filtered out; only the two fallback tools remain.
    assert len(tools) == 2


def test_agentic_tools_empty_list_falls_back_to_apply_patch_and_evaluate(
    tmp_path: Path,
) -> None:
    """A user setting agentic_tools=[] in YAML shouldn't disable the
    agent entirely (it would be a useless run); fall back to
    apply_patch + evaluate (the Phase E default)."""
    runner = _build_stub_runner(tmp_path)
    runner.evo_config.agentic_tools = []

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
            generation=22,
        )
    )
    tools = captured.get("tools") or []
    from shinka.llm.agent.tools.apply_patch import _apply_patch_tool
    from shinka.llm.agent.tools.evaluate import _evaluate_tool

    assert _apply_patch_tool in tools
    assert _evaluate_tool in tools
    assert len(tools) == 2


def test_agentic_tools_default_is_apply_patch_and_evaluate() -> None:
    """The config default is ``["apply_patch", "evaluate"]`` (Phase E):
    the agent can apply patches and call the evaluator inline."""
    from shinka.core.config import EvolutionConfig

    config = EvolutionConfig()
    assert config.agentic_tools == ["apply_patch", "evaluate"]


def test_config_flag_default_routes_to_legacy() -> None:
    """The feature flag must default to False so existing experiments
    continue to use _run_patch_async without opt-in."""
    from shinka.core.config import EvolutionConfig

    config = EvolutionConfig()
    assert config.use_agentic_proposer is False


def test_db_path_threaded_from_runner_db_config(tmp_path: Path) -> None:
    """The ShinkaToolContext.db_path should come from
    ``runner.db.config.db_path`` (the real attribute path on
    ``ProgramDatabase``), so query_evolution_db_tool can read the
    evolution database. We verify by capturing the context that
    run_agent receives."""
    runner = _build_stub_runner(tmp_path)
    runner.db = SimpleNamespace(
        config=SimpleNamespace(db_path="/tmp/run-7/programs.sqlite")
    )

    captured_ctx: list = []

    async def capture(*args: Any, **kwargs: Any) -> Any:
        captured_ctx.append(kwargs["tool_context"])
        return None

    runner.llm.run_agent = AsyncMock(side_effect=capture)

    asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=9,
        )
    )
    assert captured_ctx
    assert captured_ctx[0].db_path == "/tmp/run-7/programs.sqlite"


def test_diff_summary_populated_from_last_successful_patch_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For parity with the legacy ``_run_patch_async`` path, the
    agentic proposal must populate ``meta_patch_data["diff_summary"]``
    via ``summarize_diff(last_successful_patch_path)`` — the webui
    visualization reads this field."""
    runner = _build_stub_runner(tmp_path)
    runner.lang_ext = "py"  # used by the diff_summary unpack

    # Stub summarize_diff to a known return so we can verify wiring
    # without producing a real diff file.
    captured_args: list = []

    def fake_summarize_diff(path: str) -> dict:
        captured_args.append(path)
        return {"original.py": {"lines_added": 5, "lines_removed": 2}}

    monkeypatch.setattr(
        "shinka.core.async_runner.summarize_diff", fake_summarize_diff
    )

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx = kwargs["tool_context"]
        ctx.last_successful_patch_text = "diff body"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 3
        ctx.last_successful_patch_path = "/tmp/gen-11/patch.diff"
        ctx.current_code = "new code"
        ctx.record_tool_call(
            "apply_patch", latency_sec=0.01, success=True,
            extra={"patch_type": "diff", "num_applied": 3},
        )
        return SimpleNamespace(content="", cost=0.1, to_dict=lambda: {})

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=11,
        )
    )

    assert result is not None
    _, meta, _ = result
    # summarize_diff was called with the path the tool recorded.
    assert captured_args == ["/tmp/gen-11/patch.diff"]
    # The orchestrator unpacks original.{lang_ext} per the legacy
    # convention so the webui-friendly shape is exposed at the top.
    assert meta["diff_summary"] == {"lines_added": 5, "lines_removed": 2}


def test_diff_summary_empty_when_no_patch_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the agent never produced a successful patch (no patch_path),
    diff_summary should be an empty dict so downstream code doesn't
    have to special-case missing fields."""
    runner = _build_stub_runner(tmp_path)
    runner.lang_ext = "py"

    def should_not_call(path: str) -> dict:
        raise AssertionError(
            "summarize_diff should not be called when no patch succeeded"
        )

    monkeypatch.setattr(
        "shinka.core.async_runner.summarize_diff", should_not_call
    )

    async def fake_no_success(*args: Any, **kwargs: Any) -> Any:
        # Don't set last_successful_*. Default values stand.
        return SimpleNamespace(content="", cost=0.0, to_dict=lambda: {})

    runner.llm.run_agent = AsyncMock(side_effect=fake_no_success)

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=12,
        )
    )
    assert result is not None
    _, meta, _ = result
    assert meta["diff_summary"] == {}


def test_diff_summary_swallows_summarize_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If summarize_diff raises (corrupt diff file etc.), we log
    and continue with an empty dict — never propagate."""
    runner = _build_stub_runner(tmp_path)
    runner.lang_ext = "py"

    def boom(path: str) -> dict:
        raise RuntimeError("corrupt diff")

    monkeypatch.setattr(
        "shinka.core.async_runner.summarize_diff", boom
    )

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx = kwargs["tool_context"]
        ctx.last_successful_patch_text = "diff body"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 1
        ctx.last_successful_patch_path = "/tmp/gen-13/patch.diff"
        return SimpleNamespace(content="", cost=0.0, to_dict=lambda: {})

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=13,
        )
    )
    assert result is not None
    _, meta, success = result
    # The agentic-side metadata still claims success (patch text exists),
    # but diff_summary degraded gracefully.
    assert success is True
    assert meta["diff_summary"] == {}


def test_db_path_is_none_when_runner_db_is_none(tmp_path: Path) -> None:
    """If ``runner.db`` is None (e.g. early during setup), the tool
    context must still construct without crashing and db_path is
    None — query_evolution_db_tool surfaces this as an Error."""
    runner = _build_stub_runner(tmp_path)
    runner.db = None  # explicit
    captured_ctx: list = []

    async def capture(*args: Any, **kwargs: Any) -> Any:
        captured_ctx.append(kwargs["tool_context"])
        return None

    runner.llm.run_agent = AsyncMock(side_effect=capture)
    asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=10,
        )
    )
    assert captured_ctx
    assert captured_ctx[0].db_path is None


def test_evaluator_is_bound_on_tool_context(tmp_path: Path) -> None:
    """Phase E: the orchestrator wires an EvaluatorCallable into
    tool_ctx.evaluator before the agent run. The agent's evaluate_tool
    relies on this being non-None; without it the tool returns an error
    string instead of running the evaluator."""
    runner = _build_stub_runner(tmp_path)
    captured_ctx: list = []

    async def capture(*args: Any, **kwargs: Any) -> Any:
        captured_ctx.append(kwargs["tool_context"])
        return None

    runner.llm.run_agent = AsyncMock(side_effect=capture)
    asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=11,
        )
    )
    assert captured_ctx
    assert captured_ctx[0].evaluator is not None
    assert callable(captured_ctx[0].evaluator)


def test_cached_eval_result_surfaces_in_meta_patch_data(tmp_path: Path) -> None:
    """Phase E: when the agent's evaluate_tool ran and wrote
    last_eval_result on the context, the proposer surfaces it via
    meta_patch_data['_cached_eval_results'] so the downstream pipeline
    short-circuits the scheduler-submit step."""
    runner = _build_stub_runner(tmp_path)

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        # Simulate apply_patch + evaluate inside the agent loop.
        ctx.last_successful_patch_text = "patch"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 1
        ctx.last_eval_result = {
            "correct": {"correct": True},
            "metrics": {"combined_score": 0.75},
            "stdout_log": "",
            "stderr_log": "",
        }
        ctx.last_eval_rtime = 1.5
        ctx.record_tool_call("apply_patch", 0.01, success=True)
        ctx.record_tool_call(
            "evaluate", 1.5, success=True, extra={"combined_score": 0.75}
        )
        return SimpleNamespace(
            content="<NAME>x</NAME>",
            cost=0.01,
            to_dict=lambda: {},
        )

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)
    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=12,
        )
    )
    assert result is not None
    _patch, meta, success = result
    assert success is True
    cached = meta.get("_cached_eval_results")
    assert cached is not None
    assert cached["correct"]["correct"] is True
    assert cached["metrics"]["combined_score"] == 0.75
    assert meta.get("_cached_eval_rtime") == 1.5


def test_no_cached_eval_keys_when_agent_did_not_evaluate(tmp_path: Path) -> None:
    """If the agent's evaluate_tool wasn't called (or errored without
    setting last_eval_result), the cached-eval keys are absent from
    meta_patch_data — the downstream pipeline falls back to the normal
    scheduler-submit path."""
    runner = _build_stub_runner(tmp_path)

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        ctx.last_successful_patch_text = "patch"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 1
        # Note: ctx.last_eval_result intentionally left as None.
        return SimpleNamespace(
            content="<NAME>x</NAME>", cost=0.01, to_dict=lambda: {}
        )

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)
    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=13,
        )
    )
    assert result is not None
    _patch, meta, _success = result
    assert "_cached_eval_results" not in meta
    assert "_cached_eval_rtime" not in meta


def test_async_running_job_cached_results_defaults_none() -> None:
    """The AsyncRunningJob dataclass adds cached_results=None as default
    so legacy (scheduler-submit) jobs don't need to know about Phase E."""
    from shinka.core.async_runner import AsyncRunningJob

    job = AsyncRunningJob(
        job_id="x",
        exec_fname="/tmp/x.py",
        results_dir="/tmp/results",
        start_time=0.0,
        proposal_started_at=0.0,
        evaluation_submitted_at=0.0,
        generation=0,
    )
    assert job.cached_results is None

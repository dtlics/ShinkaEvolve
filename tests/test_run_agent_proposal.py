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
    # Phase 1 of research-grounding: Azure-aware call kwargs are read off
    # evo_config inside _run_agent_proposal. Explicit defaults keep the
    # MagicMock from auto-generating truthy values.
    evo_config.store_llm_responses = False
    evo_config.cache_static_system_prompt = True
    evo_config.tag_calls_with_metadata = True

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
        # Phase 1 of research-grounding: the proposer tags every Azure
        # call with a stable run_id + purpose/generation/island_idx via
        # _build_call_metadata. Tests don't care about the dict contents;
        # they just need the helper to exist on the stub.
        run_id="test-run",
        # Phase 2 of research-grounding: the proposer reads
        # _latest_island_briefs to inject per-island DR briefs into the
        # sampler. Tests default to empty so no brief is injected
        # unless they populate it explicitly.
        _latest_island_briefs={},
        # Phase 3 of research-grounding: the proposer reads
        # _latest_island_brief_obj to pick a BriefItem for the
        # literature_grounded mutation arm. Empty by default.
        _latest_island_brief_obj={},
        # Mocked methods on the class — bind them as plain async funcs.
        _get_current_system_prompt=MagicMock(return_value=(None, None)),
        _save_patch_attempt_async=AsyncMock(),
        _print_metadata_table=MagicMock(),
        _build_call_metadata=MagicMock(
            return_value={"run_id": "test-run", "purpose": "proposer"}
        ),
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


def test_agentic_tools_default_is_just_apply_patch() -> None:
    """The config default is ``["apply_patch"]`` after doom-remediation
    Fix 1: every successful apply auto-runs the evaluator, so the LLM
    never needs to call ``evaluate`` explicitly. Tasks that want
    manual eval control can opt ``"evaluate"`` back in via config."""
    from shinka.core.config import EvolutionConfig

    config = EvolutionConfig()
    assert config.agentic_tools == ["apply_patch"]


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


def test_structured_output_used_when_final_output_obj_set(tmp_path: Path) -> None:
    """C4: when the agent returns a PatchProposalOutput on
    response.final_output_obj, the orchestrator prefers the typed
    fields over regex-extracting from response.content."""
    from shinka.llm.agent import PatchProposalOutput

    runner = _build_stub_runner(tmp_path)

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        ctx.last_successful_patch_text = "patch"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 1
        ctx.record_tool_call("apply_patch", 0.01, success=True)
        # The agent returned BOTH a structured object AND text with
        # tags. The orchestrator must prefer the typed object.
        return SimpleNamespace(
            content="<NAME>regex-name</NAME><DESCRIPTION>regex-desc</DESCRIPTION>",
            final_output_obj=PatchProposalOutput(
                name="typed-name", description="typed-desc"
            ),
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
            generation=14,
        )
    )
    assert result is not None
    _patch, meta, success = result
    assert success is True
    # The structured output wins; the regex would have produced
    # "regex-name"/"regex-desc".
    assert meta["patch_name"] == "typed-name"
    assert meta["patch_description"] == "typed-desc"


def test_structured_output_falls_back_to_regex_when_obj_missing(tmp_path: Path) -> None:
    """C4: when response.final_output_obj is None (e.g. legacy provider
    path, or output_type unsupported), the orchestrator falls back to
    extract_between on response.content for parity."""
    runner = _build_stub_runner(tmp_path)

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        ctx.last_successful_patch_text = "patch"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 1
        ctx.record_tool_call("apply_patch", 0.01, success=True)
        return SimpleNamespace(
            content="<NAME>fallback</NAME><DESCRIPTION>fb-desc</DESCRIPTION>",
            final_output_obj=None,
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
            generation=15,
        )
    )
    assert result is not None
    _patch, meta, _success = result
    assert meta["patch_name"] == "fallback"
    assert meta["patch_description"] == "fb-desc"


def test_output_type_is_passed_to_run_agent(tmp_path: Path) -> None:
    """C4: _run_agent_proposal passes PatchProposalOutput as output_type
    so the SDK auto-instructs the model to produce structured output."""
    from shinka.llm.agent import PatchProposalOutput

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
            generation=16,
        )
    )
    assert captured.get("output_type") is PatchProposalOutput


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


# ----------------------------------------------------------------------
# Phase 1 of research-grounding: the proposer must tag each agent run
# with the Azure-aware call kwargs (call_metadata + store +
# safety_identifier + cache_static_prompt) so cost dashboards can
# attribute spend per feature and the Responses API hits its cache.
# ----------------------------------------------------------------------


def test_proposer_threads_azure_kwargs_into_run_agent(tmp_path: Path) -> None:
    """``_run_agent_proposal`` must build a ``call_metadata`` dict via
    ``_build_call_metadata`` and forward it (plus store=False,
    safety_identifier=run_id, cache_static_prompt) into
    ``self.llm.run_agent``."""
    runner = _build_stub_runner(tmp_path)

    # Capture the kwargs that reach run_agent. The stub's run_agent
    # mutates the tool_context so the success path is exercised.
    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        ctx.last_successful_patch_text = "diff"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 1
        return SimpleNamespace(
            content="<NAME>x</NAME><DESCRIPTION>y</DESCRIPTION>",
            cost=0.0,
            to_dict=lambda: {},
        )

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)

    # Reset the stub's mock so we can re-assert it was called with the
    # right kwargs (the default stub returns a fixed dict, which is
    # fine for the test — we just want to confirm it was invoked).
    runner._build_call_metadata = MagicMock(
        return_value={
            "run_id": "test-run",
            "purpose": "proposer",
            "generation": "9",
            "island_idx": "3",
        }
    )

    parent = SimpleNamespace(
        code="def f(): return 1\n", id="parent-9", island_idx=3
    )

    asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=parent,
            archive_programs=[],
            top_k_programs=[],
            generation=9,
        )
    )

    # _build_call_metadata called with the proposer purpose + the
    # parent's island_idx + the current generation.
    runner._build_call_metadata.assert_called_once()
    kwargs = runner._build_call_metadata.call_args.kwargs
    assert kwargs.get("purpose") == "proposer"
    assert kwargs.get("generation") == 9
    assert kwargs.get("island_idx") == 3

    # And those flow through to run_agent.
    run_kwargs = runner.llm.run_agent.await_args.kwargs
    assert run_kwargs["call_metadata"] == {
        "run_id": "test-run",
        "purpose": "proposer",
        "generation": "9",
        "island_idx": "3",
    }
    # store=False because evo_config.store_llm_responses is False by
    # default — the runner translates that to an explicit ``False``
    # to override Azure's store-by-default behavior.
    assert run_kwargs["store"] is False
    # safety_identifier carries the run's stable id.
    assert run_kwargs["safety_identifier"] == "test-run"
    # cache_static_prompt mirrors evo_config.cache_static_system_prompt.
    assert run_kwargs["cache_static_prompt"] is True


# ----------------------------------------------------------------------
# Phase 1b of research-grounding: fix_telemetry derived from the agent's
# tool trace. Surfaces in-loop fix dynamics that the single bandit
# reward (combined_score) cannot distinguish — "model fixed an eval
# failure" vs "model succeeded first try" look identical to the bandit.
# ----------------------------------------------------------------------


def test_summarize_fix_telemetry_first_try_success() -> None:
    """One apply + one passing eval ⇒ no failure to fix."""
    from shinka.core.async_runner import _summarize_fix_telemetry

    trace = [
        {"name": "apply_patch", "success": True, "num_applied": 1},
        {"name": "evaluate", "success": True, "combined_score": 0.9},
    ]
    out = _summarize_fix_telemetry(trace)
    assert out == {
        "apply_attempts": 1,
        "eval_attempts": 1,
        "had_failure_then_success": False,
        "final_correct": True,
    }


def test_summarize_fix_telemetry_fixed_after_failure() -> None:
    """Two applies + a fail-then-pass eval sequence ⇒ fixed inside loop."""
    from shinka.core.async_runner import _summarize_fix_telemetry

    trace = [
        {"name": "apply_patch", "success": True, "num_applied": 1},
        {"name": "evaluate", "success": False, "combined_score": 0.0},
        {"name": "apply_patch", "success": True, "num_applied": 1},
        {"name": "evaluate", "success": True, "combined_score": 0.95},
    ]
    out = _summarize_fix_telemetry(trace)
    assert out == {
        "apply_attempts": 2,
        "eval_attempts": 2,
        "had_failure_then_success": True,
        "final_correct": True,
    }


def test_summarize_fix_telemetry_never_fixed() -> None:
    """Agent gave up after a failed eval ⇒ final_correct stays False."""
    from shinka.core.async_runner import _summarize_fix_telemetry

    trace = [
        {"name": "apply_patch", "success": True, "num_applied": 1},
        {"name": "evaluate", "success": False, "combined_score": 0.0},
        {"name": "apply_patch", "success": False, "error": "diff parse error"},
    ]
    out = _summarize_fix_telemetry(trace)
    assert out == {
        "apply_attempts": 2,
        "eval_attempts": 1,
        "had_failure_then_success": False,
        "final_correct": False,
    }


def test_summarize_fix_telemetry_no_evaluate_calls() -> None:
    """When the agent never evaluates, ``final_correct`` is None.
    This happens when the agent runs out of turns before reaching an
    evaluate, or when ``evaluate`` isn't in agentic_tools."""
    from shinka.core.async_runner import _summarize_fix_telemetry

    trace = [
        {"name": "apply_patch", "success": True, "num_applied": 1},
    ]
    out = _summarize_fix_telemetry(trace)
    assert out == {
        "apply_attempts": 1,
        "eval_attempts": 0,
        "had_failure_then_success": False,
        "final_correct": None,
    }


def test_summarize_fix_telemetry_ignores_other_tools() -> None:
    """``web_search`` / ``read_host_file`` / ``query_evolution_db``
    don't speak to fix-skill and must not pollute the counts."""
    from shinka.core.async_runner import _summarize_fix_telemetry

    trace = [
        {"name": "web_search", "success": True},
        {"name": "apply_patch", "success": True, "num_applied": 2},
        {"name": "read_host_file", "success": True},
        {"name": "evaluate", "success": True, "combined_score": 0.7},
    ]
    out = _summarize_fix_telemetry(trace)
    assert out["apply_attempts"] == 1
    assert out["eval_attempts"] == 1
    assert out["had_failure_then_success"] is False


def test_proposer_writes_fix_telemetry_into_meta_patch_data(
    tmp_path: Path,
) -> None:
    """End-to-end: ``_run_agent_proposal`` must compute fix_telemetry
    from the captured tool trace and surface it on meta_patch_data so
    the orchestrator persists it onto ``Program.metadata.fix_telemetry``."""
    runner = _build_stub_runner(tmp_path)

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        # Simulate: apply → eval fails → apply → eval passes. The
        # agent's continuous-context loop fixed its own failure.
        ctx.record_tool_call(
            "apply_patch", latency_sec=0.01, success=True,
            extra={"num_applied": 1, "patch_type": "diff"},
        )
        ctx.record_tool_call(
            "evaluate", latency_sec=0.5, success=False,
            error="combined_score=0",
            extra={"combined_score": 0.0},
        )
        ctx.record_tool_call(
            "apply_patch", latency_sec=0.01, success=True,
            extra={"num_applied": 1, "patch_type": "diff"},
        )
        ctx.record_tool_call(
            "evaluate", latency_sec=0.5, success=True,
            extra={"combined_score": 0.85},
        )
        ctx.last_successful_patch_text = "diff-final"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 1
        return SimpleNamespace(
            content="<NAME>fix</NAME><DESCRIPTION>x</DESCRIPTION>",
            cost=0.0,
            to_dict=lambda: {},
        )

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)
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
    _, meta, success = result
    assert success is True
    assert "fix_telemetry" in meta
    assert meta["fix_telemetry"] == {
        "apply_attempts": 2,
        "eval_attempts": 2,
        "had_failure_then_success": True,
        "final_correct": True,
    }


def test_proposer_respects_disabled_metadata_tagging(tmp_path: Path) -> None:
    """When ``evo_config.tag_calls_with_metadata`` is False, the
    runner's ``_build_call_metadata`` returns None (cheap branch),
    and ``call_metadata`` arrives at ``run_agent`` as None too."""
    runner = _build_stub_runner(tmp_path)

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        ctx.last_successful_patch_text = "diff"
        ctx.last_successful_patch_type = "diff"
        ctx.last_successful_num_applied = 1
        return SimpleNamespace(
            content="<NAME>x</NAME><DESCRIPTION>y</DESCRIPTION>",
            cost=0.0,
            to_dict=lambda: {},
        )

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)
    runner._build_call_metadata = MagicMock(return_value=None)

    asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_parent_program(),
            archive_programs=[],
            top_k_programs=[],
            generation=1,
        )
    )

    run_kwargs = runner.llm.run_agent.await_args.kwargs
    assert run_kwargs["call_metadata"] is None

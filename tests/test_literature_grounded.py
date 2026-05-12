"""Phase 3 of research-grounding — literature_grounded mutation arm.

The arm joins ``diff`` / ``full`` / ``cross`` with probability
``literature_grounded_prob`` when ``enable_literature_grounded=True``
and the parent's island has a DR brief item with a non-empty
``reference_snippet``. Picking the arm means: render a constrained
prompt that includes the brief item, run the agent with
``web_search`` enabled for THIS call only, and treat a parent-unchanged
output as a legitimate abort (skip bandit update).

Tests cover the sampler suppression + dispatch in 3a; the orchestrator
wiring (agentic_tools override + abort handling) is exercised in 3b.
"""

from __future__ import annotations

import numpy as np

import pytest

from shinka.core.sampler import PromptSampler
from shinka.database import Program


def _parent() -> Program:
    return Program(
        id="parent-1",
        code="def f(): return 1\n",
        correct=True,
        combined_score=0.5,
        generation=10,
        island_idx=0,
    )


def _brief_item() -> dict:
    return {
        "idea": "cache-oblivious tiling",
        "rationale": "reduces L1 misses for small GEMMs",
        "reference_source": "https://example.com/cobtiling.pdf",
        "reference_snippet": "Pick T < sqrt(L1 / 2) for double precision",
        "gotchas": "Dense matrices only; sparse needs a different scheme",
    }


def test_literature_grounded_dispatched_when_chosen() -> None:
    """When the sampler picks literature_grounded, the system msg
    must include the LIT_GROUNDED constraints and the user msg must
    embed the brief item's idea + reference_snippet."""
    sampler = PromptSampler(
        task_sys_msg="TASK-SYS",
        patch_types=["literature_grounded"],
        patch_type_probs=[1.0],
        language="python",
    )
    sys_msg, user_msg, patch_type = sampler.sample(
        parent=_parent(),
        archive_inspirations=[],
        top_k_inspirations=[],
        meta_recommendations="should-be-suppressed-for-lit_grounded",
        literature_grounded_item=_brief_item(),
    )
    assert patch_type == "literature_grounded"
    # The constraint sentence is the key signal that the right
    # prompt template fired. The template uses "do NOT pursue
    # alternative ideas" (capital NOT for emphasis); test on
    # lower-cased form for robustness.
    assert "do not pursue alternative ideas" in sys_msg.lower()
    # Fabrication-prevention clause is present.
    assert "fabrication" in sys_msg.lower()
    # The brief item is rendered into the user prompt verbatim.
    assert "cache-oblivious tiling" in user_msg
    assert "Pick T < sqrt(L1 / 2) for double precision" in user_msg
    # Meta recs are suppressed for literature_grounded (the brief item
    # IS the focus material; we don't want a generic rec competing).
    assert "should-be-suppressed-for-lit_grounded" not in sys_msg


def test_literature_grounded_suppressed_when_no_reference_snippet() -> None:
    """When the chosen patch_types include literature_grounded but no
    brief item has a non-empty reference_snippet, the sampler must
    drop literature_grounded and pick from the remaining types."""
    sampler = PromptSampler(
        task_sys_msg="TASK-SYS",
        patch_types=["diff", "literature_grounded"],
        patch_type_probs=[0.5, 0.5],
        language="python",
    )
    # No item ⇒ suppress.
    np.random.seed(0)
    _, _, patch_type = sampler.sample(
        parent=_parent(),
        archive_inspirations=[],
        top_k_inspirations=[],
        literature_grounded_item=None,
    )
    assert patch_type == "diff"

    # Empty reference_snippet ⇒ also suppress.
    empty_item = {"idea": "x", "reference_snippet": ""}
    np.random.seed(0)
    _, _, patch_type = sampler.sample(
        parent=_parent(),
        archive_inspirations=[],
        top_k_inspirations=[],
        literature_grounded_item=empty_item,
    )
    assert patch_type == "diff"

    # Whitespace-only reference_snippet ⇒ also suppress (would be
    # useless to the LLM).
    ws_item = {"idea": "x", "reference_snippet": "   \n\n"}
    np.random.seed(0)
    _, _, patch_type = sampler.sample(
        parent=_parent(),
        archive_inspirations=[],
        top_k_inspirations=[],
        literature_grounded_item=ws_item,
    )
    assert patch_type == "diff"


def test_literature_grounded_and_cross_can_be_suppressed_simultaneously() -> None:
    """Both suppression rules compose: with no inspirations AND no
    brief item, the sampler renormalizes to the remaining types
    (here, just ``diff``)."""
    sampler = PromptSampler(
        task_sys_msg="T",
        patch_types=["diff", "cross", "literature_grounded"],
        patch_type_probs=[0.2, 0.4, 0.4],
        language="python",
    )
    np.random.seed(0)
    _, _, patch_type = sampler.sample(
        parent=_parent(),
        archive_inspirations=[],
        top_k_inspirations=[],
        literature_grounded_item=None,
    )
    assert patch_type == "diff"


# ----------------------------------------------------------------------
# Phase 3b: orchestrator wiring — agentic_tools override, web_search
# context size, max_turns budget bump, abort handling, call metadata
# purpose tag.
# ----------------------------------------------------------------------

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from shinka.core.async_runner import ShinkaEvolveRunner
from shinka.core.deep_research_summarizer import BriefItem, DRBrief
from shinka.llm.agent.tools import ShinkaToolContext


def _runner_stub(tmp_path: Path, *, enable_lit: bool = True):
    """Build a SimpleNamespace runner that exercises the
    literature_grounded path. Mirrors the proposer test stub but with
    DR-aware fields populated."""
    prompt_sampler = MagicMock()
    prompt_sampler.task_sys_msg = "orig"
    prompt_sampler.sample = MagicMock(
        return_value=("sys", "user", "literature_grounded")
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
    evo_config.max_patch_attempts = 1
    evo_config.language = "python"
    evo_config.store_llm_responses = False
    evo_config.cache_static_system_prompt = True
    evo_config.tag_calls_with_metadata = True
    evo_config.enable_literature_grounded = enable_lit
    evo_config.literature_grounded_max_turns = 6
    evo_config.literature_grounded_web_search_context_size = "high"
    evo_config.agentic_tools = ["apply_patch", "evaluate"]

    scheduler = MagicMock()
    scheduler.run = MagicMock(
        return_value=(
            {"correct": {"correct": True}, "metrics": {"combined_score": 1.0}},
            0.0,
        )
    )

    # Brief object with one eligible item.
    brief = DRBrief(
        island_idx=0,
        generation=10,
        items=[
            BriefItem(
                idea="hierarchical tiling",
                rationale="reduces L1 misses",
                reference_source="paper.pdf",
                reference_snippet="Pick T < sqrt(L1/2).",
                gotchas="dense only",
            )
        ],
        candidate_question="How to tile?",
        rendered_markdown="**Idea 1**: hierarchical tiling",
    )

    return SimpleNamespace(
        prompt_sampler=prompt_sampler,
        llm=llm,
        evo_config=evo_config,
        results_dir=str(tmp_path),
        lang_ext="py",
        scheduler=scheduler,
        verbose=False,
        db=None,
        llm_selection=None,
        run_id="test-run",
        _latest_island_briefs={0: brief.rendered_markdown},
        _latest_island_brief_obj={0: brief},
        _get_current_system_prompt=MagicMock(return_value=(None, None)),
        _save_patch_attempt_async=AsyncMock(),
        _print_metadata_table=MagicMock(),
        _build_call_metadata=MagicMock(
            side_effect=lambda **kw: {"purpose": kw.get("purpose", "proposer")}
        ),
    )


def _lit_parent() -> Program:
    return Program(
        id="parent-lit",
        code="def f(): return 1\n",
        correct=True,
        combined_score=0.5,
        generation=10,
        island_idx=0,
    )


def test_run_agent_proposal_overrides_agentic_tools_for_lit_grounded(
    tmp_path: Path,
) -> None:
    """When the sampler picks literature_grounded, the orchestrator
    must force ``["apply_patch", "evaluate", "web_search"]`` for THIS
    call regardless of the global agentic_tools config."""
    runner = _runner_stub(tmp_path)

    async def fake_run_agent(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        ctx.last_successful_patch_text = "diff"
        ctx.last_successful_patch_type = "literature_grounded"
        ctx.last_successful_num_applied = 1
        return SimpleNamespace(
            content="<NAME>x</NAME><DESCRIPTION>y</DESCRIPTION>",
            cost=0.0,
            to_dict=lambda: {},
        )

    runner.llm.run_agent = AsyncMock(side_effect=fake_run_agent)

    asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_lit_parent(),
            archive_programs=[],
            top_k_programs=[],
            generation=10,
        )
    )

    # The actual tool list passed to run_agent must include web_search.
    tools_passed = runner.llm.run_agent.await_args.kwargs["tools"]
    # We can't introspect FunctionTool instances trivially, but the SDK's
    # WebSearchTool has a distinct class name we can check.
    tool_class_names = {type(t).__name__ for t in tools_passed}
    assert "WebSearchTool" in tool_class_names, (
        f"Expected WebSearchTool to be in tools, got {tool_class_names}"
    )

    # max_turns honors literature_grounded_max_turns (6) rather than
    # max_patch_attempts*3 (3).
    assert runner.llm.run_agent.await_args.kwargs["max_turns"] == 6

    # call_metadata was built with purpose="lit_grounded".
    purpose_calls = [
        c.kwargs.get("purpose")
        for c in runner._build_call_metadata.call_args_list
    ]
    assert "lit_grounded" in purpose_calls


def test_run_agent_proposal_abort_marks_meta_patch_data(tmp_path: Path) -> None:
    """When the agent returns with NO apply_patch calls AND the chosen
    patch_type is literature_grounded, the orchestrator must set
    ``abort_reason="insufficient_reference"`` so the downstream bandit
    update skips this row."""
    runner = _runner_stub(tmp_path)

    async def aborting_run_agent(*args: Any, **kwargs: Any) -> Any:
        # Agent returns the parent unchanged — no apply_patch calls,
        # no tool trace entries. The DESCRIPTION explains why.
        return SimpleNamespace(
            content="<NAME>abort</NAME><DESCRIPTION>reference insufficient</DESCRIPTION>",
            cost=0.0,
            to_dict=lambda: {},
        )

    runner.llm.run_agent = AsyncMock(side_effect=aborting_run_agent)

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_lit_parent(),
            archive_programs=[],
            top_k_programs=[],
            generation=10,
        )
    )
    assert result is not None
    _, meta, success = result
    assert success is False
    assert meta.get("abort_reason") == "insufficient_reference"


def test_run_agent_proposal_does_not_abort_when_apply_was_attempted(
    tmp_path: Path,
) -> None:
    """Failed apply_patch attempts ≠ abort. abort_reason is only set
    when the agent never tried apply_patch (deliberate abort)."""
    runner = _runner_stub(tmp_path)

    async def attempted_but_failed(*args: Any, **kwargs: Any) -> Any:
        ctx: ShinkaToolContext = kwargs["tool_context"]
        # The agent tried apply_patch but it failed.
        ctx.record_tool_call(
            "apply_patch",
            latency_sec=0.01,
            success=False,
            error="diff hunk parse error",
            extra={"patch_type": "literature_grounded"},
        )
        return SimpleNamespace(
            content="<NAME>x</NAME><DESCRIPTION>diff didn't apply</DESCRIPTION>",
            cost=0.0,
            to_dict=lambda: {},
        )

    runner.llm.run_agent = AsyncMock(side_effect=attempted_but_failed)

    result = asyncio.run(
        ShinkaEvolveRunner._run_agent_proposal(
            runner,
            parent_program=_lit_parent(),
            archive_programs=[],
            top_k_programs=[],
            generation=10,
        )
    )
    assert result is not None
    _, meta, success = result
    assert success is False
    assert "abort_reason" not in meta


def test_literature_grounded_uses_island_brief_when_provided() -> None:
    """When ``island_brief`` is given AND patch_type is NOT
    literature_grounded, the island brief still flows into the rec
    slot (existing Phase 2 behavior). When patch_type IS
    literature_grounded, the brief item supersedes — and meta-rec is
    suppressed entirely."""
    sampler = PromptSampler(
        task_sys_msg="T",
        patch_types=["literature_grounded"],
        patch_type_probs=[1.0],
        language="python",
    )
    sys_msg, _user_msg, _pt = sampler.sample(
        parent=_parent(),
        archive_inspirations=[],
        top_k_inspirations=[],
        island_brief="ISLAND-MD",
        literature_grounded_item=_brief_item(),
    )
    # ISLAND-MD must NOT appear — meta-rec slot is suppressed for
    # literature_grounded (brief item is the focus material).
    assert "ISLAND-MD" not in sys_msg

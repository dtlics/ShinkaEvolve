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

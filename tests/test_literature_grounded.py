"""Phase 6 tests for the literature_grounded mutation arm.

Phase 6a coverage: prompt module, EvolutionConfig flags, sampler
suppression rule, and sample() dispatch. Phase 6b will add the runner-
side wiring (web tools + budget on the actual LLM call).
"""

from __future__ import annotations

from typing import Any, List

import numpy as np
import pytest

from shinka.core.config import EvolutionConfig
from shinka.core.deep_research_summarizer import BriefItem, IslandBrief
from shinka.core.sampler import (
    PromptSampler,
    _brief_has_grounded_item,
    _pick_brief_item,
)
from shinka.database import Program
from shinka.prompts import (
    LIT_GROUNDED_ITER_MSG,
    LIT_GROUNDED_SYS_FORMAT,
)


# ---------------------------------------------------------------------------
# Config flags
# ---------------------------------------------------------------------------


def test_literature_grounded_config_defaults():
    cfg = EvolutionConfig()
    assert cfg.enable_literature_grounded is False
    assert cfg.literature_grounded_prob == pytest.approx(0.1)
    assert cfg.literature_grounded_max_searches == 3
    assert cfg.literature_grounded_max_fetches == 2
    assert cfg.literature_grounded_max_turns == 6


# ---------------------------------------------------------------------------
# Prompt module
# ---------------------------------------------------------------------------


def test_lit_grounded_sys_format_carries_load_bearing_clauses():
    """The system message must explicitly tell the model:
    - NOT to pursue alternative ideas
    - That a clean abort is allowed
    - The output format markers
    Loss of any of these phrases is a regression."""
    text = LIT_GROUNDED_SYS_FORMAT
    # Normalize whitespace so wrapped lines don't fool the substring match.
    flat = " ".join(text.lower().split())
    assert "do not pursue alternative" in flat
    assert "even if you think of better ones" in flat
    assert "clean abort" in flat
    assert "<NAME>" in text and "</NAME>" in text
    assert "<DESCRIPTION>" in text and "</DESCRIPTION>" in text
    assert "<CODE>" in text and "</CODE>" in text


def test_lit_grounded_iter_msg_renders_item_fields():
    rendered = LIT_GROUNDED_ITER_MSG.format(
        language="python",
        code_content="def f(): pass",
        performance_metrics="score=0.5",
        text_feedback_section="\n# feedback\n",
        idea="cosine schedule",
        rationale="smooth decay",
        reference_snippet="for step in range(...):\n    lr = ...",
        source="https://arxiv.org/abs/x",
        gotchas="reset on warm restart",
    )
    assert "cosine schedule" in rendered
    assert "smooth decay" in rendered
    assert "https://arxiv.org/abs/x" in rendered
    assert "for step in range(...)" in rendered
    assert "reset on warm restart" in rendered
    assert "def f(): pass" in rendered


# ---------------------------------------------------------------------------
# Suppression helpers
# ---------------------------------------------------------------------------


def test_brief_has_grounded_item_false_for_missing():
    assert _brief_has_grounded_item(None) is False


def test_brief_has_grounded_item_false_for_empty_brief():
    brief = IslandBrief(island_idx=0, summary="x", items=[])
    assert _brief_has_grounded_item(brief) is False


def test_brief_has_grounded_item_false_when_all_snippets_empty():
    brief = IslandBrief(
        island_idx=0,
        summary="x",
        items=[
            BriefItem(idea="a", rationale="r"),
            BriefItem(idea="b", rationale="r", reference_snippet="   "),
        ],
    )
    assert _brief_has_grounded_item(brief) is False


def test_brief_has_grounded_item_true_when_any_snippet_present():
    brief = IslandBrief(
        island_idx=0,
        summary="x",
        items=[
            BriefItem(idea="a", rationale="r"),
            BriefItem(idea="b", rationale="r", reference_snippet="snippet"),
        ],
    )
    assert _brief_has_grounded_item(brief) is True


def test_pick_brief_item_skips_ungrounded_items():
    brief = IslandBrief(
        island_idx=0,
        summary="x",
        items=[
            BriefItem(idea="empty", rationale="r"),
            BriefItem(idea="grounded", rationale="r", reference_snippet="s"),
        ],
    )
    # 10 picks should always be the grounded one.
    for _ in range(10):
        item = _pick_brief_item(brief)
        assert item is not None
        assert item.idea == "grounded"


def test_pick_brief_item_returns_none_when_no_grounded():
    brief = IslandBrief(island_idx=0, summary="x", items=[])
    assert _pick_brief_item(brief) is None
    assert _pick_brief_item(None) is None


# ---------------------------------------------------------------------------
# Sampler suppression + dispatch
# ---------------------------------------------------------------------------


def _parent() -> Program:
    return Program(
        id="p",
        code="def f():\n    return 1\n",
        generation=0,
        combined_score=0.5,
    )


def _inspirations() -> List[Program]:
    return [
        Program(
            id="i1",
            code="def f():\n    return 2\n",
            generation=0,
            combined_score=0.6,
        )
    ]


def _grounded_brief() -> IslandBrief:
    return IslandBrief(
        island_idx=0,
        summary="LR scheduling family",
        items=[
            BriefItem(
                idea="cosine schedule",
                rationale="smooth decay",
                reference_snippet="for step in range(...):\n    lr = ...",
                source="https://arxiv.org/abs/x",
                gotchas="reset on warm restart",
            )
        ],
    )


def _force_lit_grounded_sampler() -> PromptSampler:
    """Construct a sampler that only emits ``literature_grounded`` so we
    can assert on its dispatch output without flakiness."""
    return PromptSampler(
        language="python",
        patch_types=["literature_grounded"],
        patch_type_probs=[1.0],
    )


def test_sampler_dispatches_literature_grounded_with_brief_item():
    sampler = _force_lit_grounded_sampler()
    sys_msg, user_msg, patch_type = sampler.sample(
        parent=_parent(),
        archive_inspirations=_inspirations(),
        top_k_inspirations=[],
        meta_recommendations=None,
        island_brief=_grounded_brief(),
    )
    assert patch_type == "literature_grounded"
    # System prompt carries the load-bearing clause (whitespace-flattened).
    sys_flat = " ".join(sys_msg.lower().split())
    assert "do not pursue alternative" in sys_flat
    # User prompt carries the brief item's content.
    assert "cosine schedule" in user_msg
    assert "smooth decay" in user_msg
    assert "https://arxiv.org/abs/x" in user_msg
    assert "for step in range" in user_msg


def test_sampler_suppresses_literature_grounded_when_brief_is_empty():
    sampler = PromptSampler(
        language="python",
        # 50/50 between diff and lit_grounded; with no grounded brief the
        # suppression rule should force all 20 samples to be diff.
        patch_types=["diff", "literature_grounded"],
        patch_type_probs=[0.5, 0.5],
    )
    parent = _parent()
    seen_types: List[str] = []
    for _ in range(20):
        _, _, patch_type = sampler.sample(
            parent=parent,
            archive_inspirations=_inspirations(),
            top_k_inspirations=[],
            meta_recommendations=None,
            island_brief=None,
        )
        seen_types.append(patch_type)
    assert "literature_grounded" not in seen_types
    assert all(pt == "diff" for pt in seen_types)


def test_sampler_unsuppresses_literature_grounded_when_brief_has_grounded_item():
    """With a grounded brief in scope, the arm becomes eligible. Across
    many samples we should see at least one literature_grounded pick."""
    np.random.seed(123)
    sampler = PromptSampler(
        language="python",
        patch_types=["diff", "literature_grounded"],
        patch_type_probs=[0.5, 0.5],
    )
    brief = _grounded_brief()
    seen_types: List[str] = []
    for _ in range(40):
        _, _, patch_type = sampler.sample(
            parent=_parent(),
            archive_inspirations=_inspirations(),
            top_k_inspirations=[],
            meta_recommendations=None,
            island_brief=brief,
        )
        seen_types.append(patch_type)
    assert "literature_grounded" in seen_types
    assert "diff" in seen_types


def test_sampler_suppresses_cross_and_lit_together_when_both_ineligible():
    """The two suppression rules compose: no inspirations AND no grounded
    brief together suppress both ``cross`` and ``literature_grounded``."""
    sampler = PromptSampler(
        language="python",
        patch_types=["diff", "cross", "literature_grounded"],
        patch_type_probs=[0.4, 0.3, 0.3],
    )
    parent = _parent()
    seen_types: List[str] = []
    for _ in range(20):
        _, _, patch_type = sampler.sample(
            parent=parent,
            archive_inspirations=[],  # cross suppressed
            top_k_inspirations=[],
            meta_recommendations=None,
            island_brief=None,  # lit_grounded suppressed
        )
        seen_types.append(patch_type)
    # Only diff survives the joint suppression.
    assert set(seen_types) == {"diff"}

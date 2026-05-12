"""Phase 5 of research-grounding: the four-stage deep-research meta pipeline.

Lifecycle (per evaluated-program tick of ``dr_meta_interval``):

1. **Stage A -- drift check** (cheap): per-island, returns a drift score
   + one-sentence justification. Below ``dr_drift_threshold`` the island
   re-uses its previous brief unchanged and we exit early.
2. **Stage B -- cross-island novelty**: embed the drift justification,
   look it up against ``dr_brief_cache``. If similarity above
   ``dr_brief_cache_threshold``, link the existing brief to this island
   (cross-pollination dedup) and exit.
3. **Stage C -- deep research**: ``o3-deep-research`` produces a
   structured brief grounded in literature + reference snippets.
   Background+poll under ``POLL_TIMEOUT_DR``.
4. **Stage D -- code grounding**: bounded web search + fetch fills in
   source URLs and snippets the DR pass left empty, restricted to
   ``dr_code_grounding_domains``.

Phase 5b ships Stage A and Stage B only. Stage C / D land in Phase 5c.
Phase 5d wires the summarizer into the runner.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from shinka.prompts.prompts_deep_research import (
    DRIFT_CHECK_SYS_MSG,
    DRIFT_CHECK_USER_MSG,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class BriefItem:
    """One concrete technique entry from a DR brief."""

    idea: str
    rationale: str
    reference_snippet: str = ""
    source: str = ""
    gotchas: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "idea": self.idea,
            "rationale": self.rationale,
            "reference_snippet": self.reference_snippet,
            "source": self.source,
            "gotchas": self.gotchas,
        }


@dataclass
class IslandBrief:
    """Per-island structured brief surfaced into proposer prompts."""

    island_idx: Optional[int]
    summary: str
    items: List[BriefItem] = field(default_factory=list)
    direction_summary: str = ""
    drift_score: float = 0.0
    source_query_embedding: List[float] = field(default_factory=list)
    generation: int = 0
    model_used: Optional[str] = None
    cost: float = 0.0
    cached: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "island_idx": self.island_idx,
            "summary": self.summary,
            "items": [item.to_dict() for item in self.items],
            "direction_summary": self.direction_summary,
            "drift_score": self.drift_score,
            "generation": self.generation,
            "model_used": self.model_used,
            "cost": self.cost,
            "cached": self.cached,
        }


@dataclass
class DriftCheckResult:
    drift_score: float
    justification: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarize_recent_programs(programs: List[Any], max_chars: int = 1500) -> str:
    """One-line per program summary that fits the Stage A user prompt."""
    lines: List[str] = []
    for program in programs:
        intent = getattr(program, "mutation_intent", None) or "(no intent)"
        score = getattr(program, "combined_score", 0.0) or 0.0
        gen = getattr(program, "generation", "?")
        mtype = getattr(program, "mutation_type", None) or "?"
        lines.append(
            f"- gen={gen} mutation={mtype} score={score:.4f} intent={str(intent)[:120]}"
        )
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 30] + "\n... [truncated]"
    return text or "(no programs recorded since last brief)"


def _parse_drift_response(raw: Any) -> DriftCheckResult:
    """Best-effort coercion of an LLM response into a DriftCheckResult.

    Models can return the JSON inside code fences or with stray prose.
    We strip / locate the JSON object, clamp the score, and fall back to
    drift_score=0.0 on parse failure so a bad model response can't
    trigger expensive DR calls accidentally.
    """
    if raw is None:
        return DriftCheckResult(drift_score=0.0, justification="parse failed: empty")
    text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
    if not isinstance(text, str):
        text = str(text)
    cleaned = text.strip()
    # Strip ```json fences if present.
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    # Locate the first '{' and matching trailing '}' to tolerate prose.
    open_idx = cleaned.find("{")
    close_idx = cleaned.rfind("}")
    if open_idx == -1 or close_idx == -1 or close_idx <= open_idx:
        return DriftCheckResult(drift_score=0.0, justification="parse failed: no JSON")
    snippet = cleaned[open_idx : close_idx + 1]
    try:
        parsed = json.loads(snippet)
    except json.JSONDecodeError as exc:
        return DriftCheckResult(
            drift_score=0.0,
            justification=f"parse failed: {exc.msg}",
        )
    score_raw = parsed.get("drift_score", 0.0)
    try:
        score = float(score_raw)
    except (TypeError, ValueError):
        score = 0.0
    if math.isnan(score) or math.isinf(score):
        score = 0.0
    score = max(0.0, min(1.0, score))
    justification = str(parsed.get("justification", ""))[:300]
    return DriftCheckResult(drift_score=score, justification=justification)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# DeepResearchSummarizer
# ---------------------------------------------------------------------------


# Type aliases for the injected hooks (so unit tests can stub them
# without instantiating real LLM / embedding clients).
EmbedFn = Callable[[str], Awaitable[Tuple[List[float], float]]]
DriftLLMFn = Callable[[str, str], Awaitable[Tuple[Any, float]]]
"""(sys_msg, user_msg) -> (response_or_content, cost). Phase 5b only uses
the content + cost; Phase 5c-style integration will pass a real LLM
client wrapper instead."""


class DeepResearchSummarizer:
    """Stage A/B of the deep-research pipeline (Phase 5b).

    The summarizer is intentionally pure-Python with hooks for the LLM
    call and the embedding call. The runner (Phase 5d) constructs it
    with real wrappers around ``self.llm`` and ``self.embedding_client``;
    tests construct it with stub callables that return canned content.
    """

    def __init__(
        self,
        *,
        drift_llm: DriftLLMFn,
        embed: Optional[EmbedFn] = None,
        cache_lookup: Optional[
            Callable[[List[float]], Optional[IslandBrief]]
        ] = None,
        drift_threshold: float = 0.5,
        cache_threshold: float = 0.95,
    ) -> None:
        self._drift_llm = drift_llm
        self._embed = embed
        self._cache_lookup = cache_lookup
        self.drift_threshold = float(drift_threshold)
        self.cache_threshold = float(cache_threshold)

    async def drift_check(
        self,
        *,
        island_idx: Optional[int],
        recent_programs: List[Any],
        previous_brief: Optional[IslandBrief],
    ) -> Tuple[DriftCheckResult, float]:
        """Stage A: ask the cheap model whether the island has drifted."""
        sys_msg = DRIFT_CHECK_SYS_MSG
        user_msg = DRIFT_CHECK_USER_MSG.format(
            island_id=str(island_idx if island_idx is not None else "global"),
            previous_brief=(
                (previous_brief.summary if previous_brief else None)
                or "(no previous brief; treat this as a fresh direction)"
            ),
            recent_programs_summary=_summarize_recent_programs(recent_programs),
        )
        response, cost = await self._drift_llm(sys_msg, user_msg)
        content = response if isinstance(response, str) else getattr(
            response, "content", None
        )
        result = _parse_drift_response(content)
        return result, float(cost or 0.0)

    async def novelty_check(
        self,
        *,
        direction_summary: str,
    ) -> Tuple[Optional[IslandBrief], List[float], float]:
        """Stage B: embed the direction and look it up against
        dr_brief_cache.

        Returns ``(matched_brief_or_None, embedding, embed_cost)``. The
        caller decides whether the match's similarity (computed
        internally by ``self._cache_lookup``) was high enough to skip
        Stage C/D.
        """
        if not direction_summary:
            return None, [], 0.0
        if self._embed is None:
            return None, [], 0.0
        embedding, cost = await self._embed(direction_summary)
        if not embedding:
            return None, embedding, float(cost or 0.0)
        match: Optional[IslandBrief] = None
        if self._cache_lookup is not None:
            try:
                match = self._cache_lookup(embedding)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Stage B cache lookup failed: %s", exc)
                match = None
        return match, embedding, float(cost or 0.0)


__all__ = [
    "BriefItem",
    "IslandBrief",
    "DriftCheckResult",
    "DeepResearchSummarizer",
    "cosine_similarity",
]

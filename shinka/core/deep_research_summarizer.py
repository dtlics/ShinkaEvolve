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
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from shinka.prompts.prompts_deep_research import (
    CODE_GROUND_SYS_MSG,
    CODE_GROUND_USER_MSG,
    DR_BRIEF_SYS_MSG,
    DR_BRIEF_USER_MSG,
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


def _parse_brief_response(raw: Any) -> Dict[str, Any]:
    """Best-effort JSON extraction for Stage C / Stage D output.

    Returns a dict with ``summary`` (str) and ``items`` (list of dicts).
    Tolerates the same prose/fence wrappers as ``_parse_drift_response``.
    Falls back to an empty brief on parse failure so a malformed DR
    response never crashes the meta cycle.
    """
    empty = {"summary": "", "items": []}
    if raw is None:
        return empty
    text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
    if not isinstance(text, str):
        text = str(text)
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    open_idx = cleaned.find("{")
    close_idx = cleaned.rfind("}")
    if open_idx == -1 or close_idx == -1 or close_idx <= open_idx:
        return empty
    try:
        parsed = json.loads(cleaned[open_idx : close_idx + 1])
    except json.JSONDecodeError:
        return empty
    summary = str(parsed.get("summary") or "")
    raw_items = parsed.get("items") or []
    items: List[Dict[str, Any]] = []
    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            items.append(
                {
                    "idea": str(raw_item.get("idea") or "")[:200],
                    "rationale": str(raw_item.get("rationale") or "")[:600],
                    "reference_snippet": str(
                        raw_item.get("reference_snippet") or ""
                    )[:800],
                    "source": str(raw_item.get("source") or "")[:300],
                    "gotchas": str(raw_item.get("gotchas") or "")[:300],
                }
            )
    return {"summary": summary, "items": items}


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
DRCallFn = Callable[[str, str], Awaitable[Tuple[Any, float, Optional[str]]]]
"""Stage C: (sys_msg, user_msg) -> (response_or_content, cost,
model_used). Wraps the long DR call (via create_and_poll_async)."""
GroundCallFn = Callable[
    [str, str, List[Dict[str, Any]]],
    Awaitable[Tuple[Any, float, Optional[str]]],
]
"""Stage D: (sys_msg, user_msg, current_items) -> (response_or_content,
cost, model_used). Runs the bounded web-search + fetch grounding pass.
The summarizer hands ``current_items`` so the call site can build the
grounding prompt; the response is parsed back through
``_parse_brief_response``."""


# ---------------------------------------------------------------------------
# Brief cache (Stage B + Stage C/D persistence)
# ---------------------------------------------------------------------------


class BriefCache:
    """SQLite-backed nearest-neighbor cache over ``dr_brief_cache``.

    Phase 5b created the table; Phase 5c uses it. Lookup is a brute-force
    cosine NN since (a) cache size stays small (one row per DR call,
    capped at ``dr_max_calls_per_run`` per run), and (b) adding a vector
    index would pull in a new dep. Linear scan is fine at this scale.
    """

    def __init__(
        self,
        db_path: Optional[str],
        *,
        similarity_threshold: float = 0.95,
    ) -> None:
        self.db_path = db_path
        self.similarity_threshold = float(similarity_threshold)

    def _connect(self) -> Optional[sqlite3.Connection]:
        if not self.db_path:
            return None
        return sqlite3.connect(self.db_path)

    def lookup(self, embedding: List[float]) -> Optional[IslandBrief]:
        """Return the closest cached brief whose cosine sim exceeds the
        configured threshold, or ``None``."""
        if not embedding:
            return None
        conn = self._connect()
        if conn is None:
            return None
        best_brief: Optional[IslandBrief] = None
        best_sim = -1.0
        try:
            rows = conn.execute(
                "SELECT query_embedding, brief_json, hits, model_used "
                "FROM dr_brief_cache"
            ).fetchall()
            for emb_blob, brief_json, hits, model_used in rows:
                try:
                    cached_emb = json.loads(emb_blob) if emb_blob else []
                    cached_brief = (
                        json.loads(brief_json) if brief_json else None
                    )
                except json.JSONDecodeError:
                    continue
                if not cached_emb or not cached_brief:
                    continue
                sim = cosine_similarity(embedding, cached_emb)
                if sim > best_sim:
                    best_sim = sim
                    if sim >= self.similarity_threshold:
                        items = [
                            BriefItem(
                                idea=str(it.get("idea") or ""),
                                rationale=str(it.get("rationale") or ""),
                                reference_snippet=str(
                                    it.get("reference_snippet") or ""
                                ),
                                source=str(it.get("source") or ""),
                                gotchas=str(it.get("gotchas") or ""),
                            )
                            for it in cached_brief.get("items") or []
                        ]
                        best_brief = IslandBrief(
                            island_idx=None,
                            summary=str(cached_brief.get("summary") or ""),
                            items=items,
                            direction_summary=str(
                                cached_brief.get("direction_summary") or ""
                            ),
                            drift_score=float(
                                cached_brief.get("drift_score") or 0.0
                            ),
                            source_query_embedding=cached_emb,
                            generation=int(
                                cached_brief.get("generation") or 0
                            ),
                            model_used=model_used,
                            cost=float(cached_brief.get("cost") or 0.0),
                            cached=True,
                        )
        finally:
            conn.close()
        return best_brief

    def store(self, brief: IslandBrief) -> None:
        """Persist the brief + its query embedding for future lookups."""
        if not self.db_path or not brief.source_query_embedding:
            return
        conn = self._connect()
        if conn is None:
            return
        try:
            payload = brief.to_dict()
            conn.execute(
                "INSERT INTO dr_brief_cache "
                "(query_text, query_embedding, brief_json, model_used, "
                "cost, hits, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (
                    brief.direction_summary,
                    json.dumps(brief.source_query_embedding),
                    json.dumps(payload),
                    brief.model_used,
                    float(brief.cost or 0.0),
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def increment_hits(self, embedding: List[float]) -> None:
        """Bump the ``hits`` counter on the closest cache row (best-effort)."""
        if not self.db_path or not embedding:
            return
        conn = self._connect()
        if conn is None:
            return
        try:
            rows = conn.execute(
                "SELECT id, query_embedding FROM dr_brief_cache"
            ).fetchall()
            best_id, best_sim = None, -1.0
            for row_id, emb_blob in rows:
                try:
                    cached = json.loads(emb_blob) if emb_blob else []
                except json.JSONDecodeError:
                    continue
                if not cached:
                    continue
                sim = cosine_similarity(embedding, cached)
                if sim > best_sim:
                    best_sim, best_id = sim, row_id
            if best_id is not None and best_sim >= self.similarity_threshold:
                conn.execute(
                    "UPDATE dr_brief_cache SET hits = hits + 1 WHERE id = ?",
                    (best_id,),
                )
                conn.commit()
        finally:
            conn.close()


class DeepResearchSummarizer:
    """Four-stage deep-research meta pipeline.

    Hooks are injected so the runner can wire real LLM / embedding /
    cache backends in Phase 5d, while tests can stub each piece in
    isolation. Stages run in order A -> B -> C -> D and short-circuit
    at any stage where the answer is "no work needed".
    """

    def __init__(
        self,
        *,
        drift_llm: DriftLLMFn,
        embed: Optional[EmbedFn] = None,
        cache: Optional[BriefCache] = None,
        cache_lookup: Optional[
            Callable[[List[float]], Optional[IslandBrief]]
        ] = None,
        dr_call: Optional[DRCallFn] = None,
        ground_call: Optional[GroundCallFn] = None,
        drift_threshold: float = 0.5,
        cache_threshold: float = 0.95,
        allowed_domains: Optional[List[str]] = None,
    ) -> None:
        self._drift_llm = drift_llm
        self._embed = embed
        self._cache = cache
        self._dr_call = dr_call
        self._ground_call = ground_call
        self.drift_threshold = float(drift_threshold)
        self.cache_threshold = float(cache_threshold)
        self.allowed_domains = list(allowed_domains or [])
        # ``cache_lookup`` is a lighter alternative to a full BriefCache --
        # used by unit tests that don't need the SQLite layer. When both
        # are supplied, the override wins.
        self._cache_lookup_override = cache_lookup

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

        Returns ``(matched_brief_or_None, embedding, embed_cost)``.
        """
        if not direction_summary:
            return None, [], 0.0
        if self._embed is None:
            return None, [], 0.0
        embedding, cost = await self._embed(direction_summary)
        if not embedding:
            return None, embedding, float(cost or 0.0)
        match: Optional[IslandBrief] = None
        if self._cache_lookup_override is not None:
            try:
                match = self._cache_lookup_override(embedding)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Stage B cache lookup (override) failed: %s", exc)
                match = None
        elif self._cache is not None:
            try:
                match = self._cache.lookup(embedding)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Stage B cache lookup failed: %s", exc)
                match = None
        return match, embedding, float(cost or 0.0)

    async def deep_research(
        self,
        *,
        task_description: str,
        direction_summary: str,
    ) -> Tuple[Dict[str, Any], float, Optional[str]]:
        """Stage C: drive ``o3-deep-research`` to produce a structured brief.

        Returns ``(parsed_brief_dict, cost, model_used)``. The dict has
        ``summary`` (str) and ``items`` (list of item dicts). Parse
        failures yield an empty brief without raising so the meta cycle
        survives.
        """
        if self._dr_call is None:
            return {"summary": "", "items": []}, 0.0, None
        sys_msg = DR_BRIEF_SYS_MSG
        user_msg = DR_BRIEF_USER_MSG.format(
            task_description=task_description or "(no task description provided)",
            direction_summary=direction_summary or "(no direction summary)",
            allowed_domains=", ".join(self.allowed_domains) or "(no domain restrictions)",
        )
        try:
            response, cost, model_used = await self._dr_call(sys_msg, user_msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Stage C DR call failed: %s", exc)
            return {"summary": "", "items": []}, 0.0, None
        content = response if isinstance(response, str) else getattr(
            response, "content", None
        )
        parsed = _parse_brief_response(content)
        return parsed, float(cost or 0.0), model_used

    async def code_ground(
        self,
        *,
        brief: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], float, Optional[str]]:
        """Stage D: bounded web search + fetch to fill source URLs +
        reference snippets in items the DR pass left empty.

        Items that already have a source AND a snippet are left as-is.
        Returns ``(grounded_brief, cost, model_used)``; on any failure
        returns the input brief unchanged with cost=0.
        """
        if self._ground_call is None:
            return brief, 0.0, None
        items = list(brief.get("items") or [])
        if not items:
            return brief, 0.0, None
        sys_msg = CODE_GROUND_SYS_MSG
        user_msg = CODE_GROUND_USER_MSG.format(
            allowed_domains=", ".join(self.allowed_domains)
            or "(no domain restrictions)",
            brief_json=json.dumps({"summary": brief.get("summary", ""), "items": items}),
        )
        try:
            response, cost, model_used = await self._ground_call(
                sys_msg, user_msg, items
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Stage D grounding call failed: %s", exc)
            return brief, 0.0, None
        content = response if isinstance(response, str) else getattr(
            response, "content", None
        )
        grounded = _parse_brief_response(content)
        # If the grounder returned nothing usable, fall back to the
        # ungrounded brief so we don't lose ideas.
        if not grounded.get("items"):
            return brief, float(cost or 0.0), model_used
        return grounded, float(cost or 0.0), model_used

    async def run_pipeline(
        self,
        *,
        island_idx: Optional[int],
        recent_programs: List[Any],
        previous_brief: Optional[IslandBrief],
        task_description: str,
        generation: int = 0,
    ) -> Tuple[IslandBrief, Dict[str, float]]:
        """Drive Stage A -> B -> C -> D and return (brief, cost_breakdown).

        Short-circuits:
        - drift_score < drift_threshold: return ``previous_brief`` unchanged
          (or an empty brief if none); stage C/D NOT called.
        - Stage B cache hit: return the cached brief; stage C/D NOT called.
        - Stage C fails or returns empty: return the previous brief / an
          empty brief (whatever's most useful) without persisting.
        """
        cost_breakdown: Dict[str, float] = {
            "stage_a": 0.0,
            "stage_b": 0.0,
            "stage_c": 0.0,
            "stage_d": 0.0,
        }

        drift_result, drift_cost = await self.drift_check(
            island_idx=island_idx,
            recent_programs=recent_programs,
            previous_brief=previous_brief,
        )
        cost_breakdown["stage_a"] = drift_cost
        if drift_result.drift_score < self.drift_threshold:
            logger.info(
                "Stage A: drift_score=%.3f below threshold %.3f; "
                "reusing previous brief for island %s",
                drift_result.drift_score,
                self.drift_threshold,
                island_idx,
            )
            if previous_brief is not None:
                return previous_brief, cost_breakdown
            empty = IslandBrief(
                island_idx=island_idx,
                summary="",
                items=[],
                direction_summary=drift_result.justification,
                drift_score=drift_result.drift_score,
                generation=generation,
            )
            return empty, cost_breakdown

        direction_summary = drift_result.justification
        matched, embedding, embed_cost = await self.novelty_check(
            direction_summary=direction_summary
        )
        cost_breakdown["stage_b"] = embed_cost
        if matched is not None:
            logger.info(
                "Stage B: cache hit for island %s (direction=%s)",
                island_idx,
                direction_summary[:60],
            )
            if self._cache is not None:
                try:
                    self._cache.increment_hits(embedding)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("increment_hits failed: %s", exc)
            # Tag the linked brief with the requesting island.
            linked = IslandBrief(
                island_idx=island_idx,
                summary=matched.summary,
                items=list(matched.items),
                direction_summary=direction_summary,
                drift_score=drift_result.drift_score,
                source_query_embedding=embedding,
                generation=generation,
                model_used=matched.model_used,
                cost=0.0,
                cached=True,
            )
            return linked, cost_breakdown

        parsed, dr_cost, model_used = await self.deep_research(
            task_description=task_description,
            direction_summary=direction_summary,
        )
        cost_breakdown["stage_c"] = dr_cost
        if not parsed.get("items"):
            logger.warning(
                "Stage C produced no items for island %s; returning previous "
                "brief unchanged",
                island_idx,
            )
            return (
                previous_brief
                or IslandBrief(
                    island_idx=island_idx,
                    summary=parsed.get("summary", ""),
                    items=[],
                    direction_summary=direction_summary,
                    drift_score=drift_result.drift_score,
                    source_query_embedding=embedding,
                    generation=generation,
                    model_used=model_used,
                    cost=dr_cost,
                )
            ), cost_breakdown

        grounded, ground_cost, ground_model = await self.code_ground(brief=parsed)
        cost_breakdown["stage_d"] = ground_cost

        items = [
            BriefItem(
                idea=item["idea"],
                rationale=item["rationale"],
                reference_snippet=item.get("reference_snippet", ""),
                source=item.get("source", ""),
                gotchas=item.get("gotchas", ""),
            )
            for item in grounded.get("items") or []
        ]
        brief = IslandBrief(
            island_idx=island_idx,
            summary=str(grounded.get("summary") or parsed.get("summary", "")),
            items=items,
            direction_summary=direction_summary,
            drift_score=drift_result.drift_score,
            source_query_embedding=embedding,
            generation=generation,
            model_used=model_used or ground_model,
            cost=dr_cost + ground_cost,
            cached=False,
        )
        if self._cache is not None:
            try:
                self._cache.store(brief)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Brief cache store failed: %s", exc)
        return brief, cost_breakdown


__all__ = [
    "BriefItem",
    "IslandBrief",
    "DriftCheckResult",
    "BriefCache",
    "DeepResearchSummarizer",
    "cosine_similarity",
]

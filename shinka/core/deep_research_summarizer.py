"""Per-island deep-research meta cycle (phase 2 of research-grounding).

The freeform ``MetaSummarizer`` runs every ``meta_rec_interval``
evaluated programs and produces a single global string injected at
``sampler.py``'s ``# Potential Recommendations`` slot. That's good for
nudging continuously but caps at the LLM's pretrained knowledge.

The DR pipeline runs at a slower cadence (``dr_meta_interval``,
default 20) and produces *structured*, *grounded*, *per-island*
briefs. Each brief is a list of techniques with reference snippets
the agent can look at directly. The four stages:

1. **Stage A — drift judge**. Cheap model compares the island's
   recent programs against its previous brief. If drift is below
   threshold, keep the prior brief and skip the rest.

2. **Stage B — novelty cache lookup**. Embed Stage A's candidate
   research question and look it up in ``dr_brief_cache`` by cosine
   similarity. If a sufficiently similar cached brief exists, link it
   to this island and skip C/D.

3. **Stage C — deep research call**. Submit the candidate question to
   ``o3-deep-research`` via the dedicated DR client. Background mode
   with polling.

4. **Stage D — code grounding**. For each technique in the Stage C
   brief, spawn a short agent run with ``web_search`` constrained to
   ``dr_code_grounding_domains`` to confirm or extend the reference
   snippet.

Output for an island is a list of ``BriefItem`` dataclasses,
persisted to ``meta_briefs`` (full structured payload) and rendered
to a backwards-compatible markdown string that the sampler injects
into the same slot as the freeform string.

This module implements Stages A and B end-to-end. Stages C and D are
exposed via overridable methods (``_run_stage_c``, ``_run_stage_d``);
the default implementations are placeholders that surface "DR not
configured" briefs so the meta cycle keeps running offline. The real
implementations land in phase 2c.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from shinka.database import Program

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------
# Stage outputs
# --------------------------------------------------------------------


@dataclass
class StageAOutput:
    """Drift judge result. ``candidate_question`` is the question the
    DR call would ask in Stage C if the drift gate fires."""

    drift_score: float
    justification: str
    candidate_question: str
    # Cost of the Stage A LLM judge call (USD). The cheap drift model
    # is still a real LLM call and used to be discarded — doom-
    # remediation Fix 2 retains it so the orchestrator can sum it into
    # ``self.total_api_cost``.
    cost: float = 0.0

    @classmethod
    def parse(cls, raw: str, cost: float = 0.0) -> "StageAOutput":
        """Tolerant JSON parser — the cheap drift judge sometimes wraps
        its output in markdown fencing or trailing prose. Strip and
        retry rather than crashing the whole DR cycle."""
        data = _strip_to_json(raw)
        return cls(
            drift_score=float(data.get("drift_score", 0.0)),
            justification=str(data.get("justification", "")),
            candidate_question=str(data.get("candidate_question", "")),
            cost=float(cost),
        )


@dataclass
class BriefItem:
    """One technique entry in a research brief.

    ``confirmed`` (doom-remediation Fix 5) defaults to True for items
    that haven't been through Stage D's per-item web_search verification
    (Stage C output before Stage D, cache-hit items, anything synthesized
    without grounding). Stage D sets it to False when it failed to
    confirm the item's reference via the web_search agent run. The
    lit_grounded mutation arm's eligibility filter consults this flag
    so the agent doesn't burn a generation re-discovering Stage D's
    "unconfirmable" verdict.
    """

    idea: str
    rationale: str = ""
    reference_source: str = ""
    reference_snippet: str = ""
    gotchas: str = ""
    confirmed: bool = True

    @classmethod
    def parse_list(cls, raw: str) -> List["BriefItem"]:
        data = _strip_to_json(raw)
        items = data.get("techniques") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out: List["BriefItem"] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            out.append(
                cls(
                    idea=str(item.get("idea", "")),
                    rationale=str(item.get("rationale", "")),
                    reference_source=str(item.get("reference_source", "")),
                    reference_snippet=str(item.get("reference_snippet", "")),
                    gotchas=str(item.get("gotchas", "")),
                    # Fresh from Stage C → not yet confirmed by Stage D.
                    # Default ``True`` here keeps backward compat for
                    # any path that constructs items without going
                    # through Stage D (cache_hit, tests, etc.). Stage
                    # D explicitly flips to False when confirmation
                    # fails.
                    confirmed=bool(item.get("confirmed", True)),
                )
            )
        return out


@dataclass
class DRBrief:
    """A complete brief for one island at one generation.

    Carries both the structured technique list (for downstream
    machine reading — e.g. the literature_grounded sampler picking a
    specific item) and a rendered markdown string for the freeform
    sampler injection slot.
    """

    island_idx: Optional[int]
    generation: int
    items: List[BriefItem] = field(default_factory=list)
    candidate_question: str = ""
    drift_score: float = 0.0
    source: str = "fresh"  # "fresh" | "cache_hit" | "drift_skip" | "placeholder"
    rendered_markdown: str = ""
    # ``cost`` is Stage C + Stage D LLM spend on this brief. Stage A
    # cost lives on ``stage_a_cost`` separately because Stage A may
    # fire on every DR cadence tick (even when the gate aborts the
    # rest of the pipeline). ``total_cost`` sums them for the
    # orchestrator's budget accounting (doom-remediation Fix 2).
    cost: float = 0.0
    stage_a_cost: float = 0.0

    @property
    def total_cost(self) -> float:
        return float(self.cost or 0.0) + float(self.stage_a_cost or 0.0)


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def _strip_to_json(raw: str) -> Dict[str, Any]:
    """Best-effort JSON parsing.

    Models occasionally wrap JSON in markdown fences (```json ... ```)
    or precede it with prose. Try the raw string first, then try
    extracting the first ``{ ... }`` block. On failure return ``{}``
    rather than raising so the DR cycle stays robust.
    """
    if not isinstance(raw, str):
        return {}
    s = raw.strip()
    # Strip code fences if present.
    if s.startswith("```"):
        s = s.strip("`")
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1 :]
        s = s.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Last resort: find the first {...} block.
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        candidate = s[start : end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Plain cosine on float lists. Returns 0.0 on empty input to make
    cache misses behave like "definitely not similar" rather than
    crashing."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _format_recent_programs_for_drift(
    programs: List[Program], max_n: int = 10
) -> str:
    """Compact rendering of the last few island programs for the Stage A
    drift judge. We include id-prefix, correct flag, combined_score,
    and a one-line patch description if present in metadata. Full code
    is omitted — the judge only needs to spot whether the *direction*
    has shifted."""
    if not programs:
        return "(none)"
    recent = programs[-max_n:]
    lines = []
    for p in recent:
        meta = p.metadata or {}
        name = meta.get("patch_name") or "?"
        desc = meta.get("patch_description") or ""
        lines.append(
            f"- gen={p.generation} score={p.combined_score:.4f} "
            f"correct={bool(p.correct)} name={name!r} desc={desc!r}"
        )
    return "\n".join(lines)


def _render_brief_markdown(items: List[BriefItem]) -> str:
    """Render a brief as the markdown block the sampler injects into
    the ``# Potential Recommendations`` slot. Rows are
    ``Idea / Rationale / Reference / Gotchas``. Empty fields are
    omitted from a row so noise stays low.
    """
    if not items:
        return ""
    chunks = []
    for i, it in enumerate(items, 1):
        parts = [f"**Idea {i}**: {it.idea}"]
        if it.rationale:
            parts.append(f"_Rationale_: {it.rationale}")
        if it.reference_snippet:
            ref = it.reference_snippet
            if it.reference_source:
                ref = f"{ref} (source: {it.reference_source})"
            parts.append(f"_Reference_: {ref}")
        elif it.reference_source:
            parts.append(f"_Reference source_: {it.reference_source}")
        if it.gotchas:
            parts.append(f"_Gotchas_: {it.gotchas}")
        chunks.append("\n".join(parts))
    return "\n\n".join(chunks)


# --------------------------------------------------------------------
# Persistence helpers (decoupled from the summarizer class for testing)
# --------------------------------------------------------------------


def persist_brief(conn: Any, brief: DRBrief, model_used: str) -> int:
    """Insert one ``meta_briefs`` row. Returns the new row id.

    ``conn`` is a sqlite3 connection (sync) — the runner passes its
    db.conn straight in. We don't use the async writer for meta briefs
    because they are low-volume and happen during a cooperative pause
    in the meta cycle.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO meta_briefs
          (island_idx, generation, stage, content, structured_json,
           model_used, cost, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            brief.island_idx,
            brief.generation,
            brief.source,
            brief.rendered_markdown,
            json.dumps([asdict(it) for it in brief.items]),
            model_used,
            float(brief.cost),
            time.time(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def cache_brief(
    conn: Any,
    query_text: str,
    query_embedding: List[float],
    brief: DRBrief,
    model_used: str,
) -> int:
    """Insert a row into the global ``dr_brief_cache`` so a future
    Stage B lookup can find this brief by embedding similarity."""
    cursor = conn.cursor()
    embedding_blob = json.dumps(query_embedding).encode("utf-8")
    cursor.execute(
        """
        INSERT INTO dr_brief_cache
          (query_text, query_embedding, brief_json, model_used, cost,
           hits, created_at)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (
            query_text,
            embedding_blob,
            json.dumps([asdict(it) for it in brief.items]),
            model_used,
            float(brief.cost),
            time.time(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def lookup_cached_brief(
    conn: Any, query_embedding: List[float], threshold: float
) -> Optional[Tuple[int, float, List[BriefItem]]]:
    """Return ``(row_id, similarity, items)`` for the best match
    above threshold, or None on miss.

    Scans the full cache table — fine for the row counts we expect
    (a few hundred per long run). For larger scales swap to a vector
    index later.
    """
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT id, query_embedding, brief_json FROM dr_brief_cache"
    ).fetchall()
    best: Optional[Tuple[int, float, List[BriefItem]]] = None
    for row in rows:
        emb_blob = row["query_embedding"] if hasattr(row, "keys") else row[1]
        if not emb_blob:
            continue
        try:
            cached_emb = json.loads(emb_blob.decode("utf-8") if isinstance(emb_blob, (bytes, bytearray)) else emb_blob)
        except (json.JSONDecodeError, AttributeError):
            continue
        sim = _cosine_similarity(query_embedding, cached_emb)
        if sim >= threshold and (best is None or sim > best[1]):
            brief_json = (
                row["brief_json"] if hasattr(row, "keys") else row[2]
            )
            try:
                items_data = json.loads(brief_json)
            except (json.JSONDecodeError, TypeError):
                items_data = []
            items = [BriefItem(**item) for item in items_data if isinstance(item, dict)]
            row_id = row["id"] if hasattr(row, "keys") else row[0]
            best = (int(row_id), float(sim), items)
    return best


def bump_cache_hit(conn: Any, row_id: int) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE dr_brief_cache SET hits = hits + 1 WHERE id = ?", (row_id,)
    )
    conn.commit()


# --------------------------------------------------------------------
# The summarizer class
# --------------------------------------------------------------------


# Callable signatures the runner injects (kept loose so tests can
# stub with AsyncMocks without importing AgentLLMClient).
StageAJudgeFn = Callable[..., Awaitable[Tuple[str, float]]]  # returns (raw_response, cost)
EmbedFn = Callable[..., Awaitable[Tuple[List[float], float]]]  # returns (embedding, cost)


class DeepResearchSummarizer:
    """Drives the Stage A→D pipeline for each island.

    Stateful only for the per-island previous-brief cache so Stage A
    has something to compare against. The ``MetaSummarizer``'s
    per-island accumulator (``evaluated_since_last_meta_by_island``)
    is the input; ``meta_briefs`` and ``dr_brief_cache`` SQLite tables
    are the persistent output.

    Stage A and Stage B are implemented in this file end-to-end.
    Stage C and Stage D are exposed via overridable ``_run_stage_c``
    and ``_run_stage_d`` methods — the default implementations are
    "no-op placeholder" that surface a brief explaining that DR is
    not yet enabled, so the meta cycle keeps running even when the
    Azure DR resource isn't configured. Phase 2c replaces those
    placeholders.

    Limits:
    * ``dr_max_calls_per_run`` caps the total number of full Stage C
      calls across all islands in one run. The summarizer tracks the
      count internally and skips C if exhausted.
    """

    def __init__(
        self,
        *,
        meta_summarizer: Any,  # MetaSummarizer instance — kept loose
        stage_a_judge: Optional[StageAJudgeFn] = None,
        embedder: Optional[EmbedFn] = None,
        stage_c_fn: Optional[Callable[..., Awaitable[Tuple[List[BriefItem], float, str]]]] = None,
        stage_d_fn: Optional[Callable[..., Awaitable[Tuple[List[BriefItem], float]]]] = None,
        db_conn: Optional[Any] = None,
        drift_threshold: float = 0.5,
        brief_cache_threshold: float = 0.95,
        dr_max_calls_per_run: int = 30,
        dr_max_tool_calls: int = 20,
        dr_reasoning_effort: str = "medium",
        dr_code_grounding_domains: Optional[List[str]] = None,
    ) -> None:
        self.meta_summarizer = meta_summarizer
        self.stage_a_judge = stage_a_judge
        self.embedder = embedder
        # Phase 2c: the runner injects real Stage C/D implementations
        # here. When ``stage_c_fn`` is None we fall back to the
        # placeholder ``_run_stage_c`` (which signals "DR not
        # configured"); same for D.
        self.stage_c_fn = stage_c_fn
        self.stage_d_fn = stage_d_fn
        self.db_conn = db_conn
        self.drift_threshold = drift_threshold
        self.brief_cache_threshold = brief_cache_threshold
        self.dr_max_calls_per_run = dr_max_calls_per_run
        self.dr_max_tool_calls = dr_max_tool_calls
        self.dr_reasoning_effort = dr_reasoning_effort
        self.dr_code_grounding_domains = dr_code_grounding_domains or []

        # Per-island prior brief; populated whenever a fresh or cached
        # brief lands. Stage A reads this to render the drift prompt.
        self._previous_brief_by_island: Dict[Optional[int], DRBrief] = {}
        # Count of full Stage C calls this run, for budget enforcement.
        self._stage_c_call_count: int = 0

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def update_async(
        self,
        *,
        generation: int,
        island_indices: List[Optional[int]],
    ) -> Dict[Optional[int], DRBrief]:
        """Run the pipeline for each island; return ``{island_idx: brief}``.

        Islands with empty accumulators are skipped silently. Islands
        with insufficient drift retain their previous brief. Cache
        hits link the cached items into the new island's brief.
        Stages C and D actually call out — the default placeholders
        emit a "DR not configured" sentinel brief instead.
        """
        results: Dict[Optional[int], DRBrief] = {}
        for island_idx in island_indices:
            brief = await self._run_for_island(generation, island_idx)
            if brief is not None:
                results[island_idx] = brief
                self._previous_brief_by_island[island_idx] = brief
        return results

    async def _run_for_island(
        self, generation: int, island_idx: Optional[int]
    ) -> Optional[DRBrief]:
        programs = self.meta_summarizer.consume_island_programs(island_idx)
        if not programs:
            return None

        # ---- Stage A: drift judge ------------------------------------
        prev_brief = self._previous_brief_by_island.get(island_idx)
        stage_a = await self._run_stage_a(
            generation=generation,
            island_idx=island_idx,
            programs=programs,
            previous_brief=prev_brief,
        )
        logger.info(
            "DR Stage A island=%s gen=%s drift=%.3f question=%r",
            island_idx,
            generation,
            stage_a.drift_score,
            (stage_a.candidate_question or "")[:80],
        )

        if stage_a.drift_score < self.drift_threshold and prev_brief is not None:
            # Drift gate not fired — reuse the previous brief.
            return DRBrief(
                island_idx=island_idx,
                generation=generation,
                items=prev_brief.items,
                candidate_question=stage_a.candidate_question,
                drift_score=stage_a.drift_score,
                source="drift_skip",
                rendered_markdown=prev_brief.rendered_markdown,
                cost=0.0,
                stage_a_cost=stage_a.cost,
            )

        # ---- Stage B: novelty cache lookup ---------------------------
        cache_brief_items: Optional[List[BriefItem]] = None
        cache_row_id: Optional[int] = None
        if self.embedder is not None and self.db_conn is not None and stage_a.candidate_question:
            try:
                embedding, _embed_cost = await self.embedder(
                    stage_a.candidate_question
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("DR Stage B embed failed: %s", exc)
                embedding = []
            if embedding:
                hit = lookup_cached_brief(
                    self.db_conn, embedding, self.brief_cache_threshold
                )
                if hit is not None:
                    cache_row_id, _sim, cache_brief_items = hit
                    logger.info(
                        "DR Stage B cache hit island=%s row=%s sim=%.3f",
                        island_idx,
                        cache_row_id,
                        _sim,
                    )
                    bump_cache_hit(self.db_conn, cache_row_id)

        if cache_brief_items is not None:
            md = _render_brief_markdown(cache_brief_items)
            brief = DRBrief(
                island_idx=island_idx,
                generation=generation,
                items=cache_brief_items,
                candidate_question=stage_a.candidate_question,
                drift_score=stage_a.drift_score,
                source="cache_hit",
                rendered_markdown=md,
                cost=0.0,
                stage_a_cost=stage_a.cost,
            )
            if self.db_conn is not None:
                try:
                    persist_brief(self.db_conn, brief, model_used="cache_hit")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("DR Stage B persist failed: %s", exc)
            return brief

        # ---- Stage C+D: deep research + grounding --------------------
        # Budget check. Run only if we have budget remaining; otherwise
        # emit a placeholder so the freeform fallback still works.
        if self._stage_c_call_count >= self.dr_max_calls_per_run:
            logger.info(
                "DR Stage C skipped: budget exhausted (%d/%d).",
                self._stage_c_call_count,
                self.dr_max_calls_per_run,
            )
            return self._placeholder_brief(
                island_idx=island_idx,
                generation=generation,
                stage_a=stage_a,
                reason="dr_budget_exhausted",
            )

        try:
            items_c, cost_c, model_used = await self._run_stage_c(
                candidate_question=stage_a.candidate_question,
                programs=programs,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("DR Stage C failed: %s", exc)
            return self._placeholder_brief(
                island_idx=island_idx,
                generation=generation,
                stage_a=stage_a,
                reason=f"stage_c_failed: {exc}",
            )

        # The default placeholder Stage C signals "DR not configured"
        # via this sentinel model name. The real Stage C (phase 2c)
        # returns the actual DR model name (e.g. ``o3-deep-research``).
        # We route the sentinel through the placeholder branch so the
        # caller sees a consistent ``source="placeholder"`` brief and
        # we don't burn the dr_max_calls_per_run budget on a no-op.
        if model_used == "dr_placeholder":
            return self._placeholder_brief(
                island_idx=island_idx,
                generation=generation,
                stage_a=stage_a,
                reason="dr_not_configured",
            )

        self._stage_c_call_count += 1

        items_d, cost_d = await self._run_stage_d(items_c)
        total_cost = cost_c + cost_d
        md = _render_brief_markdown(items_d)
        brief = DRBrief(
            island_idx=island_idx,
            generation=generation,
            items=items_d,
            candidate_question=stage_a.candidate_question,
            drift_score=stage_a.drift_score,
            source="fresh",
            rendered_markdown=md,
            cost=total_cost,
            stage_a_cost=stage_a.cost,
        )

        if self.db_conn is not None:
            try:
                persist_brief(self.db_conn, brief, model_used=model_used)
            except Exception as exc:  # noqa: BLE001
                logger.warning("DR persist_brief failed: %s", exc)
            # Cache the fresh brief if we got an embedding earlier.
            if self.embedder is not None and stage_a.candidate_question:
                try:
                    embedding, _ = await self.embedder(stage_a.candidate_question)
                    if embedding:
                        cache_brief(
                            self.db_conn,
                            stage_a.candidate_question,
                            embedding,
                            brief,
                            model_used=model_used,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("DR cache_brief failed: %s", exc)

        return brief

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------

    async def _run_stage_a(
        self,
        *,
        generation: int,
        island_idx: Optional[int],
        programs: List[Program],
        previous_brief: Optional[DRBrief],
    ) -> StageAOutput:
        """Call the drift judge. When no judge is configured (e.g. in
        tests), return a permissive default that fires the gate so
        Stage B/C/D still run if the rest of the wiring is in place.
        """
        if self.stage_a_judge is None:
            return StageAOutput(
                drift_score=1.0,
                justification="(no stage_a_judge configured; default-fire)",
                candidate_question="(no candidate question generated)",
            )
        from shinka.prompts import DR_STAGE_A_SYS_MSG, DR_STAGE_A_USER_MSG

        prev_md = (
            previous_brief.rendered_markdown if previous_brief is not None else "None"
        )
        user_msg = DR_STAGE_A_USER_MSG.format(
            island_idx=island_idx,
            generation=generation,
            previous_brief=prev_md or "None",
            recent_programs=_format_recent_programs_for_drift(programs),
            drift_threshold=self.drift_threshold,
        )
        try:
            raw, cost_a = await self.stage_a_judge(
                system_msg=DR_STAGE_A_SYS_MSG, user_msg=user_msg
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("DR Stage A judge raised: %s", exc)
            return StageAOutput(
                drift_score=1.0,
                justification=f"stage_a_judge raised: {exc}",
                candidate_question="(judge failed)",
                cost=0.0,
            )
        return StageAOutput.parse(raw, cost=float(cost_a or 0.0))

    async def _run_stage_c(
        self,
        *,
        candidate_question: str,
        programs: List[Program],
    ) -> Tuple[List[BriefItem], float, str]:
        """Real Stage C when ``stage_c_fn`` was injected; placeholder
        otherwise (returns empty items + sentinel model name so the
        caller routes to ``_placeholder_brief``)."""
        if self.stage_c_fn is not None:
            return await self.stage_c_fn(
                candidate_question=candidate_question, programs=programs
            )
        logger.info(
            "DR Stage C placeholder fired (no stage_c_fn injected); "
            "candidate_question=%r",
            (candidate_question or "")[:120],
        )
        return [], 0.0, "dr_placeholder"

    async def _run_stage_d(
        self, items: List[BriefItem]
    ) -> Tuple[List[BriefItem], float]:
        """Real Stage D when ``stage_d_fn`` was injected; identity
        passthrough otherwise."""
        if self.stage_d_fn is not None:
            return await self.stage_d_fn(items)
        return list(items), 0.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _placeholder_brief(
        self,
        *,
        island_idx: Optional[int],
        generation: int,
        stage_a: StageAOutput,
        reason: str,
    ) -> DRBrief:
        """Surface a placeholder when DR can't run. Records the reason
        in the rendered markdown so the agent reading it knows why no
        items are present, and the brief is still persisted to
        meta_briefs for diagnostic visibility."""
        md = (
            f"_DR pipeline did not produce items this cycle: {reason}._\n\n"
            f"_Candidate question:_ {stage_a.candidate_question}"
        )
        brief = DRBrief(
            island_idx=island_idx,
            generation=generation,
            items=[],
            candidate_question=stage_a.candidate_question,
            drift_score=stage_a.drift_score,
            source="placeholder",
            rendered_markdown=md,
            cost=0.0,
            stage_a_cost=stage_a.cost,
        )
        if self.db_conn is not None:
            try:
                persist_brief(self.db_conn, brief, model_used="placeholder")
            except Exception as exc:  # noqa: BLE001
                logger.warning("DR placeholder persist failed: %s", exc)
        return brief

"""sample_parent.py — choose a parent (and inspirations) for the next mutation.

MUTABILITY: MUTABLE STRATEGY (cell A). The orchestrator MAY rewrite this file via
the strategy-rewrite protocol when search stagnates. It embeds NO LLM call.
Keep the entry point signature and the output keys stable; the BODY (the
selection policy) is what evolves.

This is a faithful port of shinka's DEFAULT parent-selection path
(``WeightedSamplingStrategy`` over the island-filtered correct archive, plus
top-k + elite inspiration selection). It reads the archive through immutable
plumbing (``ProgramDatabase.get_all_programs``) and applies the *policy* — the
sigmoid-weighted sampling math and the inspiration choice — in-script, so a
rewrite is self-contained. test_parity.py asserts the probability vector here
matches shinka's WeightedSamplingStrategy on the same archive.

Policy knobs (from db_config): parent_selection_lambda (sigmoid sharpness),
num_archive_inspirations, num_top_k_inspirations, num_islands.

INPUT (stdin JSON):
  {
    "db_path": str, "db_config": {..}, "embedding_model": str,
    "island_idx": int | null,     # null => auto-select uniformly among initialized islands
    "seed": int | null,           # for deterministic sampling (tests/parity)
    "validity_floor": float | null,  # O6 lever: floor VALID parents' scores; null = inert
    "select": "errored" | null,   # P5 repair mode: pick an ERRORED parent to fix in place
                                  #   (no inspirations, needs_fix=True); skips tombstoned +
                                  #   attempt-cap-reached rows. null = normal selection.
    "repair_attempt_cap": int     # default 2; an errored parent past the cap is not picked
  }

Repair mode (``select="errored"``) and the bootstrap fallback both SKIP tombstoned
(repair-removed) programs so a dead row is never re-selected.

OUTPUT (stdout JSON):
  {
    "ok": true,
    "parent_id": str,
    "island_idx": int | null,
    "archive_inspiration_ids": [str],
    "top_k_inspiration_ids": [str],
    "needs_fix": bool,            # true if the chosen parent is incorrect (fix mode)
    "n_candidates": int,
    "selection_probs": [float]    # parallel to the weighted pool (debug/parity)
  }
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List

try:
    from . import _common
except ImportError:
    import _common  # type: ignore


def _stable_sigmoid(x: float) -> float:
    # Numerically stable logistic; matches shinka.database.parents.stable_sigmoid.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _is_tombstoned(p) -> bool:
    """True if a program was repair-tombstoned (removed from the sampling pool, P5)."""
    return (getattr(p, "metadata", None) or {}).get("repair_tombstoned") is True


def _median(xs: List[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _finite_score(x: Any) -> float:
    """Coerce a program score to a finite float (NaN / inf / None → 0.0). Defensive: a
    resumed / foreign / shared archive could carry a non-finite score that would otherwise
    NaN the whole weighted-probability vector (P10-T2)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _weighted_probs(scores: List[float], children: List[int], lam: float) -> List[float]:
    """Port of WeightedSamplingStrategy: sigmoid(performance) * novelty bonus."""
    alpha_0 = _median(scores)
    mad = _median([abs(s - alpha_0) for s in scores])
    scale = max(mad, 1e-6)
    weights = []
    for alpha_i, n_i in zip(scores, children):
        s_i = _stable_sigmoid(lam * (alpha_i - alpha_0) / scale)
        h_i = 1.0 / (1.0 + (n_i or 0))
        weights.append(s_i * h_i)
    total = sum(weights)
    if total > 0 and math.isfinite(total):
        return [w / total for w in weights]
    n = len(scores)
    return [1.0 / n] * n if n else []


def _select_island(archived_correct, islands, config, rng):
    """Pick an island per ``config.island_selection_strategy`` (M11). Default
    "uniform"/"equal" reproduces the prior hardcoded uniform draw (and parity).
    "proportional" weights by island population; "weighted" by island best-fitness."""
    if not islands:
        return None
    strategy = str(getattr(config, "island_selection_strategy", "uniform") or "uniform")
    if strategy == "proportional":
        counts = {i: 0 for i in islands}
        for p in archived_correct:
            i = getattr(p, "island_idx", None)
            if i in counts:
                counts[i] += 1
        weights = [counts[i] for i in islands]
        if sum(weights) > 0:
            return rng.choices(islands, weights=weights, k=1)[0]
        return rng.choice(islands)
    if strategy == "weighted":
        bests = {i: 0.0 for i in islands}
        for p in archived_correct:
            i = getattr(p, "island_idx", None)
            if i in bests:
                s = float(getattr(p, "combined_score", 0.0) or 0.0)
                if s > bests[i]:
                    bests[i] = s
        weights = [max(bests[i], 0.0) for i in islands]
        if sum(weights) > 0:
            return rng.choices(islands, weights=weights, k=1)[0]
        return rng.choice(islands)
    # "uniform" / "equal" (default) — preserves WeightedSamplingStrategy parity.
    return rng.choice(islands)


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    from shinka.database import ProgramDatabase, DatabaseConfig

    db_path = payload["db_path"]
    cfg_kwargs = dict(payload.get("db_config", {}))
    cfg_kwargs["db_path"] = db_path
    config = DatabaseConfig(**cfg_kwargs)
    embedding_model = payload.get("embedding_model", "text-embedding-3-small")
    rng = random.Random(payload.get("seed"))

    db = ProgramDatabase(config, embedding_model=embedding_model, read_only=True)
    try:
        programs = db.get_all_programs()
    finally:
        db.close()

    # P5-T3: REPAIR-mode selection. When the harness latches repair mode it asks for an
    # ERRORED parent to fix IN PLACE (no inspirations — the repair prompt uses the
    # program's OWN failure). Skip tombstoned (already-removed) rows and rows that have
    # used up their repair attempts. If the errored pool is empty, fall through to the
    # normal path so a spurious repair request can never crash.
    if payload.get("select") == "errored":
        cap = int(payload.get("repair_attempt_cap", 2) or 2)
        errored_pool = [
            p for p in programs
            if not getattr(p, "correct", False)
            and not _is_tombstoned(p)
            and int(((getattr(p, "metadata", None) or {}).get("repair_attempts", 0)) or 0) < cap
        ]
        if errored_pool:
            parent = max(errored_pool, key=lambda p: getattr(p, "generation", 0))
            return {
                "parent_id": parent.id,
                "island_idx": getattr(parent, "island_idx", None),
                "archive_inspiration_ids": [],
                "top_k_inspiration_ids": [],
                "needs_fix": True,
                "n_candidates": len(errored_pool),
                "selection_probs": [],
            }

    archived_correct = [
        p for p in programs if getattr(p, "in_archive", False) and getattr(p, "correct", False)
    ]

    # Bootstrap / fix fallback: no correct archived programs yet.
    if not archived_correct:
        # Prefer the best correct program; else the earliest program (the seed).
        correct = [p for p in programs if getattr(p, "correct", False)]
        # Exclude tombstoned (repair-removed) rows from the bootstrap fallback so a dead
        # row is never re-selected as the seed (correct programs are never tombstoned).
        live_incorrect = [p for p in programs if not _is_tombstoned(p)]
        if correct:
            parent = max(correct, key=lambda p: getattr(p, "combined_score", 0.0))
            needs_fix = False
        elif live_incorrect:
            parent = min(live_incorrect, key=lambda p: getattr(p, "generation", 0))
            needs_fix = not getattr(parent, "correct", False)
        else:
            raise RuntimeError("archive is empty; cannot sample a parent")
        return {
            "parent_id": parent.id,
            "island_idx": getattr(parent, "island_idx", None),
            "archive_inspiration_ids": [],
            "top_k_inspiration_ids": [],
            "needs_fix": bool(needs_fix),
            "n_candidates": 0,
            "selection_probs": [],
        }

    # Island selection (MUTABLE-LEVER, M11): honor config.island_selection_strategy
    # instead of a hardcoded uniform draw. Default "uniform" reproduces today's
    # behavior (+ the WeightedSamplingStrategy parity).
    islands = sorted({getattr(p, "island_idx", 0) for p in archived_correct})
    island_idx = payload.get("island_idx")
    if island_idx is None:
        island_idx = _select_island(archived_correct, islands, config, rng)

    # enforce_island_separation (MUTABLE-LEVER, M11): default True keeps the
    # same-island pool (today's behavior); False enables cross-island
    # cross-pollination of parents + inspirations.
    enforce_sep = bool(getattr(config, "enforce_island_separation", True))
    if enforce_sep:
        pool = [p for p in archived_correct if getattr(p, "island_idx", None) == island_idx]
        if not pool:  # island has no archived members; fall back to all archived
            pool = archived_correct
    else:
        pool = archived_correct

    scores = [_finite_score(getattr(p, "combined_score", 0.0)) for p in pool]
    # MUTABLE-LEVER (O6 — parent-selection score scale): clamp VALID parents'
    # scores to a floor so valid-but-no-gain candidates stay selectable above the
    # bottom of the pool. Default None = inert (preserves WeightedSamplingStrategy
    # parity). The bandit REWARD scale has its own separate `reward_validity_floor`.
    _vfloor = payload.get("validity_floor")
    if _vfloor is not None:
        scores = [max(s, float(_vfloor)) for s in scores]
    children = [int(getattr(p, "children_count", 0) or 0) for p in pool]
    lam = float(getattr(config, "parent_selection_lambda", 10.0))
    probs = _weighted_probs(scores, children, lam)

    # Sample a parent by the weighted probabilities.
    parent = rng.choices(pool, weights=probs, k=1)[0]

    # Inspirations: top-k by score (excluding parent) + a couple of elites.
    ranked = sorted(pool, key=lambda p: getattr(p, "combined_score", 0.0), reverse=True)
    top_k_n = int(getattr(config, "num_top_k_inspirations", 1))
    top_k = [p.id for p in ranked if p.id != parent.id][:top_k_n]

    arch_n = int(getattr(config, "num_archive_inspirations", 1))
    elite_pool = [p for p in ranked if p.id != parent.id and p.id not in top_k]
    archive_insp = [p.id for p in elite_pool[:arch_n]]

    return {
        "parent_id": parent.id,
        "island_idx": island_idx,
        "archive_inspiration_ids": archive_insp,
        "top_k_inspiration_ids": top_k,
        "needs_fix": False,
        "n_candidates": len(pool),
        "selection_probs": probs,
    }


if __name__ == "__main__":
    _common.run_main(main)

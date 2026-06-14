"""run_window.py — the inner-loop driver.

This is the ONLY file that knows the inner loop's control flow. Given a current
archive state + the current strategy files, it runs W iterations under the
current strategy and emits a window-end diagnostics JSON. The orchestrator
invokes it as a single subprocess per window; it never sequences the scripts
itself.

It composes the scripts in ``../scripts`` in the canonical Shinka per-candidate order:

    sample_parent -> construct_mutation_prompt -> select_llm -> mutate -> evaluate
    -> immediate-fix -> archive_record -> reward/bandit-update   [repeat W times]
    -> diagnostics

The driver is sequential (one candidate at a time), the clean reference order;
it writes to the same ``programs.sqlite`` schema shinka uses, so the real
ShinkaEvolveRunner could resume from a harness-produced archive.

MUTABILITY: this is harness plumbing — not a strategy file. Do not rewrite it as
part of a strategy rewrite (rewrite the ``scripts/*.py`` policies instead).

USAGE:
  python harness/run_window.py --config run.json [--windows 1] [--iters 15]
  (or import and call ``main(config) -> diagnostics_dict``)

The config schema is documented in orchestrator/tests/fixtures and SKILL.md.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HARNESS_DIR = Path(__file__).resolve().parent
_ORCH_DIR = _HARNESS_DIR.parent
_REPO_ROOT = _ORCH_DIR.parent
# The "current strategy" lives in this dir. Overridable so a fresh run_window
# subprocess loads whatever the orchestrator last deployed (and so tests can
# point at an isolated copy of scripts/). Defaults to the real scripts/.
_SCRIPTS_DIR = Path(os.environ.get("SHINKA_ORCH_SCRIPTS_DIR", _ORCH_DIR / "scripts"))
for _p in (str(_REPO_ROOT), str(_SCRIPTS_DIR), str(_HARNESS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _common  # noqa: E402
import sample_parent  # noqa: E402
import construct_mutation_prompt  # noqa: E402
import mutate  # noqa: E402
import evaluate as evaluate_script  # noqa: E402
import archive_record  # noqa: E402
import archive_query  # noqa: E402
import diagnostics as diagnostics_script  # noqa: E402
import select_llm as select_llm_script  # noqa: E402
import novelty_check as novelty_check_script  # noqa: E402
import compute_reward as compute_reward_script  # noqa: E402
import record_policy as record_policy_script  # noqa: E402
import cadence_policy as cadence_policy_script  # noqa: E402
import island_policy as island_policy_script  # noqa: E402
import meta_summarize as meta_summarize_script  # noqa: E402  (P3-T2 automatic per-window meta)
import island_brief as island_brief_script  # noqa: E402  (P3-T2 auto-record per-island briefs)
import repair_record as repair_record_script  # noqa: E402  (P5-T4 record failed repairs)
import journal  # noqa: E402  (harness sibling)
import strategy_store  # noqa: E402  (harness sibling — for the strategy fingerprint)

FOLDER_PREFIX = "gen"

# P6-T1: the starter run.json ships task_sys_msg as this sentinel; the harness refuses
# to start until the orchestrator authors a real goal (the boot first-job), so a paid
# run never proceeds with a placeholder goal.
STARTER_SYS_MSG_SENTINEL = "__UNSET_AUTHOR_AT_BOOT__"


def _read_code(path: str) -> str:
    # encoding pinned to UTF-8: program source (seed/candidate) routinely carries
    # non-ASCII; Windows would otherwise default to cp1252 and raise UnicodeDecodeError.
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _eval_budget_sec(task: Dict[str, Any]) -> Optional[float]:
    """C2: the per-eval time budget in seconds (task.eval_time 'HH:MM:SS'), or None.
    construct_mutation_prompt uses it to decide whether a parent/inspiration ran 'slow'
    vs the budget and to word the runtime-budget caution."""
    et = task.get("eval_time")
    if not et:
        return None
    try:
        from shinka.utils import parse_time_to_seconds
        return float(parse_time_to_seconds(et))
    except Exception:
        return None


def _embed(cfg: Dict[str, Any], code: str) -> Tuple[Optional[List[float]], float]:
    """Embed candidate code for the novelty check. Returns (vector, cost_usd).
    Mock = deterministic hash vector (offline, cost 0); live = shinka's
    EmbeddingClient (whose cost MUST be captured, not discarded)."""
    mock = cfg.get("mock", {}) or {}
    if mock.get("enabled"):
        import hashlib

        digest = hashlib.sha256(code.encode("utf-8")).digest()
        return [b / 255.0 for b in digest[:16]], 0.0
    try:
        from shinka.embed.embedding import EmbeddingClient

        client = EmbeddingClient(
            model_name=cfg["evo"].get("embedding_model", "text-embedding-3-small")
        )
        out = client.get_embedding(code)
        if isinstance(out, tuple):
            return out[0], float(out[1] or 0.0)
        return out, 0.0
    except Exception:
        return None, 0.0


def _novelty_embed_text(evo: Dict[str, Any], parent_code: str, candidate_code: str) -> str:
    """H2: choose WHAT the novelty gate embeds.

    'diff' (default): embed the unified parent->candidate diff, so two genuinely
    different edits separate to LOW cosine — each is accepted as novel and the
    per-island pool can GROW past one genotype — while a true re-proposal of the
    same change shares a diff and is still caught as a near-duplicate. 'code': the
    legacy whole-program embedding (a small edit on a large program reads ~0.994
    similar to its parent, so every improvement was flagged a near-dup and evicted
    its own parent -> the single-survivor greedy chain H2 describes). Falls back to
    the whole candidate when there is no parent baseline (seed/bootstrap) or the
    two codes are identical (empty diff)."""
    mode = str(evo.get("novelty_embed_mode", "diff") or "diff")
    if mode == "code" or not parent_code:
        return candidate_code
    import difflib

    diff = "\n".join(
        difflib.unified_diff(
            parent_code.splitlines(), candidate_code.splitlines(), lineterm="", n=3
        )
    )
    return diff or candidate_code


def _max_generation(db_path: str, db_config: Dict[str, Any], embedding_model: str) -> int:
    res = archive_query.main(
        {
            "db_path": db_path,
            "db_config": db_config,
            "embedding_model": embedding_model,
            "query_type": "all",
        }
    )["result"]
    gens = [int(p.get("generation", 0) or 0) for p in res]
    return max(gens) if gens else -1


def _best_score(db_path: str, db_config: Dict[str, Any], embedding_model: str) -> float:
    summ = archive_query.main(
        {
            "db_path": db_path,
            "db_config": db_config,
            "embedding_model": embedding_model,
            "query_type": "summary",
        }
    )["result"]
    return float(summ.get("best_score") or 0.0)


def _mock_score(cfg: Dict[str, Any], iter_index: int, generation: int) -> Optional[float]:
    """Resolve a mocked score: by generation (preferred, unambiguous) else by
    within-window iter index. Returns None to fall through to a real eval."""
    mock = cfg.get("mock", {}) or {}
    if not mock.get("enabled"):
        return None
    sbg = mock.get("scores_by_generation")
    if sbg is not None:
        keyed = {str(k): v for k, v in sbg.items()}
        if str(generation) in keyed:
            return float(keyed[str(generation)])
    eval_scores = mock.get("eval_scores")
    if eval_scores is not None:
        return float(eval_scores[iter_index % len(eval_scores)])
    return None


def _evaluate_candidate(
    cfg: Dict[str, Any], program_path: str, results_dir: str,
    iter_index: int, generation: int,
) -> Dict[str, Any]:
    """Real eval via evaluate.py, OR a mocked score for offline tests."""
    score = _mock_score(cfg, iter_index, generation)
    if score is not None:
        mock = cfg.get("mock", {}) or {}
        incorrect = {int(g) for g in mock.get("incorrect_generations", [])}
        correct = generation not in incorrect
        return {
            "combined_score": score,
            "correct": correct,
            "public_metrics": {},
            "private_metrics": {},
            "error": None if correct else "mock: marked incorrect",
            "error_traceback": None if correct else "MockError: marked incorrect",
            "stdout_log": "",
            "stderr_log": "",
            "runtime_sec": 0.0,
        }
    task = cfg["task"]
    os.makedirs(results_dir, exist_ok=True)
    return evaluate_script.main(
        {
            "program_path": program_path,
            "eval_program_path": task["eval_program_path"],
            "results_dir": results_dir,
            "time": task.get("eval_time"),
            "conda_env": task.get("conda_env"),
            "python_executable": task.get("python_executable"),
            "verbose": cfg.get("verbose", False),
        }
    )


def _bootstrap_initial(cfg: Dict[str, Any]) -> float:
    """If the archive is empty, evaluate the seed program and record it as gen 0.

    Returns the embedding cost incurred (0.0 when novelty is off or the archive
    was already bootstrapped) so the caller can fold it into the ledger once the
    journal exists (bootstrap runs before journal.init_run)."""
    db_path = cfg["db_path"]
    db_config = cfg["db_config"]
    evo = cfg["evo"]
    embedding_model = evo.get("embedding_model", "text-embedding-3-small")
    # A missing DB file means an empty archive (the first archive_record creates
    # it). Only query the count when the file already exists, since archive_query
    # opens read-only and read-only refuses to create a missing DB.
    if os.path.exists(db_path):
        count = archive_query.main(
            {
                "db_path": db_path,
                "db_config": db_config,
                "embedding_model": embedding_model,
                "query_type": "count",
            }
        )["result"]
        if count["total"] > 0:
            return 0.0

    task = cfg["task"]
    init_path = task["init_program_path"]
    gen_dir = os.path.join(cfg["results_dir"], f"{FOLDER_PREFIX}_0")
    results_dir = os.path.join(gen_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    ev = _evaluate_candidate(cfg, init_path, results_dir, 0, generation=0)
    seed_code = _read_code(init_path)
    program_fields: Dict[str, Any] = {
        "code": seed_code,
        "language": task.get("language", "python"),
        "generation": 0,
        "parent_id": None,
        "combined_score": ev["combined_score"],
        "correct": ev["correct"],
        "public_metrics": ev["public_metrics"],
        "private_metrics": ev["private_metrics"],
        "error_traceback": ev.get("error_traceback"),
        "metadata": {"bootstrap": True},
    }
    # F7: embed the seed so the FIRST mutations have a baseline to compare against.
    # Without this the novelty gate is a no-op (novelty_n_compared=0) until a few
    # embedded candidates accrue per island, letting near-duplicate early mutants
    # through uncounted.
    embed_cost = 0.0
    if evo.get("enable_novelty"):
        seed_embedding, embed_cost = _embed(cfg, seed_code)
        if seed_embedding is not None:
            program_fields["embedding"] = seed_embedding
    archive_record.main(
        {
            "db_path": db_path,
            "db_config": db_config,
            "embedding_model": embedding_model,
            "program": program_fields,
        }
    )
    return float(embed_cost or 0.0)


def _parse_arm(arm_id: Optional[str], default_effort: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """WS6: a bandit arm id may encode reasoning effort as ``"model@effort"`` so the
    bandit treats each (model, effort) as a distinct arm (e.g. pro@medium vs pro@high
    are learned separately). Split it into (model_name, reasoning_effort) for the
    actual call; an arm with no ``@`` uses the run's default effort. Per-model VALID
    efforts are the orchestrator's responsibility when authoring the arm list (pro
    rejects 'low')."""
    if arm_id and "@" in arm_id:
        model, effort = arm_id.split("@", 1)
        return model, (effort or default_effort)
    return arm_id, default_effort


def _sample_meta_direction(meta_directions: Optional[List[Any]], rng: random.Random) -> Optional[str]:
    """WS2: sample ONE meta direction by weight (relative promise / 'best shots').
    Returns the chosen direction text, or None if there are none."""
    if not meta_directions:
        return None
    texts: List[str] = []
    weights: List[float] = []
    for d in meta_directions:
        if isinstance(d, dict):
            t = d.get("text")
            try:
                w = max(0.0, float(d.get("weight", 1.0)))
            except (TypeError, ValueError):
                w = 1.0
        else:
            t, w = (str(d) if d else None), 1.0
        if t:
            texts.append(t)
            weights.append(w)
    if not texts:
        return None
    if sum(weights) <= 0:
        return rng.choice(texts)
    return rng.choices(texts, weights=weights, k=1)[0]


def _compose_meta_for_gen(evo: Dict[str, Any], generation: int) -> Optional[str]:
    """WS2/WS3: build THIS gen's meta DIRECTION only. With ``evo.meta_directions``
    (the weighted list from meta_summarize), sample ONE direction by weight. Falls
    back to the legacy single ``evo.meta_recommendations`` blob when no structured
    directions are present (back-compat with older run.json files).

    The persistent ``evo.meta_failure_note`` is NO LONGER embedded here — it rides
    as its own always-on ``failure_note`` field (see ``_run_one_candidate`` /
    ``_attempt_immediate_fixes``), so the caution is never clobbered by an
    island_brief or dropped on a cross/lit/empty-direction gen (M1/M2/M3/M4)."""
    meta_directions = evo.get("meta_directions")
    if meta_directions:
        seed = evo.get("seed")
        rng = random.Random((int(seed) + int(generation)) if seed is not None else None)
        chosen = _sample_meta_direction(meta_directions, rng)
        if chosen:
            return "Direction to pursue in THIS attempt: " + chosen
        return None
    return evo.get("meta_recommendations")  # legacy global blob fallback


def _attempt_immediate_fixes(
    cfg: Dict[str, Any],
    ev: Dict[str, Any],
    mut: Dict[str, Any],
    learn_from: Optional[Dict[str, Any]],
    model_name: Optional[str],
    reasoning_effort: Optional[str],
    gen_dir: str,
    results_dir: str,
    generation: int,
    language: str,
    fix_budget: int,
    counters: Dict[str, Any],
    enable_web_search: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any], float]:
    """WS1 — IMMEDIATE correctness repair (MUTABLE fix concern).

    Returns ``(ev, mut, fix_cost)`` — the final eval, the final mutation result,
    and the total $ spent on fix-mutation calls (so the caller can attribute the
    slot's whole model spend to the bandit arm; the ledger already has it).

    On an eval FAILURE, re-prompt the SAME model with the just-failed code + its
    error fed back (through the existing ``construct_mutation_prompt`` fix branch),
    re-evaluate, up to ``fix_budget`` times. Returns the final ``(ev, mut)``.

    Design (fits the window loop without side effects elsewhere):
      * Correctness-only — does NOT re-run the novelty gate; the slot already
        passed novelty, and a repair is meant to recover correctness, not change
        the idea. (Caller re-embeds the repaired code so the archived embedding
        still matches the stored code — novelty comparisons stay honest.)
      * Every attempt's mutation cost is folded into ``counters["cost"]`` so the
        per-window ledger + budget railguard account for it. A within-loop budget
        check prevents *starting* an attempt we can't afford (overshoot ≤ 0).
      * ``fix_budget`` is THE lever: 1 for ordinary gens (``evo.fix_retry_budget``),
        3 when grounding a novel DR direction as a new island's first member (WS5).
      * ``enable_web_search`` is OFF for ordinary fixes; WS4/WS5 turn it on only
        when the repair is nailing a DR reference. Left mutable for future
        outer-loops (one of the framework's policy switches, like novelty/bandit).
    """
    if ev.get("correct"):
        return ev, mut, 0.0
    evo = cfg["evo"]
    task = cfg["task"]
    budget = cfg.get("budget_usd")
    # prior_total is stable within a window (append_window folds window cost only
    # at window end; interventions land between windows), so reading it once here
    # matches the inter-candidate railguard in _one_window.
    prior_total = journal.total_cost(cfg["results_dir"])
    fix_cost = 0.0
    # P6-T3: when use_text_feedback is False, suppress BOTH the evaluator's stdout and
    # stderr/error text from the fix prompt (sample_fix reads ONLY these two channels),
    # making disabling feedback a COMPLETE spoil mitigation. Default True (feedback on).
    _utf = bool((cfg.get("evo") or {}).get("use_text_feedback", True))
    fix_used = 0
    while (not ev.get("correct")) and fix_used < int(fix_budget):
        if budget is not None and (prior_total + counters.get("cost", 0.0)) >= float(budget):
            break  # railguard: don't start a fix attempt we can't afford
        fix_used += 1
        counters["fix_count"] = counters.get("fix_count", 0) + 1
        # The just-failed candidate becomes the "incorrect program" to repair.
        # sample_fix reads the error from metadata.stdout_log/stderr_log, so route
        # error_traceback (carries the timeout reason + stderr tail) into stderr_log.
        incorrect_program = {
            "id": f"gen{generation}_fix{fix_used}",
            "code": mut["candidate_code"],
            "combined_score": ev.get("combined_score", 0.0) or 0.0,
            "generation": generation,
            "metadata": {
                "stdout_log": (ev.get("stdout_log", "") or "") if _utf else "",
                "stderr_log": (ev.get("error_traceback") or ev.get("text_feedback")
                               or ev.get("stderr_log") or "") if _utf else "",
            },
        }
        fix_prompt = construct_mutation_prompt.main(
            {
                "parent": incorrect_program,
                "needs_fix": True,
                # the correct ancestor to learn from (the sampled parent), if any.
                "ancestor_inspirations": [learn_from] if learn_from else [],
                "task_sys_msg": task.get("task_sys_msg"),
                "language": language,
                # H9: thread the no-spoil flag so the fix prompt also suppresses the
                # ANCESTOR's evaluator text (not just the just-failed candidate's
                # stdout/stderr) — this construct call previously omitted it (the leak).
                "use_text_feedback": _utf,
                # M4: the persistent failure caution rides into fix-mode too.
                "failure_note": evo.get("meta_failure_note"),
                # C3 (H6): offset by generation so fix prompts don't pin one patch type.
                "seed": (int(evo["seed"]) + generation) if evo.get("seed") is not None else None,
                # C2: the per-eval budget + the just-failed candidate's runtime (it is never
                # archived, so its runtime lives only in the live `ev`) → runtime-budget caution.
                "eval_budget_sec": _eval_budget_sec(task),
                "parent_runtime_sec": ev.get("runtime_sec"),
                "parent_timed_out": bool(ev.get("timed_out")),
            }
        )
        fix_payload: Dict[str, Any] = {
            "parent_code": mut["candidate_code"],
            "patch_sys": fix_prompt["patch_sys"],
            "patch_msg": fix_prompt["patch_msg"],
            "patch_type": fix_prompt["patch_type"],
            "patch_dir": gen_dir,
            "language": language,
            "model_name": model_name,
            "reasoning_effort": reasoning_effort,  # WS6: same arm's effort as the failed attempt
            "max_attempts": evo.get("max_patch_attempts", 3),
            "run_id": cfg.get("run_id"),
            "generation": generation,
            "verbose": cfg.get("verbose", False),
        }
        if enable_web_search:
            fix_payload["enable_web_search"] = True  # WS4 plumbs this into _azure
        fix_mut = mutate.main(fix_payload)
        _c = float(fix_mut.get("cost", 0.0) or 0.0)
        fix_cost += _c
        counters["cost"] = counters.get("cost", 0.0) + _c
        mut = fix_mut
        if not fix_mut.get("applied"):
            continue  # patch didn't apply; spend counted, retry if budget remains
        ev = _evaluate_candidate(
            cfg, fix_mut["candidate_path"], results_dir, counters["iter_index"], generation
        )
        if ev.get("correct"):
            counters["fix_success"] = counters.get("fix_success", 0) + 1
            break
    return ev, mut, fix_cost


def _run_one_candidate(cfg: Dict[str, Any], generation: int, counters: Dict[str, int],
                       repair: bool = False) -> None:
    db_path = cfg["db_path"]
    db_config = cfg["db_config"]
    evo = cfg["evo"]
    task = cfg["task"]
    embedding_model = evo.get("embedding_model", "text-embedding-3-small")
    language = task.get("language", "python")
    # C3 (H6): offset the seed by generation so the global np.random.seed in
    # construct_mutation_prompt / select_llm doesn't pin the SAME patch-type and
    # exploration draw every generation (operator-mix collapse). None => unseeded.
    _seed = evo.get("seed")
    gseed = (int(_seed) + generation) if _seed is not None else None

    # Per-step oversight trace — written ONLY when tracing is on (warmup, and the
    # framework-audit measuring window via --trace-steps); a harmless no-op otherwise.
    # The orchestrator reads steps.jsonl after a traced window to oversee one window
    # step by step. Folds no cost. (Call sites are added through the candidate flow.)
    _trace_on = bool(cfg.get("trace_steps"))

    def _trace(record: Dict[str, Any]) -> None:
        if not _trace_on:
            return
        try:
            journal.log_step(cfg["results_dir"], {**record, "generation": generation})
        except Exception:
            pass

    gen_dir = os.path.join(cfg["results_dir"], f"{FOLDER_PREFIX}_{generation}")
    results_dir = os.path.join(gen_dir, "results")
    os.makedirs(results_dir, exist_ok=True)

    # 1. sample parent + inspirations (MUTABLE policy). In repair mode, ask the sampler
    # for an ERRORED parent to fix IN PLACE (no inspirations).
    _sp_payload = {
        "db_path": db_path,
        "db_config": db_config,
        "embedding_model": embedding_model,
        "seed": gseed,
        "validity_floor": evo.get("validity_floor"),
    }
    if repair:
        _sp_payload["select"] = "errored"
        _sp_payload["repair_attempt_cap"] = int(evo.get("repair_attempt_cap", 2) or 2)
    sp = sample_parent.main(_sp_payload)
    # A repair generation = repair requested AND the sampler returned an errored parent
    # to fix (empty errored pool → needs_fix False → this behaves as a normal slot).
    _repair_gen = bool(repair and sp.get("needs_fix"))
    parent = archive_query.main(
        {
            "db_path": db_path, "db_config": db_config, "embedding_model": embedding_model,
            "query_type": "get", "program_id": sp["parent_id"], "include_code": True,
            # P5-T4: the repair escalation hook below reads parent.metadata.repair_attempts
            # to detect strike-two; without this the hook could never fire.
            "include_metadata": True,
        }
    )["result"]

    _trace({"step": "sampler", "parent_id": sp.get("parent_id"),
            "parent_score": parent.get("combined_score"),
            "island_idx": sp.get("island_idx"), "needs_fix": bool(sp.get("needs_fix")),
            "archive_inspiration_ids": sp.get("archive_inspiration_ids", []),
            "top_k_inspiration_ids": sp.get("top_k_inspiration_ids", [])})

    def _fetch(ids: List[str]) -> List[Dict[str, Any]]:
        out = []
        for pid in ids:
            out.append(
                archive_query.main(
                    {
                        "db_path": db_path, "db_config": db_config,
                        "embedding_model": embedding_model,
                        "query_type": "get", "program_id": pid, "include_code": True,
                        # C2: carry runtime_sec/timed_out metadata so the prompt builder can
                        # surface a runtime-budget caution from a slow/timed-out inspiration.
                        "include_metadata": True,
                    }
                )["result"]
            )
        return out

    needs_fix = bool(sp.get("needs_fix"))
    if needs_fix:
        # FIX/REPAIR concern: repair an incorrect parent using its ancestors.
        ancestors = archive_query.main(
            {
                "db_path": db_path, "db_config": db_config,
                "embedding_model": embedding_model, "query_type": "ancestry",
                "program_id": sp["parent_id"], "max_ancestors": 10, "include_code": True,
            }
        )["result"]
        archive_insp, top_k_insp = [], []
        # M5: feed the incorrect parent's OWN failure reason into the repair prompt.
        # sample_fix reads metadata.stderr_log; the parent summary carries
        # error_traceback as a top-level field (no include_metadata here), so route
        # it in (mirrors the immediate-fix path's stderr_log chain).
        _utf = bool(evo.get("use_text_feedback", True))
        _pmd = parent.get("metadata") or {}
        if not _utf:
            # P6-T3: feedback suppressed → blank BOTH channels sample_fix reads, so
            # use_text_feedback:false is a COMPLETE spoil mitigation on the repair path.
            _pmd["stdout_log"] = ""
            _pmd["stderr_log"] = ""
        elif not _pmd.get("stderr_log"):
            # M5: a domain failure (the common cnot class) carries no traceback — fall
            # back to the persisted text_feedback so this repair prompt isn't blind.
            _pmd["stderr_log"] = (
                parent.get("error_traceback") or parent.get("text_feedback") or ""
            )
        parent["metadata"] = _pmd
        # M9: count sampled needs_fix parents separately from immediate-fix ATTEMPTS
        # so fix_success_rate stays coherent (immediate repairs / immediate attempts).
        counters["needs_fix_count"] = counters.get("needs_fix_count", 0) + 1
    else:
        ancestors = []
        archive_insp = _fetch(sp.get("archive_inspiration_ids", []))
        top_k_insp = _fetch(sp.get("top_k_inspiration_ids", []))

    # 2a. per-island DIRECTION (H1): fetch the latest brief the orchestrator authored
    # for THIS island so different islands carry DIFFERENT directions. None => the
    # island falls back to the global meta direction (byte-identical no-brief default).
    brief_text = None
    _isl = sp.get("island_idx")
    # H1: prefer the per-gen direction the SAMPLER drew from this island's STRUCTURED brief
    # (direction-oriented; its assigned programs are already the inspirations above). Fall
    # back to the island's headline brief content when the sampler didn't draw one.
    _sampled_dir = sp.get("sampled_direction")
    if _sampled_dir:
        brief_text = _sampled_dir
    elif _isl is not None:
        try:
            _brief = archive_query.main({
                "db_path": db_path, "db_config": db_config,
                "embedding_model": embedding_model,
                "query_type": "island_brief", "island_idx": _isl,
            })["result"]
            brief_text = (_brief or {}).get("content") or None
        except Exception:
            brief_text = None

    # 2. construct mutation prompt (MUTABLE policy; fix-mode picks the repair prompt)
    # Capture the per-gen sampled meta direction ONCE (the sampler draws randomly per
    # gen — recomputing it for the trace would show a different draw than was used).
    _meta_for_gen = _compose_meta_for_gen(evo, generation)
    prompt = construct_mutation_prompt.main(
        {
            "parent": parent,
            "archive_inspirations": archive_insp,
            "top_k_inspirations": top_k_insp,
            "ancestor_inspirations": ancestors,
            "needs_fix": needs_fix,
            # WS2/WS3: per-gen weighted sample of ONE meta direction. The persistent
            # failure caution rides separately as `failure_note` (always-on, never
            # dropped) rather than embedded in the direction string (M1/M2/M3/M4).
            "meta_recommendations": _meta_for_gen,
            "failure_note": evo.get("meta_failure_note"),
            # H1: per-island direction (None unless the orchestrator authored one).
            "island_brief": brief_text,
            "brief_compose_mode": evo.get("brief_compose_mode", "replace"),
            "task_sys_msg": task.get("task_sys_msg"),
            "patch_types": evo.get("patch_types"),
            "patch_type_probs": evo.get("patch_type_probs"),
            "language": language,
            "extra_guidance": evo.get("extra_guidance"),
            "use_text_feedback": evo.get("use_text_feedback", True),
            # C2: per-eval budget → bounded runtime caution when the parent/an inspiration ran slow.
            "eval_budget_sec": _eval_budget_sec(task),
            "seed": gseed,
        }
    )
    _trace({"step": "prompt", "patch_type": prompt.get("patch_type"),
            "meta_direction_present": bool(_meta_for_gen),
            "island_brief_present": bool(brief_text),
            "failure_note_present": bool(evo.get("meta_failure_note")),
            "sys_len": len(prompt.get("patch_sys") or ""),
            "msg_len": len(prompt.get("patch_msg") or "")})

    # 2b. select LLM (MUTABLE policy). Bandit only when a model pool is given;
    # otherwise fall back to a fixed model_name (mock path uses neither).
    mock = cfg.get("mock", {}) or {}
    llm_models = evo.get("llm_models")
    state_path = os.path.join(cfg["results_dir"], "bandit_state.pkl")
    arm_id = evo.get("model_name")  # bandit arm identity (may be "model@effort")
    if llm_models:
        sel = select_llm_script.main(
            {
                "mode": "select", "models": llm_models, "state_path": state_path,
                "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                # C4 recovery levers (reachable WITHOUT a code rewrite): force_explore
                # ignores the collapsed posterior (uniform); llm_subset restricts arms.
                "force_explore": bool(evo.get("force_explore", False)),
                "subset": evo.get("llm_subset"),
                "seed": gseed,
            }
        )
        arm_id = sel["model_name"]
    # WS6: split the arm id "model@effort" → (model, effort). The bandit (select +
    # update below) keys on arm_id, so it learns per (model,effort); the actual call
    # uses the clean model_name + that arm's effort (default when the arm has no @).
    model_name, reasoning_effort = _parse_arm(arm_id, evo.get("reasoning_effort"))
    # P5-T4: escalation hook (present-but-off, default None). On a repair generation's
    # LAST attempt before the tombstone fires (the parent's repair_attempts would reach
    # the cap this round), optionally route the repair to a stronger model.
    if _repair_gen and evo.get("repair_escalation_model"):
        _cap = int(evo.get("repair_attempt_cap", 2) or 2)
        _att = int(((parent.get("metadata") or {}).get("repair_attempts", 0)) or 0)
        if _att >= _cap - 1:
            model_name, reasoning_effort = _parse_arm(
                evo.get("repair_escalation_model"), evo.get("reasoning_effort"))

    # 3. mutate: LLM call + apply (IMMUTABLE body, MUTABLE prompt) — mockable
    mut_payload = {
        "parent_code": parent.get("code", ""),
        "patch_sys": prompt["patch_sys"],
        "patch_msg": prompt["patch_msg"],
        "patch_type": prompt["patch_type"],
        "patch_dir": gen_dir,
        "language": language,
        "model_name": model_name,
        "reasoning_effort": reasoning_effort,
        # WS5: web search on the MUTATION call — OFF by default; the orchestrator
        # sets evo.mutation_web_search on a dedicated *grounding* run (pinned pro +
        # one DR direction + the reference) so the model can consult the source
        # while implementing it. Normal evolutionary runs leave it false.
        "enable_web_search": bool(evo.get("mutation_web_search", False)),
        "max_attempts": evo.get("max_patch_attempts", 3),
        "run_id": cfg.get("run_id"),
        "generation": generation,
        "verbose": cfg.get("verbose", False),
    }
    if mock.get("enabled"):
        mut_payload["mock"] = True
        mut_payload["mock_cost"] = mock.get("mutate_cost", 0.0)  # offline budget tests
        seq = mock.get("mutate_code_sequence")
        if seq is not None:
            mut_payload["mock_code"] = seq[counters["iter_index"] % len(seq)]
        # else identity copy of parent
    mut = mutate.main(mut_payload)
    # Account the mutation LLM cost immediately — it was incurred even if the
    # candidate is later rejected by novelty.
    _mut_cost = float(mut.get("cost", 0.0) or 0.0)
    counters["cost"] = counters.get("cost", 0.0) + _mut_cost
    _slot_mut_cost = _mut_cost  # arm's total model spend for this slot (+= fix cost below)
    _trace({"step": "llm_output", "applied": mut.get("applied"),
            "num_applied": mut.get("num_applied"), "name": mut.get("name"),
            "transport": mut.get("transport"), "attempts": mut.get("attempts"),
            "cost": _mut_cost})

    # 3a. TRUTHFUL RECORDING (F-INNER-1): if the patch never applied even after the
    # bounded apply-retries, NO candidate was produced. mutate returns the parent code
    # UNCHANGED with applied=False — record a TRUE failed/exhausted attempt: charge the
    # model's token cost to the picking arm (cost-only, NO reward), archive NOTHING,
    # surface it via the exhausted-retry signals, and drop the slot. Branch ONLY on
    # `applied is False` — a deliberate identity patch returns applied=True with
    # num_applied=0 and must still be evaluated.
    if mut.get("applied") is False:
        counters["apply_exhausted"] = counters.get("apply_exhausted", 0) + 1
        counters.setdefault("exhausted_retry_slots", []).append(f"gen{generation}")
        counters["exhausted_retry_count"] = counters.get("exhausted_retry_count", 0) + 1
        if llm_models and arm_id:
            # cost-only bandit feed (mirrors the novelty-reject feed): the arm pays its
            # real spend with NO fabricated reward.
            select_llm_script.main({
                "mode": "update", "models": llm_models, "state_path": state_path,
                "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                "arm": arm_id, "cost": _slot_mut_cost,
                "cost_only": True, "reward": None, "baseline": None,
            })
        _trace({"step": "framework_decision", "action": "failed_apply_no_candidate",
                "cost": _slot_mut_cost, "attempts": mut.get("attempts")})
        return  # no novelty, no eval, no reward, no record, no archive

    # 3b. NOVELTY (MUTABLE policy) — gated; live runs enable it. Compute the candidate's
    # code embedding HERE, but DEFER the accept/reject to AFTER eval: keep-the-better (H5)
    # must compare BOTH programs' scores to keep the better of a near-duplicate pair, so a
    # near-dup is now EVALUATED (not dropped pre-eval) and resolved at step 4a''.
    code_embedding: Optional[List[float]] = None
    nov: Dict[str, Any] = {}
    _slot_embed_cost = 0.0
    if evo.get("enable_novelty"):
        # H2: embed the parent->candidate DIFF (default) instead of the whole program,
        # so a genuine improvement is NOT false-flagged as a near-dup of its parent.
        code_embedding, _embed_cost = _embed(
            cfg, _novelty_embed_text(evo, parent.get("code", ""), mut["candidate_code"])
        )
        _slot_embed_cost = float(_embed_cost or 0.0)
        counters["cost"] = counters.get("cost", 0.0) + _slot_embed_cost

    # 4. evaluate (IMMUTABLE plumbing)
    ev = _evaluate_candidate(
        cfg, mut["candidate_path"], results_dir, counters["iter_index"], generation
    )
    # 4a. IMMEDIATE FIX (WS1 — MUTABLE fix concern). On an eval failure, repair the
    # candidate in-place by re-prompting the same model with the error, up to
    # evo.fix_retry_budget times (default 1 for ordinary gens). Skipped in mock mode
    # (offline tests don't make LLM calls). eval_total/eval_failures below count the
    # FINAL post-fix state, so evaluation_failure_rate is the *un-repairable* rate.
    if (
        not ev["correct"]
        and not (cfg.get("mock", {}) or {}).get("enabled")
        and int(evo.get("fix_retry_budget", 1)) > 0
    ):
        _pre_fix_code = mut["candidate_code"]
        ev, mut, _fix_cost = _attempt_immediate_fixes(
            cfg, ev, mut, parent, model_name, reasoning_effort, gen_dir, results_dir,
            generation, language, int(evo.get("fix_retry_budget", 1)), counters,
            # WS4: web search during ordinary fix-retries is OFF by default but left
            # mutable — a future outer-loop can set evo.fix_web_search to let the
            # repair model consult the web (like the other policy switches).
            enable_web_search=bool(evo.get("fix_web_search", False)),
        )
        _slot_mut_cost += _fix_cost  # attribute the repair spend to the same arm
        # Re-embed only if a fix actually changed the code, so the archived
        # embedding matches the stored code (keeps the novelty gate honest).
        if evo.get("enable_novelty") and mut["candidate_code"] != _pre_fix_code:
            # H2: re-embed the (post-fix) parent->candidate diff, consistent with the
            # pre-fix embed above, so the stored embedding matches the gate's basis.
            code_embedding, _re_embed_cost = _embed(
                cfg, _novelty_embed_text(evo, parent.get("code", ""), mut["candidate_code"])
            )
            counters["cost"] = counters.get("cost", 0.0) + float(_re_embed_cost or 0.0)
    counters["eval_total"] += 1
    if not ev["correct"]:
        counters["eval_failures"] += 1
        # P2-T4: classify the un-repairable eval failure for the agent's sensor — a
        # timeout (the harness eval-time-limit signal `timed_out`) vs a wrong answer
        # (ran to completion but incorrect). Apply-exhausted is a distinct bucket
        # handled before eval (step 3a). Coarse on purpose — do NOT parse the
        # traceback into sub-types (that would couple the harness to the evaluator).
        if ev.get("timed_out"):
            counters["timeout_count"] = counters.get("timeout_count", 0) + 1
        else:
            counters["wrong_answer_count"] = counters.get("wrong_answer_count", 0) + 1
        # G1 (H7): this candidate is un-repairable (still incorrect AFTER the immediate-
        # fix loop / apply-retries exhausted their budget). Record its generation id so
        # the debug-agent escalation ("a candidate exhausts its retry budget") can fire
        # from real data instead of the hardcoded []. Resolves via archive_query by_generation.
        counters.setdefault("exhausted_retry_slots", []).append(f"gen{generation}")
        counters["exhausted_retry_count"] = counters.get("exhausted_retry_count", 0) + 1
    # NOTE: novelty_accepts/rejects are counted at the keep-the-better resolve below
    # (only when novelty is ENABLED and the candidate is correct), so the acceptance rate
    # reflects real novelty events — null when novelty is off (M11/M12), not a phantom 1.0.
    _trace({"step": "eval", "correct": ev.get("correct"),
            "combined_score": ev.get("combined_score"), "timed_out": ev.get("timed_out"),
            "failure_kind": (None if ev.get("correct")
                             else ("timeout" if ev.get("timed_out") else "wrong"))})

    # 4a'. REPAIR generation that FAILED → do NOT archive a new child. Append the
    # failure (truncated) to the errored PARENT's own record + bump its repair count;
    # after the attempt cap the parent is tombstoned (de-archived, lineage preserved).
    # A repair that SUCCEEDED falls through and is archived as a normal correct child.
    if _repair_gen and not ev.get("correct"):
        counters["repair_fail_count"] = counters.get("repair_fail_count", 0) + 1
        try:
            _rr = repair_record_script.main({
                "db_path": db_path, "db_config": db_config,
                "embedding_model": embedding_model,
                "program_id": sp["parent_id"], "action": "append_fail",
                "traceback_chunk": (ev.get("error_traceback") or ev.get("text_feedback") or ""),
                "attempt_cap": int(evo.get("repair_attempt_cap", 2) or 2),
            })
            if _rr.get("tombstoned"):
                counters["repair_tombstoned_count"] = counters.get("repair_tombstoned_count", 0) + 1
        except Exception:
            pass
        if llm_models and arm_id:  # charge the arm's spend (cost-only, no reward)
            select_llm_script.main({
                "mode": "update", "models": llm_models, "state_path": state_path,
                "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                "arm": arm_id, "cost": _slot_mut_cost,
                "cost_only": True, "reward": None, "baseline": None,
            })
        _trace({"step": "framework_decision", "action": "repair_failed_no_archive",
                "program_id": sp.get("parent_id")})
        return  # NO new child archived — the failure rode onto the errored parent's record

    # 4a''. KEEP-THE-BETTER novelty resolve (H5). A CORRECT near-duplicate competes with its
    # nearest archived neighbor BY SCORE (novelty deferred to here): keep the better, evict
    # (tombstone) the worse. novelty_acceptance_rate is counted HERE so it reflects real
    # novelty events among correct candidates (null when novelty is off — M11/M12).
    if evo.get("enable_novelty") and ev.get("correct") and code_embedding:
        nov = novelty_check_script.main({
            "db_path": db_path, "db_config": db_config,
            "embedding_model": embedding_model,
            "candidate_embedding": code_embedding or [],
            "code_embed_sim_threshold": evo.get("code_embed_sim_threshold", 0.99),
            "island_idx": sp.get("island_idx"),
        })
        if nov.get("accept"):
            counters["novelty_accepts"] += 1  # genuinely novel — archive normally below
        else:
            _inc_id = nov.get("most_similar_id")
            _inc_score = nov.get("most_similar_score")
            _cand_score = float(ev.get("combined_score", 0.0) or 0.0)
            # H2: keep an EQUAL-scoring DISTINCT near-dup (relax strict > to >=) so the
            # search can traverse score plateaus instead of dropping every tie after a full
            # eval. The incumbent is still tombstoned below, so on a tie the surviving
            # genotype ROTATES (lineage keeps moving) rather than freezing. novelty_tie_epsilon
            # (default 0.0 => plain >=) optionally keeps a near-dup within epsilon of the
            # incumbent. NOTE: growing the per-island pool past ~1 genotype needs the
            # diff-embedding REPRESENTATION change (the other half of H2) — >= alone restores
            # plateau traversal, not pool growth.
            _tie_eps = float(evo.get("novelty_tie_epsilon", 0.0) or 0.0)
            _keep_new = (_inc_id is None or _inc_score is None
                         or _cand_score >= float(_inc_score) - _tie_eps)
            if not _keep_new:
                # newcomer is NOT better than its near-duplicate → DROP it (keep the
                # incumbent); feed the arm its real spend (cost-only / penalize per lever).
                counters["novelty_rejects"] += 1
                _rej_cost = _slot_mut_cost + _slot_embed_cost
                counters["rejected_cost"] = counters.get("rejected_cost", 0.0) + _rej_cost
                if llm_models and arm_id:
                    _penalize = str(evo.get("reward_on_reject", "cost_only")) == "penalize"
                    select_llm_script.main({
                        "mode": "update", "models": llm_models, "state_path": state_path,
                        "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                        "arm": arm_id, "cost": _rej_cost,
                        "cost_only": (not _penalize), "reward": None, "baseline": None,
                    })
                _trace({"step": "framework_decision", "action": "dropped_novelty_worse",
                        "max_similarity": nov.get("max_similarity"),
                        "incumbent": _inc_id, "rejected_cost": _rej_cost})
                return  # keep the BETTER (incumbent); the worse newcomer is not archived
            # newcomer is strictly BETTER → keep it AND evict (tombstone) the worse
            # near-duplicate so the population doesn't carry both (the incumbent's row +
            # lineage are preserved; it just leaves the archive + sampling pool).
            counters["novelty_accepts"] += 1
            counters["novelty_kept_better"] = counters.get("novelty_kept_better", 0) + 1
            if _inc_id is not None:
                try:
                    repair_record_script.main({
                        "db_path": db_path, "db_config": db_config,
                        "embedding_model": embedding_model,
                        "program_id": _inc_id, "action": "tombstone",
                    })
                except Exception:
                    pass

    # 4b. compute reward (MUTABLE — scoring concern, generation half)
    reward = compute_reward_script.main(
        {
            "candidate": ev,
            "parent": {"combined_score": parent.get("combined_score", 0.0)},
            "mode": evo.get("reward_mode", "absolute"),
            "reward_validity_floor": evo.get("reward_validity_floor", 0.001),
        }
    )

    # 4c. record policy (MUTABLE — memory concern): what to persist in metadata
    rec = record_policy_script.main(
        {
            "eval": ev,
            "parent": {"combined_score": parent.get("combined_score", 0.0)},
            "mutation": {
                "patch_type": prompt["patch_type"], "patch_name": mut.get("name"),
                "num_applied": mut.get("num_applied"), "cost": _slot_mut_cost,
                "model_name": model_name, "transport": mut.get("transport"),
                "attempts": mut.get("attempts"),
            },
            "sample": {
                "parent_id": sp["parent_id"], "needs_fix": needs_fix,
                "archive_inspiration_ids": sp.get("archive_inspiration_ids", []),
                "top_k_inspiration_ids": sp.get("top_k_inspiration_ids", []),
            },
            "novelty": nov or None,
            "reward": reward,
        }
    )

    # 5. archive_record (IMMUTABLE plumbing)
    program_fields: Dict[str, Any] = {
        "code": mut["candidate_code"],
        "language": language,
        "generation": generation,
        "parent_id": sp["parent_id"],
        "archive_inspiration_ids": sp.get("archive_inspiration_ids", []),
        "top_k_inspiration_ids": sp.get("top_k_inspiration_ids", []),
        "code_diff": mut.get("description"),
        "combined_score": ev["combined_score"],
        "correct": ev["correct"],
        "public_metrics": ev["public_metrics"],
        "private_metrics": ev["private_metrics"],
        "error_traceback": ev.get("error_traceback"),
        # M5: persist the domain-failure reason so a later sampled-parent repair (which
        # has no traceback for a domain failure) can still see WHY the parent failed.
        "text_feedback": ev.get("text_feedback"),
        "metadata": rec.get("metadata", {}),
    }
    if code_embedding is not None:
        program_fields["embedding"] = code_embedding
    archive_record.main(
        {
            "db_path": db_path, "db_config": db_config, "embedding_model": embedding_model,
            "program": program_fields,
        }
    )
    _trace({"step": "framework_decision",
            "action": "recorded_correct" if ev.get("correct") else "recorded_incorrect",
            "reward": reward.get("reward"), "arm": arm_id})

    # 6. bandit update (MUTABLE — scoring concern, consumption half) using the
    # reward from compute_reward.py (NOT a hardcoded score).
    if llm_models:
        select_llm_script.main(
            {
                "mode": "update", "models": llm_models, "state_path": state_path,
                "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                "arm": arm_id,  # WS6: per-(model,effort) arm, not the bare model
                "reward": reward.get("reward"),
                "baseline": reward.get("baseline", 0.0),
                "cost": _slot_mut_cost,
            }
        )


def main(cfg: Dict[str, Any]) -> Dict[str, Any]:
    # Install-isolation guarantee: fail loudly if `shinka` is not this repo's.
    # Pass the harness's repo root (always correct, even when scripts/ is a copy).
    _common.assert_worktree_shinka(_REPO_ROOT)

    # P6-T1: BOOT guard. The orchestrator's first job is to author task_sys_msg (the
    # goal + hard constraints, without spoiling the held-out metric). Refuse to start —
    # spending NOTHING (before bootstrap/init_run) — if it was never authored: None,
    # empty, or the starter sentinel. require_sys_msg (default True) is the override for
    # a bare debug smoke; --warmup flips it off for its throwaway run only (P2-T2).
    _task0 = cfg.get("task") or {}
    _sysmsg = _task0.get("task_sys_msg")
    if (_sysmsg is None or str(_sysmsg).strip() == ""
            or str(_sysmsg).strip() == STARTER_SYS_MSG_SENTINEL):
        _msg = ("task_sys_msg is unset/placeholder — author the goal + hard constraints "
                "(no-spoil) before running; set task.require_sys_msg=false to override "
                "for a bare debug smoke.")
        if _task0.get("require_sys_msg", True):
            raise SystemExit(f"[boot] refusing to start: {_msg}")
        sys.stderr.write(f"[boot] WARNING: {_msg}\n")

    db_path = cfg.setdefault(
        "db_path", os.path.join(cfg["results_dir"], "programs.sqlite")
    )
    db_config = cfg["db_config"]
    evo = cfg["evo"]
    embedding_model = evo.get("embedding_model", "text-embedding-3-small")
    window_size = int(cfg.get("iters") or evo.get("window_size", 10))
    num_windows = max(1, int(cfg.get("windows", 1) or 1))  # G4: --windows 0 coerces to 1 (full-keyed diag, not a near-empty dict)

    os.makedirs(cfg["results_dir"], exist_ok=True)
    _boot_embed_cost = _bootstrap_initial(cfg)

    journal.init_run(
        cfg["results_dir"],
        {
            "run_id": cfg.get("run_id"),
            "goal": cfg.get("task", {}).get("task_sys_msg"),
            "task": cfg.get("task", {}).get("eval_program_path"),
            "budget_usd": cfg.get("budget_usd"),
            "config_digest": {
                "num_islands": db_config.get("num_islands"),
                "window_size": window_size,
                "llm_models": evo.get("llm_models"),
                "tau": evo.get("tau"),
            },
        },
    )

    # Fold the bootstrap seed's embedding cost (F7) into the ledger now that the
    # journal exists — bootstrap runs before init_run, so it couldn't account it.
    if _boot_embed_cost:
        journal.add_cost(cfg["results_dir"], _boot_embed_cost)

    window_state = cfg.get("window_state", {}) or {}
    window_index = int(window_state.get("window_index", 0))
    prior_low_streak = int(window_state.get("prior_low_streak", 0))

    budget = cfg.get("budget_usd")
    # F4: the self-contained strategy pointer — {target: hash} over all mutable
    # files, computed from the live scripts/. Stamped into every window so the log
    # pins the exact strategy version (all files) that produced each window.
    strategy_fingerprint = strategy_store.current_fingerprint()
    # G2 (H13): if the strategy fingerprint CHANGED since the last window (a rewrite
    # was deployed), zero prior_low_streak so the new strategy earns a FAIR TRIAL
    # instead of inheriting the old streak and re-tripping stagnation after a single
    # low window (intervention thrashing). The fair-trial LENGTH stays tunable via
    # evo.consecutive_required. Compares to the last journal window's fingerprint.
    try:
        _prev_win = journal.read_windows(cfg["results_dir"], last_n=1)
        _prev_fp = (_prev_win[-1].get("strategy_fingerprint") if _prev_win else None) or None
        if _prev_fp is not None and _prev_fp != strategy_fingerprint:
            prior_low_streak = 0
    except Exception:
        pass

    def _one_window(widx: int, prior_streak: int) -> Dict[str, Any]:
        best_start = _best_score(db_path, db_config, embedding_model)
        next_gen = _max_generation(db_path, db_config, embedding_model) + 1
        counters = {
            "iter_index": 0, "eval_total": 0, "eval_failures": 0,
            "novelty_accepts": 0, "novelty_rejects": 0, "fix_count": 0, "cost": 0.0,
            "rejected_cost": 0.0,  # F13: spend on novelty-rejected (un-evaluated) slots
            "fix_success": 0,      # WS1: immediate fixes that recovered correctness
            "needs_fix_count": 0,  # M9: sampled incorrect parents routed to repair mode
            "exhausted_retry_slots": [],  # G1/H7: gen ids of un-repairable slots this window
            "exhausted_retry_count": 0,
            "apply_exhausted": 0,  # P1-T1: slots where the patch never applied (no candidate produced)
            "timeout_count": 0,       # P2-T4: un-repairable eval failures that timed out
            "wrong_answer_count": 0,  # P2-T4: un-repairable eval failures that ran but were wrong
            "repair_fail_count": 0,       # P5-T4: repair generations that failed to fix
            "repair_tombstoned_count": 0, # P5-T4: parents tombstoned after the attempt cap
        }
        # HARD budget railguard (immutable safety, NOT a strategy knob): stop
        # starting candidates once cumulative spend (this window so far + all
        # prior windows + orchestrator interventions, from the ledger) reaches
        # the budget. Overshoot is at most one candidate's cost.
        prior_total = journal.total_cost(cfg["results_dir"])
        budget_hit = False
        iters_run = 0  # actual candidates attempted (may be < window_size on budget break)
        # P5-T4: repair-mode gate — ON when the PRIOR window's errored_fraction (which
        # EXCLUDES tombstoned programs, so the latch can RELEASE once dead programs are
        # removed) is >= the trigger. Only the FIRST slot of the window repairs.
        _prev_win = journal.read_windows(cfg["results_dir"], last_n=1)
        _errored_frac = float((_prev_win[-1].get("errored_fraction", 0.0) if _prev_win else 0.0) or 0.0)
        repair_on = _errored_frac >= float(evo.get("repair_trigger_fraction", 0.20))
        for i in range(window_size):
            if budget is not None and (prior_total + counters["cost"]) >= float(budget):
                budget_hit = True
                break
            counters["iter_index"] = i
            _run_one_candidate(cfg, next_gen + i, counters, repair=(repair_on and i == 0))
            iters_run += 1

        # H8/O3 (opt-in): drive island spawn/migrate via the MUTABLE island_policy
        # DECISION at the window boundary (not just the db_config add()-time
        # thresholds). Default off => today's behavior. Use with the db_config
        # auto-triggers off (enable_dynamic_islands=false + migration_rate=0, the
        # defaults) to avoid double-execution. Never let it break the window.
        if evo.get("island_policy_driven"):
            try:
                island_policy_script.main({
                    "db_path": db_path, "db_config": db_config,
                    "embedding_model": embedding_model,
                    "current_generation": (next_gen + iters_run - 1) if iters_run else next_gen,
                    "apply": True,
                })
            except Exception:
                pass

        # F9: read the REAL bandit posterior (+ per-arm tallies) for diagnostics,
        # so `llm_bandit_weights` reflects bandit_state.pkl instead of an empty
        # config field. Read-only "weights" mode — never perturbs the bandit.
        bandit_weights: Dict[str, Any] = {}
        bandit_counts: Dict[str, Any] = {}
        if evo.get("llm_models"):
            try:
                peek = select_llm_script.main(
                    {
                        "mode": "weights",
                        "models": evo.get("llm_models"),
                        "state_path": os.path.join(cfg["results_dir"], "bandit_state.pkl"),
                        "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                    }
                )
                bandit_weights = peek.get("weights", {}) or {}
                bandit_counts = peek.get("counts", {}) or {}
            except Exception:
                pass

        diag = diagnostics_script.main(
            {
                "db_path": db_path, "db_config": db_config,
                "embedding_model": embedding_model,
                # F8: report the ACTUAL number of candidates attempted, not the
                # constant window_size (they differ on a budget/early break).
                "window_index": widx, "iters_completed": iters_run,
                "best_score_start": best_start, "window_size": window_size,
                "strategy_fingerprint": strategy_fingerprint,
                "tau": evo.get("tau", 0.0),  # deprecated abs_floor alias
                "stagnation_abs_floor": evo.get("stagnation_abs_floor"),
                "stagnation_rel_frac": evo.get("stagnation_rel_frac"),
                "prior_low_streak": prior_streak,
                "consecutive_required": evo.get("consecutive_required", 2),
                "trigger_metric": evo.get("trigger_metric", "hybrid"),
                "novelty_accepts": counters["novelty_accepts"],
                "novelty_rejects": counters["novelty_rejects"],
                "novelty_rejected_cost": counters["rejected_cost"],
                "eval_failures": counters["eval_failures"],
                "eval_total": counters["eval_total"],
                "fix_count": counters["fix_count"],
                "fix_success": counters.get("fix_success", 0),
                "needs_fix_count": counters.get("needs_fix_count", 0),
                "llm_bandit_weights": bandit_weights,
                "llm_bandit_counts": bandit_counts,
                "exhausted_retry_slots": counters.get("exhausted_retry_slots", []),
                "exhausted_retry_count": counters.get("exhausted_retry_count", 0),
                "apply_exhausted": counters.get("apply_exhausted", 0),
                "timeout_count": counters.get("timeout_count", 0),
                "wrong_answer_count": counters.get("wrong_answer_count", 0),
                "repair_fail_count": counters.get("repair_fail_count", 0),
                "repair_tombstoned_count": counters.get("repair_tombstoned_count", 0),
                # P2-T3 sensor knobs threaded for diagnostics (collapse + repair trigger):
                "model_collapse_frac": evo.get("model_collapse_frac", 0.85),
                "model_collapse_min_pulls": evo.get("model_collapse_min_pulls", 8),
                "repair_trigger_fraction": evo.get("repair_trigger_fraction", 0.20),
            }
        )
        diag["window_cost"] = counters["cost"]
        diag["budget_hit"] = budget_hit
        journal.append_window(cfg["results_dir"], diag)  # folds window_cost into the ledger

        # P3-T2: AUTOMATIC per-window meta round — run by the HARNESS, not the agent. One
        # call → global directions + a failure caution + ONE distinct direction per live
        # island, auto-recorded as per-island briefs so islands diverge BY DEFAULT. The
        # call self-logs + folds its own cost into the ledger (do NOT append_intervention
        # it). Wrapped so a meta/parse/brief bug can NEVER crash a window. auto_meta:false
        # skips the WHOLE round (global + per-island briefs). It runs AFTER append_window
        # so diag's island_health is final; total_cost is refreshed below to include it.
        if evo.get("auto_meta", True):
            try:
                _mock = cfg.get("mock", {}) or {}
                _meta_gen = (next_gen + iters_run - 1) if iters_run else next_gen
                # H11: give meta the current best program WITH code (capped) so its
                # directions are grounded in what actually works (not score trends alone).
                try:
                    _meta_best = archive_query.main({
                        "db_path": db_path, "db_config": db_config,
                        "embedding_model": embedding_model, "query_type": "best",
                        "include_code": True,
                    })["result"]
                except Exception:
                    _meta_best = None
                _meta_payload = {
                    "model_name": evo.get("meta_model", "azure-gpt-5.5"),
                    "reasoning_effort": evo.get("meta_reasoning_effort", "medium"),
                    "goal": cfg["task"].get("task_sys_msg"),
                    "db_path": db_path, "db_config": db_config,
                    "embedding_model": embedding_model,
                    "results_dir": cfg["results_dir"],  # self-logs + folds cost into the ledger
                    "budget_usd": budget,                # meta self-skips near the cap
                    "run_id": cfg.get("run_id"),
                    "meta_failures_first_frac": evo.get("meta_failures_first_frac", 0.5),
                    # H9/M6: gate the evaluator error text out of meta on a spoil-risk task.
                    "use_text_feedback": bool(evo.get("use_text_feedback", True)),
                    "islands": [{"id": h.get("id"), "best": h.get("best"), "count": h.get("count")}
                                for h in diag.get("island_health", []) or []],
                    "num_islands": len(diag.get("island_health", []) or []),
                    "best_program": _meta_best,
                    "meta_code_preview_chars": evo.get("meta_code_preview_chars", 1200),
                }
                if _mock.get("enabled"):  # offline runs/tests: no Azure call
                    _meta_payload["mock"] = True
                    _meta_payload["mock_text"] = _mock.get("meta_mock_text", "")
                _meta = meta_summarize_script.main(_meta_payload)
                if not (_meta.get("skipped") or _meta.get("degraded")):
                    # Write global output into the LIVE evo dict (don't clobber a non-empty
                    # prior with None). run_window samples meta_directions per gen; the
                    # failure_note rides into every gen.
                    if _meta.get("directions"):
                        evo["meta_directions"] = _meta["directions"]
                    if _meta.get("failure_note"):
                        evo["meta_failure_note"] = _meta["failure_note"]
                    # Auto-record ONE brief per live island so islands diverge. Prefer the
                    # RICH per-island output (H1/M13): persist each island's directions +
                    # assigned program ids into structured_json so the SAMPLER can be
                    # direction-oriented. Fall back to the flat island_directions (content
                    # only) when only the legacy schema is present.
                    import json as _json
                    _rich = _meta.get("islands") or []
                    if _rich:
                        for _isl in _rich:
                            try:
                                _dirs = _isl.get("directions") or []
                                _headline = _dirs[0]["text"] if _dirs else ""
                                island_brief_script.main({
                                    "db_path": db_path, "db_config": db_config,
                                    "embedding_model": embedding_model,
                                    "island_idx": int(_isl["island_idx"]),
                                    "generation": _meta_gen,
                                    "content": _headline,
                                    "structured_json": _json.dumps({"directions": _dirs}),
                                    "stage": "auto_meta", "cost": 0.0,
                                })
                            except Exception:
                                pass  # one bad island entry must not abort the rest
                    else:
                        for _isl in _meta.get("island_directions", []) or []:
                            try:
                                island_brief_script.main({
                                    "db_path": db_path, "db_config": db_config,
                                    "embedding_model": embedding_model,
                                    "island_idx": int(_isl["island_idx"]),
                                    "generation": _meta_gen,
                                    "content": _isl.get("text", ""),
                                    "stage": "auto_meta", "cost": 0.0,
                                })
                            except Exception:
                                pass  # one bad island entry must not abort the rest
            except Exception:
                pass  # a meta/parse/brief failure must NEVER crash a window

        # Refresh AFTER the meta round so the returned diag includes meta spend.
        diag["total_cost"] = journal.total_cost(cfg["results_dir"])
        diag["budget_remaining"] = journal.budget_remaining(cfg["results_dir"], budget)
        return diag

    # Cadence: "until_decision" runs windows autonomously (NO orchestrator turn)
    # and returns control only when there's a decision. The WHEN-to-return choice
    # is delegated to the MUTABLE cadence_policy.py (the orchestrator can rewrite
    # it if it sees itself triggered too often/rarely). The budget railguard is
    # NOT delegated — it always hard-stops.
    cadence = cfg.get("cadence", {}) or {}
    until_decision = cadence.get("mode") == "until_decision"
    # OPTIONAL explicit ceiling (default: none → the work-score taper is UNCAPPED,
    # bounded only by the budget hard-stop / stagnation / termination). legacy knob.
    max_per_call = cadence.get("max_windows_per_call")  # None unless the user sets one
    base_low = float(cadence.get("base_low", 5) or 5)
    low_threshold = float(cadence.get("low_threshold", 1) or 0.0)
    # STAGE-1 early-phase floor: the first `early_phase_windows` windows each return
    # control individually (frequent inspection while the framework is least proven),
    # regardless of work score; then the work-score taper takes over. 0 disables it.
    early_phase_windows = int(cadence.get("early_phase_windows", 5) or 0)

    last_diag: Dict[str, Any] = {}
    if until_decision:
        # TERMINATION (H6/H7/H8): before launching another cluster, check the deterministic
        # stop signal — N consecutive control-returns that were each STAGNANT and had an
        # orchestrator INTERVENTION (a rewrite OR a DR) yet still couldn't escape stagnation.
        # Computed from the agent's canonical control_return rows; harness-decided + auto-
        # finalized (parity with budget_exhausted) so two agents can't disagree. Stagnation
        # alone never terminates — only stagnation the interventions could not break. (The
        # simplified rule: NO ">=1 DR" requirement — a DR counts simply as an intervention.)
        _term_n = int(cadence.get("termination_streak", 5) or 5)
        _term_streak = journal.termination_streak(cfg["results_dir"])
        if _term_n > 0 and _term_streak >= _term_n:
            _last = dict((journal.read_windows(cfg["results_dir"]) or [{}])[-1])
            _last["return_reason"] = "stagnation_intervention_exhausted"
            _last["termination_streak"] = _term_streak
            _last["ok"] = True
            try:
                journal.finalize_run(cfg["results_dir"], "stagnation_intervention_exhausted")
            except Exception:
                pass
            return _last
        # The next cluster's size is driven by the LAST control-return's work score
        # (recorded by the agent before this call) + how long work has stayed low.
        _recent_work = journal.recent_work_score(cfg["results_dir"])
        _low_streak = journal.work_low_streak(cfg["results_dir"], low_threshold)
        # No-score reminder: if the agent completed several control-returns but never
        # recorded a work score, the taper has no signal (and wakes every window).
        if _recent_work is None and len(journal.read_windows(cfg["results_dir"])) >= 3:
            sys.stderr.write(
                "[cadence] no work_score recorded across recent control-returns — the "
                "taper is waking every window by default; record a work score (how much "
                "the last control-return did) after each return so the loop can taper.\n"
            )
        windows_run = 0
        while True:
            last_diag = _one_window(window_index, prior_low_streak)
            windows_run += 1
            prior_low_streak = last_diag.get("low_streak", 0)
            window_index += 1
            if last_diag.get("budget_hit"):  # HARD railguard, not mutable; NO window cap
                last_diag["return_reason"] = "budget_exhausted"
                break
            # Budget hard-stop takes PRECEDENCE over the taper at the cluster boundary:
            # if cumulative spend has reached the cap, stop NOW (return "budget_exhausted",
            # not "taper") so the run terminates rather than handing back for another cluster.
            if budget is not None and journal.total_cost(cfg["results_dir"]) >= float(budget):
                last_diag["budget_hit"] = True
                last_diag["return_reason"] = "budget_exhausted"
                break
            decision = cadence_policy_script.main(
                {
                    "stagnation_flag": last_diag.get("stagnation_flag"),
                    "windows_run": windows_run,
                    # window_index was incremented above → it is the count of windows
                    # completed so far globally, which drives the Stage-1 early phase.
                    "window_index": window_index,
                    "early_phase_windows": early_phase_windows,
                    "recent_work_score": _recent_work,
                    "work_low_streak": _low_streak,
                    "base_low": base_low,
                    "low_threshold": low_threshold,
                    "max_windows_per_call": max_per_call,  # None → no ceiling
                    "low_streak": last_diag.get("low_streak"),
                    "evaluation_failure_rate": last_diag.get("evaluation_failure_rate"),
                }
            )
            if decision.get("return"):
                last_diag["return_reason"] = decision.get("reason", "decision")
                break
        last_diag["windows_run"] = windows_run
    else:
        for _w in range(num_windows):
            last_diag = _one_window(window_index, prior_low_streak)
            prior_low_streak = last_diag.get("low_streak", 0)
            window_index += 1
            if last_diag.get("budget_hit"):
                last_diag["return_reason"] = "budget_exhausted"
                break
        last_diag.setdefault("return_reason", "windows_done")

    # P8-T1: finalize the run ledger on the budget-exhausted TERMINAL return (so the
    # status reflects the stop). User-stop / five-in-a-row terminations are the agent's
    # judgment and call the journal `finalize_run` CLI view itself; a non-terminal
    # cadence/taper return does NOT finalize.
    if last_diag.get("return_reason") == "budget_exhausted":
        try:
            journal.finalize_run(cfg["results_dir"], "budget_exhausted")
        except Exception:
            pass

    # Surface the termination streak on every return so the agent sees how close the run is
    # to the deterministic stop (N consecutive stagnant + intervened control-returns).
    try:
        last_diag["termination_streak"] = journal.termination_streak(cfg["results_dir"])
    except Exception:
        pass
    last_diag["ok"] = True
    return last_diag


def _hold_no_idle_sleep():
    """Keep the host awake for THIS process's lifetime so a long run is never
    reaped by a macOS idle-sleep.

    Root cause of earlier mid-run kills (2026-05-27): on macOS the system
    idle-slept (battery AND, per `pmset`, even on AC where `sleep`=1 min) during
    long gaps, and the run_window process got reaped across that sleep. We spawn
    `caffeinate -i -m -w <our pid>`, which asserts PreventUserIdleSystemSleep until
    THIS process exits and then auto-exits (it watches our PID) — so it self-cleans
    even if run_window is SIGKILLed, and there is no orphaned assertion.

    Best-effort and self-disabling per platform: on macOS it spawns
    `caffeinate` (a no-op if `/usr/bin/caffeinate` is absent); on Windows it
    asserts ES_SYSTEM_REQUIRED via SetThreadExecutionState (released when the
    returned guard object is GC'd / the process exits); other platforms (Linux)
    remain a no-op and never raise. Also a no-op if an outer wrapper already set
    ``SHINKA_CAFFEINATED`` (e.g. an outer caffeinate wrapper). Lives in the CLI
    path only, so imported/test calls of ``main()`` never spawn caffeinate.
    NOTE: caffeinate cannot override a closed-lid (clamshell) sleep on a laptop —
    keep the lid open for unattended runs.
    """
    # Idempotent: an outer wrapper already holds the no-idle-sleep assertion.
    if os.environ.get("SHINKA_CAFFEINATED") == "1":
        return None
    if sys.platform == "darwin":
        if not os.path.exists("/usr/bin/caffeinate"):
            return None
        try:
            import subprocess
            proc = subprocess.Popen(
                ["/usr/bin/caffeinate", "-i", "-m", "-w", str(os.getpid())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            os.environ["SHINKA_CAFFEINATED"] = "1"
            return proc
        except Exception:
            return None
    if sys.platform == "win32":
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            os.environ["SHINKA_CAFFEINATED"] = "1"
            class _WinKeepAwake:
                def __del__(self):
                    try:
                        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                    except Exception:
                        pass
            return _WinKeepAwake()
        except Exception:
            return None
    return None  # Linux / other: no-op, no raise


def cleanup_warmup(results_dir: str) -> bool:
    """Delete the throwaway <results_dir>/warmup workspace so warmup artifacts never
    pollute the real run. Idempotent: True if it removed a dir, False if none existed."""
    import shutil

    warm = os.path.join(results_dir, "warmup")
    if os.path.isdir(warm):
        shutil.rmtree(warm, ignore_errors=True)
        return True
    return False


def _cli() -> None:
    # Self-protect against host idle-sleep reaping a long run (see docstring).
    _caffeinate_proc = _hold_no_idle_sleep()  # noqa: F841 (kept alive for the run)
    ap = argparse.ArgumentParser(description="Run W iterations under the current strategy.")
    ap.add_argument("--config", required=True, help="path to run config JSON")
    ap.add_argument("--windows", type=int, default=None)
    ap.add_argument("--iters", type=int, default=None)
    ap.add_argument(
        "--until-decision", action="store_true",
        help="run windows autonomously; return only on stagnation or the window cap",
    )
    ap.add_argument("--max-windows-per-call", type=int, default=None)
    ap.add_argument(
        "--resume", action="store_true",
        help="resume window_state (window_index + prior_low_streak) from the "
             "journal's last window instead of the hand-maintained config (F15) — "
             "removes the cross-invocation bookkeeping footgun",
    )
    ap.add_argument(
        "--warmup", action="store_true",
        help="WARMUP: run ONE window in a THROWAWAY workspace (<results_dir>/warmup — its "
             "own db + journal) with per-step tracing ON, so you can oversee one window "
             "step by step (read its journal/steps.jsonl), stop-correct-restart until it is "
             "meaningful, then clean up — WITHOUT polluting the real run. Validates the "
             "mechanism on a fresh archive. Clean up with --cleanup-warmup.",
    )
    ap.add_argument(
        "--trace-steps", action="store_true",
        help="turn per-step tracing ON for this invocation WITHOUT the warmup redirect — "
             "for the framework-audit measuring window (run with --windows 1) so its "
             "journal/steps.jsonl exists for you to read.",
    )
    ap.add_argument(
        "--cleanup-warmup", action="store_true",
        help="delete the <results_dir>/warmup throwaway workspace and exit.",
    )
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    if args.cleanup_warmup:
        removed = cleanup_warmup(cfg["results_dir"])
        sys.stdout.write(_common.dumps({"ok": True, "cleaned_warmup": removed}))
        sys.stdout.flush()
        return
    _warmup_dir = None
    if args.warmup:
        # Run in a THROWAWAY workspace so the real archive/journal stay pristine; the
        # agent oversees this fresh-archive window, then cleans it up. Trace ON; the
        # boot sentinel guard is relaxed for THIS invocation only (warmup runs BEFORE the
        # agent has authored task_sys_msg) — the real run keeps require_sys_msg=True.
        _warmup_dir = os.path.join(cfg["results_dir"], "warmup")
        cfg["results_dir"] = _warmup_dir
        cfg["db_path"] = os.path.join(_warmup_dir, "programs.sqlite")
        cfg["trace_steps"] = True
        cfg.setdefault("task", {})["require_sys_msg"] = False
        cfg.setdefault("cadence", {})["mode"] = "bounded"
        if args.windows is None:
            cfg["windows"] = 1
        if args.iters is None:
            cfg["iters"] = 1
    elif args.trace_steps:
        cfg["trace_steps"] = True
    if args.windows is not None:
        cfg["windows"] = args.windows
        # F5: an explicit --windows means "run exactly N bounded windows". Force
        # the bounded branch so it isn't silently ignored when the config file
        # sets cadence.mode=until_decision. (--until-decision below still wins if
        # the user passes it explicitly alongside --windows.)
        cfg.setdefault("cadence", {})["mode"] = "bounded"
    if args.iters is not None:
        cfg["iters"] = args.iters
    if args.until_decision:
        cfg.setdefault("cadence", {})["mode"] = "until_decision"
    if args.max_windows_per_call is not None:
        cfg.setdefault("cadence", {})["max_windows_per_call"] = args.max_windows_per_call
    if args.resume:
        # Read the last window's state from the journal so the orchestrator need
        # not hand-edit window_index / prior_low_streak between calls (F15).
        _last = journal.read_windows(cfg["results_dir"], last_n=1)
        if _last:
            _w = _last[-1]
            ws = cfg.setdefault("window_state", {})
            ws["window_index"] = int(_w.get("window_index", 0) or 0) + 1
            ws["prior_low_streak"] = int(_w.get("low_streak", 0) or 0)
            sys.stderr.write(
                f"[resume] window_index→{ws['window_index']} "
                f"prior_low_streak→{ws['prior_low_streak']}\n"
            )
    result = main(cfg)
    if _warmup_dir:
        result["warmup_workspace"] = _warmup_dir
        sys.stderr.write(
            f"[warmup] ran in throwaway workspace {_warmup_dir}\n"
            f"[warmup] read the per-step trace at {_warmup_dir}/journal/steps.jsonl; when "
            f"satisfied, clean up with --cleanup-warmup (or rm -rf {_warmup_dir})\n"
        )
    sys.stdout.write(_common.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    _cli()

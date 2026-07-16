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

The driver runs a window's candidates through a SLOT STATE MACHINE: up to
``evo.parallel_slots`` slots in flight (default 1 = the sequential reference
order, byte-identical to the historical driver), every shared-state mutation
(archive, bandit, counters, journal) serialized under ONE window mutex that is
released only around the long blocking stages (LLM call / embed / eval — see
``_stage_call``), ``evo.parallel_eval_slots`` bounding concurrent evals, the
errored-fraction-latch repair slot running SOLO (a pooled 5%-draw fix slot falls
back to a normal mutation when its errored parent is already being repaired in
flight), and all in-flight slots DRAINED before the
window boundary (diagnostics/meta see a quiesced archive exactly as before).
Slot lifecycle is journaled to ``journal/slots.jsonl`` (landing order = file
order). It writes to the same ``programs.sqlite`` schema shinka uses, so the
real ShinkaEvolveRunner could resume from a harness-produced archive.

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
import math
import os
import random
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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
import meta_summarize as meta_summarize_script  # noqa: E402  (automatic per-window meta round)
import island_brief as island_brief_script  # noqa: E402  (auto-record per-island briefs)
import repair_record as repair_record_script  # noqa: E402  (record failed repair attempts)
import journal  # noqa: E402  (harness sibling)
import strategy_store  # noqa: E402  (harness sibling — for the strategy fingerprint)

FOLDER_PREFIX = "gen"

# The starter run.json ships task_sys_msg as this sentinel; the harness refuses
# to start until the orchestrator authors a real goal (the boot first-job), so a paid
# run never proceeds with a placeholder goal.
STARTER_SYS_MSG_SENTINEL = "__UNSET_AUTHOR_AT_BOOT__"


def _read_code(path: str) -> str:
    # encoding pinned to UTF-8: program source (seed/candidate) routinely carries
    # non-ASCII; Windows would otherwise default to cp1252 and raise UnicodeDecodeError.
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _eval_budget_sec(task: Dict[str, Any]) -> Optional[float]:
    """The per-eval time budget in seconds (task.eval_time 'HH:MM:SS'), or None.
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
            model_name=cfg["evo"].get("embedding_model", "azure-text-embedding-3-small")
        )
        out = client.get_embedding(code)
        if isinstance(out, tuple):
            return out[0], float(out[1] or 0.0)
        return out, 0.0
    except Exception as exc:
        # Do NOT swallow silently. A failed embedder turns the WHOLE novelty gate off
        # for this slot and otherwise reads identically to "no novelty events". Surface it on
        # stderr; the caller increments embed_failures so a blind gate shows in the diagnostics.
        import sys as _sys

        print(
            f"[embed] embedding failed ({type(exc).__name__}: {exc}) — novelty gate blind "
            f"for this slot",
            file=_sys.stderr,
        )
        return None, 0.0


def _novelty_embed_text(evo: Dict[str, Any], parent_code: str, candidate_code: str) -> str:
    """Choose WHAT the novelty gate embeds.

    'diff' (default): embed the unified parent->candidate diff, so two genuinely
    different edits separate to LOW cosine — each is accepted as novel and the
    per-island pool can GROW past one genotype — while a true re-proposal of the
    same change shares a diff and is still caught as a near-duplicate. 'code': the
    legacy whole-program embedding (a small edit on a large program reads ~0.994
    similar to its parent, so every improvement gets flagged a near-dup and evicts
    its own parent — collapsing each island to a single-survivor greedy chain). Falls back to
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


# Bin edges for the per-window max-similarity histogram folded into window diagnostics
# (novelty_sim_histogram). Coarse and clustered near the 0.99 default gate so the
# orchestrator can watch the near-dup mass shift as it tunes code_embed_sim_threshold.
_NOVELTY_SIM_BINS = (
    (0.90, "<0.90"), (0.95, "0.90-0.95"), (0.97, "0.95-0.97"),
    (0.99, "0.97-0.99"), (1.0001, "0.99-1.00"),
)


def _sim_bin(sim: float) -> str:
    for edge, label in _NOVELTY_SIM_BINS:
        if sim < edge:
            return label
    return _NOVELTY_SIM_BINS[-1][1]


def _unified_diff_line_count(parent_code: str, candidate_code: str) -> int:
    """Lines in the parent->candidate unified diff — the cheap change-magnitude proxy
    logged per candidate (a scalar tweak is a few lines; a new-direction edit is many).
    Mirrors _novelty_embed_text's diff construction so the count matches the diff the
    novelty gate embeds under the default 'diff' basis."""
    import difflib

    return sum(1 for _ in difflib.unified_diff(
        parent_code.splitlines(), candidate_code.splitlines(), lineterm="", n=3))


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


def _init_paths(cfg: Dict[str, Any], strict: bool = True) -> List[str]:
    """Normalize task.init_program_path (single, back-compat) / task.init_program_paths
    (ordered list — seed i seeds island i) to ONE ordered seed list. EXACTLY ONE of the
    two keys may be set. strict=True (boot guard + every [re]seed path) raises
    SystemExit('[boot] ...') — fail BEFORE any spend, parity with the sys-msg guard —
    on: both keys set, neither set / empty list, duplicate paths (normcase+abspath,
    Windows-safe), a missing seed file, or more seeds than db_config.num_islands (each
    seed must own an island; raise num_islands or drop seeds). strict=False (digest /
    accept-warmup fold paths) never raises — best-effort list, possibly []."""
    task = cfg.get("task", {}) or {}
    single = task.get("init_program_path")
    plural = task.get("init_program_paths")

    def _fail(msg: str) -> List[str]:
        if strict:
            raise SystemExit(f"[boot] refusing to start: {msg}")
        return []

    if single and plural:
        return _fail(
            "set EXACTLY ONE of task.init_program_path / task.init_program_paths, not both")
    if plural is not None:
        if not isinstance(plural, list) or not plural:
            return _fail("task.init_program_paths must be a non-empty list of seed program paths")
        paths = [str(p) for p in plural]
    elif single:
        paths = [str(single)]
    else:
        return _fail(
            "no seed program: set task.init_program_path (one seed) or "
            "task.init_program_paths (K seeds, one island each)")
    if strict:
        seen = set()
        for p in paths:
            key = os.path.normcase(os.path.abspath(p))
            if key in seen:
                raise SystemExit(f"[boot] refusing to start: duplicate seed program {p!r}")
            seen.add(key)
            if not os.path.exists(p):
                raise SystemExit(f"[boot] refusing to start: seed program not found: {p!r}")
        num_islands = int((cfg.get("db_config", {}) or {}).get("num_islands", 2) or 2)
        if len(paths) > num_islands:
            raise SystemExit(
                f"[boot] refusing to start: {len(paths)} seed programs > "
                f"num_islands={num_islands} — each seed must own an island; raise "
                f"db_config.num_islands to >= {len(paths)} or drop seeds")
        exts = {os.path.splitext(p)[1].lower() for p in paths}
        if len(exts) > 1:
            sys.stderr.write(
                f"[boot] WARNING: seed programs mix extensions {sorted(exts)} — all "
                f"seeds share ONE task.language and ONE evaluator contract\n")
    return paths


def _bootstrap_initial(cfg: Dict[str, Any]) -> float:
    """If the archive is empty, evaluate the seed program(s) and record them as gen 0.

    SINGLE seed (task.init_program_path, or a one-element task.init_program_paths):
    the legacy path, byte-identical — the seed lands UNPINNED (no island_idx) and the
    foundation copy strategy fills islands 1..N-1 with copies.
    MULTI seed (K>1): evaluate ALL K first (no DB writes), then
      - EVERY seed failed → SystemExit with NOTHING recorded (the archive stays
        absent, so a rerun after fixing the seeds re-boots cleanly);
      - else insert seed i PINNED to island i (archive_record passes island_idx
        through; the foundation assign_island honors a pre-set pin), recording failed
        seeds too (correct=false, forensics — the archive itself stays correct-only);
      - ROUND-ROBIN FILL: every island j in 0..num_islands-1 without a correct root
        gets a copy of seed correct_idx[j % len(correct_idx)] (== j mod K when all
        seeds pass), pinned to island j, flagged metadata._seed_copy — so all islands
        start with a correct gen-0 root. Fill copies reuse the seed's embedding.

    Returns the total embedding cost incurred (0.0 when novelty is off or the archive
    was already bootstrapped) so the caller can fold it into the ledger once the
    journal exists (bootstrap runs before journal.init_run). This is the SINGLE
    seeding function — boot, the all-tombstoned gate below, and the mid-run
    empty-archive recovery all re-seed ALL K seeds through here."""
    db_path = cfg["db_path"]
    db_config = cfg["db_config"]
    evo = cfg["evo"]
    embedding_model = evo.get("embedding_model", "azure-text-embedding-3-small")
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
        # Gate on LIVE rows, not total. When total>0 but live==0 every row is
        # tombstoned (repair struck out the whole population incl. the seed) — fall through
        # to RE-SEED all K seed program(s) so the run recovers instead of crash-looping
        # (sample_parent would otherwise raise "archive is empty"). Auto-reseed with a
        # stderr event so it is visible.
        if int(count.get("live", count.get("total", 0)) or 0) > 0:
            return 0.0
        if int(count.get("total", 0) or 0) > 0:
            import sys as _sys

            print("[bootstrap] every archived row is tombstoned (live=0) — re-seeding "
                  "all seed program(s) to recover the run", file=_sys.stderr)

    task = cfg["task"]
    paths = _init_paths(cfg)
    K = len(paths)
    num_islands = int(db_config.get("num_islands", 2) or 2)
    mock = cfg.get("mock", {}) or {}
    seed_overrides = ({str(k): v for k, v in (mock.get("seed_overrides") or {}).items()}
                      if mock.get("enabled") else {})

    # Phase 1 — evaluate (and embed) ALL seeds before any DB write, so an all-fail
    # multi-seed boot can refuse with the archive still absent.
    gen_dir = os.path.join(cfg["results_dir"], f"{FOLDER_PREFIX}_0")
    seeds: List[Dict[str, Any]] = []
    embed_total = 0.0
    for i, p in enumerate(paths):
        results_dir = (os.path.join(gen_dir, "results") if K == 1
                       else os.path.join(gen_dir, f"seed_{i}", "results"))
        os.makedirs(results_dir, exist_ok=True)
        ev = _evaluate_candidate(cfg, p, results_dir, i, generation=0)
        ov = seed_overrides.get(str(i))
        if ov:  # offline-test hook: fail/score one seed of K deterministically
            ev = {**ev, **{k: ov[k] for k in
                           ("combined_score", "correct", "error_traceback") if k in ov}}
        code = _read_code(p)
        # Embed the seed so the FIRST mutations have a baseline to compare against.
        # Without this the novelty gate is a no-op (novelty_n_compared=0) until a few
        # embedded candidates accrue per island, letting near-duplicate early mutants
        # through uncounted.
        embedding = None
        if evo.get("enable_novelty"):
            embedding, _ecost = _embed(cfg, code)
            embed_total += float(_ecost or 0.0)
        seeds.append({"path": p, "ev": ev, "code": code, "embedding": embedding})

    def _fields(i: int, s: Dict[str, Any], island_idx: Optional[int] = None,
                seed_copy_island: Optional[int] = None) -> Dict[str, Any]:
        md: Dict[str, Any] = {"bootstrap": True}
        if K > 1:
            md["seed_index"] = i
        if seed_copy_island is not None:
            md["_seed_copy"] = True            # deliberately NOT _is_island_copy —
            md["_seed_copy_island"] = seed_copy_island  # distinct provenance from the copy strategy
        f: Dict[str, Any] = {
            "code": s["code"],
            "language": task.get("language", "python"),
            "generation": 0,
            "parent_id": None,
            "combined_score": s["ev"]["combined_score"],
            "correct": s["ev"]["correct"],
            "public_metrics": s["ev"]["public_metrics"],
            "private_metrics": s["ev"]["private_metrics"],
            "error_traceback": s["ev"].get("error_traceback"),
            "metadata": md,
        }
        if island_idx is not None:
            f["island_idx"] = int(island_idx)   # honored by the foundation assign_island
        if s["embedding"] is not None:
            f["embedding"] = s["embedding"]
        return f

    def _record(fields: Dict[str, Any]) -> None:
        archive_record.main(
            {
                "db_path": db_path,
                "db_config": db_config,
                "embedding_model": embedding_model,
                "program": fields,
            }
        )

    if K == 1:
        # Legacy single-seed path: unpinned insert → CopyInitialProgramIslandStrategy
        # fills islands 1..N-1 exactly as before this function grew multi-seed support.
        _record(_fields(0, seeds[0]))
        return float(embed_total or 0.0)

    correct_idx = [i for i, s in enumerate(seeds) if s["ev"].get("correct")]
    if not correct_idx:
        import sys as _sys

        for i, s in enumerate(seeds):
            head = str(s["ev"].get("error_traceback") or s["ev"].get("error") or "")[:300]
            print(f"[bootstrap] seed {i} ({s['path']}) FAILED evaluation: {head}",
                  file=_sys.stderr)
        raise SystemExit(
            "[boot] all seed programs failed evaluation — nothing was recorded (the "
            "archive stays absent so a rerun re-boots cleanly); fix the seeds or the "
            "evaluator and rerun")
    for i, s in enumerate(seeds):
        if not s["ev"].get("correct"):
            import sys as _sys

            print(f"[bootstrap] seed {i} ({s['path']}) failed evaluation — recorded for "
                  f"forensics; island {i} also gets a round-robin fill copy",
                  file=_sys.stderr)
        _record(_fields(i, s, island_idx=i))
    for j in range(num_islands):
        if j < K and seeds[j]["ev"].get("correct"):
            continue  # island j already has a correct seed root
        src = correct_idx[j % len(correct_idx)]
        _record(_fields(src, seeds[src], island_idx=j, seed_copy_island=j))
    return float(embed_total or 0.0)


def _parse_arm(arm_id: Optional[str], default_effort: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """A bandit arm id may encode reasoning effort as ``"model@effort"`` so the
    bandit treats each (model, effort) as a distinct arm (e.g. pro@medium vs pro@high
    are learned separately). Split it into (model_name, reasoning_effort) for the
    actual call; an arm with no ``@`` uses the run's default effort. Per-model VALID
    efforts are the orchestrator's responsibility when authoring the arm list (pro
    rejects 'low')."""
    if arm_id and "@" in arm_id:
        model, effort = arm_id.split("@", 1)
        return model, (effort or default_effort)
    return arm_id, default_effort


def _sample_patch_mode(patch_types, patch_type_probs, seed, exclude_fix=False) -> str:
    """Sample ONE patch MODE (diff/full/cross/fix) by weight, seeded. The mode is drawn
    BEFORE the parent so a "fix" draw can be paired with an INCORRECT parent and the others
    with a CORRECT parent. exclude_fix drops "fix" and renormalizes — the fallback used when a
    "fix" draw finds no errored parent in the pool."""
    types = list(patch_types or ["diff", "full", "cross", "fix"])
    probs = list(patch_type_probs or [0.55, 0.3, 0.1, 0.05])
    pairs = [(t, float(p)) for t, p in zip(types, probs) if not (exclude_fix and t == "fix")]
    if not pairs:
        return "diff"
    rng = random.Random(seed)
    _types = [t for t, _ in pairs]
    _w = [max(p, 0.0) for _, p in pairs]
    if sum(_w) <= 0:
        return rng.choice(_types)
    return rng.choices(_types, weights=_w, k=1)[0]


def _compose_meta_for_gen(evo: Dict[str, Any], generation: int) -> Optional[str]:
    """The no-brief fallback direction for THIS gen: None. Island differentiation is driven by
    the per-island brief (applied in sample_parent from the meta round's per-island output); an
    island that has no brief YET — a brand-new island, or any island before the first meta
    round — gets NO direction header, so the sampler renders its EXPERT_CREATIVE_PREAMBLE
    instead (a directive header wrapped around placeholder text read as a contradiction:
    "must pursue the direction below …: No explicit direction yet"). There is no separate
    global-directions channel any more. (The persistent ``evo.meta_failure_note`` rides as its
    own always-on ``failure_note`` field, not here, so the caution is never clobbered by an
    island brief or dropped on a cross/empty gen.)"""
    return None


def _stage_call(locks: Optional[Dict[str, Any]], sem_key: Optional[str], fn, *args, **kwargs):
    """Run a long BLOCKING stage (LLM call / embed / eval) OUTSIDE the window slot
    mutex so other slots can progress — the inverse-GIL pattern: every piece of
    shared state (counters, archive, bandit pkl, journal) mutates only while the
    mutex is held, and the mutex is released exactly around the calls where the
    wall-clock goes. ``sem_key`` names an extra concurrency gate ("eval" bounds
    simultaneous eval subprocesses to evo.parallel_eval_slots). Args are evaluated
    at the call site (mutex still held), so snapshotting shared values into the
    payload is race-free. locks=None (the sequential driver) => a plain call."""
    if not locks:
        return fn(*args, **kwargs)
    sem = locks.get(sem_key) if sem_key else None
    locks["mutex"].release()
    try:
        if sem is not None:
            with sem:
                return fn(*args, **kwargs)
        return fn(*args, **kwargs)
    finally:
        locks["mutex"].acquire()


def _head_tail_trunc(text: Any, cap: int = 2048) -> str:
    """Head+tail truncation for ONE error-history entry — same shape as the
    error_traceback bound, at a smaller per-entry cap so the whole history fits
    the record."""
    t = "" if text is None else str(text)
    if len(t) <= cap:
        return t
    half = cap // 2
    return t[:half] + "\n[... truncated ...]\n" + t[-half:]


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
    error_history: Optional[List[str]] = None,
    locks: Optional[Dict[str, Any]] = None,
    iter_index: Optional[int] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], float]:
    """IMMEDIATE correctness repair (MUTABLE fix concern).

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
        3 when grounding a TRIAGED discovery direction (R1 Azure deep research or
        R2 archive-analyst) as a new island's first member.
      * ``enable_web_search`` is OFF for ordinary fixes; it is turned on only
        when the repair is nailing a DISCOVERY reference. Left mutable for future
        outer-loops (one of the framework's policy switches, like novelty/bandit).
      * ``error_history`` (caller-owned list, seeded with the original attempt's
        error) collects each failed fix attempt's error — head+tail-truncated per
        entry — so a still-incorrect candidate archives its WHOLE failure history,
        not just the last error.
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
                "stdout_log": ev.get("stdout_log", "") or "",
                "stderr_log": (ev.get("error_traceback") or ev.get("text_feedback")
                               or ev.get("stderr_log") or ""),
            },
        }
        fix_prompt = construct_mutation_prompt.main(
            {
                "parent": incorrect_program,
                "needs_fix": True,
                # the correct ancestor to learn from (the sampled parent), if any.
                "ancestor_inspirations": [learn_from] if learn_from else [],
                "task_sys_msg": task.get("task_sys_msg"),
                "objective_brief": task.get("objective_brief"),  # orchestrator-authored objective gloss
                "language": language,
                # The persistent failure caution rides into fix-mode too.
                "failure_note": evo.get("meta_failure_note"),
                # Offset the seed by generation so fix prompts don't pin one patch type.
                "seed": (int(evo["seed"]) + generation) if evo.get("seed") is not None else None,
                # The per-eval budget + the just-failed candidate's runtime (it is never
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
            "reasoning_effort": reasoning_effort,  # same arm's effort as the failed attempt
            "max_attempts": evo.get("max_patch_attempts", 3),
            "run_id": cfg.get("run_id"),
            "generation": generation,
            "verbose": cfg.get("verbose", False),
            # Fix prompts share the task_sys_msg prefix with each other — co-route them.
            "prompt_cache_key": (f"{cfg.get('run_id')}:fix" if cfg.get("run_id") else None),
        }
        if enable_web_search:
            fix_payload["enable_web_search"] = True  # plumbed through mutate into the Azure call
        # A fix round re-enters the LLM stage: run it outside the slot mutex so
        # concurrent slots keep moving while this repair thinks.
        fix_mut = _stage_call(locks, None, mutate.main, fix_payload)
        _c = float(fix_mut.get("cost", 0.0) or 0.0)
        fix_cost += _c
        counters["cost"] = counters.get("cost", 0.0) + _c
        if evo.get("log_llm_content", True):
            # Durable per-call forensics (journal/llm_content/, 10GB run cap enforced by the
            # journal helper). attempt_index 0 is the original mutation; fixes are 1..N.
            # Wrapped so forensic logging can never break a window.
            try:
                journal.log_llm_content(
                    cfg["results_dir"], generation, fix_used, "fix",
                    {"model": model_name, "patch_type": fix_prompt["patch_type"],
                     "patch_sys": fix_prompt["patch_sys"], "patch_msg": fix_prompt["patch_msg"],
                     "raw_response": fix_mut.get("raw_response")},
                )
            except Exception:
                pass
        mut = fix_mut
        if not fix_mut.get("applied"):
            if error_history is not None:
                error_history.append(_head_tail_trunc(
                    f"(fix attempt {fix_used}) patch did not apply: {fix_mut.get('error')}"
                ))
            continue  # patch didn't apply; spend counted, retry if budget remains
        ev = _stage_call(
            locks, "eval", _evaluate_candidate,
            cfg, fix_mut["candidate_path"], results_dir,
            (iter_index if iter_index is not None else counters.get("iter_index", 0)),
            generation,
        )
        if ev.get("correct"):
            counters["fix_success"] = counters.get("fix_success", 0) + 1
            break
        if error_history is not None:
            error_history.append(_head_tail_trunc(
                ev.get("error_traceback") or ev.get("text_feedback") or ""
            ))
    return ev, mut, fix_cost


def _run_one_candidate(cfg: Dict[str, Any], generation: int, counters: Dict[str, int],
                       repair: bool = False,
                       locks: Optional[Dict[str, Any]] = None,
                       slot_ctx: Optional[Dict[str, Any]] = None) -> None:
    db_path = cfg["db_path"]
    db_config = cfg["db_config"]
    evo = cfg["evo"]
    task = cfg["task"]
    embedding_model = evo.get("embedding_model", "azure-text-embedding-3-small")
    language = task.get("language", "python")
    # Offset the seed by generation so the global np.random.seed in
    # construct_mutation_prompt / select_llm doesn't pin the SAME patch-type and
    # exploration draw every generation (operator-mix collapse). None => unseeded.
    _seed = evo.get("seed")
    gseed = (int(_seed) + generation) if _seed is not None else None
    # SIBLING slot (evo.sibling_samples, opt-in): reproduce the LEADER's prepare —
    # its prompt seed + pinned parent + arm — so the patch-mode draw, direction
    # sampling, inspiration choice and the full prompt string come out identical
    # (byte-identical while the archive is unchanged between the pair, which the
    # pairing makes the common case → the sibling's call hits the Azure prompt
    # cache on the WHOLE prompt) while the candidate itself still varies. All
    # children flow the NORMAL novelty/archive/bandit path — no best-of-K discard.
    # Per-slot iteration index. counters["iter_index"] is a window-global field a
    # concurrent slot can overwrite between mutex-released stages; the slot's own
    # index (threaded via slot_ctx) is race-free. Fallback keeps direct callers
    # (tests) working.
    _iter_idx = int((slot_ctx or {}).get("slot_index",
                                         counters.get("iter_index", 0)) or 0)
    _sib = (slot_ctx or {}).get("sibling_prepare") or None
    if _sib and not _sib.get("sp"):
        # A prepare payload without the leader's sample output can't reproduce the
        # prompt (re-sampling with a pinned parent skips the island/parent RNG
        # draws and diverges the direction/inspiration draws) — run fresh instead.
        _sib = None
    if _sib and _sib.get("gseed") is not None:
        gseed = _sib["gseed"]

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
        # How inspirations are picked BEFORE an island has a brief: "top" (default) |
        # "random". Orchestrator lever (docs teach it); sample_parent implements it.
        "prebrief_inspiration_mode": evo.get("prebrief_inspiration_mode", "top"),
    }
    # RPUCG-lite decay for the lineage_weighted strategy (evo lever; sample_parent
    # defaults to 0.8 when unset — forward only when the config actually sets it).
    if evo.get("lineage_gamma") is not None:
        _sp_payload["lineage_gamma"] = evo["lineage_gamma"]
    # Sample the patch MODE first (diff/full/cross + the 5% fix mode). A "fix" draw — or
    # the errfrac repair latch — pairs with an INCORRECT parent (select="errored" + the repair
    # path); the other modes pair with a CORRECT parent and the chosen mode is forced downstream.
    _mode = _sample_patch_mode(evo.get("patch_types"), evo.get("patch_type_probs"), gseed)
    # A sibling is always a NORMAL mutation of its leader's (correct) parent —
    # never a repair, even if the reproduced seed would draw "fix" against a
    # changed errored pool.
    _want_fix = (bool(repair) or (_mode == "fix")) and not _sib
    if _want_fix:
        _sp_payload["select"] = "errored"
        _sp_payload["repair_attempt_cap"] = int(evo.get("repair_attempt_cap", 2) or 2)
    if _sib:
        # Sibling: REUSE the leader's whole sample output (parent + island +
        # direction + inspiration ids) instead of re-sampling. Re-running the
        # sampler with just a pinned parent would SKIP the island/parent RNG
        # draws the leader consumed, so the direction/inspiration draws would
        # diverge — a different prompt (no whole-prompt cache hit) mislabeled as
        # a sibling. Archive rows are immutable, so re-fetching the same ids
        # below reproduces the leader's prompt byte-for-byte.
        sp = {**_sib["sp"], "needs_fix": False}
    else:
        try:
            sp = sample_parent.main(_sp_payload)
        except RuntimeError as _exc:
            # The live population is empty (every row tombstoned mid-cluster) — sample_parent
            # can't pick a parent. Re-seed ALL seed program(s) via _bootstrap_initial (fold the
            # embed cost) and retry once; if it STILL fails the run is genuinely unrecoverable,
            # so re-raise.
            if "archive is empty" not in str(_exc):
                raise
            counters["cost"] = counters.get("cost", 0.0) + float(_bootstrap_initial(cfg) or 0.0)
            sp = sample_parent.main(_sp_payload)
    # A repair generation = a fix was wanted (the 5% fix mode OR the errfrac repair latch) AND
    # the sampler returned an errored parent (empty errored pool → needs_fix False → normal slot).
    # The 5% fix mode reuses the full repair machinery (escalation, attempt accounting,
    # tombstoning) since fixing an incorrect parent IS repair.
    _repair_gen = bool(_want_fix and sp.get("needs_fix"))
    # The in-flight parent pins are a REFCOUNT map (two slots may legally share one
    # parent — every sibling pair does), maintained under the window mutex.
    _inflight_parents = (slot_ctx or {}).get("inflight_parents")
    # Concurrent-repair guard: the errored-parent pick is DETERMINISTIC (newest
    # errored), so two pooled 5%-fix draws would repair the SAME parent blind to
    # each other — double-charging repair_attempt_cap and skipping the strike-two
    # escalation. If that parent is already pinned by an in-flight slot, fall back
    # to a NORMAL mutation slot. (The errfrac-latch repair on slot 0 runs solo and
    # is unaffected.)
    if (_repair_gen and _inflight_parents is not None
            and _inflight_parents.get(sp.get("parent_id"), 0) > 0):
        _want_fix = False
        _repair_gen = False
        _mode = _sample_patch_mode(
            evo.get("patch_types"), evo.get("patch_type_probs"),
            (gseed + 15013) if gseed is not None else None, exclude_fix=True,
        )
        for _k in ("select", "repair_attempt_cap"):
            _sp_payload.pop(_k, None)
        sp = sample_parent.main(_sp_payload)
    # In-flight duplicate-parent guard (parallel slots only): concurrent slots sample
    # from one archive snapshot and the children-count bonus can't see in-flight
    # children, so two slots can draw the SAME parent. Resample with a perturbed seed
    # a couple of times; a persistent duplicate is accepted (siblings are legal).
    # Repair slots are exempt (deterministic newest-errored pick + the same-parent
    # fallback above).
    if _inflight_parents is not None and not sp.get("needs_fix") and not _sib:
        for _r in range(2):
            if sp.get("parent_id") not in _inflight_parents:
                break
            _sp_payload["seed"] = ((gseed or 0) + 104729 * (_r + 1))
            sp = sample_parent.main(_sp_payload)
    if slot_ctx is not None:
        # Pin this slot's parent in the shared refcount map: keep-the-better
        # eviction must not tombstone a program ANOTHER slot is actively deriving
        # from (the eviction check subtracts this slot's own pin, so a slot can
        # still evict its own parent exactly as the sequential driver always did).
        # The slot task decrements the pin when it lands.
        slot_ctx["parent_id"] = sp.get("parent_id")
        if _inflight_parents is not None and sp.get("parent_id"):
            _pid = sp["parent_id"]
            _inflight_parents[_pid] = _inflight_parents.get(_pid, 0) + 1
    # The forced patch MODE for a NON-fix slot. If a "fix" draw found no errored parent
    # (needs_fix False), fall back to a diff/full/cross draw (perturbed seed so it isn't "fix"
    # again); cross-with-no-inspirations suppression is handled inside the sampler.
    if sp.get("needs_fix"):
        _forced_patch = None
    elif _mode in ("diff", "full", "cross"):
        _forced_patch = _mode
    else:
        _forced_patch = _sample_patch_mode(
            evo.get("patch_types"), evo.get("patch_type_probs"),
            (gseed + 7919) if gseed is not None else None, exclude_fix=True,
        )
    parent = archive_query.main(
        {
            "db_path": db_path, "db_config": db_config, "embedding_model": embedding_model,
            "query_type": "get", "program_id": sp["parent_id"], "include_code": True,
            # The repair escalation hook below reads parent.metadata.repair_attempts
            # to detect strike-two; without this the hook could never fire.
            "include_metadata": True,
            # Only a CROSS gen needs the embedding (get_cross_component picks the
            # crossover partner MOST DISTANT from the parent); skip the blob read otherwise.
            "include_embedding": (_forced_patch == "cross"),
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
                        # Carry runtime_sec/timed_out metadata so the prompt builder can
                        # surface a runtime-budget caution from a slow/timed-out inspiration.
                        "include_metadata": True,
                        # A CROSS gen picks the crossover partner by embedding distance
                        # (get_cross_component -> _most_distant); carry embeddings only then.
                        "include_embedding": (_forced_patch == "cross"),
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
        # Feed the incorrect parent's OWN failure reason into the repair prompt.
        # sample_fix reads metadata.stderr_log; the parent summary carries
        # error_traceback as a top-level field (no include_metadata here), so route
        # it in (mirrors the immediate-fix path's stderr_log chain).
        # Evaluator feedback ALWAYS feeds the repair prompt — it is never gated or stripped.
        _pmd = parent.get("metadata") or {}
        if not _pmd.get("stderr_log"):
            # A domain failure (the common cnot class) carries no traceback — fall back
            # to the persisted text_feedback so this repair prompt isn't blind.
            _pmd["stderr_log"] = (
                parent.get("error_traceback") or parent.get("text_feedback") or ""
            )
        parent["metadata"] = _pmd
        # Count sampled needs_fix parents separately from immediate-fix ATTEMPTS
        # so fix_success_rate stays coherent (immediate repairs / immediate attempts).
        counters["needs_fix_count"] = counters.get("needs_fix_count", 0) + 1
    else:
        ancestors = []
        archive_insp = _fetch(sp.get("archive_inspiration_ids", []))
        top_k_insp = _fetch(sp.get("top_k_inspiration_ids", []))

    # 2a. per-island DIRECTION: fetch the latest brief the orchestrator authored
    # for THIS island so different islands carry DIFFERENT directions. None => no
    # direction header at all; the sampler renders EXPERT_CREATIVE_PREAMBLE instead.
    brief_text = None
    _isl = sp.get("island_idx")
    # Prefer the per-gen direction the SAMPLER drew from this island's STRUCTURED brief
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
    # No-brief gens carry no direction (None from _compose_meta_for_gen) and get the
    # expert/creative preamble; island differentiation rides on the per-island brief above.
    _meta_for_gen = _compose_meta_for_gen(evo, generation)
    prompt = construct_mutation_prompt.main(
        {
            "parent": parent,
            "archive_inspirations": archive_insp,
            "top_k_inspirations": top_k_insp,
            # A repair prompt carries NO inspirations — just fix the parent (owner design).
            # `ancestors` itself stays fetched: the repair reward baseline (step 4b) still
            # reads the nearest CORRECT ancestor's score from it.
            "ancestor_inspirations": [] if needs_fix else ancestors,
            "needs_fix": needs_fix,
            # The persistent failure caution rides separately as `failure_note`
            # (always-on, never dropped) rather than embedded in the direction
            # string, so an island brief or a cross/empty gen can never clobber it.
            "meta_recommendations": _meta_for_gen,
            "failure_note": evo.get("meta_failure_note"),
            # Per-island direction (None unless the orchestrator authored one).
            "island_brief": brief_text,
            "brief_compose_mode": evo.get("brief_compose_mode", "replace"),
            "task_sys_msg": task.get("task_sys_msg"),
            "objective_brief": task.get("objective_brief"),  # orchestrator-authored objective gloss
            "forced_patch_type": _forced_patch,              # mode sampled before the parent
            "patch_types": evo.get("patch_types"),
            "patch_type_probs": evo.get("patch_type_probs"),
            "language": language,
            "extra_guidance": evo.get("extra_guidance"),
            # Per-eval budget → bounded runtime caution when the parent/an inspiration ran slow.
            "eval_budget_sec": _eval_budget_sec(task),
            "seed": gseed,
            # Pin ONE full-rewrite format variant per WINDOW (instead of per-call
            # random): removes prefix-cache noise from the system prompt's tail while
            # windows still rotate through all variants across the run.
            "full_format_variant": counters.get("window_index"),
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
    if _sib and _sib.get("arm_id"):
        # Sibling reuses the leader's ARM (no fresh bandit pull — its completion
        # update below still credits/charges the arm; upstream counts in-flight
        # pulls the same way).
        arm_id = _sib["arm_id"]
    elif llm_models:
        sel = select_llm_script.main(
            {
                "mode": "select", "models": llm_models, "state_path": state_path,
                "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                # Bandit recovery levers (reachable WITHOUT a code rewrite): force_explore
                # ignores the collapsed posterior (uniform); llm_subset restricts arms.
                "force_explore": bool(evo.get("force_explore", False)),
                "subset": evo.get("llm_subset"),
                "seed": gseed,
            }
        )
        arm_id = sel["model_name"]
    # Split the arm id "model@effort" → (model, effort). The bandit (select +
    # update below) keys on arm_id, so it learns per (model,effort); the actual call
    # uses the clean model_name + that arm's effort (default when the arm has no @).
    model_name, reasoning_effort = _parse_arm(arm_id, evo.get("reasoning_effort"))
    # Count THIS window's per-arm submissions — the per-window collapse signal the
    # rollback decision's bandit-collapse counts-share check reads (the cumulative
    # bandit pkl counts can never move the share mid-run). Keyed
    # on arm_id (the bandit identity), once per candidate, BEFORE the apply-exhausted
    # early-return (mirrors update_submitted). The repair-escalation override below changes
    # model_name but NOT arm_id, so the count stays attributed to the bandit-selected arm.
    if arm_id is not None:
        counters.setdefault("arm_submitted", {})
        counters["arm_submitted"][arm_id] = counters["arm_submitted"].get(arm_id, 0) + 1
    # Escalation hook (present-but-off, default None). On a repair generation's
    # LAST attempt before the tombstone fires (the parent's repair_attempts would reach
    # the cap this round), optionally route the repair to a stronger model.
    # When a repair slot ESCALATES to repair_escalation_model, that model is NOT a bandit
    # arm — its reward/cost must NOT be credited/charged to the bandit-SELECTED (cheap) arm,
    # which would inflate the cheap arm's posterior with pro-level successes + cost. _escalated
    # gates the bandit feeds below; the spend still lands in counters['cost'] (the ledger).
    _escalated = False
    if _repair_gen and evo.get("repair_escalation_model"):
        _cap = int(evo.get("repair_attempt_cap", 2) or 2)
        _att = int(((parent.get("metadata") or {}).get("repair_attempts", 0)) or 0)
        if _att >= _cap - 1:
            model_name, reasoning_effort = _parse_arm(
                evo.get("repair_escalation_model"), evo.get("reasoning_effort"))
            _escalated = True

    # LEAD-slot publish (sibling pairing): expose this slot's WHOLE sample output —
    # parent + island + sampled direction + inspiration ids — plus the prompt seed
    # and arm, so the paired sibling reproduces the identical prompt without
    # re-sampling (re-sampling would diverge the RNG draws). Normal non-repair
    # slots only; a leader that never reaches this point publishes None from the
    # slot task's finally, and the sibling falls back to a fresh prepare.
    if (slot_ctx or {}).get("publish_prepare") and not _repair_gen and not needs_fix:
        try:
            slot_ctx["publish_prepare"]({
                "sp": {k: sp.get(k) for k in (
                    "parent_id", "island_idx", "sampled_island_idx",
                    "archive_inspiration_ids", "top_k_inspiration_ids",
                    "sampled_direction", "n_candidates", "selection_probs")},
                "gseed": gseed, "arm_id": arm_id, "ts": time.time(),
            })
        except Exception:
            pass

    # NOTE: there is no run_window "secondary grounding gate". Canonical
    # grounding is a STANDALONE mutate.py call the orchestrator makes BETWEEN clusters
    # (enable_web_search:true passed directly) — it never flows through this per-candidate
    # inner loop. The discovery-before-grounding rule is enforced where a grounded program
    # ENTERS the archive as a new family: the PRIMARY fail-closed gate in spawn_island.py
    # (novel new-island grounding) + grounding-engineer's refusal. A combine grounding into
    # an existing island is ordinary insertion; the work_discovery/work_grounding split
    # keeps it from padding the termination streak. evo.mutation_web_search below is just an
    # (unused) inner-loop web-search toggle, NOT a grounding signal.

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
        # Optional web search on the INNER-LOOP mutation call — OFF by default and
        # unused in practice (no run config has ever set it). NOT the grounding signal:
        # canonical grounding is a standalone mutate.py call with enable_web_search:true
        # passed directly, gated at spawn_island (PRIMARY) — not via this per-candidate
        # knob. Left as a plain toggle for any future inner-loop web use.
        "enable_web_search": bool(evo.get("mutation_web_search", False)),
        "max_attempts": evo.get("max_patch_attempts", 3),
        "run_id": cfg.get("run_id"),
        "generation": generation,
        "verbose": cfg.get("verbose", False),
        # Cache-routing hint: same run + same island share the longest prompt prefix
        # (task_sys_msg + direction/failure-note), so co-route them onto one warm
        # cache machine. Azure prompt caching itself is automatic; this only steers.
        "prompt_cache_key": (f"{cfg.get('run_id')}:isl{sp.get('island_idx')}"
                             if cfg.get("run_id") else None),
    }
    if mock.get("enabled"):
        mut_payload["mock"] = True
        mut_payload["mock_cost"] = mock.get("mutate_cost", 0.0)  # offline budget tests
        seq = mock.get("mutate_code_sequence")
        if seq is not None:
            mut_payload["mock_code"] = seq[_iter_idx % len(seq)]
        # else identity copy of parent
    # Sibling stagger: give the leader's identical-prefix call a head start so it
    # prefills the Azure prompt cache (prefill completes in seconds; the stagger is
    # noise against a minutes-long call). Skipped when the leader published long
    # enough ago (e.g. the sequential driver, where the leader already finished).
    _sib_stagger = 0.0
    if _sib and not mock.get("enabled"):
        _sst = float(evo.get("sibling_stagger_sec", 30) or 0)
        _sib_stagger = max(0.0, _sst - (time.time() - float(_sib.get("ts", 0) or 0)))

    def _mutate_stage():
        if _sib_stagger > 0:
            time.sleep(_sib_stagger)
        return mutate.main(mut_payload)

    mut = _stage_call(locks, None, _mutate_stage)
    # Account the mutation LLM cost immediately — it was incurred even if the
    # candidate is later rejected by novelty.
    _mut_cost = float(mut.get("cost", 0.0) or 0.0)
    counters["cost"] = counters.get("cost", 0.0) + _mut_cost
    _slot_mut_cost = _mut_cost  # arm's total model spend for this slot (+= fix cost below)
    if evo.get("log_llm_content", True) and not mock.get("enabled"):
        # Durable per-call forensics (journal/llm_content/, 10GB run cap enforced by the
        # journal helper). attempt_index 0 = the original mutation; fix attempts are 1..N
        # (logged inside _attempt_immediate_fixes). Wrapped so forensic logging can never
        # break a window.
        try:
            journal.log_llm_content(
                cfg["results_dir"], generation, 0, "mutation",
                {"model": model_name, "patch_type": prompt["patch_type"],
                 "patch_sys": prompt["patch_sys"], "patch_msg": prompt["patch_msg"],
                 "raw_response": mut.get("raw_response")},
            )
        except Exception:
            pass
    _trace({"step": "llm_output", "applied": mut.get("applied"),
            "num_applied": mut.get("num_applied"), "name": mut.get("name"),
            "transport": mut.get("transport"), "attempts": mut.get("attempts"),
            "cost": _mut_cost})

    # 3a. TRUTHFUL RECORDING: if the patch never applied even after the
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
        if llm_models and arm_id and not _escalated:  # escalated repair credits no arm
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
    # code embedding HERE, but DEFER the accept/reject to AFTER eval: keep-the-better
    # must compare BOTH programs' scores to keep the better of a near-duplicate pair, so a
    # near-dup is EVALUATED (not dropped pre-eval) and resolved at step 4a''.
    code_embedding: Optional[List[float]] = None
    nov: Dict[str, Any] = {}
    _slot_embed_cost = 0.0
    _cand_id = str(uuid.uuid4())   # generated up front so the novelty log + the archived row share one id
    _slot_diff_lines: Optional[int] = None
    if evo.get("enable_novelty"):
        # Embed the parent->candidate DIFF (default) instead of the whole program,
        # so a genuine improvement is NOT false-flagged as a near-dup of its parent.
        _slot_diff_lines = _unified_diff_line_count(parent.get("code", ""), mut["candidate_code"])
        code_embedding, _embed_cost = _stage_call(
            locks, None, _embed,
            cfg, _novelty_embed_text(evo, parent.get("code", ""), mut["candidate_code"]),
        )
        _slot_embed_cost = float(_embed_cost or 0.0)
        counters["cost"] = counters.get("cost", 0.0) + _slot_embed_cost
        if code_embedding is None:  # novelty gate is blind for this slot — make it visible
            counters["embed_failures"] = counters.get("embed_failures", 0) + 1

    # 4. evaluate (IMMUTABLE plumbing) — the eval stage: outside the slot mutex,
    # bounded by the eval semaphore (evo.parallel_eval_slots) so concurrent slots
    # never oversubscribe the machine's cores.
    ev = _stage_call(
        locks, "eval", _evaluate_candidate,
        cfg, mut["candidate_path"], results_dir, _iter_idx, generation,
    )
    # 4a. IMMEDIATE FIX (MUTABLE fix concern). On an eval failure, repair the
    # candidate in-place by re-prompting the same model with the error, up to
    # evo.fix_retry_budget times (default 1 for ordinary gens). Skipped in mock mode
    # (offline tests don't make LLM calls). eval_total/eval_failures below count the
    # FINAL post-fix state, so evaluation_failure_rate is the *un-repairable* rate.
    # Owner design: keep ALL error messages as metadata on a still-incorrect entry —
    # seed the history with the ORIGINAL attempt's error; each failed fix attempt
    # appends its own (per-entry head+tail-truncated; total capped at archive time).
    _err_history: List[str] = []
    if (
        not ev["correct"]
        and not (cfg.get("mock", {}) or {}).get("enabled")
        and int(evo.get("fix_retry_budget", 1)) > 0
    ):
        _pre_fix_code = mut["candidate_code"]
        _err_history.append(_head_tail_trunc(
            ev.get("error_traceback") or ev.get("text_feedback") or ""
        ))
        ev, mut, _fix_cost = _attempt_immediate_fixes(
            cfg, ev, mut, parent, model_name, reasoning_effort, gen_dir, results_dir,
            generation, language, int(evo.get("fix_retry_budget", 1)), counters,
            # Web search during ordinary fix-retries is OFF by default but left
            # mutable — a future outer-loop can set evo.fix_web_search to let the
            # repair model consult the web (like the other policy switches).
            enable_web_search=bool(evo.get("fix_web_search", False)),
            error_history=_err_history,
            locks=locks,
            iter_index=_iter_idx,
        )
        _slot_mut_cost += _fix_cost  # attribute the repair spend to the same arm
        # Re-embed only if a fix actually changed the code, so the archived
        # embedding matches the stored code (keeps the novelty gate honest).
        if evo.get("enable_novelty") and mut["candidate_code"] != _pre_fix_code:
            # Re-embed the (post-fix) parent->candidate diff, consistent with the
            # pre-fix embed above, so the stored embedding matches the gate's basis.
            _slot_diff_lines = _unified_diff_line_count(parent.get("code", ""), mut["candidate_code"])
            code_embedding, _re_embed_cost = _stage_call(
                locks, None, _embed,
                cfg, _novelty_embed_text(evo, parent.get("code", ""), mut["candidate_code"]),
            )
            counters["cost"] = counters.get("cost", 0.0) + float(_re_embed_cost or 0.0)
            if code_embedding is None:  # re-embed failed → gate blind for this slot
                counters["embed_failures"] = counters.get("embed_failures", 0) + 1
    counters["eval_total"] += 1
    if not ev["correct"]:
        counters["eval_failures"] += 1
        # Classify the un-repairable eval failure for the agent's sensor — a
        # timeout (the harness eval-time-limit signal `timed_out`) vs a wrong answer
        # (ran to completion but incorrect). Apply-exhausted is a distinct bucket
        # handled before eval (step 3a). Coarse on purpose — do NOT parse the
        # traceback into sub-types (that would couple the harness to the evaluator).
        if ev.get("timed_out"):
            counters["timeout_count"] = counters.get("timeout_count", 0) + 1
        else:
            counters["wrong_answer_count"] = counters.get("wrong_answer_count", 0) + 1
        # This candidate is un-repairable (still incorrect AFTER the immediate-
        # fix loop / apply-retries exhausted their budget). Record its generation id so
        # the debug-agent escalation ("a candidate exhausts its retry budget") can fire
        # from real data instead of the hardcoded []. Resolves via archive_query by_generation.
        counters.setdefault("exhausted_retry_slots", []).append(f"gen{generation}")
        counters["exhausted_retry_count"] = counters.get("exhausted_retry_count", 0) + 1
    # NOTE: novelty_accepts/rejects are counted at the keep-the-better resolve below
    # (only when novelty is ENABLED and the candidate is correct), so the acceptance rate
    # reflects real novelty events — null when novelty is off, not a phantom 1.0.
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
        if llm_models and arm_id and not _escalated:  # escalated repair credits no arm (cost-only otherwise)
            select_llm_script.main({
                "mode": "update", "models": llm_models, "state_path": state_path,
                "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                "arm": arm_id, "cost": _slot_mut_cost,
                "cost_only": True, "reward": None, "baseline": None,
            })
        _trace({"step": "framework_decision", "action": "repair_failed_no_archive",
                "program_id": sp.get("parent_id")})
        return  # NO new child archived — the failure rode onto the errored parent's record

    # 4a''. KEEP-THE-BETTER novelty resolve. A CORRECT near-duplicate competes with its
    # nearest archived neighbor BY SCORE (novelty deferred to here): keep the better, evict
    # (tombstone) the worse. novelty_acceptance_rate is counted HERE so it reflects real
    # novelty events among correct candidates (null when novelty is off).
    if evo.get("enable_novelty") and ev.get("correct") and code_embedding:
        nov = novelty_check_script.main({
            "db_path": db_path, "db_config": db_config,
            "embedding_model": embedding_model,
            "candidate_embedding": code_embedding or [],
            "code_embed_sim_threshold": evo.get("code_embed_sim_threshold", 0.99),
            # evo.novelty_scope is an orchestrator lever (docs teach it): "island"
            # (default) gates within the parent's island; "global" against the whole archive.
            "island_idx": (None if str(evo.get("novelty_scope", "island")) == "global"
                           else sp.get("island_idx")),
        })
        _max_sim = float(nov.get("max_similarity", 0.0) or 0.0)
        _n_cmp = int(nov.get("n_compared", 0) or 0)
        if _n_cmp > 0:  # feature: per-window similarity histogram (only real comparisons)
            _hist = counters.setdefault("novelty_sim_histogram", {})
            _b = _sim_bin(_max_sim)
            _hist[_b] = _hist.get(_b, 0) + 1

        def _log_nov(decision: str) -> None:
            # feature: ONE per-candidate novelty record (ids + numbers, never code). Use
            # cfg["results_dir"] (the RUN dir), NOT the local results_dir (the per-gen eval dir).
            journal.log_novelty(cfg["results_dir"], {
                "window_index": counters.get("window_index"),
                "generation": generation,
                "candidate_id": _cand_id,
                "parent_id": sp.get("parent_id"),
                "island_idx": sp.get("island_idx"),
                "decision": decision,
                "max_similarity": _max_sim,
                "most_similar_id": nov.get("most_similar_id"),
                "most_similar_score": nov.get("most_similar_score"),
                "candidate_score": float(ev.get("combined_score", 0.0) or 0.0),
                "n_compared": _n_cmp,
                "diff_lines": _slot_diff_lines,
                "threshold": float(evo.get("code_embed_sim_threshold", 0.99) or 0.99),
            })

        if nov.get("accept"):
            # An accept with n_compared==0 means the gate had NOTHING to compare against
            # (novelty enabled mid-run over an unembedded archive, or every neighbor's embed
            # failed) — the gate is IDLE, not "perfectly diverse". Count it separately so
            # novelty_acceptance_rate stays honest (None when there were no REAL comparisons)
            # instead of reading a phantom 1.0.
            if int(nov.get("n_compared", 0) or 0) > 0:
                counters["novelty_accepts"] += 1  # genuinely novel — archive normally below
                _log_nov("accepted_novel")
            else:
                counters["novelty_idle_count"] = counters.get("novelty_idle_count", 0) + 1
                _log_nov("idle_no_compare")
        else:
            _inc_id = nov.get("most_similar_id")
            _inc_score = nov.get("most_similar_score")
            _cand_score = float(ev.get("combined_score", 0.0) or 0.0)
            # Keep an EQUAL-scoring DISTINCT near-dup (relax strict > to >=) so the
            # search can traverse score plateaus instead of dropping every tie after a full
            # eval. The incumbent is still tombstoned below, so on a tie the surviving
            # genotype ROTATES (lineage keeps moving) rather than freezing. novelty_tie_epsilon
            # (default 0.0 => plain >=) optionally keeps a near-dup within epsilon of the
            # incumbent. NOTE: growing the per-island pool past ~1 genotype needs the
            # diff-embedding REPRESENTATION (novelty_embed_mode "diff", the default) — >=
            # alone restores plateau traversal, not pool growth.
            _tie_eps = float(evo.get("novelty_tie_epsilon", 0.0) or 0.0)
            _keep_new = (_inc_id is None or _inc_score is None
                         or _cand_score >= float(_inc_score) - _tie_eps)
            if not _keep_new:
                # newcomer is NOT better than its near-duplicate → DROP it (keep the
                # incumbent); feed the arm its real spend (cost-only / penalize per lever).
                counters["novelty_rejects"] += 1
                _rej_cost = _slot_mut_cost + _slot_embed_cost
                counters["rejected_cost"] = counters.get("rejected_cost", 0.0) + _rej_cost
                if llm_models and arm_id and not _escalated:  # escalated repair credits no arm
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
                _log_nov("dropped_worse")
                return  # keep the BETTER (incumbent); the worse newcomer is not archived
            # newcomer is strictly BETTER → keep it AND evict (tombstone) the worse
            # near-duplicate so the population doesn't carry both (the incumbent's row +
            # lineage are preserved; it just leaves the archive + sampling pool).
            counters["novelty_accepts"] += 1
            # Pin check SUBTRACTS this slot's own pin: a slot may evict its OWN
            # parent (the sequential driver always could — parity preserved); only
            # a pin held by ANOTHER in-flight slot blocks the eviction.
            _pins = (slot_ctx or {}).get("inflight_parents") or {}
            _own_pin = 1 if (_inc_id is not None and _inc_id == sp.get("parent_id")) else 0
            if _inc_id is not None and (_pins.get(_inc_id, 0) - _own_pin) > 0:
                # The incumbent is pinned as ANOTHER in-flight slot's parent —
                # skip the eviction (keep both near-dups) rather than tombstone a
                # program a concurrent slot is actively deriving from; the gate
                # self-corrects on a later comparison.
                counters["novelty_evict_skipped_pinned"] = (
                    counters.get("novelty_evict_skipped_pinned", 0) + 1)
                _trace({"step": "framework_decision",
                        "action": "kept_better_evict_skipped_pinned",
                        "incumbent": _inc_id,
                        "max_similarity": nov.get("max_similarity")})
                _log_nov("kept_better_evict_skipped_pinned")
            elif _inc_id is not None:
                try:
                    repair_record_script.main({
                        "db_path": db_path, "db_config": db_config,
                        "embedding_model": embedding_model,
                        # This incumbent is a CORRECT program evicted as the worse of a
                        # near-dup pair — NOT a repair removal. reason keeps errored_fraction
                        # from counting it as an errored program.
                        "program_id": _inc_id, "action": "tombstone", "reason": "novelty_evict",
                    })
                    # Make the eviction OBSERVABLE — it is the activity that reveals a
                    # near-dup flood. novelty_kept_better counts ACTUAL evictions only
                    # and is surfaced into the diag below.
                    counters["novelty_kept_better"] = counters.get("novelty_kept_better", 0) + 1
                    _trace({"step": "framework_decision", "action": "kept_better_evicted",
                            "incumbent": _inc_id, "max_similarity": nov.get("max_similarity")})
                    _log_nov("kept_better_evicted")
                except Exception as _exc:
                    # A FAILED eviction leaves BOTH near-dups live — count + trace it
                    # (do NOT silently pass), but never crash the window.
                    counters["novelty_evict_fail_count"] = counters.get("novelty_evict_fail_count", 0) + 1
                    _trace({"step": "framework_decision", "action": "kept_better_evict_failed",
                            "incumbent": _inc_id, "error": repr(_exc)})
                    _log_nov("kept_better_evict_failed")
            else:
                # Defensive: the gate said not-novel but named no incumbent —
                # the newcomer is kept, nothing to evict.
                _log_nov("kept_better_no_incumbent")

    # 4b. compute reward (MUTABLE — scoring concern, generation half)
    # On a REPAIR gen the parent is the ERRORED program (score ≈ 0), so crediting
    # repaired_score - 0 makes a routine bug-fix look like a huge gain and BLOWS OUT the bandit's
    # obs_max — after which every normal small delta normalizes to ~0. Use the nearest CORRECT
    # ancestor's score (the last-good version before the error) as the pre-error baseline, so the
    # credited delta is the repair's REAL improvement over that, not over a broken ~0 parent.
    _reward_parent_score = parent.get("combined_score", 0.0)
    if _repair_gen:
        for _a in reversed(ancestors or []):  # ancestry is oldest-first → nearest is last
            _asc = _a.get("combined_score")
            if _a.get("correct") and isinstance(_asc, (int, float)) and math.isfinite(float(_asc)):
                _reward_parent_score = float(_asc)
                break
    reward = compute_reward_script.main(
        {
            "candidate": ev,
            "parent": {"combined_score": _reward_parent_score},
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
    _rec_metadata = rec.get("metadata", {}) or {}
    if not ev["correct"] and _err_history:
        # Owner design: a still-incorrect entry keeps ALL its error messages, not only the
        # final error_traceback — a later repair sees the whole failure history. Total is
        # capped ~8KB (matching the error_traceback bound) by dropping the OLDEST entries.
        _total, _kept = 0, []
        for _e in reversed(_err_history):  # most recent attempts win the cap
            _total += len(_e)
            if _total > 8192 and _kept:
                break
            _kept.append(_e)
        _rec_metadata["error_history"] = list(reversed(_kept))
    program_fields: Dict[str, Any] = {
        "id": _cand_id,
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
        # Persist the domain-failure reason so a later sampled-parent repair (which
        # has no traceback for a domain failure) can still see WHY the parent failed.
        "text_feedback": ev.get("text_feedback"),
        "metadata": _rec_metadata,
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
    # reward from compute_reward.py (NOT a hardcoded score). Skip on an escalated repair
    # slot — the escalation model is not a bandit arm, so crediting arm_id would distort it.
    if llm_models and not _escalated:
        select_llm_script.main(
            {
                "mode": "update", "models": llm_models, "state_path": state_path,
                "bandit_kwargs": evo.get("llm_dynamic_selection_kwargs", {}),
                "arm": arm_id,  # per-(model,effort) arm, not the bare model
                "reward": reward.get("reward"),
                "baseline": reward.get("baseline", 0.0),
                "cost": _slot_mut_cost,
            }
        )


def main(cfg: Dict[str, Any]) -> Dict[str, Any]:
    # Install-isolation guarantee: fail loudly if `shinka` is not this repo's.
    # Pass the harness's repo root (always correct, even when scripts/ is a copy).
    _common.assert_worktree_shinka(_REPO_ROOT)

    # SETUP-CHECK: green-light the discovery-gate contract at boot.
    # Cheap, non-fragile: confirm the fail-closed recency helper is wired and that
    # recent_work_axes carries the three work axes (audit/discovery/grounding) so the
    # termination streak can tell discovery from grounding. Does NOT read results_dir
    # (works on an empty run) — it only inspects the journal module's shape.
    assert callable(getattr(journal, "discovery_in_interval", None)), (
        "[setup] journal.discovery_in_interval missing — the fail-closed discovery "
        "recency gate is not wired; refusing to boot.")
    assert callable(getattr(journal, "recent_work_axes", None)), (
        "[setup] journal.recent_work_axes missing — refusing to boot.")
    assert callable(getattr(journal, "pending_steering", None)), (
        "[setup] journal.pending_steering missing — the human-steering queue is not "
        "wired; refusing to boot.")
    import inspect as _inspect
    try:
        _axes_src = _inspect.getsource(journal.recent_work_axes)
    except (OSError, TypeError):  # source unavailable (e.g. frozen) — don't block boot
        _axes_src = "work_audit work_discovery work_grounding"
    assert all(_k in _axes_src for _k in
               ("work_discovery", "work_grounding")), (
        "[setup] recent_work_axes does not expose the three work axes "
        "(work_audit/work_discovery/work_grounding); refusing to boot.")
    # stderr ONLY: run_window's stdout is the single JSON envelope (_common.run_main /
    # the launcher json.loads it). A stray stdout line corrupts that contract.
    print("[setup] discovery-gate contract OK", file=sys.stderr)

    # BOOT guard. The orchestrator's first job is to author task_sys_msg (the
    # goal + hard constraints). Refuse to start —
    # spending NOTHING (before bootstrap/init_run) — if it was never authored: None,
    # empty, or the starter sentinel. require_sys_msg (default True) is the override for
    # a bare debug smoke; --warmup flips it off for its throwaway run only.
    _task0 = cfg.get("task") or {}
    _sysmsg = _task0.get("task_sys_msg")
    if (_sysmsg is None or str(_sysmsg).strip() == ""
            or str(_sysmsg).strip() == STARTER_SYS_MSG_SENTINEL):
        _msg = ("task_sys_msg is unset/placeholder — author the goal + hard constraints "
                "before running; set task.require_sys_msg=false to override "
                "for a bare debug smoke.")
        if _task0.get("require_sys_msg", True):
            raise SystemExit(f"[boot] refusing to start: {_msg}")
        sys.stderr.write(f"[boot] WARNING: {_msg}\n")

    # SEED guard (multi-seed aware): normalize + validate task.init_program_path /
    # task.init_program_paths BEFORE any spend — same fail-before-spend semantics as
    # the sys-msg guard above (both-set / neither-set / duplicates / missing files /
    # more seeds than islands all refuse here, not after evaluations).
    _init_paths(cfg)

    db_path = cfg.setdefault(
        "db_path", os.path.join(cfg["results_dir"], "programs.sqlite")
    )
    db_config = cfg["db_config"]
    evo = cfg["evo"]
    embedding_model = evo.get("embedding_model", "azure-text-embedding-3-small")
    window_size = int(cfg.get("iters") or evo.get("window_size", 10))
    num_windows = max(1, int(cfg.get("windows", 1) or 1))  # --windows 0 coerces to 1 so the diag comes back full-keyed, not a near-empty dict

    os.makedirs(cfg["results_dir"], exist_ok=True)
    # The cooperative .stop sentinel target — the run's results_dir, or its PARENT during warmup
    # (cfg["stop_dir"], set by _cli before the warmup redirect) so a .stop the agent writes to the
    # dir it naturally targets still stops a warmup, not only at the warmup window's end.
    stop_dir = cfg.get("stop_dir") or cfg["results_dir"]
    # Clear any stale cooperative-stop sentinel left by a prior process generation, so a
    # crash-orphaned .stop cannot immediately stop this fresh/resumed run (a stop-loop).
    _clear_stop(stop_dir)
    _boot_embed_cost = _bootstrap_initial(cfg)

    journal.init_run(cfg["results_dir"], _run_meta(cfg))

    # Fold the bootstrap seed's embedding cost into the ledger now that the
    # journal exists — bootstrap runs before init_run, so it couldn't account it.
    if _boot_embed_cost:
        journal.add_cost(cfg["results_dir"], _boot_embed_cost)

    # The persistent failure_note (every gen's prompt) and the rolling global_insights
    # scratchpad (the meta round's cross-window memory) live only in the in-memory cfg, so
    # they are LOST at every cluster relaunch (each early-phase window runs in a fresh
    # process). Re-hydrate both from the last logged meta round so they survive a relaunch;
    # this window's meta round overwrites them. (Island directions live per-island as briefs
    # in the archive, read directly by the sampler — nothing else to rehydrate.)
    if not evo.get("meta_failure_note") or not evo.get("meta_global_insights"):
        _mh = _common.recent_meta_output(cfg["results_dir"])
        if not evo.get("meta_failure_note") and _mh.get("failure_note"):
            evo["meta_failure_note"] = _mh["failure_note"]
        if not evo.get("meta_global_insights") and _mh.get("global_insights"):
            evo["meta_global_insights"] = _mh["global_insights"]

    window_state = cfg.get("window_state", {}) or {}
    window_index = int(window_state.get("window_index", 0))
    prior_low_streak = int(window_state.get("prior_low_streak", 0))

    budget = cfg.get("budget_usd")
    run_id = cfg.get("run_id")  # for the cooperative .stop sentinel target match
    _coop_stop = {"hit": False}  # set by the between-candidate stop check inside _one_window
    # The self-contained strategy pointer — {target: hash} over all mutable
    # files, computed from the live scripts/. Stamped into every window so the log
    # pins the exact strategy version (all files) that produced each window.
    strategy_fingerprint = strategy_store.current_fingerprint()
    # The config LEVER hash — stamped into every window row so the verified
    # termination check can detect a deliberate config-lever flip between clusters
    # (a lever flip only takes effect at a process relaunch, so once per process is
    # exactly right). See _config_lever_hash for the volatile-key exclusions.
    _lever_hash = _config_lever_hash(cfg)
    # If the strategy fingerprint CHANGED since the last window (a rewrite
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
            "window_index": widx,            # threaded into the per-candidate novelty log
            "novelty_sim_histogram": {},     # per-window max-similarity histogram (feature)
            "arm_submitted": {},   # per-arm submitted counts THIS window (feeds the bandit-collapse rollback check)
            "novelty_accepts": 0, "novelty_rejects": 0, "fix_count": 0, "cost": 0.0,
            "novelty_kept_better": 0,       # near-dup pairs where the newcomer won + evicted the incumbent
            "novelty_idle_count": 0,        # novelty accepts with nothing to compare (idle gate)
            "novelty_evict_fail_count": 0,  # keep-better evictions that FAILED (both near-dups left live)
            "embed_failures": 0,            # slots where embedding failed (novelty gate blind)
            "rejected_cost": 0.0,  # spend on novelty-DROPPED slots (EVALUATED near-dups that lost keep-the-better — not "un-evaluated")
            "fix_success": 0,      # immediate fixes that recovered correctness
            "needs_fix_count": 0,  # sampled incorrect parents routed to repair mode
            "exhausted_retry_slots": [],  # gen ids of un-repairable slots this window
            "exhausted_retry_count": 0,
            "apply_exhausted": 0,  # slots where the patch never applied (no candidate produced)
            "timeout_count": 0,       # un-repairable eval failures that timed out
            "wrong_answer_count": 0,  # un-repairable eval failures that ran but were wrong
            "repair_fail_count": 0,       # repair generations that failed to fix
            "repair_tombstoned_count": 0, # parents tombstoned after the attempt cap
        }
        # HARD budget railguard (immutable safety, NOT a strategy knob): stop
        # starting candidates once cumulative spend (this window so far + all
        # prior windows + orchestrator interventions, from the ledger) reaches
        # the budget. Overshoot is at most ~parallel_slots candidates' cost
        # (exactly one at parallel_slots=1) — see the slot-machine admission
        # note below.
        prior_total = journal.total_cost(cfg["results_dir"])
        budget_hit = False
        iters_run = 0  # actual candidates attempted (may be < window_size on budget break)
        # Repair-mode gate — ON when the PRIOR window's errored_fraction (which
        # EXCLUDES tombstoned programs, so the latch can RELEASE once dead programs are
        # removed) is >= the trigger. Only the FIRST slot of the window repairs.
        _prev_win = journal.read_windows(cfg["results_dir"], last_n=1)
        _errored_frac = float((_prev_win[-1].get("errored_fraction", 0.0) if _prev_win else 0.0) or 0.0)
        repair_on = _errored_frac >= float(evo.get("repair_trigger_fraction", 0.20))
        # Mark the window LIVE for the duration of the sqlite/bandit-writing candidate loop,
        # so strategy_store.snapshot_state can DETECT (and flag) a framework snapshot taken while
        # a window subprocess is mutating the archive/selector — which could capture a half-written
        # programs.sqlite / bandit_state.pkl as a "restore point". Best-effort; removed in finally
        # so a crash never leaks a stale sentinel.
        _wact = os.path.join(cfg["results_dir"], ".window_active")
        try:
            with open(_wact, "w", encoding="utf-8") as _wf:
                _wf.write(str(os.getpid()))
        except Exception:
            pass
        # SLOT STATE MACHINE. Up to evo.parallel_slots candidates in flight (default 1
        # = the sequential reference order — the plain loop below runs the IDENTICAL
        # task body on the main thread). All shared state (counters, archive, bandit,
        # journal) mutates only under ONE mutex, released around the long blocking
        # stages (_stage_call: LLM / embed / eval); evo.parallel_eval_slots bounds
        # concurrent evals; a repair slot runs SOLO; every in-flight slot DRAINS
        # before the window boundary; `.stop` stops ADMITTING and drains (in-flight
        # Azure calls are never killed). Admission re-checks the budget with an
        # in-flight estimate (in-flight count x the worst committed slot cost so far
        # — approximate under concurrency, exact at parallel_slots=1), so worst-case
        # overshoot stays ~parallel_slots candidates. Lifecycle is journaled to
        # journal/slots.jsonl (landing order = file order).
        _par = max(1, int(evo.get("parallel_slots", 1) or 1))
        _epar = max(1, min(int(evo.get("parallel_eval_slots", 1) or 1), _par))
        _locks = ({"mutex": threading.Lock(), "eval": threading.Semaphore(_epar)}
                  if _par > 1 else None)
        # REFCOUNT map {parent_id: n in-flight slots deriving from it} — a plain
        # set cannot represent two slots sharing one parent (every sibling pair
        # does), where the first slot to land would strip the other's protection.
        _inflight_parents: Dict[str, int] = {}
        _wstate: Dict[str, Any] = {"budget_hit": False, "inflight": 0,
                                   "max_slot_cost": 0.0, "iters_run": 0,
                                   "crashed": False}
        # SIBLING pairing (evo.sibling_samples, default 1 = off, cap 2): slots pair
        # (lead, sibling); the sibling reproduces the lead's prepare — identical
        # prompt → its call pays ~cached input — and its child flows the NORMAL
        # novelty/archive/bandit path (keep-all, never best-of-K). A repair slot
        # never pairs.
        _sibs = max(1, min(int(evo.get("sibling_samples", 1) or 1), 2))
        _sib_leader: Dict[int, int] = {}
        if _sibs > 1:
            _i = 1 if repair_on else 0
            while _i + 1 < window_size:
                _sib_leader[_i + 1] = _i
                _i += 2
        _preps: Dict[int, Dict[str, Any]] = {
            lead: {"event": threading.Event(), "payload": None}
            for lead in set(_sib_leader.values())
        }

        def _slot_event(event: str, i: int, extra: Optional[Dict[str, Any]] = None) -> None:
            try:
                journal.log_slot_event(cfg["results_dir"], {
                    "window_index": widx, "slot": i, "generation": next_gen + i,
                    "event": event, "inflight": _wstate["inflight"], **(extra or {}),
                })
            except Exception:
                pass

        def _slot_task(i: int) -> None:
            # The whole task body runs with the mutex held (when parallel); the
            # long stages inside _run_one_candidate release it via _stage_call.
            _lead_prep = _preps.get(i)          # set when THIS slot leads a sibling
            _my_leader = _sib_leader.get(i)     # set when THIS slot IS a sibling
            if _locks:
                _locks["mutex"].acquire()
            try:
                # ---- admission (mutex held) ----
                if _coop_stop["hit"] or _wstate["crashed"]:
                    return
                _est = _wstate["inflight"] * _wstate["max_slot_cost"]
                if budget is not None and (
                    prior_total + counters["cost"] + _est
                ) >= float(budget):
                    _wstate["budget_hit"] = True
                if _wstate["budget_hit"]:
                    return
                counters["iter_index"] = i
                _wstate["inflight"] += 1
                _cost_before = counters["cost"]
                _landed = False
                _slot_event("admitted", i)
                _slot_ctx = {"inflight_parents": _inflight_parents,
                             "parent_id": None, "slot_index": i}
                if _lead_prep is not None:
                    def _publish(payload, _p=_lead_prep):
                        _p["payload"] = payload
                        _p["event"].set()
                    _slot_ctx["publish_prepare"] = _publish
                if _my_leader is not None:
                    # Wait for the leader's prepare OUTSIDE the mutex (prepare is
                    # local work, normally sub-second; 60s cap). A skipped/crashed
                    # leader publishes None / times out → run as a fresh slot.
                    _p = _preps[_my_leader]
                    _stage_call(_locks, None, _p["event"].wait, 60)
                    if _p.get("payload"):
                        _slot_ctx["sibling_prepare"] = _p["payload"]
                try:
                    _run_one_candidate(cfg, next_gen + i, counters,
                                       repair=(repair_on and i == 0),
                                       locks=_locks, slot_ctx=_slot_ctx)
                    _wstate["iters_run"] += 1
                    _landed = True
                except BaseException:
                    # First crash aborts the window like the sequential driver did:
                    # queued slots stop admitting, in-flight slots drain.
                    _wstate["crashed"] = True
                    raise
                finally:
                    _wstate["inflight"] -= 1
                    _pid = _slot_ctx.get("parent_id")
                    if _pid:  # decrement the refcounted pin; drop the key at zero
                        _n = _inflight_parents.get(_pid, 0) - 1
                        if _n > 0:
                            _inflight_parents[_pid] = _n
                        else:
                            _inflight_parents.pop(_pid, None)
                    _wstate["max_slot_cost"] = max(
                        _wstate["max_slot_cost"], counters["cost"] - _cost_before)
                    # Cooperative stop BETWEEN candidates (also honored mid-warmup via
                    # stop_dir): each slot commits atomically before this point, so
                    # stopping admissions here never half-writes the archive. The
                    # cluster loop reads _coop_stop and returns "cooperative_stop".
                    if _stop_requested(stop_dir, run_id):
                        _coop_stop["hit"] = True
                    # 'committed' ONLY when the commit phase actually landed; a slot
                    # that raised is journaled as 'crashed'.
                    _slot_event("committed" if _landed else "crashed", i, {
                        "slot_cost": round(counters["cost"] - _cost_before, 6)})
            finally:
                # A lead slot that exits by ANY path (admission skip, crash, or a
                # prepare that never reached the publish point) must unblock its
                # sibling — an unset event with payload None = "run fresh".
                if _lead_prep is not None and not _lead_prep["event"].is_set():
                    _lead_prep["event"].set()
                if _locks:
                    _locks["mutex"].release()

        try:
            if _par <= 1:
                for i in range(window_size):
                    _slot_task(i)
                    if _coop_stop["hit"] or _wstate["budget_hit"]:
                        break
            else:
                with ThreadPoolExecutor(
                    max_workers=_par, thread_name_prefix="slot"
                ) as _pool:
                    _start = 0
                    if repair_on and window_size > 0:
                        # Repair slot runs SOLO (its repair_record read-modify-write
                        # and deterministic errored-parent pick must not race).
                        _pool.submit(_slot_task, 0).result()
                        _start = 1
                    # map() submits all remaining slots; workers pick them up in
                    # order and the admission flags (stop/budget/crash) are
                    # monotonic, so executed slots form a prefix under normal
                    # scheduling. (The mutex is not fairness-ordered, so extreme
                    # scheduler skew exactly at a flag flip can skip a lower slot
                    # while a higher one already admitted runs — accounting stays
                    # correct because gens are per-slot; the hole is only cosmetic.)
                    # Iterating the results re-raises the first slot exception
                    # (window aborts, in-flight drained by the executor's shutdown).
                    list(_pool.map(_slot_task, range(_start, window_size)))
        finally:
            budget_hit = _wstate["budget_hit"]
            iters_run = _wstate["iters_run"]
            try:
                os.remove(_wact)
            except OSError:
                pass

        # Opt-in: drive island spawn/migrate via the MUTABLE island_policy
        # DECISION at the window boundary (not just the db_config add()-time
        # thresholds). Default off => today's behavior. Use with the db_config
        # auto-triggers off (enable_dynamic_islands=false + migration_rate=0, the
        # defaults) to avoid double-execution. Never let it break the window.
        # Carry the spawn-once marker across windows so island_policy suppresses a repeat
        # spawn while an island stays stagnant (default None on the first window / a fresh run).
        _spawn_marker = _prev_win[-1].get("last_policy_spawn_generation") if _prev_win else None
        if evo.get("island_policy_driven"):
            import sys as _sys
            try:
                _ip_payload = {
                    "db_path": db_path, "db_config": db_config,
                    "embedding_model": embedding_model,
                    "current_generation": (next_gen + iters_run - 1) if iters_run else next_gen,
                    # Pass the durable marker + the configured cooldown so the policy is the
                    # primary spawn-once guard; capture the (advanced/carried) value back below.
                    "last_policy_spawn_generation": _spawn_marker,
                    "policy_spawn_cooldown": evo.get("policy_spawn_cooldown", 0),
                    "apply": True,
                }
                # Forward the policy's OWN gates from evo.* when set (SKILL.md teaches them
                # as config levers). Unset keys are NOT forwarded, so island_policy keeps its
                # db_config-derived defaults for them (payload-first, back-compat).
                for _pk in ("policy_spawn_enabled", "policy_spawn_stagnation",
                            "policy_migrate_enabled", "policy_migrate_interval"):
                    if evo.get(_pk) is not None:
                        _ip_payload[_pk] = evo[_pk]
                _ip_res = island_policy_script.main(_ip_payload)
                _spawn_marker = (_ip_res or {}).get("last_policy_spawn_generation", _spawn_marker)
                # SURFACE what actually ran, so the agent can
                # tell "policy decided nothing" from "policy crashed". stderr (not log_step,
                # which only writes the trace stream; not _trace, out of scope here).
                print(f"[island_policy] window {widx}: actions={(_ip_res or {}).get('actions')} "
                      f"executed={(_ip_res or {}).get('executed')} "
                      f"spawn_marker={_spawn_marker}", file=_sys.stderr)
            except Exception:
                # Log the traceback instead of a silent pass; never break the window.
                import traceback as _tb

                print(f"[island_policy] FAILED (window {widx}):\n{_tb.format_exc()}", file=_sys.stderr)

        # Read the REAL bandit posterior (+ per-arm tallies) for diagnostics,
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
                # Report the ACTUAL number of candidates attempted, not the
                # constant window_size (they differ on a budget/early break).
                "window_index": widx, "iters_completed": iters_run,
                "best_score_start": best_start, "window_size": window_size,
                "strategy_fingerprint": strategy_fingerprint,
                "tau": evo.get("tau"),  # default None so the detector's 1e-3 abs_floor fallback engages when unset
                "stagnation_abs_floor": evo.get("stagnation_abs_floor"),
                "stagnation_rel_frac": evo.get("stagnation_rel_frac"),
                "prior_low_streak": prior_streak,
                "consecutive_required": evo.get("consecutive_required", 2),
                "trigger_metric": evo.get("trigger_metric", "hybrid"),
                "novelty_accepts": counters["novelty_accepts"],
                "novelty_rejects": counters["novelty_rejects"],
                "novelty_rejected_cost": counters["rejected_cost"],
                # Novelty observability — kept-better evictions, idle-gate accepts,
                # failed evictions, and blind-embed slots, so a near-dup flood / a disabled gate
                # is visible in the control-return diagnostics instead of by db spelunking.
                "novelty_kept_better": counters.get("novelty_kept_better", 0),
                "novelty_idle_count": counters.get("novelty_idle_count", 0),
                "novelty_evict_fail_count": counters.get("novelty_evict_fail_count", 0),
                "embed_failures": counters.get("embed_failures", 0),
                "novelty_sim_histogram": counters.get("novelty_sim_histogram", {}),
                "eval_failures": counters["eval_failures"],
                "eval_total": counters["eval_total"],
                "fix_count": counters["fix_count"],
                "fix_success": counters.get("fix_success", 0),
                "needs_fix_count": counters.get("needs_fix_count", 0),
                "llm_bandit_weights": bandit_weights,
                "llm_bandit_counts": bandit_counts,
                # THIS window's per-arm submitted counts (the real source for the bandit-collapse
                # rollback check; the cumulative llm_bandit_counts above stays for steady-state
                # model_collapse).
                "llm_bandit_window_counts": {
                    a: {"submitted": c} for a, c in counters.get("arm_submitted", {}).items()
                },
                "exhausted_retry_slots": counters.get("exhausted_retry_slots", []),
                "exhausted_retry_count": counters.get("exhausted_retry_count", 0),
                "apply_exhausted": counters.get("apply_exhausted", 0),
                "timeout_count": counters.get("timeout_count", 0),
                "wrong_answer_count": counters.get("wrong_answer_count", 0),
                "repair_fail_count": counters.get("repair_fail_count", 0),
                "repair_tombstoned_count": counters.get("repair_tombstoned_count", 0),
                # Sensor knobs threaded for diagnostics (collapse + repair trigger):
                "model_collapse_frac": evo.get("model_collapse_frac", 0.85),
                "model_collapse_min_pulls": evo.get("model_collapse_min_pulls", 8),
                "repair_trigger_fraction": evo.get("repair_trigger_fraction", 0.20),
            }
        )
        diag["window_cost"] = counters["cost"]
        diag["budget_hit"] = budget_hit
        # Code-verified config-flip artifact for the termination check (journal.
        # _config_flip_between): a lever flip shows as a hash change across windows.
        diag["config_lever_hash"] = _lever_hash
        # Persist the spawn-once marker into the durable window record so the NEXT window's
        # island_policy call sees "already spawned this episode" and suppresses a repeat spawn.
        diag["last_policy_spawn_generation"] = _spawn_marker
        journal.append_window(cfg["results_dir"], diag)  # folds window_cost into the ledger

        # AUTOMATIC per-window meta round — run by the HARNESS, not the agent. One
        # call → a failure caution + one distinct direction LIST per live island,
        # auto-recorded as per-island briefs so islands diverge BY DEFAULT. The
        # call self-logs + folds its own cost into the ledger (do NOT append_intervention
        # it). Wrapped so a meta/parse/brief bug can NEVER crash a window. auto_meta:false
        # skips the WHOLE round (caution + per-island briefs). It runs AFTER append_window
        # so diag's island_health is final; total_cost is refreshed below to include it.
        # Skipped on a cooperative stop: the agent asked for control back, so don't spend
        # minutes + dollars on a meta call it didn't order — the next window's meta
        # re-derives the briefs.
        if evo.get("auto_meta", True) and not _coop_stop["hit"]:
            try:
                _mock = cfg.get("mock", {}) or {}
                _meta_gen = (next_gen + iters_run - 1) if iters_run else next_gen
                # Give meta the current best program WITH code (capped) so its
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
                    "islands": [{"id": h.get("id"), "best": h.get("best"), "count": h.get("count")}
                                for h in diag.get("island_health", []) or []],
                    "num_islands": len(diag.get("island_health", []) or []),
                    "best_program": _meta_best,
                    "meta_code_preview_chars": evo.get("meta_code_preview_chars", 1200),
                    # Depth knob (orchestrator lever, evo.meta_n_recent in the config):
                    # how many recent programs meta renders in depth.
                    "meta_n_recent": evo.get("meta_n_recent", 32),
                    # Deterministic failure-histogram recency: ~the last two windows.
                    "failure_hist_recent_gens": 2 * int(evo.get("window_size", 10) or 10),
                    # Rolling scratchpad: hand meta its own previous blob to UPDATE
                    # (cross-window memory; "" on the first round of a fresh run).
                    "global_insights_prev": evo.get("meta_global_insights") or "",
                    "window_index": widx,
                }
                if _mock.get("enabled"):  # offline runs/tests: no Azure call
                    _meta_payload["mock"] = True
                    _meta_payload["mock_text"] = _mock.get("meta_mock_text", "")
                _meta = meta_summarize_script.main(_meta_payload)
                # Track island coverage so the returned diag's meta_health can report a
                # degraded/skipped/crashed round + which live islands got a brief, were OMITTED,
                # or were HALLUCINATED (a non-existent island_idx — don't write a phantom brief).
                _live_ids = {h.get("id") for h in (diag.get("island_health") or [])}
                _written: set = set()
                _hallucinated: List[int] = []
                if not (_meta.get("skipped") or _meta.get("degraded")):
                    # Write the failure caution into the LIVE evo dict (don't clobber a
                    # non-empty prior with None); it rides into every gen as failure_note.
                    if _meta.get("failure_note"):
                        evo["meta_failure_note"] = _meta["failure_note"]
                    # The updated scratchpad REPLACES the previous one wholesale (the
                    # prompt's merge/drop/add rules make it self-pruning); an empty
                    # return keeps the prior blob — accidental wipe loses the only
                    # cross-window memory, while a stale line just gets dropped by a
                    # later round.
                    if _meta.get("global_insights"):
                        evo["meta_global_insights"] = _meta["global_insights"]
                    # Auto-record ONE brief per live island so islands diverge. Prefer the RICH
                    # per-island output: persist directions + assigned program ids into
                    # structured_json so the SAMPLER can be direction-oriented; fall back to the
                    # flat island_directions (content only) when only the legacy schema is present.
                    import json as _json
                    _rich = _meta.get("islands") or []
                    _entries = _rich if _rich else (_meta.get("island_directions", []) or [])
                    for _isl in _entries:
                        try:
                            _iid = int(_isl["island_idx"])
                            if _live_ids and _iid not in _live_ids:
                                _hallucinated.append(_iid)  # skip phantom-island briefs
                                continue
                            if _rich:
                                _dirs = _isl.get("directions") or []
                                # headline = highest-WEIGHT direction (matches the derived
                                # island_directions headline), not directions[0].
                                _headline = (max(_dirs, key=lambda d: d.get("weight", 0.0))["text"]
                                             if _dirs else "")
                                _extra = {"structured_json": _json.dumps({"directions": _dirs})}
                            else:
                                _headline = _isl.get("text", "")
                                _extra = {}
                            island_brief_script.main({
                                "db_path": db_path, "db_config": db_config,
                                "embedding_model": embedding_model,
                                "island_idx": _iid, "generation": _meta_gen,
                                "content": _headline, "stage": "auto_meta", "cost": 0.0, **_extra,
                            })
                            _written.add(_iid)
                        except Exception:
                            pass  # one bad island entry must not abort the rest
                # Attach meta health to the RETURNED diag (control-return surface).
                diag["meta_health"] = {
                    "status": ("skipped" if _meta.get("skipped")
                               else "degraded" if _meta.get("degraded") else "ok"),
                    "n_global_directions": len(_meta.get("directions") or []),
                    "islands_written": sorted(_written),
                    "islands_missing": sorted(_live_ids - _written) if _live_ids else [],
                    "islands_hallucinated": sorted(_hallucinated),
                    "error": _meta.get("error"),
                }
            except Exception as _exc:
                diag["meta_health"] = {"status": "crashed", "error": repr(_exc)}

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
        # TERMINATION: before launching another cluster, check the deterministic
        # stop signal — N consecutive control-return intervals that were each STAGNANT
        # and each had an orchestrator INTERVENTION (a rewrite, a discovery round, or a
        # config-lever flip) yet still couldn't escape stagnation. VERIFIED FROM CODE
        # ARTIFACTS (journal.termination_streak: foundation-recomputed stagnation over
        # windows.jsonl + attributed strategy deploys / usable discovery stubs /
        # config_lever_hash flips) — the agent's control_return rows delimit the
        # intervals and carry the work score, but their flags are claims; code truth
        # drives this number. Harness-decided + auto-finalized (parity with
        # budget_exhausted) so two agents can't disagree. Stagnation alone never
        # terminates — only stagnation the interventions could not break.
        _term_n = int(cadence.get("termination_streak", 5) or 5)
        _term_streak = journal.termination_streak(cfg["results_dir"])
        if _term_n > 0 and _term_streak >= _term_n:
            _last = dict((journal.read_windows(cfg["results_dir"]) or [{}])[-1])
            _last["return_reason"] = "stagnation_intervention_exhausted"
            _last["termination_streak"] = _term_streak
            _last["ok"] = True
            _finalize_terminal(cfg["results_dir"], "stagnation_intervention_exhausted", _last)
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
            # Cooperative graceful stop: the agent asked this run to stop (e.g. for a
            # framework-rewrite measure window). Honored between candidates (above) or at the
            # window boundary — never a process kill.
            if _coop_stop["hit"] or _stop_requested(stop_dir, run_id):
                last_diag["return_reason"] = "cooperative_stop"
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
            if _coop_stop["hit"] or _stop_requested(stop_dir, run_id):
                last_diag["return_reason"] = "cooperative_stop"
                break
        last_diag.setdefault("return_reason", "windows_done")

    # Finalize the run ledger on the budget-exhausted TERMINAL return (so the status
    # reflects the stop). budget_exhausted and stagnation_intervention_exhausted are
    # HARNESS-finalized (here and at the top-of-cluster check above); the agent
    # finalizes only stopped_by_user, via the journal `finalize_run` CLI view WITH
    # evidence={"user_quote": <the literal user turn>}. A non-terminal cadence/taper
    # return does NOT finalize.
    if last_diag.get("return_reason") == "budget_exhausted":
        _finalize_terminal(cfg["results_dir"], "budget_exhausted", last_diag)

    # Surface the termination streak on every return so the agent sees how close the run is
    # to the deterministic stop (N consecutive VERIFIED stagnant+intervened intervals),
    # plus how many trailing rows diverge from code truth (inspect via the journal
    # `termination_report` view) and how many user steers are queued (pending_steering —
    # consume one at this control-return if you would not otherwise fire discovery).
    try:
        last_diag["termination_streak"] = journal.termination_streak(cfg["results_dir"])
        _term_report = journal.termination_report(cfg["results_dir"])
        last_diag["termination_divergence"] = sum(
            1 for iv in _term_report[-5:] if iv.get("diverged"))
        last_diag["pending_steering"] = len(journal.pending_steering(cfg["results_dir"]))
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


# ---------------------------------------------------------------------------
# Per-run isolation (cross-worktree / cross-session). A run_window owns its
# results_dir for its whole lifetime via an exclusive OS advisory lock on
# <results_dir>/.run.lock. Holding the lock IS the run's identity, liveness, and
# co-tenancy: the kernel releases it on ANY exit (clean, crash, SIGKILL, sandbox
# reclaim), so there is never a stale lock to clean up. A second run_window on the
# same results_dir fails to acquire it and refuses to start instead of silently
# commingling programs.sqlite / bandit_state.pkl / journal. This is what lets
# concurrent worktree runs stay independent WITHOUT anyone touching the global OS
# PID space — no Get-Process / Stop-Process by pid, so PID reuse can never make one
# session act on another session's run_window. To stop a run, write the cooperative
# <results_dir>/.stop sentinel (below); to recover a dead run, relaunch --resume
# (the lock turns a wrong "it's dead" guess into a harmless refuse-to-start, not a
# double-writer).
# ---------------------------------------------------------------------------
class _RunLock:
    """Holds the open lock fd for the process lifetime. MUST stay referenced — if
    it is garbage-collected the fd closes and the OS drops the lock."""

    def __init__(self, fd: int) -> None:
        self._fd: Optional[int] = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    os.lseek(self._fd, 0, 0)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __del__(self) -> None:  # best-effort release if release() was never called
        self.release()


def acquire_run_lock(results_dir: str, run_id: Optional[str] = None) -> _RunLock:
    """Take the exclusive per-run lock on <results_dir>/.run.lock, or raise
    SystemExit if another LIVE run_window already owns this results_dir. Returns a
    guard whose fd must stay alive for the whole run (store it at process-lifetime
    scope — a dropped guard releases the lock). Cross-platform: fcntl.flock on
    POSIX, msvcrt.locking on Windows; both auto-released by the kernel on death, so
    a crash never leaves the directory falsely 'owned'. The fd is opened
    non-inheritable so the eval subprocess cannot keep the lock alive past us."""
    os.makedirs(results_dir, exist_ok=True)
    lock_path = os.path.join(results_dir, ".run.lock")
    owner_path = os.path.join(results_dir, ".run_owner.json")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0), 0o644)
    try:
        if sys.platform == "win32":
            import msvcrt
            try:
                os.set_inheritable(fd, False)  # don't leak the lock into the eval child
            except (OSError, AttributeError, ValueError):
                pass
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        owner = ""
        try:
            with open(owner_path, encoding="utf-8") as f:
                owner = " owned by " + f.read().strip()
        except OSError:
            pass
        raise SystemExit(
            f"[run-lock] refusing to start: {results_dir} is already locked by a live "
            f"run_window{owner}. Let it finish, or relaunch with --resume only after it "
            f"exits — never start a second run on one results_dir."
        )
    # Human-readable forensics only (NOT a gate): lets the refuse message name the holder.
    try:
        with open(owner_path, "w", encoding="utf-8") as f:
            f.write(_common.dumps({"pid": os.getpid(), "run_id": run_id, "started_at": time.time()}))
    except OSError:
        pass
    return _RunLock(fd)


# Cooperative graceful-stop sentinel. The agent asks a LIVE run to stop by writing
# <results_dir>/.stop (optionally {"target_run_id": ...}); it never kills a process.
# run_window honors it BETWEEN CANDIDATES (and at window boundaries) and exits 0 —
# each candidate commits atomically, so no half-written sqlite. A stale .stop from a
# prior process generation is cleared at startup, so it can never stop-loop a --resume.
def _read_stop(results_dir: str) -> Optional[Dict[str, Any]]:
    p = os.path.join(results_dir, ".stop")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.loads(f.read() or "{}")
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}  # present-but-malformed still means "stop requested"


def _clear_stop(results_dir: str) -> None:
    try:
        os.remove(os.path.join(results_dir, ".stop"))
    except OSError:
        pass


def _stop_requested(results_dir: str, run_id: Optional[str]) -> bool:
    """True (and consumes the sentinel) iff a .stop targets THIS run."""
    st = _read_stop(results_dir)
    if st is None:
        return False
    tgt = st.get("target_run_id")
    if tgt is None or tgt == run_id:
        _clear_stop(results_dir)
        return True
    return False


def _config_lever_hash(cfg: Dict[str, Any]) -> str:
    """Content hash over the run config's LEVER keys — the code-verified "a config
    lever was flipped between clusters" artifact for the termination check. Drops the
    volatile / CLI-injected keys (window_state bookkeeping; the --windows/--iters/
    --trace-steps overrides; stop_dir; the warmup-forced cadence.mode and
    task.require_sys_msg) so a --resume, a measure window, or warmup plumbing never
    reads as a lever flip. Every other key (evo.*, db_config.*, cadence knobs,
    budget_usd, models, task levers) is lever material — including keys added later,
    with no schema to maintain. 16 hex chars, parity with strategy_store.file_hash."""
    import hashlib

    canon = {k: v for k, v in cfg.items()
             if k not in ("window_state", "stop_dir", "trace_steps", "windows", "iters")}
    cad = dict(canon.get("cadence") or {})
    cad.pop("mode", None)  # --warmup/--windows force it; not a lever
    canon["cadence"] = cad
    tsk = dict(canon.get("task") or {})
    tsk.pop("require_sys_msg", None)  # --warmup forces it; not a lever
    canon["task"] = tsk
    blob = json.dumps(canon, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _finalize_terminal(results_dir: str, status: str, diag: Dict[str, Any]) -> None:
    """Harness-side terminal finalize — LOUD, never silent (the old bare
    try/except:pass could leave status="running" while returning a terminal reason).
    Same-status re-finalize is a benign no-op (finalize_run is idempotent); an
    EXISTING different terminal status is respected and surfaced via
    diag["finalized_status"], never overwritten; a real write failure is retried once
    and then surfaced via diag["finalize_error"] so the agent can run the journal
    ``finalize_run`` CLI view as the recovery path (the view re-checks the
    precondition). On success, also auto-draft RUN_SUMMARY.md so a terminated run is
    never summary-less — the agent still enriches the draft and runs archive_run."""
    try:
        current = (journal.read_run(results_dir) or {}).get("status")
    except Exception:
        current = None
    if current in getattr(journal, "TERMINAL_STATUSES", set()) and current != status:
        sys.stderr.write(
            f"[terminate] run already finalized as {current!r}; keeping it "
            f"(wanted {status!r})\n")
        diag["finalized_status"] = current
        return
    last_exc: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            journal.finalize_run(results_dir, status)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            import traceback

            sys.stderr.write(
                f"[terminate] finalize_run({status!r}) FAILED (attempt {attempt}/2): "
                f"{exc}\n{traceback.format_exc()}\n")
            time.sleep(0.5)
    if last_exc is not None:
        diag["finalize_error"] = repr(last_exc)
        return
    diag["finalized"] = True
    diag["finalized_status"] = status
    try:
        draft = journal.write_run_summary_draft(results_dir)
        if draft:
            diag["summary_draft"] = draft
    except Exception as exc:
        sys.stderr.write(f"[terminate] RUN_SUMMARY.md draft failed (non-fatal): {exc}\n")


def _absolutize_paths(cfg: Dict[str, Any], config_path: str) -> None:
    """Anchor a relative results_dir / db_path to the CONFIG-FILE directory (not the launch CWD),
    in place, so a relative "results" resolves to <config dir>/results deterministically and two
    worktrees can never collide on a shared launch CWD. Absolute paths in the config are kept."""
    cfg_dir = os.path.dirname(os.path.abspath(config_path))
    for key in ("results_dir", "db_path"):
        val = cfg.get(key)
        if val and not os.path.isabs(val):
            cfg[key] = os.path.normpath(os.path.join(cfg_dir, val))


def _run_meta(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The run.json meta passed to journal.init_run. Factored so the accept-warmup fold can
    PRE-CREATE run.json with the SAME shape main() would — main()'s init_run is idempotent, so a
    pre-created run.json makes it a clean no-op with no config_digest drift."""
    evo = cfg.get("evo", {}) or {}
    db_config = cfg.get("db_config", {}) or {}
    cadence = cfg.get("cadence", {}) or {}
    window_size = int(cfg.get("iters") or evo.get("window_size", 10))
    # Seed forensics (best-effort, never raises — accept_warmup folds with a bare
    # task:{}): count + short content hashes of the seed program(s).
    seed_paths = _init_paths(cfg, strict=False)
    seed_hashes: List[Optional[str]] = []
    for _p in seed_paths:
        try:
            import hashlib as _hashlib

            with open(_p, "rb") as _f:
                seed_hashes.append(_hashlib.sha256(_f.read()).hexdigest()[:12])
        except Exception:
            seed_hashes.append(None)
    return {
        "run_id": cfg.get("run_id"),
        "goal": cfg.get("task", {}).get("task_sys_msg"),
        "task": cfg.get("task", {}).get("eval_program_path"),
        "budget_usd": cfg.get("budget_usd"),
        "config_digest": {
            "num_islands": db_config.get("num_islands"),
            "window_size": window_size,
            "llm_models": evo.get("llm_models"),
            # Record the REAL stagnation knobs actually in force (not just the
            # deprecated `tau` alias) so the journal shows the bar that was used.
            # The verified-termination floor (journal.foundation_stagnation_flags)
            # reads THESE boot-frozen copies — a mid-run knob flip moves the cadence
            # bar but never the termination floor.
            "stagnation_abs_floor": evo.get("stagnation_abs_floor"),
            "stagnation_rel_frac": evo.get("stagnation_rel_frac"),
            "consecutive_required": evo.get("consecutive_required"),
            # Boot-frozen N for criterion 2 — the finalize CLI view's precondition
            # re-check reads this, not the live config.
            "termination_streak": int(cadence.get("termination_streak", 5) or 5),
            "num_seeds": len(seed_paths) or 1,
            "seed_sha256": seed_hashes,
        },
    }


def accept_warmup(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Keep-approved fold-back: promote the orchestrator-APPROVED final warmup into the real
    run. Backs the warmup archive up into the (still-absent) real db via the sqlite backup API
    so the run CONTINUES from the warmed, reviewed population, and folds the warmup's spend into
    the real ledger as a DURABLE intervention (so the budget cap counts the tokens already
    burned). Failed/abandoned warmups are never accepted — the next ``--warmup`` simply
    auto-resets them away.

    Refuses (no-op, accepted:False) if there is no populated warmup archive, if the warmup
    archive has no LIVE rows, or if the real archive ALREADY exists (the real run has started —
    folding would clobber it). On success the warmup workspace is cleaned up so the kept
    population now lives only in the real archive."""
    results_dir = cfg["results_dir"]
    warm = os.path.join(results_dir, "warmup")
    wdb = os.path.join(warm, "programs.sqlite")
    rdb = cfg.get("db_path") or os.path.join(results_dir, "programs.sqlite")
    if not os.path.exists(wdb):
        return {"ok": False, "accepted": False, "reason": "no warmup archive to accept"}
    if os.path.exists(rdb):
        return {"ok": False, "accepted": False,
                "reason": f"real archive already exists at {rdb}; refusing to clobber a "
                          f"started run (accept-warmup must run BEFORE the first real window)"}
    # Require LIVE rows — an all-tombstoned warmup (repair struck out the whole population) is
    # not worth keeping; let the orchestrator rerun --warmup instead.
    live = int(
        archive_query.main(
            {
                "db_path": wdb,
                "db_config": cfg["db_config"],
                "embedding_model": cfg.get("evo", {}).get(
                    "embedding_model", "azure-text-embedding-3-small"
                ),
                "query_type": "count",
            }
        )["result"].get("live", 0)
        or 0
    )
    if live <= 0:
        return {"ok": False, "accepted": False,
                "reason": "warmup archive has no live rows — nothing worth keeping"}
    import sqlite3

    os.makedirs(os.path.dirname(rdb) or ".", exist_ok=True)
    # Fold via the sqlite backup API, NOT a raw file copy: the live-row count above reads
    # THROUGH the WAL, so an unclean warmup shutdown's -wal tail must land in the real db
    # too — copy2 of the main db file alone would silently drop that tail.
    _src = sqlite3.connect(wdb)
    try:
        _dst = sqlite3.connect(rdb)
        try:
            _src.backup(_dst)
        finally:
            _dst.close()
    finally:
        _src.close()
    # Fold the warmup spend DURABLY: pre-create run.json on a genuinely fresh boot (no journal
    # streams yet, so add_cost takes the plain path — NOT the reconstruct path, which would
    # double-count the just-appended intervention), then record the spend as an intervention so
    # a later corrupt/deleted-run.json recompute recovers it from interventions.jsonl.
    wcost = float(journal.total_cost(warm) or 0.0)
    journal.init_run(results_dir, _run_meta(cfg))
    if wcost:
        journal.append_intervention(
            results_dir,
            {
                "type": "warmup_accepted",
                "cost": wcost,
                "reason": "folded the approved final warmup into the real run",
                "outcome": f"copied {live} live program(s) + ${wcost:.4f} prior spend",
            },
        )
    cleanup_warmup(results_dir)
    return {"ok": True, "accepted": True, "live_programs": live,
            "folded_cost_usd": wcost, "db": rdb}


def cleanup_warmup(results_dir: str) -> bool:
    """Delete the throwaway <results_dir>/warmup workspace so warmup artifacts never pollute the
    real run. Returns True ONLY if the dir is actually gone afterward: rmtree(ignore_errors)
    can SILENTLY fail on a Windows lock (e.g. an open sqlite handle), and reporting 'cleaned'
    while the dir survived would let a stale workspace persist into the next warmup. A missing
    dir returns False (nothing to remove)."""
    import shutil

    warm = os.path.join(results_dir, "warmup")
    if not os.path.isdir(warm):
        return False
    shutil.rmtree(warm, ignore_errors=True)
    if os.path.isdir(warm):
        sys.stderr.write(
            f"[warmup] WARNING: could not fully remove {warm} (locked? close any open sqlite "
            f"handle and retry) — it may persist into the next warmup\n"
        )
        return False
    return True


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
             "journal's last window instead of the hand-maintained config — "
             "removes the cross-invocation bookkeeping footgun",
    )
    ap.add_argument(
        "--warmup", action="store_true",
        help="WARMUP: run ONE window in a THROWAWAY workspace (<results_dir>/warmup — its "
             "own db + journal) with per-step tracing ON, so you can oversee one window "
             "step by step (read its journal/steps.jsonl), stop-correct-restart until it is "
             "meaningful, then keep it — WITHOUT polluting the real run. Validates the "
             "mechanism on a fresh archive. Keep it with --accept-warmup.",
    )
    ap.add_argument(
        "--trace-steps", action="store_true",
        help="turn per-step tracing ON for this invocation WITHOUT the warmup redirect — "
             "for the framework-audit measuring window (run with --windows 1) so its "
             "journal/steps.jsonl exists for you to read.",
    )
    ap.add_argument(
        "--accept-warmup", action="store_true",
        help="KEEP the approved warmup — fold its archive into the real db (the run then "
             "CONTINUES from the warmed population) and its spend into the real ledger, then "
             "clean up, and exit. Run this BEFORE the first real window; it refuses if the real "
             "archive already exists. A failed warmup is NOT accepted (just rerun --warmup, "
             "which auto-resets).",
    )
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    # Anchor a relative results_dir / db_path to the CONFIG-FILE directory, not the launch CWD,
    # so two worktrees can never collide on a shared launch CWD (the load-bearing anchor for the
    # per-run lock below — distinct configs ⇒ distinct results_dir ⇒ distinct lock).
    _absolutize_paths(cfg, args.config)
    # The cooperative .stop sentinel lives in the run's results_dir. Capture it BEFORE the warmup
    # redirect below so a .stop the agent writes to the parent dir still stops a warmup.
    cfg["stop_dir"] = cfg["results_dir"]
    # ONE exclusive lock at <results_dir>/.run.lock guards EVERY _cli mode — taken on the
    # PARENT results_dir before any branch below, so: a second --warmup cannot wipe
    # a LIVE warmup's workspace (cleanup_warmup runs under the lock), --accept-warmup
    # cannot fold a live warmup or race a booting real run, and a warmup + a real run on
    # one results_dir cannot run concurrently. The warmup subdir carries no .run.lock of
    # its own. Held until this process exits (the OS releases it on any death);
    # kept in a local that outlives main() — dropping it would release the lock mid-run.
    _run_lock = acquire_run_lock(cfg["results_dir"], cfg.get("run_id"))  # noqa: F841
    if args.accept_warmup:
        res = accept_warmup(cfg)
        sys.stdout.write(_common.dumps(res))
        sys.stdout.flush()
        return
    _warmup_dir = None
    if args.warmup:
        # Run in a THROWAWAY workspace so the real archive/journal stay pristine; the agent
        # oversees this fresh-archive window. Trace ON; the boot sentinel guard is relaxed for
        # THIS invocation only — the real run keeps require_sys_msg=True.
        # AUTO-RESET the workspace at the START of every --warmup, so a rerun never
        # validates a fix against the PRIOR broken attempt's population / bandit / errored_fraction
        # (which could silently flip the rerun into repair mode). Each --warmup is a fresh archive.
        cleanup_warmup(cfg["results_dir"])
        _warmup_dir = os.path.join(cfg["results_dir"], "warmup")
        cfg["results_dir"] = _warmup_dir
        cfg["db_path"] = os.path.join(_warmup_dir, "programs.sqlite")
        cfg["trace_steps"] = True
        cfg.setdefault("task", {})["require_sys_msg"] = False
        cfg.setdefault("cadence", {})["mode"] = "bounded"
        if args.windows is None:
            cfg["windows"] = 1
        if args.iters is None:
            # Warmup runs a small CONFIGURED number of iterations (default 3), NOT 1 — a
            # single iteration can't surface the sampler-spread / bandit-collapse /
            # brief-differentiation signals warmup exists to observe. Override with --iters.
            cfg["iters"] = int((cfg.get("warmup") or {}).get("iters", 3))
    elif args.trace_steps:
        cfg["trace_steps"] = True
    if args.windows is not None:
        cfg["windows"] = args.windows
        # An explicit --windows means "run exactly N bounded windows". Force
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
        # not hand-edit window_index / prior_low_streak between calls.
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
            f"[warmup] read the per-step trace at {_warmup_dir}/journal/steps.jsonl; rerun "
            f"--warmup to restart (auto-resets), or --accept-warmup to keep it and start the "
            f"real run\n"
        )
    sys.stdout.write(_common.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    _cli()

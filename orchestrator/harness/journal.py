"""journal.py — the hierarchical, greppable run history.

The orchestrator's long-term memory at four granularities, all plain JSON/JSONL
so it can be read with grep/Read (no unpickling, no query layer):

  journal/run.json            run-level summary (overwritten each window): goal,
                              status, windows_completed, best_score, totals.
  journal/windows.jsonl       one line per window — the full diagnostics. The
                              J-trajectory and every per-window signal live here.
  journal/interventions.jsonl one line per orchestrator action (rewrite, deep
                              research, debug-agent, island action) + rationale +
                              outcome. The orchestrator appends to this.
  journal/islands/island_<i>.jsonl  per-island per-window best/diversity — the
                              "regional" view for spotting a collapsing island.
  journal/steps.jsonl         (OPTIONAL — written only when per-step tracing is on:
                              warmup, and the framework-audit measuring window) one
                              line per inner-loop decision (sampler / prompt summary
                              / llm output / eval / framework decision). Absent in a
                              normal run; cleaned up after warmup. Folds no cost.
  journal/novelty.jsonl       (per-candidate novelty-comparison records — one row per
                              evaluated correct candidate whose novelty gate ran; ids+numbers
                              only, the audit trail behind novelty_acceptance_rate). Folds no cost.

`strategy_history/` (separate) holds the per-strategy-version snapshots. Together
they let the orchestrator zoom from "how's the run overall" → "what did window 37
look like" → "every reward-related intervention" → "is island 2 dying."

run.json durability contract (so the hard budget cap can never be silently lost):
every run.json write is atomic (write a UNIQUE-named temp file, fsync, os.replace with a
Windows-PermissionError retry, then fsync the parent dir on POSIX — L68/L70), and a
missing-or-corrupt run.json is REPAIRED by recomputing total_cost from the durable
append-only streams (windows.jsonl window_cost + interventions.jsonl cost + calls.jsonl
cost). The repair fires BOTH on read (corrupt-in-place) AND at init_run when run.json is
ABSENT but the streams exist (deleted-then-restart — H6), so the cap can never restart
from $0. Append is torn-write-safe: a newline-less torn tail is isolated rather than
merged, and an unparseable line is skipped with a stderr warning, never silently dropped
(L72). The only spend not recoverable this way is a cost added directly via add_cost
outside any window/intervention/call (e.g. the one boot-time embedding) — a deliberately
accepted small loss. read_run returns {} only when run.json is genuinely absent AND no
journal streams exist.

MUTABILITY: harness plumbing. Not a strategy file; do not rewrite.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional


def journal_dir(results_dir: str) -> str:
    return os.path.join(results_dir, "journal")


def _calls_dir(results_dir: str) -> str:
    return os.path.join(journal_dir(results_dir), "calls")


def _ensure(results_dir: str) -> str:
    d = journal_dir(results_dir)
    os.makedirs(os.path.join(d, "islands"), exist_ok=True)
    return d


def _run_path(results_dir: str) -> str:
    return os.path.join(journal_dir(results_dir), "run.json")


def _append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # L72: if a prior append was TORN (a power-loss/kill mid-write left a newline-less
    # tail), prefix a newline so the torn record stays isolated on its own (droppable)
    # line instead of MERGING with this record into one unparseable line that both
    # _read_jsonl and the cost recompute would silently drop (losing a window/cost row).
    prefix = ""
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as rf:
                rf.seek(-1, os.SEEK_END)
                if rf.read(1) != b"\n":
                    prefix = "\n"
    except Exception:
        prefix = ""
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + json.dumps(obj) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out = []
    dropped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    dropped += 1  # L72: a torn/merged line — surface it, don't hide it
                    continue
    if dropped:
        import sys as _sys

        print(
            f"[journal] skipped {dropped} unparseable line(s) in "
            f"{os.path.basename(path)} (torn write?) — totals recomputed from the rest",
            file=_sys.stderr,
        )
    return out


def _write_json_atomic(path: str, obj: Dict[str, Any]) -> None:
    """Crash-safe JSON write: write a temp file, fsync it, then atomically rename
    over the target. A crash mid-write leaves either the old file or the new one
    intact — never a truncated run.json that would zero the cost ledger."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # L70: per-write UNIQUE temp name so two writers to the same target can never clobber
    # each other's temp file mid-rename (a fixed `{path}.tmp` collides).
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    # L70: on Windows os.replace can raise PermissionError against a concurrent reader —
    # retry briefly before giving up.
    for _attempt in range(5):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            time.sleep(0.05)
    else:
        os.replace(tmp, path)  # final attempt; let it raise if the target is truly locked
    # L68: fsync the PARENT DIRECTORY so a power-loss AFTER the rename can't lose it
    # (POSIX only — Windows has no O_DIRECTORY; best-effort, never raises).
    try:
        if os.name == "posix":
            dfd = os.open(os.path.dirname(path) or ".", os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    except Exception:
        pass


def _has_journal_streams(results_dir: str) -> bool:
    jd = journal_dir(results_dir)
    return any(
        os.path.exists(os.path.join(jd, name))
        for name in ("windows.jsonl", "interventions.jsonl", "calls.jsonl")
    )


def _recompute_total_cost(results_dir: str) -> float:
    """Rebuild the cumulative cost from the durable append-only streams. Each cost
    source lives in exactly one stream (window_cost ← windows.jsonl, orchestrator
    actions ← interventions.jsonl, external LLM calls ← calls.jsonl), so the sum is
    the true total with no double-counting."""
    jd = journal_dir(results_dir)
    total = 0.0
    for w in _read_jsonl(os.path.join(jd, "windows.jsonl")):
        total += float(w.get("window_cost", 0.0) or 0.0)
    for it in _read_jsonl(os.path.join(jd, "interventions.jsonl")):
        total += float(it.get("cost", 0.0) or 0.0)
    for c in _read_jsonl(os.path.join(jd, "calls.jsonl")):
        total += float(c.get("cost", 0.0) or 0.0)
    return total


def _reconstruct_run(results_dir: str, prior: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Repair a missing/corrupt run.json from the durable streams and write it back
    atomically, so the budget railguard keeps a truthful (never-zeroed) ledger."""
    import sys as _sys

    run: Dict[str, Any] = dict(prior) if isinstance(prior, dict) else {}
    windows = _read_jsonl(os.path.join(journal_dir(results_dir), "windows.jsonl"))
    run["total_cost"] = _recompute_total_cost(results_dir)
    if not run.get("windows_completed"):
        run["windows_completed"] = len(windows)
    if windows:
        last = windows[-1]
        run["last_window_index"] = last.get("window_index")
        run["total_programs"] = last.get("total_programs")
        if run.get("best_score") is None:
            run["best_score"] = last.get("best_score_end")
    run.setdefault("status", "running")
    run["recovered_from_corruption"] = True
    run["updated_at"] = time.time()
    _write_json_atomic(_run_path(results_dir), run)
    print(
        f"[journal] run.json missing/corrupt — reconstructed total_cost="
        f"{run['total_cost']:.4f} from journal streams ({results_dir})",
        file=_sys.stderr,
    )
    return run


# --- writers ---------------------------------------------------------------
def init_run(results_dir: str, meta: Dict[str, Any]) -> None:
    """Create run.json on first window if absent (idempotent).

    H6: if run.json is ABSENT but the durable streams already exist (run.json was
    deleted / sync-quarantined mid-run, then a restart or --resume), do NOT write a
    fresh ZEROED ledger — recompute total_cost from the streams via _reconstruct_run so
    the budget hard-cap can never silently restart from $0. Only a genuine fresh boot
    (no streams) writes the zeroed ledger below."""
    _ensure(results_dir)
    if os.path.exists(_run_path(results_dir)):
        return
    if _has_journal_streams(results_dir):
        _reconstruct_run(
            results_dir,
            {
                "run_id": meta.get("run_id"),
                "goal": meta.get("goal"),
                "task": meta.get("task"),
                "started_at": time.time(),
                "budget_usd": meta.get("budget_usd"),
                "config_digest": meta.get("config_digest"),
            },
        )
        return
    run = {
        "run_id": meta.get("run_id"),
        "goal": meta.get("goal"),
        "task": meta.get("task"),
        "started_at": time.time(),
        "status": "running",
        "windows_completed": 0,
        "best_score": None,
        "total_programs": 0,
        "last_window_index": None,
        "last_J": None,
        "total_cost": 0.0,            # cumulative USD across windows + interventions
        "budget_usd": meta.get("budget_usd"),
        "config_digest": meta.get("config_digest"),
    }
    _write_json_atomic(_run_path(results_dir), run)


def append_window(results_dir: str, diag: Dict[str, Any]) -> None:
    """Append the window diagnostics to the trajectory + update run.json +
    per-island lines. Called once per window by run_window."""
    _ensure(results_dir)
    _append_jsonl(os.path.join(journal_dir(results_dir), "windows.jsonl"), diag)

    # per-island regional view
    for isl in diag.get("island_health", []) or []:
        iid = isl.get("id")
        if iid is None:
            continue
        _append_jsonl(
            os.path.join(journal_dir(results_dir), "islands", f"island_{iid}.jsonl"),
            {
                "window_index": diag.get("window_index"),
                "best": isl.get("best"),
                "diversity": isl.get("diversity"),
            },
        )

    # roll up run.json (incl. the cost ledger)
    run = read_run(results_dir) or {}
    run["windows_completed"] = int(run.get("windows_completed", 0)) + 1
    run["best_score"] = diag.get("best_score_end")
    run["total_programs"] = diag.get("total_programs")
    run["last_window_index"] = diag.get("window_index")
    run["last_J"] = diag.get("J_score")
    # F4: the active strategy fingerprint ({target: hash}) so run.json is
    # self-contained about which strategy version is currently live.
    if diag.get("strategy_fingerprint") is not None:
        run["strategy_fingerprint"] = diag.get("strategy_fingerprint")
    run["total_cost"] = float(run.get("total_cost", 0.0)) + float(diag.get("window_cost", 0.0) or 0.0)
    run["updated_at"] = time.time()
    _write_json_atomic(_run_path(results_dir), run)


def append_intervention(results_dir: str, entry: Dict[str, Any]) -> None:
    """Log an orchestrator action. The orchestrator calls this whenever it
    rewrites a strategy, calls deep research / meta, spawns a subagent, etc.
    If the entry carries a ``cost``, it is added to the run's cost ledger so the
    budget railguard accounts for orchestrator-initiated LLM spend too."""
    entry = {**entry, "timestamp": entry.get("timestamp", time.time())}
    _append_jsonl(os.path.join(journal_dir(results_dir), "interventions.jsonl"), entry)
    cost = float(entry.get("cost", 0.0) or 0.0)
    if cost:
        add_cost(results_dir, cost)


def add_cost(results_dir: str, amount: float) -> float:
    """Add USD to the run's cumulative cost ledger; return the new total. This is
    the single source of truth the budget railguard checks — EVERY LLM call's
    cost (mutation, meta, deep research, embeddings) must land here."""
    run = read_run(results_dir) or {}
    run["total_cost"] = float(run.get("total_cost", 0.0)) + float(amount or 0.0)
    run["updated_at"] = time.time()
    _write_json_atomic(_run_path(results_dir), run)
    return run["total_cost"]


def log_call(
    results_dir: str,
    kind: str,
    request: Dict[str, Any],
    response: Dict[str, Any],
    cost: float = 0.0,
    summary: Optional[str] = None,
) -> str:
    """WS7: persist ONE external LLM call (meta / deep_research) in full, NEVER
    overwriting, and fold its cost into the ledger. This is what was missing when
    round-1's DR prompt was lost to an overwritten runner script.

    Writes two things:
      journal/calls/<kind>_<ts>_<rand>.json  — the FULL {request, response} (prompts
                                                + raw output; can be large)
      journal/calls.jsonl                     — one compact POINTER line per call
                                                {kind, timestamp, file, cost, summary}

    The pointer index is the key to "detailed but not context-polluting": the
    orchestrator reads ``calls.jsonl`` (tiny) to see WHAT was called and when, and
    opens a detail file via ``read_call`` only when it actually needs the prompt or
    raw output. Returns the detail file path.

    COST: this is THE place an external-call cost enters the ledger. A caller that
    uses ``log_call`` must NOT also ``append_intervention`` with the same cost
    (that would double-count). Mutation/embedding cost still flows via window_cost.
    """
    _ensure(results_dir)
    cdir = _calls_dir(results_dir)
    os.makedirs(cdir, exist_ok=True)
    ts = time.time()
    fname = f"{kind}_{int(ts)}_{uuid.uuid4().hex[:6]}.json"
    fpath = os.path.join(cdir, fname)
    _write_json_atomic(
        fpath,
        {"kind": kind, "timestamp": ts, "cost": float(cost or 0.0),
         "request": request, "response": response},
    )
    pointer = {
        "kind": kind, "timestamp": ts,
        "file": os.path.join("calls", fname),  # relative to journal/
        "cost": float(cost or 0.0),
        "summary": summary or "",
    }
    _append_jsonl(os.path.join(journal_dir(results_dir), "calls.jsonl"), pointer)
    if cost:
        add_cost(results_dir, float(cost))
    return fpath


def log_step(results_dir: str, record: Dict[str, Any]) -> None:
    """Append ONE per-step trace record to journal/steps.jsonl. Written ONLY when
    step tracing is on (warmup, and the framework-audit measuring window); absent in
    a normal run. Folds NO cost. The orchestrator reads it after each traced window
    to oversee one window step-by-step (sampler → prompt → llm output → eval →
    framework decision)."""
    rec = {**record, "timestamp": record.get("timestamp", time.time())}
    _append_jsonl(os.path.join(journal_dir(results_dir), "steps.jsonl"), rec)


def log_novelty(results_dir: str, record: Dict[str, Any]) -> None:
    """Append ONE per-candidate novelty-comparison record to journal/novelty.jsonl.

    Written for every EVALUATED CORRECT candidate whose novelty gate ran (one row per
    keep-best-vs-keep-separate decision), so the orchestrator can audit individual calls
    — not just the per-window aggregate acceptance rate — and TUNE
    evo.code_embed_sim_threshold from real pairs. ids + numbers only, NEVER code. Folds
    NO cost. Schema (see SKILL.md 'Tuning the novelty threshold'):
      {timestamp, window_index, generation, candidate_id, parent_id, island_idx,
       decision in {accepted_novel|kept_better_evicted|dropped_worse|idle_no_compare},
       max_similarity, most_similar_id, most_similar_score, candidate_score,
       n_compared, diff_lines, threshold}
    The most_similar_id link + both scores is the point: it lets the orchestrator fetch
    JUST the two programs of a borderline row by id (archive_query) instead of scanning
    the archive; diff_lines (unified-diff length) is the change-magnitude proxy that
    separates a scalar tweak (tiny diff, high similarity) from a new-direction edit
    (larger diff, lower similarity)."""
    rec = {**record, "timestamp": record.get("timestamp", time.time())}
    _append_jsonl(os.path.join(journal_dir(results_dir), "novelty.jsonl"), rec)


def read_novelty(results_dir: str, last_n: Optional[int] = None,
                 window_index: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read per-candidate novelty records (optionally a single window, and/or the last N)."""
    rows = _read_jsonl(os.path.join(journal_dir(results_dir), "novelty.jsonl"))
    if window_index is not None:
        rows = [r for r in rows if r.get("window_index") == window_index]
    return rows[-last_n:] if last_n else rows


def novelty_near_threshold(results_dir: str, margin: float = 0.02,
                           window_index: Optional[int] = None) -> List[Dict[str, Any]]:
    """The BORDERLINE novelty rows — abs(max_similarity - threshold) <= margin — the pairs
    the gate could plausibly have classified either way. The efficient entry point for
    tuning evo.code_embed_sim_threshold: read these compact rows (ids + numbers), then
    fetch ONLY each row's {candidate_id, most_similar_id} pair via archive_query to eyeball
    whether they are truly similar (-> threshold too low, raise it) or genuinely different
    (-> threshold too high, lower it) — never scanning full programs. Skips rows with no
    comparison (n_compared==0 / missing threshold)."""
    out: List[Dict[str, Any]] = []
    for r in read_novelty(results_dir, window_index=window_index):
        thr = r.get("threshold")
        sim = r.get("max_similarity")
        if not isinstance(thr, (int, float)) or not isinstance(sim, (int, float)):
            continue
        if int(r.get("n_compared", 0) or 0) <= 0:
            continue
        if abs(float(sim) - float(thr)) <= margin:
            out.append(r)
    return out


def total_cost(results_dir: str) -> float:
    return float((read_run(results_dir) or {}).get("total_cost", 0.0))


def budget_remaining(results_dir: str, budget_usd: Optional[float]) -> Optional[float]:
    """Remaining budget (None = no budget set). Negative means over budget."""
    if budget_usd is None:
        return None
    return float(budget_usd) - total_cost(results_dir)


def finalize_run(results_dir: str, status: str, summary: Optional[Dict[str, Any]] = None) -> None:
    run = read_run(results_dir) or {}
    run["status"] = status
    run["finished_at"] = time.time()
    if summary:
        run["summary"] = summary
    _write_json_atomic(_run_path(results_dir), run)


# --- readers (multi-granularity) -------------------------------------------
def read_run(results_dir: str) -> Dict[str, Any]:
    p = _run_path(results_dir)
    if not os.path.exists(p):
        # Genuinely absent → {} ONLY if no durable streams exist either. If run.json
        # vanished mid-run but the journal streams survive, rebuild it from them.
        if not _has_journal_streams(results_dir):
            return {}
        return _reconstruct_run(results_dir, None)
    try:
        data = json.loads(open(p, encoding="utf-8").read())
    except (json.JSONDecodeError, ValueError):
        data = None
    if not isinstance(data, dict) or "total_cost" not in data:
        # Truncated/corrupt (a crash mid-write) or a pre-ledger format → rebuild the
        # cost ledger from the durable streams so the budget cap is never zeroed.
        return _reconstruct_run(results_dir, data if isinstance(data, dict) else None)
    return data


def read_windows(results_dir: str, last_n: Optional[int] = None) -> List[Dict[str, Any]]:
    rows = _read_jsonl(os.path.join(journal_dir(results_dir), "windows.jsonl"))
    return rows[-last_n:] if last_n else rows


def j_trajectory(results_dir: str) -> List[Dict[str, Any]]:
    """Compact (window_index, J, best, stagnation) trajectory for a quick read."""
    return [
        {
            "window_index": w.get("window_index"),
            "J": w.get("J_score"),
            "best": w.get("best_score_end"),
            "stagnation": w.get("stagnation_flag"),
        }
        for w in read_windows(results_dir)
    ]


def read_interventions(results_dir: str) -> List[Dict[str, Any]]:
    return _read_jsonl(os.path.join(journal_dir(results_dir), "interventions.jsonl"))


def _work_scores(results_dir: str) -> List[float]:
    return [float(it["work_score"]) for it in read_interventions(results_dir)
            if isinstance(it.get("work_score"), (int, float))]


def recent_work_score(results_dir: str, n: int = 1, decay: Optional[float] = None) -> Optional[float]:
    """The per-control-return WORK SCORE the agent records on interventions.jsonl
    (how much real work the last control-return did — the scalar
    ``work_score = work_audit + work_discovery + work_grounding``, DEC-6).
    Returns the last (n=1), the plain mean of the last n, or a recency-decayed mean
    when ``decay`` is given. None when none recorded yet — the taper's no-signal
    default (which the harness reads as "wake every window"). The cadence taper reads
    only this scalar, so the three-axis split is invisible to cadence_policy.py."""
    scores = _work_scores(results_dir)
    if not scores:
        return None
    tail = scores[-int(max(1, n)):]
    if n == 1:
        return tail[-1]
    if decay is None:
        return sum(tail) / len(tail)
    weights = [decay ** (len(tail) - 1 - i) for i in range(len(tail))]
    wsum = sum(weights) or 1.0
    return sum(s * w for s, w in zip(tail, weights)) / wsum


def recent_work_axes(results_dir: str, n: int = 1) -> Optional[Dict[str, Any]]:
    """The last recorded {work_audit, work_discovery, work_grounding} THREE-axis work
    magnitudes (DEC-6; the hook for a finer, per-axis cadence rule). Splitting discovery
    from grounding makes a grounding-WITHOUT-discovery stretch detectable — grounding
    alone is real spend but does not count as the intervention that breaks stagnation.
    None when none recorded yet."""
    for it in reversed(read_interventions(results_dir)):
        if "work_audit" in it or "work_discovery" in it or "work_grounding" in it:
            return {
                "work_audit": it.get("work_audit"),
                "work_discovery": it.get("work_discovery"),
                "work_grounding": it.get("work_grounding"),
            }
    return None


def work_low_streak(results_dir: str, low_threshold: float = 1.0) -> int:
    """Count of consecutive most-recent control-returns whose recorded work_score is
    <= low_threshold (0 if the latest was high, or none recorded). The escalation
    counter the UNCAPPED taper uses: the longer recent work stays low, the larger the
    next window-cluster grows — with no ceiling (bounded only by budget / termination
    / stagnation)."""
    streak = 0
    for s in reversed(_work_scores(results_dir)):
        if s <= low_threshold:
            streak += 1
        else:
            break
    return streak


def termination_streak(results_dir: str) -> int:
    """Count trailing consecutive 'control_return' rows that are BOTH stagnant AND had an
    orchestrator intervention. The counted interventions are EXACTLY (H12 INCLUSIVE): a
    framework rewrite, a DISCOVERY ROUND (R1 Azure DR or R2 archive-analyst) — which is then
    GROUNDED — OR a deliberate config-lever flip. The automatic per-window meta round does NOT
    count. A hand-authored GROUNDING is NOT a standalone counted intervention: a grounding never
    runs without the in-interval discovery that produced its technique (the spawn_island PRIMARY
    gate + grounding-engineer refusal enforce this), so it RIDES that DR and counts only via it
    (work_discovery>0), never on its own. This is the deterministic termination signal (H6/H7/H8):
    N-in-a-row means the search cannot escape stagnation DESPITE intervening at every return. A
    stagnation-break (stagnation_flag False) or a no-intervention return resets the streak.
    Computed from interventions.jsonl — the agent writes one canonical control_return row per
    control-return; the harness reads it.

    Each row: {type:"control_return", stagnation_flag: bool, intervened: bool,
    work_audit, work_discovery, work_grounding, work_score, ...}. ``intervened`` is the agent's
    explicit (DEC-6: work_audit>0 OR work_discovery>0 — work_grounding ALONE never flips it, so a
    grounding that grounds NO in-interval discovery cannot pad the streak); rows missing it fall
    back to that derivation so the signal is robust to either shape."""
    rows = [r for r in read_interventions(results_dir) if r.get("type") == "control_return"]
    streak = 0
    for r in reversed(rows):
        intervened = r.get("intervened")
        if intervened is None:  # robust fallback if the agent omitted the explicit flag
            # DEC-6: key on work_discovery (NOT work_grounding) — grounding alone is real spend
            # but is not the intervention that breaks stagnation.
            work_discovery = r.get("work_discovery", 0)
            intervened = float(r.get("work_audit", 0) or 0) > 0 or float(work_discovery or 0) > 0
        if bool(r.get("stagnation_flag")) and bool(intervened):
            streak += 1
        else:
            break
    return streak


def read_calls(results_dir: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """WS7: the compact external-call pointer index (no big prompts). Optionally
    filter by kind ('meta' / 'dr' / 'archive_analyst'). The two DISCOVERY-stub kinds the
    recency gate recognizes are {dr, archive_analyst} (R1 Azure deep research and R2 the
    archive-analyst subagent); 'meta' is the automatic per-window round (not a discovery
    stub). Open a specific call's full detail with ``read_call(results_dir, row['file'])``."""
    rows = _read_jsonl(os.path.join(journal_dir(results_dir), "calls.jsonl"))
    return [r for r in rows if (kind is None or r.get("kind") == kind)]


def read_call(results_dir: str, file: str) -> Dict[str, Any]:
    """Read one full call-detail file (the {request, response}) by its pointer
    ``file`` (relative to journal/, as stored in calls.jsonl)."""
    p = os.path.join(journal_dir(results_dir), file)
    if not os.path.exists(p):
        return {}
    try:
        return json.loads(open(p, encoding="utf-8").read())
    except json.JSONDecodeError:
        return {}


def _control_return_boundary(results_dir: str) -> float:
    """The interval anchor for the recency gate (DEC-7): the timestamp of the
    MOST-RECENT type=="control_return" intervention row (0.0 if none → first interval).
    control_return rows are the only timestamped interval anchor — windows carry none.
    Relies on the orchestrator convention of writing the control_return row AFTER acting,
    so a discovery stub written this interval is strictly-greater than the prior boundary."""
    boundary = 0.0
    for r in read_interventions(results_dir):
        if r.get("type") == "control_return":
            ts = r.get("timestamp")
            if isinstance(ts, (int, float)) and float(ts) > boundary:
                boundary = float(ts)
    return boundary


def discovery_in_interval(results_dir: str) -> List[Dict[str, Any]]:
    """DEC-7 recency gate — THE single source of truth for "is there a fresh, usable
    discovery this control-return interval?". Read-only.

    A *discovery round* (== "DR round") is a discovery pass via EXACTLY ONE OF R1 (Azure
    deep research, kind="dr") OR R2 (the archive-analyst subagent, kind="archive_analyst").
    This returns the in-interval, USABLE discovery stubs of those two kinds; the caller
    (the PRIMARY spawn_island.py gate; the grounding-engineer subagent likewise refuses
    without it) fails CLOSED on an empty list — no in-interval discovery ⇒ grounding refused.

    In-interval iff ``stub.timestamp > boundary`` (STRICT, DEC-7/O6), where boundary =
    the most-recent control_return row timestamp (0.0 ⇒ first interval). USABLE iff the
    stub denotes >=1 returned direction: the pointer ``summary`` is not a refusal AND, when
    the full detail file is readable, its ``response.usable`` is True (an explicit
    ``usable:false`` from R1/R2 disqualifies it; a missing detail/usable flag is treated as
    usable so a legitimate stub is never silently dropped). A stale stub (timestamp <=
    boundary) never satisfies the gate."""
    boundary = _control_return_boundary(results_dir)
    stubs = read_calls(results_dir, kind="dr") + read_calls(results_dir, kind="archive_analyst")
    out: List[Dict[str, Any]] = []
    for s in stubs:
        ts = s.get("timestamp")
        if not isinstance(ts, (int, float)) or float(ts) <= boundary:
            continue  # stale (or undated) → not in this interval
        # Pointer-level refusal screen: a summary that reads as a refusal disqualifies.
        summary = str(s.get("summary") or "").strip().lower()
        if summary and ("refus" in summary or "no usable" in summary or "unusable" in summary):
            continue
        # Confirm against the full detail blob when present: an explicit response.usable
        # is False disqualifies; absent flag/detail is treated as usable (fail OPEN on a
        # legitimate stub, not closed).
        usable = True
        file = s.get("file")
        if file:
            detail = read_call(results_dir, file)
            resp = detail.get("response") if isinstance(detail, dict) else None
            if isinstance(resp, dict) and "usable" in resp:
                usable = bool(resp.get("usable"))
        if not usable:
            continue
        out.append(s)
    return out


def read_island(results_dir: str, island_id: int) -> List[Dict[str, Any]]:
    return _read_jsonl(
        os.path.join(journal_dir(results_dir), "islands", f"island_{island_id}.jsonl")
    )


def read_steps(results_dir: str, generation: Optional[int] = None,
               last_n: Optional[int] = None) -> List[Dict[str, Any]]:
    """The per-step oversight trace (present only when tracing was on). Filter to a
    single generation, and/or take the last N records."""
    rows = _read_jsonl(os.path.join(journal_dir(results_dir), "steps.jsonl"))
    if generation is not None:
        rows = [r for r in rows if r.get("generation") == generation]
    return rows[-last_n:] if last_n else rows


def build_run_summary(results_dir: str) -> str:
    """Assemble a Markdown RUN_SUMMARY draft from the journal. The orchestrator
    writes this to the run dir and then augments it with a postmortem and the
    'Recommended framework changes (out of scope)' section."""
    run = read_run(results_dir)
    traj = j_trajectory(results_dir)
    interventions = read_interventions(results_dir)

    lines = ["# Run Summary", ""]
    lines.append(f"- run_id: {run.get('run_id')}")
    lines.append(f"- goal: {run.get('goal')}")
    lines.append(f"- status: {run.get('status')}")
    lines.append(f"- finished_at: {run.get('finished_at')}")
    lines.append(f"- windows completed: {run.get('windows_completed')}")
    lines.append(f"- best score: {run.get('best_score')}")
    lines.append(f"- total programs: {run.get('total_programs')}")
    lines.append(f"- total cost (USD): {run.get('total_cost')}  /  budget: {run.get('budget_usd')}")
    lines.append("")
    lines.append("## Progress trajectory (window: best-score / stagnation)")
    for w in traj:
        lines.append(f"- w{w['window_index']}: best={w['best']} stagnant={w['stagnation']}")
    lines.append("")
    lines.append("## Interventions")
    if interventions:
        for it in interventions:
            lines.append(
                f"- [{it.get('type')}] target={it.get('target')} "
                f"reason={it.get('reason')} → {it.get('outcome')}"
            )
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Postmortem")
    lines.append("_(orchestrator: what worked, what didn't, why)_")
    lines.append("")
    lines.append("## Future fixes for the user before the next run")
    lines.append(
        "_(orchestrator: foundation/outer-loop changes you could NOT make mid-run — "
        "sqlite schema, the JSON contract, new primitives, evaluator changes, scalability "
        "(serial eval / O(N) novelty if it ever bottlenecks) — for a human pass between "
        "runs)_"
    )
    return "\n".join(lines)


def archive_run(
    results_dir: str,
    dest_root: str = "orchestrator/run_archive",
    run_id: Optional[str] = None,
    finished_at: Optional[float] = None,
) -> str:
    """Archive a COMPLETED run's COMPACT history into ``<dest_root>/<run_id>__<ts>/`` for
    the user's later reference. Copies the journal (MINUS the bulky calls/<x>.json detail
    blobs — keeps calls.jsonl) + programs.sqlite + the ending document (RUN_SUMMARY.md) +
    strategy_history/index.json. Does NOT copy per-version code snapshots or gen_* eval
    dirs. Defaults run_id/finished_at from run.json (then the results_dir basename / now),
    so the dir name never does int(None). Teach the agent: do NOT read prior archives
    while running a NEW job — they exist only for the user's later reference."""
    run = read_run(results_dir)
    rid = run_id or run.get("run_id") or os.path.basename(os.path.normpath(results_dir))
    fin = finished_at if finished_at is not None else (run.get("finished_at") or time.time())
    dest = os.path.join(dest_root, f"{rid}__{int(fin)}")
    os.makedirs(dest, exist_ok=True)
    jd = journal_dir(results_dir)
    if os.path.isdir(jd):
        dest_j = os.path.join(dest, "journal")
        os.makedirs(dest_j, exist_ok=True)
        for name in os.listdir(jd):
            if name == "calls":  # skip the heavy per-call detail blobs; keep calls.jsonl
                continue
            src = os.path.join(jd, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dest_j, name))
            elif os.path.isdir(src):
                shutil.copytree(src, os.path.join(dest_j, name), dirs_exist_ok=True)
    for rel in ("programs.sqlite", "RUN_SUMMARY.md"):
        src = os.path.join(results_dir, rel)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, os.path.basename(rel)))
    # M36: the strategy history lives at strategy_store.history_dir() (the orchestrator tree, or
    # SHINKA_ORCH_HISTORY_DIR), NOT under results_dir — the old `results_dir/strategy_history`
    # path never existed, so the archive silently omitted the deploy/outcome audit trail. Read
    # the index from the REAL location.
    try:
        import sys as _sys

        if os.path.dirname(__file__) not in _sys.path:
            _sys.path.insert(0, os.path.dirname(__file__))
        import strategy_store as _ss  # harness sibling

        sidx = str(_ss.index_path())
    except Exception:
        sidx = os.path.join(results_dir, "strategy_history", "index.json")  # fallback
    if os.path.exists(sidx):
        os.makedirs(os.path.join(dest, "strategy_history"), exist_ok=True)
        shutil.copy2(sidx, os.path.join(dest, "strategy_history", "index.json"))
    return dest


# --- CLI for orchestrator convenience --------------------------------------
if __name__ == "__main__":
    import sys

    try:
        from . import _common  # type: ignore
    except Exception:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import _common  # type: ignore

    def main(payload: Dict[str, Any]) -> Dict[str, Any]:
        rd = payload["results_dir"]
        view = payload.get("view", "run")
        if view == "run":
            return {"result": read_run(rd)}
        if view == "windows":
            return {"result": read_windows(rd, payload.get("last_n"))}
        if view == "trajectory":
            return {"result": j_trajectory(rd)}
        if view == "interventions":
            return {"result": read_interventions(rd)}
        if view == "island":
            return {"result": read_island(rd, int(payload["island_id"]))}
        if view == "calls":
            return {"result": read_calls(rd, payload.get("kind"))}
        if view == "call":
            return {"result": read_call(rd, payload["file"])}
        if view == "steps":
            return {"result": read_steps(rd, payload.get("generation"), payload.get("last_n"))}
        if view == "step_tail":
            return {"result": read_steps(rd, last_n=int(payload.get("last_n", 20)))}
        if view == "novelty":
            return {"result": read_novelty(rd, payload.get("last_n"), payload.get("window_index"))}
        if view == "novelty_near_threshold":
            return {"result": novelty_near_threshold(
                rd, float(payload.get("margin", 0.02) or 0.02), payload.get("window_index"))}
        if view == "log_novelty":
            log_novelty(rd, payload["record"])
            return {"logged": True}
        if view == "append_intervention":
            append_intervention(rd, payload["entry"])
            return {"appended": True}
        if view == "log_call":
            path = log_call(
                rd, payload["kind"], payload.get("request", {}),
                payload.get("response", {}), float(payload.get("cost", 0.0) or 0.0),
                payload.get("summary"),
            )
            return {"logged": True, "file": path}
        if view == "build_run_summary":
            return {"result": build_run_summary(rd)}
        if view == "finalize_run":
            finalize_run(rd, payload["status"], payload.get("summary"))
            return {"finalized": True, "status": payload["status"]}
        if view == "archive_run":
            dest = archive_run(rd, payload.get("dest_root", "orchestrator/run_archive"),
                               payload.get("run_id"), payload.get("finished_at"))
            return {"archived": True, "dest": dest}
        raise ValueError(f"unknown view: {view}")

    _common.run_main(main)

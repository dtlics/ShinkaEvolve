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
  journal/steering.jsonl      human-steering ledger: one `user_steer` row per LITERAL user
                              direction the orchestrator transcribes from the live chat, and
                              one `steer_consumed` row when a steer is acted on (a steered
                              DR / steered-analyst round, or surfaced-and-declined). The
                              steering evidence that makes a kind="steered_analyst" discovery
                              stub gate-valid (R2 is steering-only). Folds no cost.

`strategy_history/` (separate) holds the per-strategy-version snapshots. Together
they let the orchestrator zoom from "how's the run overall" → "what did window 37
look like" → "every reward-related intervention" → "is island 2 dying."

run.json durability contract (so the hard budget cap can never be silently lost):
every run.json write is atomic (write a UNIQUE-named temp file, fsync, os.replace with a
Windows-PermissionError retry, then fsync the parent dir on POSIX), and a
missing-or-corrupt run.json is REPAIRED by recomputing total_cost from the durable
append-only streams (windows.jsonl window_cost + interventions.jsonl cost + calls.jsonl
cost). The repair fires BOTH on read (corrupt-in-place) AND at init_run when run.json is
ABSENT but the streams exist (deleted-then-restart), so the cap can never restart
from $0. Append is torn-write-safe: a newline-less torn tail is isolated rather than
merged, and an unparseable line is skipped with a stderr warning, never silently dropped.
The only spend not recoverable this way is a cost added directly via add_cost
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
    # If a prior append was TORN (a power-loss/kill mid-write left a newline-less
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
                    dropped += 1  # a torn/merged line — surface it, don't hide it
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
    # Per-write UNIQUE temp name so two writers to the same target can never clobber
    # each other's temp file mid-rename (a fixed `{path}.tmp` collides).
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    # On Windows os.replace can raise PermissionError against a concurrent reader —
    # retry briefly before giving up.
    for _attempt in range(5):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            time.sleep(0.05)
    else:
        os.replace(tmp, path)  # final attempt; let it raise if the target is truly locked
    # Fsync the PARENT DIRECTORY so a power-loss AFTER the rename can't lose it
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

    If run.json is ABSENT but the durable streams already exist (run.json was
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
    # Record the active strategy fingerprint ({target: hash}) so run.json is
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
    """Persist ONE external LLM call (meta / deep_research) in full, NEVER
    overwriting, and fold its cost into the ledger — every call gets its own
    uniquely named detail file, so no later call can clobber an earlier prompt.

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


# log_llm_content 10GB per-run cap: forensics must never eat the disk. One stderr
# warning per process once the cap trips (module-level flag, not per-call spam).
_LLM_CONTENT_CAP_BYTES = 10_737_418_240
_llm_content_cap_warned = False


def log_llm_content(results_dir: str, generation: int, attempt: int, kind: str,
                    payload: Dict[str, Any]) -> Optional[str]:
    """Durable per-call INNER-LOOP forensics: persist ONE external LLM call's full
    content (prompt + raw response + outcome) as JSON to
    journal/llm_content/gen{generation:05d}_a{attempt}_{kind}.json, so every
    mutation/fix call's request and response survive the run and can be audited
    after the fact. Meta / deep-research (and any standalone orchestrator call)
    are already content-logged in full via ``log_call`` (journal/calls/) — this
    covers the high-volume per-candidate calls that path skips.

    Best-effort: NEVER raises to the caller — forensics must not break a mutation.
    Disk use is bounded by a 10GB per-run cap: a running byte total lives in
    journal/llm_content/.bytes (read int, add this file's size, write back — a
    single writer process owns results_dir via .run.lock, so no locking is
    needed); once a write would push the total past the cap it is skipped and
    ONE stderr warning is emitted per process. Returns the written path, or
    None when capped/failed."""
    global _llm_content_cap_warned
    try:
        d = os.path.join(journal_dir(results_dir), "llm_content")
        os.makedirs(d, exist_ok=True)
        data = json.dumps(payload, default=str)
        size = len(data.encode("utf-8"))
        bytes_path = os.path.join(d, ".bytes")
        total = 0
        try:
            if os.path.exists(bytes_path):
                with open(bytes_path, encoding="utf-8") as bf:
                    total = int(bf.read().strip() or 0)
        except Exception:
            total = 0
        if total + size > _LLM_CONTENT_CAP_BYTES:
            if not _llm_content_cap_warned:
                _llm_content_cap_warned = True
                import sys as _sys

                print(
                    f"[journal] llm_content hit the 10GB per-run cap — skipping "
                    f"further per-call content logs ({results_dir})",
                    file=_sys.stderr,
                )
            return None
        fpath = os.path.join(d, f"gen{int(generation):05d}_a{attempt}_{kind}.json")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(data)
        with open(bytes_path, "w", encoding="utf-8") as bf:
            bf.write(str(total + size))
        return fpath
    except Exception:
        return None


def log_step(results_dir: str, record: Dict[str, Any]) -> None:
    """Append ONE per-step trace record to journal/steps.jsonl. Written ONLY when
    step tracing is on (warmup, and the framework-audit measuring window); absent in
    a normal run. Folds NO cost. The orchestrator reads it after each traced window
    to oversee one window step-by-step (sampler → prompt → llm output → eval →
    framework decision)."""
    rec = {**record, "timestamp": record.get("timestamp", time.time())}
    _append_jsonl(os.path.join(journal_dir(results_dir), "steps.jsonl"), rec)


def log_slot_event(results_dir: str, record: Dict[str, Any]) -> None:
    """Append ONE slot-lifecycle event to journal/slots.jsonl — the audit trail that
    keeps a PARALLEL window (evo.parallel_slots > 1) as readable as the sequential
    driver's implicit ordering. Events: 'admitted' (the slot passed the budget/stop
    admission checks and started), 'committed' (its commit phase landed —
    landing order IS file order), and 'crashed' (the slot raised; nothing was
    committed for it and the window aborts). Schema:
      {timestamp, window_index, slot, generation, event, inflight (concurrent
       slots at emit time), slot_cost (committed only; approximate under
       concurrency — slots share one window cost counter, so it is an upper
       bound — and exact when parallel_slots=1)}
    ids + numbers only; folds NO cost."""
    rec = {**record, "timestamp": record.get("timestamp", time.time())}
    _append_jsonl(os.path.join(journal_dir(results_dir), "slots.jsonl"), rec)


def log_novelty(results_dir: str, record: Dict[str, Any]) -> None:
    """Append ONE per-candidate novelty-comparison record to journal/novelty.jsonl.

    Written for every EVALUATED CORRECT candidate whose novelty gate ran (one row per
    keep-best-vs-keep-separate decision), so the orchestrator can audit individual calls
    — not just the per-window aggregate acceptance rate — and TUNE
    evo.code_embed_sim_threshold from real pairs. ids + numbers only, NEVER code. Folds
    NO cost. Schema (see SKILL.md 'Tuning the novelty threshold'):
      {timestamp, window_index, generation, candidate_id, parent_id, island_idx,
       decision in {accepted_novel|kept_better_evicted|kept_better_evict_failed|
                    kept_better_evict_skipped_pinned|kept_better_no_incumbent|
                    dropped_worse|idle_no_compare}
       — the kept_better_* variants distinguish a REAL eviction from a failed one
       (both near-dups left live) and from a deliberate skip (the incumbent is
       another in-flight slot's parent),
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
    them — never scanning full programs. The gate rejects as near-dup when
    max_similarity >= threshold, so a HIGHER threshold is STRICTER about what counts as a
    near-dup (fewer rejects): a borderline pair that is truly similar means the threshold
    is too high (near-dups are slipping through) -> LOWER it; a genuinely different pair
    means the threshold is too low (real new work is being consolidated away) -> RAISE it.
    Skips rows with no comparison (n_compared==0 / missing threshold)."""
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


# The ONLY statuses a run may terminate with — the three sanctioned criteria.
# budget_exhausted + stagnation_intervention_exhausted are HARNESS-owned (run_window
# finalizes them in-process after verifying the condition itself; the CLI view
# additionally re-checks the precondition before accepting either, keeping a recovery
# path when the in-process finalize failed). stopped_by_user is agent-finalized but
# requires recorded evidence (the literal user turn) — see below.
TERMINAL_STATUSES = {"budget_exhausted", "stagnation_intervention_exhausted", "stopped_by_user"}

# CLI-view slack for budget_exhausted: the predictive admission railguard stops
# ADMITTING candidates when prior_total + in-flight estimate would cross the cap, so a
# legitimately capped run can come to rest up to ~parallel_slots × per-slot-cost short
# of budget_usd (per-call cap ~$10). The CLI accepts budget_exhausted only within this
# slack of the cap, so an agent cannot stamp it on a half-spent run.
_FINALIZE_BUDGET_SLACK_USD = 25.0


def finalize_run(
    results_dir: str,
    status: str,
    summary: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> None:
    """Write the terminal status. HARDENED (verify-in-code termination):

    - ``status`` must be one of TERMINAL_STATUSES — anything else raises ValueError.
    - A run already finalized with a DIFFERENT terminal status is never overwritten
      (ValueError); re-finalizing the SAME status is an idempotent no-op that
      preserves the first ``finished_at`` (relaunching an exhausted run re-hits the
      harness check and re-finalizes benignly).
    - ``stopped_by_user`` requires ``evidence={"user_quote": <the LITERAL user turn>}``
      (non-empty) for EVERY caller; it is stored as run.json ``stop_evidence`` with an
      auto-stamped ``noted_at``. Chat is outside code-observable state, so the quote is
      an auditability bar, not proof — but an evidence-less stop can no longer be
      stamped at all.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(
            f"finalize_run: status {status!r} is not a sanctioned terminal status "
            f"(allowed: {sorted(TERMINAL_STATUSES)})"
        )
    run = read_run(results_dir) or {}
    current = run.get("status")
    if current in TERMINAL_STATUSES:
        if current == status:
            return  # idempotent re-finalize; keep the first finished_at
        raise ValueError(
            f"finalize_run: run already finalized as {current!r}; refusing to "
            f"overwrite with {status!r}"
        )
    if status == "stopped_by_user":
        quote = str((evidence or {}).get("user_quote", "") or "").strip()
        if not quote:
            raise ValueError(
                "finalize_run: stopped_by_user requires evidence={'user_quote': "
                "<the literal user stop message>} — never finalize a user stop "
                "without quoting the actual user turn"
            )
        ev = dict(evidence or {})
        ev.setdefault("noted_at", time.time())
        run["stop_evidence"] = ev
    run["status"] = status
    run["finished_at"] = time.time()
    if summary:
        run["summary"] = summary
    _write_json_atomic(_run_path(results_dir), run)


def finalize_run_checked(
    results_dir: str,
    status: str,
    summary: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> None:
    """The CLI-view finalize: RE-CHECKS the harness-owned preconditions from journal
    state before delegating to finalize_run. This is the agent's recovery path when the
    in-process finalize failed (`finalize_error` on a terminal return) — and the guard
    that an agent cannot stamp a harness status the run has not actually earned:

    - ``budget_exhausted``: requires a budget set AND remaining budget within the
      acceptance slack of the cap (max($25, 2% of budget) — covers the predictive
      admission stop, which can rest a legitimately capped run slightly short).
    - ``stagnation_intervention_exhausted``: requires the VERIFIED termination streak
      >= the boot-frozen N (run.json config_digest.termination_streak, default 5).
    - ``stopped_by_user``: evidence enforcement lives in finalize_run itself.

    The in-process harness calls (run_window's _finalize_terminal) use finalize_run
    directly — they just verified the condition themselves."""
    if status == "budget_exhausted":
        run = read_run(results_dir) or {}
        bud = run.get("budget_usd")
        if bud is None:
            raise ValueError("finalize_run refused: budget_exhausted needs a budget_usd set")
        rem = budget_remaining(results_dir, float(bud))
        slack = max(_FINALIZE_BUDGET_SLACK_USD, 0.02 * float(bud))
        if rem is None or rem > slack:
            raise ValueError(
                f"finalize_run refused: budget remaining {rem} exceeds the acceptance "
                f"slack {slack} — the run is not budget-exhausted")
    elif status == "stagnation_intervention_exhausted":
        run = read_run(results_dir) or {}
        need = int((run.get("config_digest") or {}).get("termination_streak") or 5)
        got = termination_streak(results_dir)
        if got < need:
            raise ValueError(
                f"finalize_run refused: verified termination_streak {got} < {need}")
    finalize_run(results_dir, status, summary, evidence)


def write_run_summary_draft(results_dir: str) -> Optional[str]:
    """Write ``<results_dir>/RUN_SUMMARY.md`` from build_run_summary IF ABSENT and
    return its path (None when one already exists). Called by the harness right after
    an auto-finalize so a terminated run is never summary-less; the orchestrator still
    ENRICHES the draft (postmortem, future fixes) and runs archive_run itself."""
    path = os.path.join(results_dir, "RUN_SUMMARY.md")
    if os.path.exists(path):
        return None
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_run_summary(results_dir))
    return path


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
    ``work_score = work_audit + work_discovery + work_grounding``).
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
    magnitudes (the hook for a finer, per-axis cadence rule). Splitting discovery
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


# --- human steering (journal/steering.jsonl) --------------------------------
# The user may text a direction into the live session mid-run. The orchestrator
# transcribes it VERBATIM the moment it arrives (same anti-confabulation bar as
# stopped_by_user's stop_evidence), queues it across clusters, and consumes it at a
# control-return. A recorded steer is what authorizes a kind="steered_analyst"
# discovery stub (R2 is STEERING-ONLY — never autonomous); kind="dr" stubs need none.

_STEER_ACTIONS = {"dr", "steered_analyst", "declined", "merged"}


def _steering_path(results_dir: str) -> str:
    return os.path.join(journal_dir(results_dir), "steering.jsonl")


def log_steering(results_dir: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Record ONE user-steering direction as a ``user_steer`` row. ``entry`` must
    carry a non-empty ``quoted_user_text`` — the LITERAL user message (never a
    paraphrase alone; put your reading in ``paraphrase``). ``steer_id``/``timestamp``
    are auto-stamped when absent. Returns the completed row (incl. steer_id)."""
    quote = str(entry.get("quoted_user_text", "") or "").strip()
    if not quote:
        raise ValueError(
            "log_steering: quoted_user_text is required and must be the LITERAL "
            "user message (record the user's own words, not a summary)"
        )
    row = {
        "type": "user_steer",
        "steer_id": str(entry.get("steer_id") or uuid.uuid4().hex[:8]),
        "timestamp": float(entry.get("timestamp") or time.time()),
        "quoted_user_text": quote,
        "paraphrase": entry.get("paraphrase"),
        "window_index": entry.get("window_index"),
    }
    _ensure(results_dir)
    _append_jsonl(_steering_path(results_dir), row)
    return row


def read_steering(results_dir: str) -> List[Dict[str, Any]]:
    return _read_jsonl(_steering_path(results_dir))


def pending_steering(results_dir: str) -> List[Dict[str, Any]]:
    """The queued (recorded but not yet consumed) user steers, oldest first.
    Deliberately NOT interval-bound: a steer recorded mid-cluster (or several
    control-returns ago) stays pending until a ``steer_consumed`` row lands."""
    rows = read_steering(results_dir)
    consumed = {r.get("steer_id") for r in rows if r.get("type") == "steer_consumed"}
    return [r for r in rows
            if r.get("type") == "user_steer" and r.get("steer_id") not in consumed]


def consume_steering(
    results_dir: str,
    steer_id: str,
    action: str,
    stub_file: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Mark a recorded steer as acted on. ``action``: "dr" (steered external DR) /
    "steered_analyst" (steered R2 round — pass the stub's calls.jsonl ``file`` as
    ``stub_file`` so the gate can bind steer↔stub) / "declined" (surfaced to the user,
    not actionable) / "merged" (duplicate of another steer). Refuses an unknown
    steer_id or action."""
    if action not in _STEER_ACTIONS:
        raise ValueError(f"consume_steering: unknown action {action!r} (allowed: {sorted(_STEER_ACTIONS)})")
    known = {r.get("steer_id") for r in read_steering(results_dir) if r.get("type") == "user_steer"}
    if steer_id not in known:
        raise ValueError(f"consume_steering: no user_steer row with steer_id {steer_id!r}")
    row = {
        "type": "steer_consumed",
        "steer_id": steer_id,
        "timestamp": time.time(),
        "action": action,
        "stub_file": stub_file,
        "note": note,
    }
    _append_jsonl(_steering_path(results_dir), row)
    return row


def _steer_validates_stub(
    results_dir: str,
    stub: Dict[str, Any],
    steering_rows: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Does recorded steering evidence authorize this kind="steered_analyst" stub?
    FAIL CLOSED for this kind (the steer↔stub linkage is the point of the R2
    demotion): requires (i) a readable detail blob whose ``request.steer_id`` is a
    non-empty string; (ii) a ``user_steer`` row with that id, recorded at or before
    the stub's timestamp (a steer may be from a PRIOR interval — steering queues);
    (iii) the steer not already consumed by a DIFFERENT stub — if any
    ``steer_consumed`` row exists for the id, one of them must carry THIS stub's
    ``file`` (blocks replaying one steer across rounds, and blocks a stub claiming a
    steer that was resolved as declined/merged)."""
    file = stub.get("file")
    if not file:
        return False
    detail = read_call(results_dir, file)
    req = detail.get("request") if isinstance(detail, dict) else None
    sid = (req or {}).get("steer_id")
    if not isinstance(sid, str) or not sid.strip():
        return False
    sid = sid.strip()
    rows = read_steering(results_dir) if steering_rows is None else steering_rows
    stub_ts = stub.get("timestamp")
    steer_ok = any(
        r.get("type") == "user_steer" and r.get("steer_id") == sid
        and isinstance(r.get("timestamp"), (int, float))
        and (isinstance(stub_ts, (int, float)) and float(r["timestamp"]) <= float(stub_ts))
        for r in rows
    )
    if not steer_ok:
        return False
    consumes = [r for r in rows if r.get("type") == "steer_consumed" and r.get("steer_id") == sid]
    if consumes and not any(c.get("stub_file") == file for c in consumes):
        return False
    return True


# --- verified termination (criterion 2, computed from code artifacts) --------
# Frozen foundation copies of the stagnation-detector defaults, used only when
# run.json's boot-time config_digest lacks the thresholds. Keep in sync with
# orchestrator/scripts/stagnation_detector.py (_DEFAULT_ABS_FLOOR/_DEFAULT_REL_FRAC)
# — duplicated deliberately: termination must NOT import the mutable detector.
_TERM_ABS_FLOOR_DEFAULT = 1e-3
_TERM_REL_FRAC_DEFAULT = 0.05
_TERM_CONSECUTIVE_DEFAULT = 2

_termination_divergence_warned = False


def foundation_stagnation_flags(results_dir: str) -> Dict[int, bool]:
    """FOUNDATION-side stagnation recompute for TERMINATION ONLY: per window_index,
    was the run stagnant at that window — derived from windows.jsonl
    ``best_score_start``/``best_score_end`` (written by immutable diagnostics) and the
    BOOT-FROZEN thresholds in run.json ``config_digest``. Never reads the mutable
    detector's ``delta``/``stagnation_flag``/``low_streak`` fields and never imports
    stagnation_detector.py, so a rewritten/sabotaged detector (or a mid-run threshold
    flip) can neither disable nor force termination. Cadence/return-control keeps
    using the mutable detector — this floor is only for criterion 2.

    Mirrors the detector's bar (low when Δ <= max(abs_floor, rel_frac·max(s_start,0)))
    and run_window's FAIR-TRIAL reset (a strategy_fingerprint change between windows
    zeroes the low-streak — keep in sync with run_window.py's prior_low_streak reset).
    Unparseable scores ⇒ non-low + reset (fail toward keep-running)."""
    run = read_run(results_dir) or {}
    digest = run.get("config_digest") or {}
    try:
        abs_floor = float(digest.get("stagnation_abs_floor"))
    except (TypeError, ValueError):
        abs_floor = _TERM_ABS_FLOOR_DEFAULT
    try:
        rel_frac = float(digest.get("stagnation_rel_frac"))
    except (TypeError, ValueError):
        rel_frac = _TERM_REL_FRAC_DEFAULT
    try:
        consecutive = int(digest.get("consecutive_required"))
    except (TypeError, ValueError):
        consecutive = _TERM_CONSECUTIVE_DEFAULT
    flags: Dict[int, bool] = {}
    low_streak = 0
    prev_fp = None
    for w in read_windows(results_dir):
        try:
            wi = int(w.get("window_index"))
        except (TypeError, ValueError):
            continue  # an unindexed row cannot participate in termination
        fp = w.get("strategy_fingerprint")
        if prev_fp is not None and fp is not None and fp != prev_fp:
            low_streak = 0  # fair trial for a freshly deployed strategy
        if fp is not None:
            prev_fp = fp
        try:
            s_start = float(w.get("best_score_start"))
            s_end = float(w.get("best_score_end"))
        except (TypeError, ValueError):
            low_streak = 0
            flags[wi] = False
            continue
        low = (s_end - s_start) <= max(abs_floor, rel_frac * max(s_start, 0.0))
        low_streak = low_streak + 1 if low else 0
        flags[wi] = low_streak >= consecutive
    return flags


def _strategy_deploy_times(results_dir: str) -> List[float]:
    """Timestamps of strategy_history index entries ATTRIBUTED TO THIS RUN (the
    code-verified 'framework rewrite happened' artifact). The index lives at
    strategy_store.history_dir() — repo-level and SHARED across worktrees/runs — so
    only entries whose stamped ``results_dir`` matches this run count; unattributed
    entries (old/smoke deploys) never count. Any import/read failure (incl. a corrupt
    index) degrades to [] — termination under-counts and the run continues, never
    crashes."""
    try:
        import sys as _sys

        if os.path.dirname(__file__) not in _sys.path:
            _sys.path.insert(0, os.path.dirname(__file__))
        import strategy_store as _ss  # harness sibling

        entries = _ss.read_index()
    except Exception:
        return []
    try:
        want = os.path.normcase(os.path.abspath(results_dir))
    except Exception:
        return []
    out: List[float] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        rd = e.get("results_dir")
        if not rd:
            continue
        try:
            if os.path.normcase(os.path.abspath(str(rd))) != want:
                continue
        except Exception:
            continue
        ts = e.get("timestamp")
        if isinstance(ts, (int, float)):
            out.append(float(ts))
    return out


def _config_flip_between(
    windows: List[Dict[str, Any]], prev_wi: Optional[int], wi: Optional[int]
) -> bool:
    """Code-verified config-lever flip: the harness stamps ``config_lever_hash``
    (a content hash over the non-volatile config keys) into every window row; a flip
    in the interval means some in-interval window's hash differs from the baseline
    (the last window at index <= prev_wi). No baseline (first interval) or missing
    hashes (old journals) ⇒ no flip evidence."""
    if prev_wi is None or wi is None:
        return False
    baseline: Optional[tuple] = None
    for w in windows:
        try:
            w_i = int(w.get("window_index"))
        except (TypeError, ValueError):
            continue
        h = w.get("config_lever_hash")
        if h and w_i <= prev_wi and (baseline is None or w_i >= baseline[0]):
            baseline = (w_i, h)
    if baseline is None:
        return False
    for w in windows:
        try:
            w_i = int(w.get("window_index"))
        except (TypeError, ValueError):
            continue
        if prev_wi < w_i <= wi:
            h = w.get("config_lever_hash")
            if h and h != baseline[1]:
                return True
    return False


def _termination_intervals(results_dir: str) -> List[Dict[str, Any]]:
    """Per-control-return interval detail for the VERIFIED termination streak.

    Intervals are delimited by consecutive type=="control_return" rows in
    interventions.jsonl (the rows keep their role as the agent's cadence marker and
    work-score carrier — but their stagnation_flag/intervened fields are now CLAIMS;
    code truth is derived from artifacts). Windows are matched by INDEX
    (prev.window_index < w.window_index <= row.window_index — windows.jsonl rows
    carry no timestamps); artifacts (discovery stubs, strategy deploys) by TIMESTAMP
    (prev.timestamp < ts <= row.timestamp — the agent writes its row AFTER acting).

    verified.stagnant  = the foundation-recomputed flag of the LAST window in the
                         interval, and the interval must contain >=1 window row (an
                         empty/duplicate interval never counts — no streak padding).
    verified.intervened = OR of the three code-verified artifact classes:
                          a strategy deploy attributed to this run, a usable
                          in-interval discovery stub (dr unconditional;
                          steered_analyst only with steering evidence), or a
                          config_lever_hash flip. Meta rounds and groundings are
                          naturally excluded (wrong kind / no artifact class).
    Rows missing window_index/timestamp verify as non-stagnant/non-intervened —
    fail toward keep-running (budget still bounds the run)."""
    rows = [r for r in read_interventions(results_dir) if r.get("type") == "control_return"]
    if not rows:
        return []
    windows = read_windows(results_dir)
    f_flags = foundation_stagnation_flags(results_dir)
    deploy_times = _strategy_deploy_times(results_dir)
    steering_rows = read_steering(results_dir)
    out: List[Dict[str, Any]] = []
    prev_ts = 0.0
    prev_wi: Optional[int] = None
    for r in rows:
        ts = r.get("timestamp")
        ts = float(ts) if isinstance(ts, (int, float)) else None
        try:
            wi: Optional[int] = int(r.get("window_index"))
        except (TypeError, ValueError):
            wi = None
        in_windows: List[int] = []
        if wi is not None:
            for w in windows:
                try:
                    w_i = int(w.get("window_index"))
                except (TypeError, ValueError):
                    continue
                if (prev_wi is None or w_i > prev_wi) and w_i <= wi:
                    in_windows.append(w_i)
        v_stag = bool(in_windows) and bool(f_flags.get(max(in_windows), False))
        if ts is not None:
            n_deploys = sum(1 for d in deploy_times if prev_ts < d <= ts)
            stubs = discovery_stubs_between(results_dir, prev_ts, ts, steering_rows=steering_rows)
        else:
            n_deploys, stubs = 0, []
        flip = _config_flip_between(windows, prev_wi, wi)
        v_int = bool(n_deploys) or bool(stubs) or flip
        claimed_int = r.get("intervened")
        if claimed_int is None:
            claimed_int = (float(r.get("work_audit", 0) or 0) > 0
                           or float(r.get("work_discovery", 0) or 0) > 0)
        c_stag, c_int = bool(r.get("stagnation_flag")), bool(claimed_int)
        out.append({
            "window_index": wi,
            "timestamp": ts,
            "claimed": {"stagnation_flag": c_stag, "intervened": c_int},
            "verified": {"stagnant": v_stag, "intervened": v_int},
            "evidence": {"deploys": n_deploys, "discovery_stubs": len(stubs),
                         "config_flip": bool(flip), "windows": len(in_windows)},
            "diverged": (c_stag != v_stag) or (c_int != v_int),
        })
        if ts is not None:
            prev_ts = ts
        if wi is not None:
            prev_wi = wi
    return out


def termination_report(results_dir: str) -> List[Dict[str, Any]]:
    """The per-interval claimed-vs-verified termination detail (CLI view
    ``termination_report``) — read this when your control_return rows and the
    verified streak disagree."""
    return _termination_intervals(results_dir)


def termination_streak(results_dir: str) -> int:
    """Count trailing consecutive control-return intervals that are BOTH stagnant AND
    intervened — VERIFIED FROM CODE ARTIFACTS, not from the agent's row fields.

    Stagnation truth: the foundation recompute over windows.jsonl best-score deltas
    with boot-frozen thresholds (see foundation_stagnation_flags). Intervention truth:
    a strategy deploy attributed to this run, a usable in-interval discovery stub
    (kind="dr" unconditional; kind="steered_analyst" only with recorded steering
    evidence — R2 is steering-only), or a config_lever_hash flip. The automatic meta
    round has no artifact class and never counts; a grounding alone (kind="grounding")
    is not a discovery-stub kind and never counts — it rides the discovery that
    produced its technique. The agent's control_return row remains the interval
    delimiter and work-score carrier, but its stagnation_flag/intervened fields are
    CLAIMS: divergence is surfaced (termination_report + one stderr warning per
    process) while code truth silently drives this number. N-in-a-row means the
    search could not escape verified stagnation despite a verified intervention at
    every return."""
    global _termination_divergence_warned
    intervals = _termination_intervals(results_dir)
    streak = 0
    scanned: List[Dict[str, Any]] = []
    for iv in reversed(intervals):
        scanned.append(iv)
        if iv["verified"]["stagnant"] and iv["verified"]["intervened"]:
            streak += 1
        else:
            break
    diverged = [iv for iv in scanned if iv.get("diverged")]
    if diverged and not _termination_divergence_warned:
        _termination_divergence_warned = True
        import sys as _sys

        idxs = [iv.get("window_index") for iv in diverged]
        print(
            f"[journal] termination: {len(diverged)} trailing control_return row(s) "
            f"(window_index {idxs}) diverge from code-verified truth — the verified "
            f"streak drives termination; inspect with the 'termination_report' view",
            file=_sys.stderr,
        )
    return streak


def read_calls(results_dir: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """The compact external-call pointer index (no big prompts). Optionally
    filter by kind ('meta' / 'dr' / 'steered_analyst' / 'grounding'). The two DISCOVERY-stub
    kinds the recency gate recognizes are {dr, steered_analyst} (R1 Azure deep research — the
    only AUTONOMOUS route — and the human-STEERED R2 steered-analyst subagent, valid only with
    recorded steering evidence); 'meta' is the automatic per-window round (not a discovery
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
    """The interval anchor for the discovery recency gate: the timestamp of the
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


def discovery_stubs_between(
    results_dir: str,
    lo_ts: float,
    hi_ts: Optional[float] = None,
    steering_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """The SHARED discovery-stub predicate — the one place that decides whether a
    calls.jsonl pointer is a valid discovery stub. Used by discovery_in_interval (the
    grounding gate: lo = last control_return boundary, hi = None) and by the verified
    termination intervals (lo/hi = consecutive control_return timestamps).

    A stub qualifies iff ALL of:
      - kind ∈ {"dr", "steered_analyst"} ('meta' is the automatic per-window round;
        'grounding' is a mutate.py self-log — neither is a discovery stub);
      - ``lo_ts < timestamp`` (STRICTLY greater) and, when hi_ts is given,
        ``timestamp <= hi_ts``;
      - USABLE: when the full detail file is readable and carries an explicit
        ``response.usable``, THAT flag alone decides (False disqualifies, True counts
        — even when the free-text pointer summary happens to mention a refusal in
        passing); the pointer-``summary`` substring screen ('refus'/'no usable'/
        'unusable') is only the FALLBACK for a stub whose detail is missing/unreadable
        or has no usable key; a stub with neither signal is treated as usable (fail
        OPEN — a legitimate stub is never silently dropped);
      - kind="steered_analyst" ONLY: recorded steering evidence must authorize it
        (R2 is STEERING-ONLY) — see _steer_validates_stub; this leg FAILS CLOSED
        (missing detail / no request.steer_id / no matching user_steer row / steer
        already consumed by a different stub ⇒ disqualified)."""
    stubs = read_calls(results_dir, kind="dr") + read_calls(results_dir, kind="steered_analyst")
    rows = steering_rows
    out: List[Dict[str, Any]] = []
    for s in stubs:
        ts = s.get("timestamp")
        if not isinstance(ts, (int, float)) or float(ts) <= lo_ts:
            continue  # stale (or undated) → not in this interval
        if hi_ts is not None and float(ts) > hi_ts:
            continue
        # The explicit response.usable flag in the full detail blob is AUTHORITATIVE —
        # R1/R2 write it deliberately, while the pointer summary is free text (a usable
        # stub may legitimately MENTION a refusal). Consult the detail first; fall back
        # to the summary substring screen only when the detail can't answer.
        usable: Optional[bool] = None
        file = s.get("file")
        if file:
            detail = read_call(results_dir, file)
            resp = detail.get("response") if isinstance(detail, dict) else None
            if isinstance(resp, dict) and "usable" in resp:
                usable = bool(resp.get("usable"))
        if usable is None:
            summary = str(s.get("summary") or "").strip().lower()
            usable = not (summary and ("refus" in summary or "no usable" in summary
                                       or "unusable" in summary))
        if not usable:
            continue
        if s.get("kind") == "steered_analyst":
            if rows is None:
                rows = read_steering(results_dir)
            if not _steer_validates_stub(results_dir, s, rows):
                continue
        out.append(s)
    return out


def discovery_in_interval(results_dir: str) -> List[Dict[str, Any]]:
    """The discovery recency gate — THE single source of truth for "is there a fresh, usable
    discovery this control-return interval?". Read-only.

    A *discovery round* (== "DR round") is a discovery pass via EXACTLY ONE OF R1 (Azure
    deep research, kind="dr" — the ONLY autonomous route, whole-task or sub-task scoped)
    OR a human-STEERED R2 (the steered-analyst subagent, kind="steered_analyst" — valid
    only with recorded, unreplayed steering evidence; see discovery_stubs_between).
    This returns the in-interval, USABLE discovery stubs; the caller (the PRIMARY
    spawn_island.py gate; the grounding-engineer subagent likewise refuses without it)
    fails CLOSED on an empty list — no in-interval discovery ⇒ grounding refused.

    In-interval iff ``stub.timestamp > boundary`` (STRICTLY greater), where boundary =
    the most-recent control_return row timestamp (0.0 ⇒ first interval). The usable /
    steering predicates live in discovery_stubs_between — the ONE shared place."""
    return discovery_stubs_between(results_dir, _control_return_boundary(results_dir))


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
    # The strategy history lives at strategy_store.history_dir() (the orchestrator tree, or
    # SHINKA_ORCH_HISTORY_DIR), NOT under results_dir — read the index from that real location
    # so the archive always includes the deploy/outcome audit trail.
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
        if view == "steering":
            return {"result": read_steering(rd)}
        if view == "pending_steering":
            return {"result": pending_steering(rd)}
        if view == "log_steering":
            row = log_steering(rd, payload["entry"])
            return {"logged": True, "steer_id": row["steer_id"]}
        if view == "consume_steering":
            row = consume_steering(
                rd, payload["steer_id"], payload["action"],
                payload.get("stub_file"), payload.get("note"),
            )
            return {"consumed": True, "steer_id": row["steer_id"], "action": row["action"]}
        if view == "termination_report":
            return {"result": termination_report(rd)}
        if view == "build_run_summary":
            return {"result": build_run_summary(rd)}
        if view == "finalize_run":
            # Precondition re-check + whitelist + evidence enforcement live in
            # finalize_run_checked / finalize_run (importable, tested directly).
            finalize_run_checked(rd, payload["status"], payload.get("summary"),
                                 payload.get("evidence"))
            return {"finalized": True, "status": payload["status"]}
        if view == "archive_run":
            dest = archive_run(rd, payload.get("dest_root", "orchestrator/run_archive"),
                               payload.get("run_id"), payload.get("finished_at"))
            return {"archived": True, "dest": dest}
        raise ValueError(f"unknown view: {view}")

    _common.run_main(main)

"""strategy_store.py — versioning + deploy/rollback for mutable strategy files.

This is the deterministic machinery behind the SKILL.md strategy-rewrite
protocol. The orchestrator (Claude) decides WHAT to rewrite and WHEN; this module
executes the bookkeeping so the audit trail is never lost:

  * ``snapshot(target)``      — hash scripts/<target>, copy it into
                                strategy_history/<hash>/ with a meta.json.
  * ``deploy(...)``           — snapshot the current file, copy the candidate over
                                scripts/<target>, append a "deployed" index entry.
  * ``rollback(target, hash)``— restore a prior snapshot over scripts/<target>,
                                append a "rolledback" index entry.
  * ``record_outcome(...)``   — attach the measured J and accept/reject to the
                                most recent deploy of a target.
  * ``read_index()``          — the full (strategy_hash, J, window, status) log.

strategy_history/ is append-only; nothing is ever deleted. This is what lets the
user debug a long run after the fact, and what the orchestrator reads to avoid
proposing a retread of a strategy that already failed.

MUTABILITY: harness plumbing. Not a strategy file; do not rewrite.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_HARNESS_DIR = Path(__file__).resolve().parent
_ORCH_DIR = _HARNESS_DIR.parent


# Directories are resolved lazily and may be overridden via env vars so tests
# (and the smoke test) can isolate strategy_history/ instead of mutating the
# real repo. Defaults point at the real orchestrator tree.
def scripts_dir() -> Path:
    return Path(os.environ.get("SHINKA_ORCH_SCRIPTS_DIR", _ORCH_DIR / "scripts"))


def history_dir() -> Path:
    return Path(os.environ.get("SHINKA_ORCH_HISTORY_DIR", _ORCH_DIR / "strategy_history"))


def index_path() -> Path:
    return history_dir() / "index.json"


def _now() -> float:
    return time.time()


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def read_index() -> List[Dict[str, Any]]:
    if not index_path().exists():
        return []
    try:
        return json.loads(index_path().read_text())
    except json.JSONDecodeError:
        # M21: a PRESENT-but-corrupt index must NOT silently read as [] — that erases the whole
        # deploy/outcome audit trail AND disarms the rejected-hash guard (deploy iterates this).
        # The atomic write below prevents self-corruption; an externally-corrupted index fails
        # LOUD (env override to force the old fail-open behavior).
        if os.environ.get("SHINKA_STRATEGY_INDEX_FAILOPEN"):
            return []
        raise RuntimeError(
            f"strategy index {index_path()} is present but unparseable (corruption?) — refusing "
            f"to read it as empty, which would disarm the rejected-hash guard. Set "
            f"SHINKA_STRATEGY_INDEX_FAILOPEN=1 to override."
        )


def _write_index(entries: List[Dict[str, Any]]) -> None:
    history_dir().mkdir(parents=True, exist_ok=True)
    # M21: atomic write (unique temp + os.replace) so a kill mid-write can't truncate the index
    # into the corrupt state read_index now refuses to read as empty.
    _p = index_path()
    _tmp = _p.with_suffix(_p.suffix + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    _tmp.write_text(json.dumps(entries, indent=2))
    os.replace(_tmp, _p)


def append_index(entry: Dict[str, Any]) -> None:
    entries = read_index()
    entries.append(entry)
    _write_index(entries)


def _hash_was_rejected(
    idx: List[Dict[str, Any]], target: str, cand_hash: str
) -> Optional[Dict[str, Any]]:
    """Return the index entry that REJECTED ``cand_hash`` for ``target`` (single OR bundle), or
    None. The 'don't retread a failed strategy' guard, shared by deploy() and deploy_bundle()
    (M19): a SINGLE deploy must also be blocked by a hash a BUNDLE outcome rejected for that
    target — otherwise the orchestrator could slip a known-bad version back in one file at a
    time. Checks both the single-entry shape (new_hash/target) and the bundle shape
    (new_hashes[target])."""
    for _e in idx:
        if _e.get("status") != "rejected":
            continue
        if _e.get("new_hash") == cand_hash and _e.get("target") == target:
            return _e
        if _e.get("type") == "bundle" and (_e.get("new_hashes") or {}).get(target) == cand_hash:
            return _e
    return None


def _assert_mutable(target: str) -> None:
    """E1/H10: refuse to snapshot/deploy/rollback a NON-mutable target. The rewrite
    protocol must NEVER touch a FOUNDATION file (the JSON contract, evaluator,
    diagnostics, journal, harness); an off-by-one in the orchestrator's reasoning would
    silently corrupt the contract AND make the corruption the restore point. Human
    override for tooling: SHINKA_ALLOW_FOUNDATION_WRITE."""
    if target in MUTABLE_TARGETS or os.environ.get("SHINKA_ALLOW_FOUNDATION_WRITE"):
        return
    raise PermissionError(
        f"refusing to write non-mutable strategy target {target!r}: not in MUTABLE_TARGETS "
        f"(set SHINKA_ALLOW_FOUNDATION_WRITE=1 to override for tooling)"
    )


def snapshot(target: str, reason: str = "snapshot") -> str:
    """Copy the CURRENT scripts/<target> into strategy_history/<hash>/. Idempotent.

    Returns the content hash (the snapshot directory name).
    """
    _assert_mutable(target)
    src = scripts_dir() / target
    if not src.exists():
        raise FileNotFoundError(f"strategy file not found: {src}")
    h = file_hash(src)
    snap_dir = history_dir() / h
    snap_dir.mkdir(parents=True, exist_ok=True)
    dst = snap_dir / target
    if not dst.exists():
        shutil.copy2(src, dst)
        (snap_dir / "meta.json").write_text(
            json.dumps(
                {
                    "hash": h,
                    "target": target,
                    "created_at": _now(),
                    "reason": reason,
                    "J": None,
                    "window_index": None,
                },
                indent=2,
            )
        )
    return h


def update_meta(hash_: str, **fields: Any) -> None:
    meta_path = history_dir() / hash_ / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.update(fields)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2))


def deploy(
    candidate_path: str,
    target: str,
    reason: str,
    window_index: Optional[int] = None,
    prior_J: Optional[float] = None,
    concern: Optional[str] = None,
    force: bool = False,
    results_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Snapshot the current strategy, then deploy ``candidate_path`` over it.

    Returns ``{prior_hash, new_hash}``. The new file's snapshot is also written
    so it can be restored if a *later* rewrite needs to roll back to it.
    ``concern`` records WHICH concern this rewrite targets (e.g. "prompt",
    "scoring") so the index narrates the change without a snapshot lookup (F4).
    ``force`` bypasses the rejected-hash guard (E6).
    """
    # E6 (rewrite F8): refuse to re-deploy a candidate whose content-hash a prior
    # outcome marked `rejected` for this target (the "don't retread a failed strategy"
    # invariant) — unless explicitly forced. Cheap guard over the append-only index.
    if not force:
        _cand_hash = file_hash(Path(candidate_path))
        _rej = _hash_was_rejected(read_index(), target, _cand_hash)  # M19: single OR bundle
        if _rej is not None:
            raise ValueError(
                f"candidate hash {_cand_hash[:8]} for {target} was REJECTED at window "
                f"{_rej.get('window_index')}; pass force=True to re-deploy anyway"
            )
    # P7-T1 + C1: snapshot the pre-deploy CODE first, then the run STATE (archive DB +
    # bandit + ledger), recording the code hash INTO the state snapshot so a regressing/
    # crashing measure window can be FULLY rewound — code AND state (ledger preserved).
    prior_hash = snapshot(target, reason="pre-deploy snapshot")
    state_snap_id = (
        snapshot_state(results_dir, label=reason, prior_code={target: prior_hash})
        if results_dir
        else None
    )
    # M22: a deploy with NO results_dir takes no STATE snapshot (archive/bandit/ledger), so it is
    # only CODE-revertible — a measure-window regression could not be fully rewound. Rather than
    # hard-require results_dir (which breaks smoke_test + bundle unit tests that deploy without a
    # run), WARN and stamp revertible:False so the audit trail is honest about what a later
    # rollback can actually restore.
    revertible = state_snap_id is not None
    if not revertible:
        import sys as _sys

        _sys.stderr.write(
            f"[strategy_store] WARNING: deploy of {target!r} has no results_dir → no state "
            f"snapshot; this deploy is CODE-revertible only (not a full code+state rewind). "
            f"Pass results_dir to make it fully revertible.\n"
        )
    dst = scripts_dir() / target
    shutil.copy2(candidate_path, dst)
    new_hash = snapshot(target, reason=reason)
    update_meta(new_hash, window_index=window_index, reason=reason)
    append_index(
        {
            "target": target,
            "concern": concern,
            "prior_hash": prior_hash,
            "new_hash": new_hash,
            "state_snap_id": state_snap_id,
            "revertible": revertible,  # M22: full code+state rewind possible?
            "reason": reason,
            "window_index": window_index,
            "prior_J": prior_J,
            "J": None,
            "status": "deployed",
            "timestamp": _now(),
        }
    )
    return {"prior_hash": prior_hash, "new_hash": new_hash, "state_snap_id": state_snap_id,
            "revertible": revertible}


def rollback(target: str, prior_hash: str, reason: str = "J regression") -> Dict[str, Any]:
    """Restore strategy_history/<prior_hash>/<target> over scripts/<target>."""
    _assert_mutable(target)
    snap = history_dir() / prior_hash / target
    if not snap.exists():
        raise FileNotFoundError(f"snapshot not found: {snap}")
    shutil.copy2(snap, scripts_dir() / target)
    append_index(
        {
            "target": target,
            "restored_hash": prior_hash,
            "reason": reason,
            "status": "rolledback",
            "timestamp": _now(),
        }
    )
    return {"restored_hash": prior_hash}


# ---------------------------------------------------------------------------
# Run-STATE snapshots (P7-T1): make a framework rewrite recoverable by snapshotting
# the archive DB + bandit + ledger before deploy, so a regressing/crashing measure
# window can be FULLY rewound — code AND state — except the cost ledger, which is
# never rewound (spend stays counted; a revert-and-retry can't exceed the budget).
# ---------------------------------------------------------------------------
def _prune_state_snapshots(keep: int) -> None:
    if keep <= 0:
        return
    # L60: never prune a state snapshot still referenced by an UNRESOLVED deploy (status
    # 'deployed' — its measure outcome has not been recorded yet). Pruning it would destroy the
    # ONLY revert point for a rewrite still under measurement, so a later regression verdict
    # could not be rolled back. Pinned snapshots are retained regardless of age/keep.
    pinned: set = set()
    try:
        for e in read_index():
            if e.get("status") == "deployed" and e.get("state_snap_id"):
                pinned.add(f"state_{e['state_snap_id']}")
    except Exception:
        pinned = set()
    states = sorted(history_dir().glob("state_*"), key=lambda p: p.stat().st_mtime)
    stale = states[:-keep] if keep < len(states) else []
    for old in stale:
        if old.name in pinned:
            continue  # L60: protect an unresolved deploy's revert point
        shutil.rmtree(old, ignore_errors=True)


def snapshot_state(results_dir: str, label: Optional[str] = None, keep: int = 5,
                   prior_code: Optional[Dict[str, str]] = None) -> str:
    """Snapshot run STATE (archive DB + bandit + ledger) into strategy_history/state_<id>/
    so a framework rewrite is recoverable. Copies programs.sqlite + bandit_state.pkl +
    journal/run.json (each only if present) AND records ``prior_code`` (the pre-deploy
    {target: code-hash} map) so restore_state can ALSO rewind the strategy .py — a FULL
    revert = code + DB + bandit. Operates on results_dir state files — NOT
    MUTABLE_TARGETS — so it does NOT route through _assert_mutable. Retains the last
    ``keep`` snapshots. Returns the snap id.

    PRECONDITION: call only when NO window subprocess is live — the measuring window runs
    as a separate, fully-exited subprocess, so the snapshot is taken either before
    launching it (pre-deploy) or after it has exited (pre-revert); no sqlite/pkl writer
    is active during the copy, so a half-written bandit_state.pkl cannot be captured."""
    snap_id = uuid.uuid4().hex[:12]
    dest = history_dir() / f"state_{snap_id}"
    dest.mkdir(parents=True, exist_ok=True)
    rd = Path(results_dir)
    # M20: DETECT a snapshot taken while a window is LIVE (the candidate loop mutates
    # programs.sqlite / bandit_state.pkl; run_window writes <results_dir>/.window_active for that
    # span). Snapshotting then risks capturing a half-written file as the restore point. The
    # rewrite protocol serializes deploy/measure/restore so this should never happen — so we WARN
    # loudly and FLAG the snapshot rather than silently trusting it (the env override REFUSES).
    window_live = (rd / ".window_active").exists()
    if window_live:
        import sys as _sys

        msg = (f"[strategy_store] WARNING: snapshot_state({results_dir}) taken while a window is "
               f"LIVE (.window_active present) — the captured programs.sqlite/bandit_state.pkl may "
               f"be mid-write. Snapshots must be taken between windows (deploy/measure/restore are "
               f"serialized).")
        if os.environ.get("SHINKA_REFUSE_SNAPSHOT_DURING_WINDOW"):
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(msg + " Refusing (SHINKA_REFUSE_SNAPSHOT_DURING_WINDOW set).")
        print(msg, file=_sys.stderr)
    for rel in ("programs.sqlite", "bandit_state.pkl", os.path.join("journal", "run.json")):
        src = rd / rel
        if src.exists():
            shutil.copy2(src, dest / Path(rel).name)
    (dest / "state_meta.json").write_text(json.dumps(
        {"snap_id": snap_id, "label": label, "created_at": _now(),
         "results_dir": str(results_dir),
         # M20: flag in the audit trail if this snapshot was taken during a live window.
         "window_active_at_snapshot": bool(window_live),
         # C1: {target: pre-deploy code-hash} so restore_state rewinds code too ({} for a bare snapshot).
         "prior_code": dict(prior_code or {})}, indent=2))
    _prune_state_snapshots(keep)
    return snap_id


def restore_state(results_dir: str, snap_id: str) -> Dict[str, Any]:
    """FULL rewind of a framework rewrite: restore the strategy CODE (scripts/<target>.py
    for every target captured at deploy via ``prior_code``) PLUS programs.sqlite (archive)
    + bandit_state.pkl (selector) byte-for-byte. NEVER rewinds the COST LEDGER — the LIVE
    total_cost is preserved; if the live run.json is unreadable at revert time the ledger is
    RECOMPUTED from the durable journal streams (which a revert never touches), then we stamp
    max(live-or-recomputed, snapshot) so spend can only stay flat or rise and a revert-and-
    retry can never exceed the budget (H10). Returns {restored, code_restored,
    total_cost_preserved}."""
    dest = history_dir() / f"state_{snap_id}"
    if not dest.exists():
        raise FileNotFoundError(f"state snapshot not found: {dest}")
    rd = Path(results_dir)
    live_run = rd / "journal" / "run.json"
    # Capture the LIVE spend BEFORE we overwrite run.json with the (older, lower) snapshot.
    live_total: Optional[float] = None
    if live_run.exists():
        try:
            live_total = float(json.loads(live_run.read_text()).get("total_cost", 0.0) or 0.0)
        except Exception:
            live_total = None
    # The snapshot's recorded total is a hard lower bound on the preserved ledger.
    snap_total = 0.0
    _snap_run = dest / "run.json"
    if _snap_run.exists():
        try:
            snap_total = float(json.loads(_snap_run.read_text()).get("total_cost", 0.0) or 0.0)
        except Exception:
            snap_total = 0.0
    # 1) Restore run STATE (archive DB + bandit + the ledger file). L66: a managed state file
    #    that did NOT exist at snapshot time but the measure window CREATED (e.g. a cold-start run
    #    had no bandit_state.pkl / no programs.sqlite yet) must be DELETED on revert — otherwise
    #    the selector / archive keeps measure-window-born state after a "full" rewind. The LEDGER
    #    (run.json) is exempt: it is never rewound (handled below), so we never delete it here.
    _LEDGER = "run.json"
    restored: List[str] = []
    removed: List[str] = []
    for name, rel in (("programs.sqlite", "programs.sqlite"),
                      ("bandit_state.pkl", "bandit_state.pkl"),
                      ("run.json", os.path.join("journal", "run.json"))):
        src = dest / name
        tgt = rd / rel
        if src.exists():
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tgt)
            restored.append(name)
        elif name != _LEDGER and tgt.exists():
            try:
                tgt.unlink()
                removed.append(name)
            except Exception:
                pass
    # 2) C1: restore the strategy CODE for every target captured at deploy. The pre-deploy
    #    snapshots already live under strategy_history/<hash>/; copy each back over scripts/.
    code_restored: List[str] = []
    prior_code: Dict[str, str] = {}
    _meta = dest / "state_meta.json"
    if _meta.exists():
        try:
            prior_code = dict((json.loads(_meta.read_text()).get("prior_code") or {}))
        except Exception:
            prior_code = {}
    for target, h in prior_code.items():
        try:
            _assert_mutable(target)
        except Exception:
            continue
        snap_file = history_dir() / h / target
        if snap_file.exists():
            shutil.copy2(snap_file, scripts_dir() / target)
            code_restored.append(target)
    # 3) H10: the ledger is NEVER rewound. If the live total was unreadable (missing/corrupt
    #    run.json), recompute it from the durable streams; then stamp the max of (live-or-
    #    recomputed, snapshot) so a corrupt run.json at revert can't silently lower the cap.
    if live_total is None:
        try:
            import sys as _sys
            if str(_HARNESS_DIR) not in _sys.path:
                _sys.path.insert(0, str(_HARNESS_DIR))
            import journal as _journal  # harness sibling
            live_total = float(_journal._recompute_total_cost(results_dir))
        except Exception:
            live_total = None
    _cands = [v for v in (live_total, snap_total) if v is not None]
    preserved = max(_cands) if _cands else None
    if preserved is not None:
        # L64: TOLERATE a corrupt/missing run.json READ (fall back to {}), but do NOT swallow a
        # WRITE failure. The old blanket try/except: pass meant a failed re-stamp left run.json at
        # the snapshot's (lower) total_cost — silently REWINDING the ledger, the exact invariant
        # this code exists to protect. A write failure now propagates so the caller sees it.
        try:
            run = json.loads(live_run.read_text()) if live_run.exists() else {}
            if not isinstance(run, dict):
                run = {}
        except Exception:
            run = {}
        run["total_cost"] = preserved
        run["restored_from_state"] = snap_id
        # L70 parity: unique temp name so concurrent writers can't clobber each other's temp.
        tmp = f"{live_run}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        live_run.parent.mkdir(parents=True, exist_ok=True)
        Path(tmp).write_text(json.dumps(run, indent=2, default=str))
        os.replace(tmp, live_run)
    return {"restored": restored, "removed": removed, "code_restored": code_restored,
            "total_cost_preserved": preserved}


# Compact set of measure-window signals worth pinning into the index entry so a
# later reader sees the outcome's evidence without re-reading windows.jsonl.
_MEASURE_KEYS = (
    "window_index", "delta", "J_score", "best_score_end", "novelty_acceptance_rate",
    "evaluation_failure_rate", "novelty_rejected_cost", "stagnation_flag", "threshold",
)


def _measure_summary(diag: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not diag:
        return None
    return {k: diag.get(k) for k in _MEASURE_KEYS if k in diag}


def record_outcome(
    new_hash: str,
    J: float,
    accepted: bool,
    decision: Optional[Dict[str, Any]] = None,
    measure_diagnostics: Optional[Dict[str, Any]] = None,
) -> None:
    """Attach the measured window J + accept/reject to the latest deploy of new_hash.

    ``decision`` (the ``rollback_decision`` result: regressed/reasons/signals) and a
    compact ``measure_diagnostics`` summary are stored on the entry so the index
    records WHY the rewrite was accepted/rejected and the evidence behind it (F4)."""
    entries = read_index()
    _found = False
    for entry in reversed(entries):
        if entry.get("new_hash") == new_hash and entry.get("status") == "deployed":
            entry["J"] = J
            entry["status"] = "accepted" if accepted else "rejected"
            entry["outcome_timestamp"] = _now()
            if decision is not None:
                entry["decision"] = decision
            ms = _measure_summary(measure_diagnostics)
            if ms is not None:
                entry["measure"] = ms
            _found = True
            break
    if not _found:
        # N14: an unmatched/typo'd hash must NOT silently no-op — that leaves the REAL deploy
        # stuck at status 'deployed' (so the rejected-hash guard never arms) and update_meta
        # would fabricate a phantom strategy_history/<bogus>/ dir. Raise so the caller fixes it.
        raise ValueError(
            f"record_outcome: no deployed index entry for hash {new_hash[:8]} — nothing to "
            f"record (refusing to fabricate a phantom history entry)."
        )
    _write_index(entries)
    update_meta(new_hash, J=J, accepted=accepted)


def current_hash(target: str) -> str:
    return file_hash(scripts_dir() / target)


# The full set of orchestrator-mutable strategy files (the SKILL "MUTABLE" rows).
# A fingerprint over ALL of them is what makes the per-window log self-contained:
# a single `strategy_hash` can't say which of these was active (F4).
MUTABLE_TARGETS = (
    "sample_parent.py",
    "novelty_check.py",
    "select_llm.py",
    "compute_reward.py",
    "record_policy.py",
    "stagnation_detector.py",
    "island_policy.py",
    # S1: cadence_policy.py is FOUNDATION (the wake-decay schedule + run termination are not
    # orchestrator-rewritable); it is intentionally NOT in MUTABLE_TARGETS, so snapshot()/deploy()
    # refuse it. Its knobs (early_phase_windows/base_low/termination_streak/…) are boot-only config.
    "construct_mutation_prompt.py",
    "mutate.py",
    "meta_summarize.py",
    "island_brief.py",  # M3: doc'd Mutable=Yes; must be deployable via the rewrite cycle
)


def current_fingerprint() -> Dict[str, str]:
    """{target: content-hash} over every mutable strategy file present.

    This is the self-contained pointer the harness stamps into each window's
    diagnostics + run.json: it pins the EXACT version of every mutable file that
    produced a window, and each hash resolves to a `strategy_history/<hash>/`
    snapshot holding that file's full content. Replaces the ambiguous single
    `strategy_hash` (F4)."""
    sd = scripts_dir()
    fp: Dict[str, str] = {}
    for target in MUTABLE_TARGETS:
        p = sd / target
        if p.exists():
            fp[target] = file_hash(p)
    return fp


# ---------------------------------------------------------------------------
# Concern bundles: atomically deploy/rollback a SET of related files together.
# This is how the orchestrator changes a whole concern (e.g. scoring =
# compute_reward + select_llm + sample_parent) in one compatible step, with a
# single deploy → measure → rollback cycle. Either all change or none does.
# ---------------------------------------------------------------------------
def deploy_bundle(
    changes: List[Dict[str, str]],
    reason: str,
    window_index: Optional[int] = None,
    prior_J: Optional[float] = None,
    concern: Optional[str] = None,
    force: bool = False,
    results_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """changes: [{"candidate_path":..., "target":...}, ...].

    Snapshots every target first (so the whole bundle can be restored), then
    deploys every candidate, then logs ONE bundle entry. Returns
    {prior_hashes, new_hashes, state_snap_id}.
    """
    # P7-T4: rejected-hash guard, parity with deploy() — refuse the bundle if ANY
    # target's candidate hash was previously REJECTED for that target (single or bundle
    # entry), unless explicitly forced. Runs BEFORE any snapshot/copy.
    if not force:
        idx = read_index()
        for ch in changes:
            _ch = file_hash(Path(ch["candidate_path"]))
            _rej = _hash_was_rejected(idx, ch["target"], _ch)  # M19: shared single-OR-bundle guard
            if _rej is not None:
                raise ValueError(
                    f"bundle candidate hash {_ch[:8]} for {ch['target']} was REJECTED "
                    f"at window {_rej.get('window_index')}; pass force=True to re-deploy")
    # C1: snapshot every target's pre-bundle CODE first, then the run STATE recording the
    # {target: hash} map, so restore_state(snap_id) rewinds the whole bundle's code too.
    prior_hashes: Dict[str, str] = {}
    for ch in changes:
        prior_hashes[ch["target"]] = snapshot(ch["target"], reason="pre-bundle snapshot")
    state_snap_id = (
        snapshot_state(results_dir, label=reason, prior_code=dict(prior_hashes))
        if results_dir
        else None
    )
    # M22: parity with deploy() — a bundle with no results_dir is code-revertible only.
    revertible = state_snap_id is not None
    if not revertible:
        import sys as _sys

        _sys.stderr.write(
            f"[strategy_store] WARNING: bundle deploy {[c['target'] for c in changes]} has no "
            f"results_dir → no state snapshot; CODE-revertible only. Pass results_dir for a full "
            f"code+state rewind.\n"
        )
    new_hashes: Dict[str, str] = {}
    _applied: List[str] = []
    try:
        for ch in changes:
            shutil.copy2(ch["candidate_path"], scripts_dir() / ch["target"])
            _applied.append(ch["target"])
            new_hashes[ch["target"]] = snapshot(ch["target"], reason=reason)
            update_meta(new_hashes[ch["target"]], window_index=window_index, reason=reason)
    except Exception:
        # E2/H11: a mid-bundle failure must leave scripts/ byte-identical to before (no
        # half-applied, incompatible concern). Restore every already-copied target from
        # its pre-bundle snapshot, then re-raise WITHOUT writing an index row (we never
        # reached append_index, so there is no misleading bundle entry / rollback handle).
        for _t in _applied:
            try:
                shutil.copy2(history_dir() / prior_hashes[_t] / _t, scripts_dir() / _t)
            except Exception:
                pass
        raise
    append_index(
        {
            "type": "bundle",
            "concern": concern,
            "targets": [ch["target"] for ch in changes],
            "prior_hashes": prior_hashes,
            "new_hashes": new_hashes,
            "state_snap_id": state_snap_id,
            "revertible": revertible,  # M22
            "reason": reason,
            "window_index": window_index,
            "prior_J": prior_J,
            "J": None,
            "status": "deployed",
            "timestamp": _now(),
        }
    )
    return {"prior_hashes": prior_hashes, "new_hashes": new_hashes,
            "state_snap_id": state_snap_id, "revertible": revertible}


def rollback_bundle(prior_hashes: Dict[str, str], reason: str = "J regression") -> Dict[str, Any]:
    """Restore every target in the bundle from its prior snapshot."""
    # L63: all-or-nothing — verify EVERY target is mutable AND its snapshot exists BEFORE copying
    # any. The old loop copied targets one-by-one and only raised when it HIT a missing snapshot,
    # leaving scripts/ half-restored (a partial, incompatible concern) on disk. Pre-check first.
    for target, h in prior_hashes.items():
        _assert_mutable(target)
        if not (history_dir() / h / target).exists():
            raise FileNotFoundError(
                f"bundle snapshot not found: {history_dir() / h / target} — refusing a "
                f"partial bundle restore (no file copied)"
            )
    for target, h in prior_hashes.items():
        shutil.copy2(history_dir() / h / target, scripts_dir() / target)
    append_index(
        {
            "type": "bundle",
            "restored_hashes": prior_hashes,
            "reason": reason,
            "status": "rolledback",
            "timestamp": _now(),
        }
    )
    return {"restored_hashes": prior_hashes}


def record_bundle_outcome(
    new_hashes: Dict[str, str],
    J: float,
    accepted: bool,
    decision: Optional[Dict[str, Any]] = None,
    measure_diagnostics: Optional[Dict[str, Any]] = None,
) -> None:
    """Attach measured J + accept/reject (+ decision/measure context, F4) to the
    latest matching bundle deploy."""
    entries = read_index()
    for entry in reversed(entries):
        if entry.get("type") == "bundle" and entry.get("new_hashes") == new_hashes and entry.get("status") == "deployed":
            entry["J"] = J
            entry["status"] = "accepted" if accepted else "rejected"
            entry["outcome_timestamp"] = _now()
            if decision is not None:
                entry["decision"] = decision
            ms = _measure_summary(measure_diagnostics)
            if ms is not None:
                entry["measure"] = ms
            break
    _write_index(entries)

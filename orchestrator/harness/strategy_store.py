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
        return []


def _write_index(entries: List[Dict[str, Any]]) -> None:
    history_dir().mkdir(parents=True, exist_ok=True)
    index_path().write_text(json.dumps(entries, indent=2))


def append_index(entry: Dict[str, Any]) -> None:
    entries = read_index()
    entries.append(entry)
    _write_index(entries)


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
        for _e in read_index():
            if (
                _e.get("new_hash") == _cand_hash
                and _e.get("target") == target
                and _e.get("status") == "rejected"
            ):
                raise ValueError(
                    f"candidate hash {_cand_hash[:8]} for {target} was REJECTED at window "
                    f"{_e.get('window_index')}; pass force=True to re-deploy anyway"
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
            "reason": reason,
            "window_index": window_index,
            "prior_J": prior_J,
            "J": None,
            "status": "deployed",
            "timestamp": _now(),
        }
    )
    return {"prior_hash": prior_hash, "new_hash": new_hash, "state_snap_id": state_snap_id}


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
    states = sorted(history_dir().glob("state_*"), key=lambda p: p.stat().st_mtime)
    for old in states[:-keep]:
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
    for rel in ("programs.sqlite", "bandit_state.pkl", os.path.join("journal", "run.json")):
        src = rd / rel
        if src.exists():
            shutil.copy2(src, dest / Path(rel).name)
    (dest / "state_meta.json").write_text(json.dumps(
        {"snap_id": snap_id, "label": label, "created_at": _now(),
         "results_dir": str(results_dir),
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
    # 1) Restore run STATE (archive DB + bandit + the ledger file).
    restored: List[str] = []
    for name, rel in (("programs.sqlite", "programs.sqlite"),
                      ("bandit_state.pkl", "bandit_state.pkl"),
                      ("run.json", os.path.join("journal", "run.json"))):
        src = dest / name
        if src.exists():
            tgt = rd / rel
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tgt)
            restored.append(name)
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
        try:
            run = json.loads(live_run.read_text()) if live_run.exists() else {}
            if not isinstance(run, dict):
                run = {}
            run["total_cost"] = preserved
            run["restored_from_state"] = snap_id
            tmp = str(live_run) + ".tmp"
            live_run.parent.mkdir(parents=True, exist_ok=True)
            Path(tmp).write_text(json.dumps(run, indent=2, default=str))
            os.replace(tmp, live_run)
        except Exception:
            pass
    return {"restored": restored, "code_restored": code_restored,
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
            break
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
    "cadence_policy.py",
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
            for _e in idx:
                _rej_single = (_e.get("new_hash") == _ch and _e.get("target") == ch["target"]
                               and _e.get("status") == "rejected")
                _rej_bundle = (_e.get("type") == "bundle" and _e.get("status") == "rejected"
                               and (_e.get("new_hashes") or {}).get(ch["target"]) == _ch)
                if _rej_single or _rej_bundle:
                    raise ValueError(
                        f"bundle candidate hash {_ch[:8]} for {ch['target']} was REJECTED "
                        f"at window {_e.get('window_index')}; pass force=True to re-deploy")
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
            "reason": reason,
            "window_index": window_index,
            "prior_J": prior_J,
            "J": None,
            "status": "deployed",
            "timestamp": _now(),
        }
    )
    return {"prior_hashes": prior_hashes, "new_hashes": new_hashes, "state_snap_id": state_snap_id}


def rollback_bundle(prior_hashes: Dict[str, str], reason: str = "J regression") -> Dict[str, Any]:
    """Restore every target in the bundle from its prior snapshot."""
    for target, h in prior_hashes.items():
        _assert_mutable(target)
        snap = history_dir() / h / target
        if not snap.exists():
            raise FileNotFoundError(f"bundle snapshot not found: {snap}")
        shutil.copy2(snap, scripts_dir() / target)
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

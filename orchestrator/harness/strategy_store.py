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


def snapshot(target: str, reason: str = "snapshot") -> str:
    """Copy the CURRENT scripts/<target> into strategy_history/<hash>/. Idempotent.

    Returns the content hash (the snapshot directory name).
    """
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
) -> Dict[str, Any]:
    """Snapshot the current strategy, then deploy ``candidate_path`` over it.

    Returns ``{prior_hash, new_hash}``. The new file's snapshot is also written
    so it can be restored if a *later* rewrite needs to roll back to it.
    """
    prior_hash = snapshot(target, reason="pre-deploy snapshot")
    dst = scripts_dir() / target
    shutil.copy2(candidate_path, dst)
    new_hash = snapshot(target, reason=reason)
    update_meta(new_hash, window_index=window_index, reason=reason)
    append_index(
        {
            "target": target,
            "prior_hash": prior_hash,
            "new_hash": new_hash,
            "reason": reason,
            "window_index": window_index,
            "prior_J": prior_J,
            "J": None,
            "status": "deployed",
            "timestamp": _now(),
        }
    )
    return {"prior_hash": prior_hash, "new_hash": new_hash}


def rollback(target: str, prior_hash: str, reason: str = "J regression") -> Dict[str, Any]:
    """Restore strategy_history/<prior_hash>/<target> over scripts/<target>."""
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


def record_outcome(new_hash: str, J: float, accepted: bool) -> None:
    """Attach the measured window J + accept/reject to the latest deploy of new_hash."""
    entries = read_index()
    for entry in reversed(entries):
        if entry.get("new_hash") == new_hash and entry.get("status") == "deployed":
            entry["J"] = J
            entry["status"] = "accepted" if accepted else "rejected"
            entry["outcome_timestamp"] = _now()
            break
    _write_index(entries)
    update_meta(new_hash, J=J, accepted=accepted)


def current_hash(target: str) -> str:
    return file_hash(scripts_dir() / target)


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
) -> Dict[str, Any]:
    """changes: [{"candidate_path":..., "target":...}, ...].

    Snapshots every target first (so the whole bundle can be restored), then
    deploys every candidate, then logs ONE bundle entry. Returns
    {prior_hashes, new_hashes} (both target->hash dicts).
    """
    prior_hashes: Dict[str, str] = {}
    for ch in changes:
        prior_hashes[ch["target"]] = snapshot(ch["target"], reason="pre-bundle snapshot")
    new_hashes: Dict[str, str] = {}
    for ch in changes:
        shutil.copy2(ch["candidate_path"], scripts_dir() / ch["target"])
        new_hashes[ch["target"]] = snapshot(ch["target"], reason=reason)
        update_meta(new_hashes[ch["target"]], window_index=window_index, reason=reason)
    append_index(
        {
            "type": "bundle",
            "targets": [ch["target"] for ch in changes],
            "prior_hashes": prior_hashes,
            "new_hashes": new_hashes,
            "reason": reason,
            "window_index": window_index,
            "prior_J": prior_J,
            "J": None,
            "status": "deployed",
            "timestamp": _now(),
        }
    )
    return {"prior_hashes": prior_hashes, "new_hashes": new_hashes}


def rollback_bundle(prior_hashes: Dict[str, str], reason: str = "J regression") -> Dict[str, Any]:
    """Restore every target in the bundle from its prior snapshot."""
    for target, h in prior_hashes.items():
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


def record_bundle_outcome(new_hashes: Dict[str, str], J: float, accepted: bool) -> None:
    """Attach measured J + accept/reject to the latest matching bundle deploy."""
    entries = read_index()
    for entry in reversed(entries):
        if entry.get("type") == "bundle" and entry.get("new_hashes") == new_hashes and entry.get("status") == "deployed":
            entry["J"] = J
            entry["status"] = "accepted" if accepted else "rejected"
            entry["outcome_timestamp"] = _now()
            break
    _write_index(entries)

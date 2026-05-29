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

`strategy_history/` (separate) holds the per-strategy-version snapshots. Together
they let the orchestrator zoom from "how's the run overall" → "what did window 37
look like" → "every reward-related intervention" → "is island 2 dying."

MUTABILITY: harness plumbing. Not a strategy file; do not rewrite.
"""

from __future__ import annotations

import json
import os
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
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# --- writers ---------------------------------------------------------------
def init_run(results_dir: str, meta: Dict[str, Any]) -> None:
    """Create run.json on first window if absent (idempotent)."""
    _ensure(results_dir)
    if os.path.exists(_run_path(results_dir)):
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
    with open(_run_path(results_dir), "w") as f:
        json.dump(run, f, indent=2)


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
    with open(_run_path(results_dir), "w") as f:
        json.dump(run, f, indent=2)


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
    os.makedirs(journal_dir(results_dir), exist_ok=True)
    with open(_run_path(results_dir), "w") as f:
        json.dump(run, f, indent=2)
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
    with open(fpath, "w") as f:
        json.dump(
            {"kind": kind, "timestamp": ts, "cost": float(cost or 0.0),
             "request": request, "response": response},
            f, indent=2, default=str,
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
    os.makedirs(journal_dir(results_dir), exist_ok=True)
    with open(_run_path(results_dir), "w") as f:
        json.dump(run, f, indent=2)


# --- readers (multi-granularity) -------------------------------------------
def read_run(results_dir: str) -> Dict[str, Any]:
    p = _run_path(results_dir)
    if not os.path.exists(p):
        return {}
    try:
        return json.loads(open(p).read())
    except json.JSONDecodeError:
        return {}


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


def read_calls(results_dir: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """WS7: the compact external-call pointer index (no big prompts). Optionally
    filter by kind ('meta' / 'dr'). Open a specific call's full detail with
    ``read_call(results_dir, row['file'])``."""
    rows = _read_jsonl(os.path.join(journal_dir(results_dir), "calls.jsonl"))
    return [r for r in rows if (kind is None or r.get("kind") == kind)]


def read_call(results_dir: str, file: str) -> Dict[str, Any]:
    """Read one full call-detail file (the {request, response}) by its pointer
    ``file`` (relative to journal/, as stored in calls.jsonl)."""
    p = os.path.join(journal_dir(results_dir), file)
    if not os.path.exists(p):
        return {}
    try:
        return json.loads(open(p).read())
    except json.JSONDecodeError:
        return {}


def read_island(results_dir: str, island_id: int) -> List[Dict[str, Any]]:
    return _read_jsonl(
        os.path.join(journal_dir(results_dir), "islands", f"island_{island_id}.jsonl")
    )


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
    lines.append(f"- windows completed: {run.get('windows_completed')}")
    lines.append(f"- best score: {run.get('best_score')}")
    lines.append(f"- total programs: {run.get('total_programs')}")
    lines.append("")
    lines.append("## J trajectory (window: J / best / stagnation)")
    for w in traj:
        lines.append(
            f"- w{w['window_index']}: J={w['J']:.4f} best={w['best']} stagnant={w['stagnation']}"
            if isinstance(w.get("J"), (int, float))
            else f"- w{w['window_index']}: {w}"
        )
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
    lines.append("## Recommended framework changes (out of orchestrator scope)")
    lines.append(
        "_(orchestrator: foundation ideas you could not act on — sqlite schema, "
        "the JSON contract, new primitives, evaluator changes — for a human pass "
        "between runs)_"
    )
    return "\n".join(lines)


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
        raise ValueError(f"unknown view: {view}")

    _common.run_main(main)

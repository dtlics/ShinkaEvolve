"""Shared per-generation state for shinka agent tools.

A single ``ShinkaToolContext`` instance is constructed per agent run
(per generation) and passed to ``Runner.run(..., context=ctx)``. Every
tool receives it via the ``RunContextWrapper`` first parameter; tools
mutate fields that need to evolve across the loop (notably
``current_code`` after a successful ``apply_patch_tool`` call).

Design notes
------------
- Mutable fields live in the dataclass directly rather than in a
  separate state object so callers don't have to worry about pointer
  aliasing across tool invocations.
- Path-shaped fields (``patch_dir``, ``eval_program_path``,
  ``tool_root_dir``, ``db_path``) accept ``str`` rather than
  ``pathlib.Path`` to stay aligned with the rest of the shinka
  codebase, which generally passes ``str`` to OS calls.
- The context is **not** serialized into the agent's prompt — tools
  read from it on each call. Telemetry callers (Phase B+) may read
  ``tool_call_trace`` at the end of a run to record per-tool
  latencies and costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


DEFAULT_EVAL_TIMEOUT_SEC = 600
DEFAULT_PROBE_TIMEOUT_SEC = 30
DEFAULT_READ_FILE_MAX_BYTES = 64 * 1024  # 64 KB


@dataclass
class ShinkaToolContext:
    """State threaded through every shinka tool invocation in a run.

    Required fields
    ---------------
    patch_dir
        Directory the agent writes the in-progress patched program
        to (the evaluator reads from here). Created by the
        orchestrator before the run starts.
    parent_code
        The parent program's source code, used to seed
        ``current_code`` and to give tools (e.g. apply_patch) a
        baseline to diff against.

    Optional fields with sensible defaults
    --------------------------------------
    current_code
        The currently-applied code. Initialized to ``parent_code``
        and mutated by ``apply_patch_tool``. ``evaluate_tool``
        operates against the disk file (``patch_dir / evolve.<ext>``)
        which ``apply_patch_tool`` is responsible for writing.
    language
        Programming language of the program — one of ``python``,
        ``cpp``, ``julia``, etc. Drives patch-application logic and
        evaluator launch.
    eval_program_path
        Path to the task's ``evaluate.py`` module. ``evaluate_tool``
        invokes its experiment function against the patched program.
    experiment_fn_name
        Function name to call inside ``evaluate.py``. Default
        ``"main"`` matches shinka's existing convention.
    eval_results_dir
        Where ``evaluate_tool`` writes ``metrics.json`` and
        ``correct.json``. Defaults to ``patch_dir``.
    eval_timeout_sec
        Hard cap per evaluator invocation. Mirrors the existing
        ``shinka.cli.run`` timeout knob.
    db_path
        Path to ``evolution_db.sqlite`` for the ``query_evolution_db``
        tool. ``None`` disables that tool.
    tool_root_dir
        Sandbox root for ``read_host_file`` and ``run_probe``.
        Defaults to ``patch_dir`` if unset.
    read_file_max_bytes
        Truncation cap for ``read_host_file_tool`` results.
    probe_timeout_sec
        Per-probe wall-clock cap for ``run_probe_tool`` (Phase C+).

    Telemetry
    ---------
    tool_call_trace
        Tools append a small dict per invocation (name, latency,
        success, optional cost). Orchestrator reads it after the run
        to persist into the DB row's ``metadata``. Don't mutate
        externally.
    """

    # Required
    patch_dir: str
    parent_code: str

    # Code state (mutated by apply_patch_tool)
    current_code: str = ""
    language: str = "python"

    # Evaluator
    eval_program_path: Optional[str] = None
    experiment_fn_name: str = "main"
    eval_results_dir: Optional[str] = None
    eval_timeout_sec: int = DEFAULT_EVAL_TIMEOUT_SEC

    # Database read-access
    db_path: Optional[str] = None

    # Filesystem sandbox
    tool_root_dir: Optional[str] = None
    read_file_max_bytes: int = DEFAULT_READ_FILE_MAX_BYTES

    # Probe
    probe_timeout_sec: int = DEFAULT_PROBE_TIMEOUT_SEC

    # Telemetry, mutated by tool wrappers
    tool_call_trace: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Seed current_code from parent_code if the caller didn't
        # provide it explicitly. Avoids the common bug where a tool
        # is invoked before any apply_patch and sees empty code.
        if not self.current_code:
            self.current_code = self.parent_code
        # tool_root_dir defaults to patch_dir; keeps the sandbox
        # narrow by default.
        if self.tool_root_dir is None:
            self.tool_root_dir = self.patch_dir
        # eval_results_dir defaults to patch_dir; the evaluator's
        # JSON output lands alongside the patched program.
        if self.eval_results_dir is None:
            self.eval_results_dir = self.patch_dir

    # Convenience accessor used by tool wrappers when recording
    # telemetry. Kept here so the format is consistent across tools.
    def record_tool_call(
        self,
        name: str,
        latency_sec: float,
        success: bool,
        *,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry: Dict[str, Any] = {
            "name": name,
            "latency_sec": round(latency_sec, 4),
            "success": success,
        }
        if error:
            entry["error"] = error
        if extra:
            entry.update(extra)
        self.tool_call_trace.append(entry)

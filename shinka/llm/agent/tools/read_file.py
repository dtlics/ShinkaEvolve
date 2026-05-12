"""``read_host_file_tool`` — sandboxed file read.

Lets the agent peek at files in the host codebase (e.g. another module
in the task directory, an existing library file, a config) that the
prompt didn't include. Bounded by:

* ``ctx.tool_root_dir`` — sandbox root. Reads outside this root are
  refused. Defaults to ``ctx.patch_dir`` (very narrow); the
  orchestrator widens it per task as needed.
* ``ctx.read_file_max_bytes`` — truncation cap on the returned
  content (default 64 KB).
* Symlink resolution — both the root and the candidate path are
  resolved via ``Path.resolve(strict=False)`` before the containment
  check, so a symlink inside the root that points outside cannot
  escape.

The agent gets back either the file contents (truncated and
annotated if it was long) or an error string. We always include the
resolved path in the response so the agent can confirm what it
actually read.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from agents import RunContextWrapper, function_tool

from .context import ShinkaToolContext
from .registry import register_tool

logger = logging.getLogger(__name__)


def _resolve_under_root(root: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` against ``root`` and confirm it stays
    inside. Raises ``ValueError`` on escape attempts."""
    # If candidate is absolute, use it as-is; otherwise interpret it
    # relative to the root.
    if candidate.is_absolute():
        target = candidate
    else:
        target = root / candidate

    # strict=False so we can still report a clean error when the
    # file doesn't exist; the OS-level error from open() is reserved
    # for genuinely missing files.
    resolved_target = target.resolve(strict=False)
    resolved_root = root.resolve(strict=False)

    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"Path {str(candidate)!r} resolves to {str(resolved_target)!r}, "
            f"which is outside the sandbox root {str(resolved_root)!r}."
        ) from exc

    return resolved_target


def _read_file_sync(path: Path, max_bytes: int) -> tuple[str, bool]:
    """Read up to ``max_bytes`` of ``path`` as UTF-8 (errors='replace').

    Returns ``(text, was_truncated)``. ``was_truncated`` is True if
    the file was longer than ``max_bytes``.
    """
    file_size = path.stat().st_size
    truncated = file_size > max_bytes
    with path.open("rb") as fh:
        data = fh.read(max_bytes)
    text = data.decode("utf-8", errors="replace")
    return text, truncated


async def _read_host_file_impl(
    state: ShinkaToolContext,
    path: str,
    max_bytes: int = 0,
) -> str:
    """Pure tool body. ``max_bytes <= 0`` falls back to the context
    default. Telemetry is owned by ``ShinkaAgentHooks``; per-call
    structured data (resolved path, bytes_returned, truncated) goes on
    ``state.last_tool_extras``."""
    root_str = state.tool_root_dir
    if not root_str:
        return "Error: tool_root_dir is not set; read_host_file is disabled."

    effective_max = max_bytes if max_bytes and max_bytes > 0 else state.read_file_max_bytes
    # Hard cap regardless of agent request to prevent unbounded
    # context blowup.
    effective_max = max(1, min(effective_max, state.read_file_max_bytes))

    root = Path(root_str)

    try:
        resolved = _resolve_under_root(root, Path(path))
    except ValueError as exc:
        return f"Error: {exc}"

    if not resolved.exists():
        return f"Error: File not found: {resolved}"

    if resolved.is_dir():
        return f"Error: Path is a directory, not a file: {resolved}"

    try:
        text, truncated = await asyncio.to_thread(
            _read_file_sync, resolved, effective_max
        )
    except OSError as exc:
        logger.info("read_host_file OSError: %s", exc)
        return f"Error: {exc}"

    state.last_tool_extras = {
        "path": str(resolved),
        "bytes_returned": len(text),
        "truncated": truncated,
    }

    suffix = (
        f"\n\n...(truncated at {effective_max} bytes)"
        if truncated
        else ""
    )
    return f"OK: path={resolved}\n---\n{text}{suffix}"


@function_tool
async def _read_host_file_tool(
    ctx: RunContextWrapper[ShinkaToolContext],
    path: str,
    max_bytes: int = 0,
) -> str:
    """Read a file from the host codebase inside the run's sandbox.

    Use this to inspect code, config, or notes that aren't part of
    your initial context — e.g. another module in the task
    directory, the evaluate.py source, or an adjacent helper.

    Args:
        path: Path to read. Absolute paths must be inside the
            sandbox root; relative paths are resolved against the
            root.
        max_bytes: Optional truncation cap. Defaults to the run's
            configured ``read_file_max_bytes`` (64 KB by default).
            Values larger than the configured cap are clamped down.

    Returns:
        ``"OK: path=...\\n---\\n<contents>"`` on success (with
        ``...(truncated at N bytes)`` appended if the file was
        longer than the cap), or ``"Error: <message>"`` for path
        outside sandbox, file not found, directory, permission
        denied, etc.
    """
    return await _read_host_file_impl(ctx.context, path, max_bytes)


def make_read_host_file_tool(ctx: ShinkaToolContext) -> Any:
    return _read_host_file_tool


register_tool("read_host_file", make_read_host_file_tool)

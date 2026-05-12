"""Tests for ``read_host_file_tool``.

Exercises the sandbox containment logic with real filesystem paths
via ``tmp_path``, since symlink resolution is the whole point and
mocking ``Path.resolve`` would defeat the test.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from shinka.llm.agent.tools import ShinkaToolContext
from shinka.llm.agent.tools.read_file import (
    _read_host_file_impl,
    _read_host_file_tool,
)


def _ctx(tool_root: Path, max_bytes: int = 1024) -> ShinkaToolContext:
    return ShinkaToolContext(
        patch_dir=str(tool_root),
        parent_code="",
        tool_root_dir=str(tool_root),
        read_file_max_bytes=max_bytes,
    )


def test_reads_file_inside_sandbox(tmp_path: Path) -> None:
    file_path = tmp_path / "hello.txt"
    file_path.write_text("hello world", encoding="utf-8")
    state = _ctx(tmp_path)

    result = asyncio.run(_read_host_file_impl(state, str(file_path)))

    assert result.startswith("OK: path=")
    assert "hello world" in result
    trace = state.tool_call_trace[0]
    assert trace["success"] is True
    assert trace["bytes_returned"] == 11
    assert trace["truncated"] is False


def test_accepts_relative_path_resolved_against_root(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.txt").write_text("inner", encoding="utf-8")
    state = _ctx(tmp_path)

    result = asyncio.run(_read_host_file_impl(state, "sub/x.txt"))
    assert "inner" in result


def test_truncates_when_file_exceeds_max_bytes(tmp_path: Path) -> None:
    big_file = tmp_path / "big.txt"
    big_file.write_text("x" * 10000, encoding="utf-8")
    state = _ctx(tmp_path, max_bytes=100)

    result = asyncio.run(_read_host_file_impl(state, str(big_file)))
    assert "(truncated at 100 bytes)" in result
    trace = state.tool_call_trace[0]
    assert trace["truncated"] is True
    assert trace["bytes_returned"] == 100


def test_agent_requested_max_bytes_clamped_to_context_cap(
    tmp_path: Path,
) -> None:
    """The agent shouldn't be able to widen the cap beyond what
    the orchestrator configured. Larger requests get clamped down."""
    big_file = tmp_path / "big.txt"
    big_file.write_text("y" * 5000, encoding="utf-8")
    state = _ctx(tmp_path, max_bytes=200)

    result = asyncio.run(
        _read_host_file_impl(state, str(big_file), max_bytes=10000)
    )
    # Truncated at 200, not 10000.
    assert "(truncated at 200 bytes)" in result
    assert state.tool_call_trace[0]["bytes_returned"] == 200


def test_rejects_absolute_path_outside_sandbox(tmp_path: Path) -> None:
    """An absolute path to /etc/passwd (or similar) must be refused."""
    state = _ctx(tmp_path)

    # /etc exists on macOS/Linux and is definitely outside tmp_path.
    result = asyncio.run(_read_host_file_impl(state, "/etc/passwd"))

    assert result.startswith("Error:")
    assert "outside the sandbox root" in result
    assert state.tool_call_trace[0]["success"] is False


def test_rejects_dot_dot_escape(tmp_path: Path) -> None:
    """``../`` traversal must be caught by the resolve+containment
    check."""
    inner = tmp_path / "sandbox"
    inner.mkdir()
    outer_file = tmp_path / "outside.txt"
    outer_file.write_text("secret", encoding="utf-8")

    state = ShinkaToolContext(
        patch_dir=str(inner),
        parent_code="",
        tool_root_dir=str(inner),
    )

    # Path inside sandbox that uses .. to escape.
    escape_path = inner / ".." / "outside.txt"
    result = asyncio.run(_read_host_file_impl(state, str(escape_path)))
    assert result.startswith("Error:")
    assert "outside the sandbox root" in result


def test_rejects_symlink_that_points_outside_sandbox(
    tmp_path: Path,
) -> None:
    """A symlink inside the sandbox that points outside should be
    refused. Symlink escape is the classic sandbox bypass — we
    resolve before the containment check."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    symlink = sandbox / "escape.txt"
    try:
        os.symlink(outside, symlink)
    except OSError:
        pytest.skip("Symlink creation not supported on this platform.")

    state = ShinkaToolContext(
        patch_dir=str(sandbox),
        parent_code="",
        tool_root_dir=str(sandbox),
    )

    result = asyncio.run(_read_host_file_impl(state, str(symlink)))
    assert result.startswith("Error:")
    assert "outside the sandbox root" in result


def test_missing_file_returns_clean_error(tmp_path: Path) -> None:
    state = _ctx(tmp_path)
    result = asyncio.run(_read_host_file_impl(state, "does_not_exist.txt"))
    assert result.startswith("Error: File not found")


def test_directory_returns_clean_error(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    state = _ctx(tmp_path)
    result = asyncio.run(_read_host_file_impl(state, "subdir"))
    assert result.startswith("Error:")
    assert "directory, not a file" in result


def test_unset_tool_root_dir_disables_tool() -> None:
    state = ShinkaToolContext(
        patch_dir="/tmp/x", parent_code="", tool_root_dir=""
    )
    # __post_init__ sets tool_root_dir = patch_dir when None, but the
    # caller could explicitly clear it to disable the tool.
    state.tool_root_dir = None  # type: ignore[assignment]
    result = asyncio.run(_read_host_file_impl(state, "x.txt"))
    assert result.startswith("Error:")
    assert "disabled" in result


def test_binary_file_decoded_with_replace(tmp_path: Path) -> None:
    """Binary content shouldn't crash the tool — we use
    errors='replace' so the agent at least gets *something*."""
    p = tmp_path / "binary.bin"
    p.write_bytes(b"abc\x00\xff\xfe\xfddef")
    state = _ctx(tmp_path)

    result = asyncio.run(_read_host_file_impl(state, str(p)))
    assert result.startswith("OK:")
    # "abc" and "def" survive intact; the invalid utf-8 bytes get
    # replaced with U+FFFD.
    assert "abc" in result
    assert "def" in result


def test_decorated_tool_registered() -> None:
    from shinka.llm.agent.tools import available_tool_names, select_shinka_tools

    assert "read_host_file" in available_tool_names()
    ctx = ShinkaToolContext(patch_dir="/tmp/x", parent_code="")
    selected = select_shinka_tools(["read_host_file"], ctx)
    assert selected == [_read_host_file_tool]


def test_tool_schema_has_expected_args() -> None:
    schema = _read_host_file_tool.params_json_schema
    properties = schema.get("properties", {})
    assert "path" in properties
    assert "max_bytes" in properties
    assert "ctx" not in properties

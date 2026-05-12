"""SDK-native lifecycle hooks for shinka tool telemetry.

The OpenAI Agents SDK exposes ``on_tool_start`` / ``on_tool_end`` /
``on_llm_*`` / ``on_end`` callbacks. We use them to record per-tool
latency + success into ``ShinkaToolContext.tool_call_trace``, replacing
the manual ``record_tool_call`` plumbing each tool wrapper used to do.

Tools that want to surface structured per-call data (e.g.
``apply_patch``'s ``num_applied`` and ``patch_type``) set
``ctx.last_tool_extras`` before returning; the hook merges that into
the trace entry produced for ``on_tool_end``. ``ctx.last_tool_extras``
is cleared on every ``on_tool_start`` so stale data from a previous
tool can't leak forward.

Success is inferred from the tool's return-value prefix:
- ``"OK..."`` → success
- ``"Error: ..."`` or ``"FAILED: ..."`` → failure (the first line of
  the message is recorded as the error field)

Tools that don't fit this convention can still write directly to
``ctx.tool_call_trace`` via ``ctx.record_tool_call`` — both paths are
supported. The hooks layer is purely additive when a tool sets
``last_tool_extras=None``.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Deque, Tuple

from agents import AgentHooks

from .tools.context import ShinkaToolContext

logger = logging.getLogger(__name__)


class ShinkaAgentHooks(AgentHooks):
    """Record per-tool latency + success into the run's tool_call_trace.

    Sequential tools within a single agent run are matched via a FIFO
    of in-flight (tool_name, start_monotonic) tuples. Parallel tool
    invocations (rare in shinka but supported by the SDK) are still
    matched FIFO-by-arrival, which gives approximate latencies — good
    enough for downstream telemetry.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_flight: Deque[Tuple[str, float]] = deque()

    async def on_tool_start(
        self,
        context: Any,
        agent: Any,
        tool: Any,
    ) -> None:
        tool_name = getattr(tool, "name", None) or str(tool)
        self._in_flight.append((tool_name, time.monotonic()))
        ctx_obj = getattr(context, "context", None)
        if isinstance(ctx_obj, ShinkaToolContext):
            # Tools opt into structured per-call data by writing here
            # before returning. Clear stale extras from a prior call.
            ctx_obj.last_tool_extras = None

    async def on_tool_end(
        self,
        context: Any,
        agent: Any,
        tool: Any,
        result: Any,
    ) -> None:
        tool_name = getattr(tool, "name", None) or str(tool)
        latency = self._pop_matching_start(tool_name)

        ctx_obj = getattr(context, "context", None)
        if not isinstance(ctx_obj, ShinkaToolContext):
            return

        success, error = _classify_result(result)
        entry = {
            "name": tool_name,
            "latency_sec": round(latency, 4) if latency is not None else None,
            "success": success,
        }
        if error:
            entry["error"] = error
        if ctx_obj.last_tool_extras:
            entry.update(ctx_obj.last_tool_extras)
            ctx_obj.last_tool_extras = None
        ctx_obj.tool_call_trace.append(entry)

    def _pop_matching_start(self, tool_name: str) -> float | None:
        """FIFO match by tool_name; falls back to popleft if mismatched
        so we never leak an in-flight entry that would skew future
        latency calculations."""
        if not self._in_flight:
            return None
        # Most common case: tools are sequential, oldest matches.
        head_name, head_start = self._in_flight[0]
        if head_name == tool_name:
            self._in_flight.popleft()
            return time.monotonic() - head_start
        # Out-of-order completion: scan and remove the first match.
        for idx, (name, start) in enumerate(self._in_flight):
            if name == tool_name:
                del self._in_flight[idx]
                return time.monotonic() - start
        # No match — return None and don't leak the queue.
        return None


def _classify_result(result: Any) -> tuple[bool, str | None]:
    """Infer (success, error_msg) from the tool's return string.

    Shinka tools share a convention: ``"OK: ..."`` on success,
    ``"Error: ..."`` or ``"FAILED: ..."`` on failure. The first line
    of the failure message becomes the ``error`` field on the trace
    entry — multi-line failure detail stays in the LLM's transcript
    where the agent can use it to self-correct.
    """
    if not isinstance(result, str):
        return True, None
    stripped = result.strip()
    if stripped.startswith("OK"):
        return True, None
    if stripped.startswith("Error:") or stripped.startswith("FAILED:"):
        first_line = stripped.split("\n", 1)[0]
        # Strip the leading "Error: " / "FAILED: " label for a cleaner
        # error field; downstream code that wants the prefix can
        # inspect the success boolean.
        if first_line.startswith("Error:"):
            return False, first_line[len("Error:") :].strip() or first_line
        return False, first_line[len("FAILED:") :].strip() or first_line
    # Convention escape hatch: assume success on anything we can't
    # classify (e.g. tools returning raw JSON like query_evolution_db).
    return True, None

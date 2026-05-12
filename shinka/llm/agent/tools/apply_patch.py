"""``apply_patch_tool`` — apply a code patch and update the run state.

Wraps the existing ``shinka.edit.async_apply.apply_patch_async`` so the
agent can decide *when* to apply patches (and how many times), rather
than the orchestrator firing exactly one patch attempt per generation
the way ``_run_patch_async`` does today.

On success the tool:
- Updates ``ctx.current_code`` with the modified source.
- The underlying ``apply_patch_async`` has already written the
  modified code to the patch directory; the agent doesn't need to
  know the filename — the path is reported back in the success
  message.

On failure the tool returns the error string so the agent can
self-correct on its next turn (same "feed the error back" pattern
shinka's existing patch-retry loop uses, but now driven by the agent
rather than the orchestrator).

Testing
-------
The tool body lives in ``_apply_patch_impl`` as a plain async
function. ``_apply_patch_tool`` is a thin ``@function_tool`` wrapper
that pulls the context out of the ``RunContextWrapper`` and calls
``_apply_patch_impl``. Unit tests target ``_apply_patch_impl``
directly, which avoids having to construct the SDK's ToolContext.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agents import RunContextWrapper, function_tool

from shinka.edit.async_apply import apply_patch_async

from .context import ShinkaToolContext
from .registry import register_tool

logger = logging.getLogger(__name__)


# Valid patch_type values mirror those accepted by apply_patch_async /
# the existing PromptSampler. ``cross`` is for crossover patches.
_VALID_PATCH_TYPES = frozenset({"diff", "full", "cross"})


async def _apply_patch_impl(
    state: ShinkaToolContext,
    patch_text: str,
    patch_type: str = "diff",
) -> str:
    """Pure tool body. Easier to unit-test than the decorated
    function_tool wrapper because the caller passes the state object
    directly instead of going through ``RunContextWrapper``."""
    start = time.monotonic()

    if patch_type not in _VALID_PATCH_TYPES:
        latency = time.monotonic() - start
        msg = (
            f"Invalid patch_type {patch_type!r}. Expected one of "
            f"{sorted(_VALID_PATCH_TYPES)}."
        )
        state.record_tool_call(
            "apply_patch", latency, success=False, error=msg
        )
        return f"Error: {msg}"

    try:
        modified_code, num_applied, output_path, error_msg, patch_txt, patch_path = (
            await apply_patch_async(
                original_str=state.current_code,
                patch_str=patch_text,
                patch_dir=state.patch_dir,
                language=state.language,
                patch_type=patch_type,
            )
        )
    except Exception as exc:
        latency = time.monotonic() - start
        logger.info("apply_patch tool raised: %s", exc)
        state.record_tool_call(
            "apply_patch", latency, success=False, error=str(exc)
        )
        return f"Error: {exc}"

    latency = time.monotonic() - start

    if error_msg:
        state.record_tool_call(
            "apply_patch",
            latency,
            success=False,
            error=error_msg,
            extra={"patch_type": patch_type},
        )
        return f"Error: {error_msg}"

    # ``apply_patch_async`` may return ``error_msg=None`` together
    # with ``num_applied=0`` for inputs that parse cleanly but
    # produce no changes (e.g. a no-op diff). Mirror
    # ``_run_patch_async``'s contract: that's a failure, not a
    # success — surface a clear error so the agent can retry with
    # a real patch.
    if num_applied <= 0 or modified_code is None:
        msg = "No changes applied (patch parsed cleanly but produced no diff)."
        state.record_tool_call(
            "apply_patch",
            latency,
            success=False,
            error=msg,
            extra={"patch_type": patch_type, "num_applied": num_applied},
        )
        return f"Error: {msg}"

    state.current_code = modified_code

    # Record the patch artifacts on the context so the orchestrator
    # can surface them after the agent run (without scanning the
    # entire tool_call_trace for the latest success).
    state.last_successful_patch_text = patch_txt or patch_text
    state.last_successful_patch_type = patch_type
    state.last_successful_num_applied = num_applied
    state.last_successful_patch_path = str(patch_path) if patch_path else None

    state.record_tool_call(
        "apply_patch",
        latency,
        success=True,
        extra={
            "patch_type": patch_type,
            "num_applied": num_applied,
            "output_path": str(output_path) if output_path else None,
        },
    )
    return (
        f"OK: applied {num_applied} change(s) via {patch_type} patch. "
        f"Updated program written to {output_path}."
    )


@function_tool
async def _apply_patch_tool(
    ctx: RunContextWrapper[ShinkaToolContext],
    patch_text: str,
    patch_type: str = "diff",
) -> str:
    """Apply a code change to the current working solution.

    Use this tool to mutate the program toward a better solution. The
    patched code becomes the new current state and is written to the
    patch directory ready for evaluation.

    Args:
        patch_text: The patch content. Format depends on ``patch_type``:
            - ``"diff"``: unified diff format (preferred for small
              targeted edits — minimizes token use)
            - ``"full"``: a complete rewrite of the program. Use only
              when you need to restructure substantially.
            - ``"cross"``: crossover format combining two programs.
              Rarely needed in single-program flows.
        patch_type: One of ``"diff"`` (default), ``"full"``, ``"cross"``.

    Returns:
        A short status string. ``"OK: applied N change(s). ..."`` on
        success, or ``"Error: <message>"`` on failure. On error, the
        current code is unchanged and you should fix the patch and
        try again.
    """
    return await _apply_patch_impl(ctx.context, patch_text, patch_type)


def make_apply_patch_tool(ctx: ShinkaToolContext) -> Any:
    """Factory returns the shared decorated tool. ``ctx`` is unused at
    construction time — the tool reads from ``ctx`` at call time via
    the ``RunContextWrapper`` parameter, which is how the SDK threads
    per-run state through."""
    return _apply_patch_tool


register_tool("apply_patch", make_apply_patch_tool)

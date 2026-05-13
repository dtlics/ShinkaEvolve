"""``apply_patch_tool`` — apply a code patch and run the evaluator.

Wraps the existing ``shinka.edit.async_apply.apply_patch_async`` so the
agent can decide *when* to apply patches (and how many times), rather
than the orchestrator firing exactly one patch attempt per generation
the way ``_run_patch_async`` does today.

Doom-remediation Fix 1: every successful apply is immediately followed
by a deterministic evaluator call. The eval result is appended to the
tool's return string so the agent sees the score on its next turn
without having to call a separate ``evaluate`` tool. This makes
"every code change is evaluated" a structural invariant (the agent
can't commit a change without the framework scoring it). It also
guarantees ``ctx.last_eval_result`` is always the eval of the latest
code on disk — the orchestrator's cache-and-skip path is therefore
always fresh.

On success the tool:
- Updates ``ctx.current_code`` with the modified source.
- Writes the modified code to the patch directory.
- Runs the evaluator (if ``ctx.evaluator`` is set) against the new
  code; caches the result on ``ctx.last_eval_result``.
- Returns ``"OK: applied N change(s) ... EVAL: <eval result>"``.

On apply failure the tool returns ``"Error: <message>"`` and does NOT
run the evaluator (no code on disk to evaluate). The agent feeds the
error back into its next apply_patch.

If ``ctx.evaluator`` is ``None`` (legacy paths and tests that don't
wire an evaluator), the tool returns just the apply success message —
no eval section appended. This keeps unit-test ergonomics simple.

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
from typing import Any

from agents import RunContextWrapper, function_tool

from shinka.edit.async_apply import apply_patch_async

from .context import ShinkaToolContext
from .evaluate import _evaluate_impl
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
    directly instead of going through ``RunContextWrapper``.

    Telemetry: name + latency + success are recorded by
    ``ShinkaAgentHooks.on_tool_end`` (success is inferred from the
    return-string prefix ``"OK"`` vs ``"Error:"``). Structured per-call
    data goes on ``state.last_tool_extras``; the hook merges it into
    the trace entry.
    """
    if patch_type not in _VALID_PATCH_TYPES:
        return (
            f"Error: Invalid patch_type {patch_type!r}. Expected one of "
            f"{sorted(_VALID_PATCH_TYPES)}."
        )

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
        logger.info("apply_patch tool raised: %s", exc)
        return f"Error: {exc}"

    if error_msg:
        state.last_tool_extras = {"patch_type": patch_type}
        return f"Error: {error_msg}"

    # ``apply_patch_async`` may return ``error_msg=None`` together
    # with ``num_applied=0`` for inputs that parse cleanly but
    # produce no changes (e.g. a no-op diff). Mirror
    # ``_run_patch_async``'s contract: that's a failure, not a
    # success — surface a clear error so the agent can retry with
    # a real patch.
    if num_applied <= 0 or modified_code is None:
        state.last_tool_extras = {
            "patch_type": patch_type,
            "num_applied": num_applied,
        }
        return (
            "Error: No changes applied (patch parsed cleanly but "
            "produced no diff)."
        )

    state.current_code = modified_code

    # Record the patch artifacts on the context so the orchestrator
    # can surface them after the agent run (without scanning the
    # entire tool_call_trace for the latest success).
    state.last_successful_patch_text = patch_txt or patch_text
    state.last_successful_patch_type = patch_type
    state.last_successful_num_applied = num_applied
    state.last_successful_patch_path = str(patch_path) if patch_path else None

    apply_msg = (
        f"OK: applied {num_applied} change(s) via {patch_type} patch. "
        f"Updated program written to {output_path}."
    )

    # Doom-remediation Fix 1: auto-eval after every successful apply.
    # The agent never has to call evaluate explicitly; the framework
    # guarantees the just-applied code is scored, and the result is
    # appended to the tool return so the agent sees it next turn.
    #
    # If no evaluator is wired (legacy paths, unit tests), skip the
    # eval step and return only the apply message. Downstream
    # consumers that read ``last_tool_extras["eval_correct"]`` for
    # fix-telemetry will see it absent and treat the call as
    # eval-not-run.
    eval_extras: dict = {}
    eval_msg_part = ""
    if state.evaluator is not None:
        eval_result_str = await _evaluate_impl(state)
        eval_msg_part = f"\nEVAL: {eval_result_str}"
        # The evaluator path already set state.last_eval_result /
        # state.last_eval_rtime and state.last_tool_extras (with
        # combined_score / metrics_keys). We extract a small subset
        # to merge into the apply_patch trace entry so fix_telemetry
        # can read eval outcomes from the apply_patch entries
        # directly (no separate ``evaluate`` trace entry exists in
        # the auto-eval flow).
        prior_extras = state.last_tool_extras or {}
        eval_extras = {
            "eval_combined_score": prior_extras.get("combined_score"),
            "eval_correct": eval_result_str.startswith("OK"),
        }

    state.last_tool_extras = {
        "patch_type": patch_type,
        "num_applied": num_applied,
        "output_path": str(output_path) if output_path else None,
        **eval_extras,
    }
    return apply_msg + eval_msg_part


@function_tool
async def _apply_patch_tool(
    ctx: RunContextWrapper[ShinkaToolContext],
    patch_text: str,
    patch_type: str = "diff",
) -> str:
    """Apply a code change AND automatically evaluate the result.

    Use this tool to mutate the program toward a better solution. The
    patched code becomes the new current state, is written to the
    patch directory, and is then **automatically evaluated**. You see
    both the apply outcome and the eval outcome (score + correct flag)
    in this tool's return string — there is no separate evaluate step
    you need to remember to call.

    **Format**: the system prompt (above) specifies the exact format
    you must use. Pass that content as ``patch_text`` verbatim and
    set ``patch_type`` to match the format the system prompt
    described:

    - ``"diff"``: shinka's SEARCH/REPLACE block format (NOT a unified
      diff). The system prompt shows the ``<<<<<<< SEARCH / =======
      / >>>>>>> REPLACE`` template. Replacements are constrained to
      lines between ``EVOLVE-BLOCK-START`` and ``EVOLVE-BLOCK-END``
      markers in the program. Preferred for small targeted edits.
    - ``"full"``: a complete rewrite of the program in a single
      markdown code fence (e.g. ```python ... ```). Use only when
      restructuring substantially.
    - ``"cross"``: crossover between two parent programs. Rare.

    Args:
        patch_text: The patch content in the format the system prompt
            specifies. Copy SEARCH text verbatim (including
            indentation) when using ``diff``.
        patch_type: One of ``"diff"`` (default), ``"full"``, ``"cross"``.

    Returns:
        On apply+eval success:
        ``"OK: applied N change(s) ... \\nEVAL: OK: combined_score=...; correct=True; details=..."``

        On apply success + eval failure (your patch ran but failed
        validation or scored badly):
        ``"OK: applied N change(s) ... \\nEVAL: FAILED: <err>; partial_metrics=..."``
        — read the EVAL section, write a fix patch, call this tool
        again. Repeat until you see ``correct=True`` or you run out
        of turns.

        On apply failure:
        ``"Error: <apply failure message>"`` — the eval was NOT run
        (there's no new code on disk). Fix your patch text and try
        again.
    """
    return await _apply_patch_impl(ctx.context, patch_text, patch_type)


def make_apply_patch_tool(ctx: ShinkaToolContext) -> Any:
    """Factory returns the shared decorated tool. ``ctx`` is unused at
    construction time — the tool reads from ``ctx`` at call time via
    the ``RunContextWrapper`` parameter, which is how the SDK threads
    per-run state through."""
    return _apply_patch_tool


register_tool("apply_patch", make_apply_patch_tool)

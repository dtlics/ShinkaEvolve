"""End-to-end smoke test for the agentic LLM path.

Exercises against real Azure OpenAI:
- ``AgentLLMClient`` constructor + provider routing
- ``BackgroundOpenAIResponsesModel`` (background-mode + polling)
- ``apply_patch_tool`` actually mutates code via the agent
- The QueryResult adapter (cost, tokens, num_tool_calls)

Budget target: well under $1 for a single run. Uses ``gpt-5.4-mini``
(the cheapest of our deployments at $0.75/$4.50 per 1M).

Run from repo root (so ``.env`` is found in the launch dir):

    /opt/anaconda3/envs/shinka/bin/python scripts/test_agentic.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

from shinka.llm.agent import AgentLLMClient
from shinka.llm.agent.tools import ShinkaToolContext, select_shinka_tools


# A trivial parent program. The agent will be asked to rename the
# function — a change that requires a real patch. The EVOLVE-BLOCK
# markers tell shinka's diff parser which region is mutable.
PARENT_CODE = '''\
# EVOLVE-BLOCK-START
def add(a, b):
    """Return a+b."""
    return a + b
# EVOLVE-BLOCK-END
'''


# System prompt: shinka's exact SEARCH/REPLACE format (lifted from
# shinka/prompts/prompts_diff.py:DIFF_SYS_FORMAT) plus the
# tool-driven flow this smoke test exercises.
SYSTEM_MSG = """You are a code-evolution agent. You have one tool available:
- apply_patch(patch_text, patch_type): apply a code change to the
  current program. patch_type must be "diff" for the SEARCH/REPLACE
  format described below.

Your job:
1. Rename the function ``add`` to ``sum_two`` while preserving behavior.
2. Call apply_patch with patch_type="diff" and a SEARCH/REPLACE block
   that performs the rename.
3. After the patch applies successfully, emit a final message wrapped
   in <NAME>...</NAME> and <DESCRIPTION>...</DESCRIPTION> tags.

**Required format for patch_text** (no unified diff, no markdown
fence — exactly this):

<<<<<<< SEARCH
<old code copied verbatim from inside the EVOLVE-BLOCK region,
including indentation>
=======
<new code that replaces the SEARCH text>
>>>>>>> REPLACE

You may only modify lines between ``EVOLVE-BLOCK-START`` and
``EVOLVE-BLOCK-END``. Copy the SEARCH text verbatim (preserve
indentation and whitespace). Do not include the EVOLVE-BLOCK
markers themselves in the SEARCH/REPLACE blocks.
"""

USER_MSG = """Current program:

```python
{code}
```

Apply a patch to rename ``add`` to ``sum_two`` and confirm with the
<NAME> / <DESCRIPTION> tags.
"""


async def main() -> int:
    patch_dir = Path(tempfile.mkdtemp(prefix="shinka_agentic_smoke_"))
    print(f"[smoke] patch_dir: {patch_dir}")

    ctx = ShinkaToolContext(
        patch_dir=str(patch_dir),
        parent_code=PARENT_CODE,
        language="python",
    )
    tools = select_shinka_tools(["apply_patch"], ctx)
    print(f"[smoke] tools: {[getattr(t, 'name', t) for t in tools]}")

    client = AgentLLMClient(
        model_names=["azure-gpt-5.4-mini"],
        temperatures=[1.0],
        max_tokens=[8000],
        reasoning_efforts=["medium"],
        verbose=False,
        # Generous poll timeout for the smoke test (don't want to
        # spuriously fail on Azure latency).
        poll_timeout_sec=600.0,
        max_queued_wait_sec=120.0,
    )

    print(f"[smoke] dispatching agent run...")
    t0 = time.time()
    try:
        result = await client.run_agent(
            msg=USER_MSG.format(code=PARENT_CODE),
            system_msg=SYSTEM_MSG,
            tool_context=ctx,
            tools=tools,
            max_turns=5,
        )
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"[smoke] FAILED after {elapsed:.1f}s: {type(exc).__name__}: {exc}")
        shutil.rmtree(patch_dir, ignore_errors=True)
        return 1
    elapsed = time.time() - t0

    print()
    print(f"=== Agent run completed in {elapsed:.1f}s ===")
    if result is None:
        print("[smoke] FAILED: result is None (agent run returned None)")
        shutil.rmtree(patch_dir, ignore_errors=True)
        return 1

    print(f"Model:         {result.model_name}")
    print(f"Cost:          ${result.cost:.4f}")
    print(f"  Input:       ${result.input_cost:.4f} ({result.input_tokens} tokens)")
    print(f"  Output:      ${result.output_cost:.4f} ({result.output_tokens} tokens)")
    print(f"  Thinking:    {result.thinking_tokens} tokens")
    print(f"Tool calls:    {result.num_tool_calls}")
    print(f"Total queries: {result.num_total_queries}")
    print()
    final_snippet = (result.content or "").strip()[:300]
    print(f"Final content (truncated 300ch):")
    print(f"  {final_snippet!r}")
    print()
    print(f"Tool call trace ({len(ctx.tool_call_trace)} entries):")
    for i, entry in enumerate(ctx.tool_call_trace):
        success = entry.get("success")
        name = entry.get("name")
        latency = entry.get("latency_sec", 0)
        extra = ", ".join(
            f"{k}={v!r}"
            for k, v in entry.items()
            if k not in {"name", "success", "latency_sec"}
        )
        print(f"  [{i}] {name} success={success} ({latency:.2f}s) {extra}")
    print()
    print(f"current_code after run:")
    for line in ctx.current_code.splitlines():
        print(f"  | {line}")
    print()
    print(
        f"last_successful_patch_text set: "
        f"{ctx.last_successful_patch_text is not None}"
    )
    print(
        f"last_successful_patch_type:     {ctx.last_successful_patch_type!r}"
    )
    print(
        f"last_successful_num_applied:    {ctx.last_successful_num_applied}"
    )

    print()
    failed = False
    if ctx.last_successful_patch_text is None:
        print("[smoke] FAIL: agent did not produce a successful apply_patch.")
        failed = True
    elif "sum_two" not in ctx.current_code:
        print("[smoke] FAIL: 'sum_two' not present in mutated code.")
        failed = True
    elif "def add(" in ctx.current_code:
        print(
            "[smoke] FAIL: 'def add(' still present in mutated code "
            "(rename did not happen)."
        )
        failed = True
    else:
        print("[smoke] PASS: function was renamed successfully.")

    if result.cost > 1.0:
        print(f"[smoke] WARN: this run cost ${result.cost:.4f} (> $1).")

    # Keep the patch dir for inspection on failure; clean up on success.
    if failed:
        print(f"[smoke] leaving patch_dir for inspection: {patch_dir}")
    else:
        shutil.rmtree(patch_dir, ignore_errors=True)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

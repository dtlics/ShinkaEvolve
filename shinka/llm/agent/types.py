"""Pydantic output types for the agentic path's structured outputs.

Passed as ``output_type=`` on the ``Agent`` so the SDK auto-instructs
the model to produce a typed final response and parses it into a
Python instance accessible via ``run_result.final_output``. The
adapter surfaces that on ``QueryResult.final_output_obj``.

Adding new structured outputs: define a Pydantic model here, export
it from ``shinka.llm.agent``, and pass it to ``run_agent(...,
output_type=YourModel)`` from the orchestrator.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PatchProposalOutput(BaseModel):
    """The agentic proposer's structured final response.

    The agent applies a patch (and optionally evaluates it) via tool
    calls during the run. After it's done iterating, this object is
    the final-turn response — a short human-readable label for the
    change. The orchestrator persists ``name`` and ``description`` on
    the program row so the webui timeline and downstream analysis can
    refer to each generation by its intent.

    Replaces the pre-Phase-E pattern where the agent emitted
    ``<NAME>``/``<DESCRIPTION>`` tags inside a text response that the
    orchestrator regex-parsed.
    """

    name: str = Field(
        description=(
            "A short label (3-6 words) for the change you applied. "
            "Used as the row label in the webui. Examples: 'switch to "
            "quicksort', 'add memoization', 'tighter inner loop'."
        ),
    )
    description: str = Field(
        description=(
            "Two or three sentences explaining what the change does "
            "and why you expect it to help."
        ),
    )

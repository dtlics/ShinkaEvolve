"""Prompts for the ``literature_grounded`` mutation arm (phase 3 of
research-grounding).

This arm consumes ONE concrete ``BriefItem`` from the DR meta cycle
(an "Idea + Reference snippet + Source + Gotchas" entry) and asks the
agent to attempt a code edit grounded in that reference, with bounded
``web_search`` access to confirm or extend the snippet.

Critical guardrails (mirrored from the old plan §6.1, verbatim):

* "Use the ``web_search`` tool ONLY to confirm or extend the reference
  material above — do not pursue alternative ideas, even if you think
  of better ones."
* Explicit abort permission: "If the reference material is
  insufficient or contradicted by your search, return the parent
  unchanged with a one-sentence comment in ``<DESCRIPTION>``; this is
  preferable to fabrication."

The orchestrator special-cases the abort path: when the agent returns
the parent unchanged, ``meta_patch_data["abort_reason"]`` is set to
``"insufficient_reference"`` and the bandit update is skipped.
Otherwise success follows the normal path.
"""

from __future__ import annotations


LIT_GROUNDED_SYS_FORMAT = """\

# Mutation type: literature_grounded

You are attempting a code edit that is grounded in a specific
reference from the run's deep-research brief. The orchestrator picked
this arm because the DR pipeline produced a concrete technique with
a referenceable source.

Constraints
-----------
1. Use the ``apply_patch`` tool to make the edit, then ``evaluate`` to
   check the result. Iterate within your turn budget.
2. You have the ``web_search`` tool. Use it ONLY to confirm or extend
   the reference material below — do NOT pursue alternative ideas,
   even if you think of better ones. The orchestrator decides later
   whether to broaden the search.
3. If the reference material is insufficient or contradicted by your
   search, return the parent unchanged with a one-sentence
   ``<DESCRIPTION>`` explaining why; this is preferable to
   fabrication. The orchestrator treats a clean abort as a legitimate
   "insufficient_reference" outcome and does NOT penalize the model.

Output format
-------------
After your final patch (or your decision to abort), reply with:
- ``<NAME>``: ≤5-word name of the change.
- ``<DESCRIPTION>``: 1-3 sentence rationale citing the reference.

The structured output type ``PatchProposalOutput`` is enforced on
OpenAI/Azure providers; ``<NAME>``/``<DESCRIPTION>`` tags are the
fallback for legacy providers.
"""


LIT_GROUNDED_ITER_MSG = """\
Language: {language}

## Reference material from the deep-research brief

**Idea**: {idea}

**Rationale**: {rationale}

**Reference source**: {reference_source}

**Reference snippet**:
{reference_snippet}

**Gotchas**: {gotchas}

## Current parent program

Score: {performance_metrics}

```{language}
{code_content}
```
{text_feedback_section}

## Your task

Attempt to apply the technique described above to this program. Use
``web_search`` ONLY to confirm or extend the reference material —
do not pursue alternative ideas. If you cannot confirm the reference
or it does not apply to this program, return the parent unchanged
with a one-sentence ``<DESCRIPTION>`` explaining why.

Iterate via ``apply_patch`` + ``evaluate`` within your turn budget.
"""

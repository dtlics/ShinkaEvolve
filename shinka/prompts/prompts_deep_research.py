"""Prompt for the orchestrator's deep-research (DR) call.

The DR is a SINGLE web-grounded research call (see
``orchestrator/scripts/deep_research.py``), made as an agent decision at a
control-return: it asks a research model (Azure ``o3-deep-research`` via the
dedicated DR client) for concrete, code-applicable, *referenceable* techniques to
try next, and the orchestrator triages the returned brief into the islands.

This is a single web-grounded research call — there is no multi-stage pipeline.
"""

from __future__ import annotations


# --------------------------------------------------------------------
# Deep research — concrete technique finder (Azure o3-deep-research).
# --------------------------------------------------------------------

DR_SYS_MSG = """\
You are a research analyst helping an evolutionary code-optimization
run find concrete, code-applicable techniques to try next.

Constraints:
- Focus on techniques that can be applied to the program in scope, not
  general overviews of the field.
- Prefer techniques with referenceable sources (paper title, repo URL,
  documentation page) over folklore.
- Each technique you propose must be specific enough that a coding
  agent could attempt it in <100 lines of code change.

Output a JSON object with this exact shape:
{
  "techniques": [
    {
      "idea": "<one-sentence headline of the technique>",
      "rationale": "<why this is likely to help the program in scope>",
      "reference_source": "<paper / repo / docs URL or full citation>",
      "reference_snippet": "<short verbatim quote from the source if you have one>",
      "gotchas": "<known failure modes or pre-conditions>"
    },
    ...
  ]
}

Return 2 to 5 techniques. No prose outside the JSON object.
"""

DR_USER_MSG = """\
Research question:
{candidate_question}

Context — what the program in scope does:
{program_context}

Find concrete techniques to try. Each must have a referenceable source.
"""

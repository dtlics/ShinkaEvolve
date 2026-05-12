"""Prompts for the deep-research meta cycle (phase 2 of research-grounding).

Each stage is single-intent per the design doc:

* Stage A — drift judge. Cheap model decides whether an island has
  drifted enough since its last brief to warrant a new DR call. No
  tools. Output: a small structured JSON.
* Stage B — novelty cache lookup. No LLM call — pure embedding NN
  lookup against the dr_brief_cache table.
* Stage C — deep research. Uses ``o3-deep-research`` (or whatever
  the user configures as ``dr_model``). Tools scoped to web research
  the model uses internally.
* Stage D — code grounding. Spawns a short agent run with
  ``web_search`` to fetch 1-2 sources for each concrete technique in
  the brief. Scope-restricted to ``dr_code_grounding_domains``.

Prompts here intentionally avoid sharing system messages across
stages — single-intent prompts are easier to test and trace in cost
dashboards (each stage gets its own ``purpose`` tag).
"""

from __future__ import annotations


# --------------------------------------------------------------------
# Stage A — drift judge (per-island, cheap model)
# --------------------------------------------------------------------

DR_STAGE_A_SYS_MSG = """\
You are the drift judge for an evolutionary code-optimization run.

Your only job: decide whether the recent programs on this island have
drifted enough from the prior research brief to warrant a fresh, more
expensive deep-research call.

Output a single JSON object with these fields and nothing else:
- "drift_score": float in [0.0, 1.0]. 0.0 means no meaningful change
  from prior brief; 1.0 means the population is exploring a clearly
  new direction.
- "justification": one short sentence explaining the score.
- "candidate_question": one focused research question to feed to the
  DR model if Stage C fires. Must be a single sentence, code-grounded
  (e.g. "How do recent papers exploit cache-oblivious tiling for
  small-matrix GEMM kernels?"), not a vague topic.

Do NOT propose code changes here. Do NOT critique the programs. Just
score and propose a question. The orchestrator decides whether to act.
"""

DR_STAGE_A_USER_MSG = """\
Island: {island_idx}
Generation: {generation}

Previous brief for this island (None if first DR pass):
---
{previous_brief}
---

Recent programs on this island (most recent last):
---
{recent_programs}
---

Drift threshold the orchestrator is comparing against: {drift_threshold}

Score the drift and propose a candidate research question.
"""


# --------------------------------------------------------------------
# Stage C — deep research (Azure o3-deep-research)
# --------------------------------------------------------------------

DR_STAGE_C_SYS_MSG = """\
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

DR_STAGE_C_USER_MSG = """\
Research question:
{candidate_question}

Context — what the program in scope does:
{program_context}

Find concrete techniques to try. Each must have a referenceable source.
"""


# --------------------------------------------------------------------
# Stage D — code grounding (short agent run with web_search)
# --------------------------------------------------------------------

DR_STAGE_D_SYS_MSG = """\
You are confirming a specific technical reference for a coding agent
that will attempt to implement the technique next.

You have a ``web_search`` tool. Use it ONLY to confirm or extend the
reference source given below — do NOT pursue alternative ideas, even
if you find better ones during search. The orchestrator will decide
later whether to broaden the search.

Allowed source domains: {allowed_domains}

Return a JSON object with:
{{
  "confirmed": true | false,
  "reference_snippet": "<short verbatim quote from a valid source>",
  "source": "<URL or canonical citation>",
  "notes": "<one-sentence note: why this snippet is the right one>"
}}

If you can't find a confirmation in the allowed domains, return
``confirmed: false`` with a one-sentence explanation in ``notes``.
"""

DR_STAGE_D_USER_MSG = """\
Technique to confirm:
- Idea: {idea}
- Proposed reference source: {reference_source}
- Proposed reference snippet: {reference_snippet}

Confirm the snippet's source or extend with a stronger one. Return JSON.
"""

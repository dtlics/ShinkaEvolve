"""Prompt templates for the deep-research meta pipeline (Phase 5).

The pipeline is four single-intent prompts (the plan's gotcha #2 makes
combining them a footgun). Each stage gets its own template here:

- **Stage A (drift check, cheap)**: given an island's previous brief and
  the programs added since, ask a cheap model whether the island has
  meaningfully drifted into territory the brief doesn't cover. Output is
  a JSON object ``{"drift_score": 0-1, "justification": "..."}``.
- **Stage B (cross-island novelty)**: no LLM prompt -- the work is an
  embedding lookup against ``dr_brief_cache``. We still expose a tiny
  template for the canonical-direction summary the Stage A model
  produces; that summary becomes Stage B's embedding input.
- **Stage C (deep research)**: instructs ``o3-deep-research`` to produce
  a structured brief. The output schema lives next to the prompt so
  Stage C can call the Responses API in ``structured outputs`` mode.
- **Stage D (code grounding)**: a final pass that takes the DR brief and
  uses bounded web search + fetch (within the configured allowed
  domains) to attach concrete snippets / sources to each technique.

Stage A and Stage B's small canonical-direction prompt are the only
parts Phase 5b uses; the rest are wired in Phase 5c/d.
"""

from __future__ import annotations


# --- Stage A: drift check -----------------------------------------------------

DRIFT_CHECK_SYS_MSG = """
You are auditing whether an evolutionary-search island has drifted away
from the territory described in its previous research brief. The drift
score is a single number in [0, 1] that captures "how much new ground
has the island covered since the brief was written".

Calibration:
- 0.0  Programs explore exactly the same techniques as the brief.
- 0.3  Programs explore minor variations of the brief's ideas.
- 0.6  Programs explore a new family of techniques adjacent to the brief.
- 1.0  Programs explore a completely different research direction.

Output strict JSON only. Do NOT include explanatory prose outside the
JSON object. The JSON object has exactly two fields:

{
  "drift_score": <float in [0, 1]>,
  "justification": "<one short sentence, <= 200 chars>"
}
""".rstrip()


DRIFT_CHECK_USER_MSG = """## Island id
{island_id}

## Previous brief
{previous_brief}

## Programs added since the previous brief
{recent_programs_summary}

## Task
Score how much this island has drifted off the brief. Respond with the
JSON object described in the system message and nothing else.
""".rstrip()


# --- Stage B: canonical-direction summary ------------------------------------

# Stage B does not require an LLM call beyond what Stage A already
# returns: the drift justification IS the canonical direction summary we
# embed for the cache lookup. This module exposes a helper so Stage B
# stays trivially testable.


# --- Stage C: deep research (Phase 5c) ---------------------------------------

DR_BRIEF_SYS_MSG = """
You are a research assistant compiling a focused brief on a specific
research direction for an evolutionary code-search system. Find concrete
techniques, papers, and reference implementations that could be applied
in the target program.

Constraints:
- Stay strictly within the direction described in the user message.
- Prefer specific named techniques over vague "consider X" advice.
- Each technique you surface MUST include: a short name, a one-paragraph
  rationale tied to the target program, and (where available) a source
  link and a short reference snippet from that source.
- Use the provided tools sparingly -- prefer your own training knowledge
  unless a recent technique is clearly relevant.

Output strict JSON only. The top-level shape:

{
  "summary": "<one short paragraph, <= 400 chars>",
  "items": [
    {
      "idea": "<short name, <= 80 chars>",
      "rationale": "<one paragraph, <= 400 chars>",
      "reference_snippet": "<verbatim quote / pseudocode, <= 600 chars>",
      "source": "<url or paper id>",
      "gotchas": "<known issues, <= 300 chars; may be empty>"
    }
  ]
}
""".rstrip()


DR_BRIEF_USER_MSG = """## Target program task
{task_description}

## Research direction for this brief
{direction_summary}

## Allowed source domains
{allowed_domains}

## Task
Produce a structured brief for the direction above. Return strict JSON.
""".rstrip()


# --- Stage D: code grounding (Phase 5c) --------------------------------------

CODE_GROUND_SYS_MSG = """
You are filling in source links and reference snippets for an existing
research brief. For each item in the brief that is missing a source or
snippet, run a SINGLE targeted web search (restricted to the allowed
domains) and at most ONE fetch on the most relevant hit.

Output strict JSON only -- the same brief shape you received, with
``source`` and ``reference_snippet`` populated where possible. Do NOT
invent URLs or quotes. If a search yields nothing usable, leave the
field empty.
""".rstrip()


CODE_GROUND_USER_MSG = """## Allowed source domains
{allowed_domains}

## Brief to ground
{brief_json}

## Task
Return the same brief structure with ``source`` and ``reference_snippet``
populated where you can. Strict JSON.
""".rstrip()


__all__ = [
    "DRIFT_CHECK_SYS_MSG",
    "DRIFT_CHECK_USER_MSG",
    "DR_BRIEF_SYS_MSG",
    "DR_BRIEF_USER_MSG",
    "CODE_GROUND_SYS_MSG",
    "CODE_GROUND_USER_MSG",
]

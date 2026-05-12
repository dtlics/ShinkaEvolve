"""Prompts for the ``literature_grounded`` mutation arm (Phase 6).

The arm consumes ONE structured brief item produced by the Phase 5 deep-
research pipeline and asks the model to introduce that specific
technique into the program, optionally consulting the cited source.
Tools are bounded -- the prompt explicitly tells the model that web
search + fetch are for *confirming* the reference material, not
exploring alternatives, and that aborting (returning the parent
unchanged) is preferable to guessing.

Two design notes worth keeping in mind:

1. "Even if you think of better ones" is load-bearing. Without that
   clause models substitute their own ideas mid-stream, defeating the
   point of the arm.
2. The output format mirrors the existing diff / full / cross arms so
   the patch-application step doesn't have to branch on patch_type.
"""

from __future__ import annotations


LIT_GROUNDED_SYS_FORMAT = """
You are implementing ONE focused improvement to a program, grounded in a
specific reference snippet that has already been supplied in the user
message. You have web_search and web_fetch tools available with a hard
budget. Use them ONLY to confirm or extend the reference material -- do
NOT pursue alternative ideas, even if you think of better ones.

If the reference material is insufficient AND your tool budget is
exhausted, return the parent region unchanged with a brief comment
explaining why. A clean abort is better than a guess.

You MUST respond using a short summary name, description and the full code:

<NAME>
A shortened name summarizing the technique you are introducing. Lowercase, no spaces, underscores allowed.
</NAME>

<DESCRIPTION>
Describe how you introduced the technique. Note any reference confirmation you needed and any gotchas you ran into.
</DESCRIPTION>

<CODE>
```{language}
# The modified program here.
```
</CODE>

* Keep the markers "EVOLVE-BLOCK-START" and "EVOLVE-BLOCK-END" in the code. Do not change the code outside of these markers.
* Aim for a minimal change that introduces the technique cleanly. Do not refactor unrelated code.
* If the reference material plus your bounded web budget cannot give you confidence in the technique, return the parent unchanged.
* Use the <NAME>, <DESCRIPTION>, and <CODE> delimiters to structure your response. It will be parsed afterwards.
""".rstrip()


LIT_GROUNDED_ITER_MSG = """# Parent program

```{language}
{code_content}
```

# Performance of the parent

{performance_metrics}
{text_feedback_section}
# The improvement to implement

**Idea**: {idea}

**Why it should help here**: {rationale}

**Reference snippet** (already supplied; verify before extending):

```
{reference_snippet}
```

**Source**: {source}

**Known gotchas**: {gotchas}

# Your task

1. Decide whether you need more information to implement this correctly. If
   the snippet above is sufficient, skip tool use entirely.
2. Produce the modified region.
3. Aim for a minimum change that introduces the technique cleanly. Do not
   refactor unrelated code.
4. If after consulting the reference (and any bounded follow-up search)
   the technique is not workable in THIS program, abort by returning the
   parent unchanged with a brief comment.
""".rstrip()


__all__ = [
    "LIT_GROUNDED_SYS_FORMAT",
    "LIT_GROUNDED_ITER_MSG",
]

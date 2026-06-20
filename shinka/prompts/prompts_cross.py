import random
from typing import List

from shinka.database import Program
from .prompts_base import perf_str


CROSS_SYS_FORMAT = """
You are given multiple code scripts that solve the same problem in different ways.
Your task is to perform a CROSSOVER: produce one new program that combines the
strongest mechanisms from each script to score HIGHER than any of them on the
target metric — not a mechanical concatenation, but a deliberate merge of the
best ideas. Where two scripts conflict, keep the choice that the metrics suggest
is better; do not merely interleave their lines.
Provide the complete new program code.
You MUST respond using a short summary name, description and the full code:

<NAME>
A shortened name summarizing the code you are proposing. Lowercase, no spaces, underscores allowed.
</NAME>

<DESCRIPTION>
A description and argumentation process of the code you are proposing.
</DESCRIPTION>

<CODE>
```{language}
# The new rewritten program here.
```
</CODE>

* Keep the markers "EVOLVE-BLOCK-START" and "EVOLVE-BLOCK-END" in the code. Do not change the code outside of these markers.
* Make sure your rewritten program maintains the same inputs and outputs as the original program, but with improved internal implementation.
* Make sure the file still runs after your changes.
* Use the <NAME>, <DESCRIPTION>, and <CODE> delimiters to structure your response. It will be parsed afterwards.
""".rstrip()


CROSS_ITER_MSG = """# Current program

Here is the current program we are trying to improve (you will need to propose a new program with the same inputs and outputs as the original program, but with improved internal implementation):

```{language}
{code_content}
```

Here are the performance metrics of the program:

{performance_metrics}{text_feedback_section}

# Task

Perform a cross-over between the code script above and the one below. Identify
what each one does well, then combine those strengths into a single program that
scores higher than either parent. Prefer the better-performing mechanism wherever
the two disagree; avoid a naive line-by-line stitch that inherits both parents'
weaknesses.
Provide the complete new program code.

IMPORTANT: Make sure your rewritten program maintains the same inputs and outputs as the original program, but with improved internal implementation.
Only the region between the EVOLVE-BLOCK-START and EVOLVE-BLOCK-END markers is editable; keep both markers verbatim and leave everything outside them unchanged.
""".rstrip()


def _most_distant(parent: Program, candidates: List[Program]) -> Program:
    """Pick the candidate whose embedding is FURTHEST (lowest cosine similarity)
    from the parent, so the crossover partner contributes a genuinely different
    mechanism rather than a near-duplicate (the long-standing diversity TODO).
    Falls back to a uniform random pick when embeddings are missing/degenerate."""
    p_emb = getattr(parent, "embedding", None)
    if not p_emb:
        return random.choice(candidates)
    try:
        import numpy as np

        pv = np.asarray(p_emb, dtype=float)
        p_norm = float(np.linalg.norm(pv))
        if p_norm == 0.0:
            return random.choice(candidates)
        best, best_sim = None, None
        for c in candidates:
            c_emb = getattr(c, "embedding", None)
            if not c_emb:
                continue
            cv = np.asarray(c_emb, dtype=float)
            c_norm = float(np.linalg.norm(cv))
            if c_norm == 0.0:
                continue
            sim = float(pv @ cv) / (p_norm * c_norm)
            if best_sim is None or sim < best_sim:
                best, best_sim = c, sim
        return best if best is not None else random.choice(candidates)
    except Exception:
        # Never let a crossover-partner heuristic crash the prompt build.
        return random.choice(candidates)


def get_cross_component(
    archive_inspirations: List[Program],
    top_k_inspirations: List[Program],
    language: str = "python",
    parent: Program = None,
) -> str:
    all_inspirations = archive_inspirations + top_k_inspirations

    # Choose the crossover partner that is MOST DIFFERENT from the parent (by
    # embedding distance) when a parent + embeddings are available, so crossover
    # actually merges distinct mechanisms; otherwise sample uniformly at random.
    if parent is not None:
        inspiration = _most_distant(parent, all_inspirations)
    else:
        inspiration = random.choice(all_inspirations)

    crossover_inspiration = "# Crossover partner program\n"
    crossover_inspiration += f"```{language}\n{inspiration.code}\n```\n\n"
    crossover_inspiration += f"Performance metrics: {perf_str(inspiration.combined_score, inspiration.public_metrics)}\n\n"

    return crossover_inspiration

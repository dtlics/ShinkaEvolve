# Bootstrap fix prompt -- used when no correct parent exists in the database.
# This prompt helps the LLM fix an incorrect program using error logs and feedback.
# (Distinct from ERROR_FIX_SYS_FORMAT below, which is the per-candidate retry
# loop introduced in Phase 4 of research-grounding.)

from dataclasses import dataclass, field
from typing import List, Optional


FIX_SYS_FORMAT = """
You are debugging and fixing an incorrect program that has failed validation.
Your task is to analyze the error output and fix the program so it passes validation.
You MUST respond using a short summary name, description and the full code:

<NAME>
A shortened name summarizing the fix you are proposing. Lowercase, no spaces, underscores allowed.
</NAME>

<DESCRIPTION>
Describe the bug you identified and the fix you are applying. Include your analysis of the error messages.
</DESCRIPTION>

<CODE>
```{language}
# The fixed program here.
```
</CODE>

* Keep the markers "EVOLVE-BLOCK-START" and "EVOLVE-BLOCK-END" in the code. Do not change the code outside of these markers.
* Make sure your fixed program maintains the same inputs and outputs as the original program.
* Focus on making the program correct first - performance optimization is secondary.
* Make sure the file still runs after your changes.
* Use the <NAME>, <DESCRIPTION>, and <CODE> delimiters to structure your response. It will be parsed afterwards.
""".rstrip()

FIX_ITER_MSG = """# Incorrect Program to Fix

The following program has failed validation and needs to be fixed:

```{language}
{code_content}
```

## Error Information

The program is marked as **incorrect** and did not pass validation tests.

{text_feedback_section}
{error_output_section}
# Task

Analyze the error output above and fix the program. Focus on:
1. Understanding why the program failed validation
2. Identifying the root cause from the error messages
3. Implementing a fix that addresses the issue

IMPORTANT: Make the program correct first. Performance improvements can come later.
""".rstrip()


def format_error_output_section(
    stdout_log: str = "",
    stderr_log: str = "",
) -> str:
    """Format error output section for fix prompts."""
    sections = []

    if stdout_log and stdout_log.strip():
        sections.append(
            f"### Standard Output (stdout):\n\n```\n{stdout_log.strip()}\n```"
        )

    if stderr_log and stderr_log.strip():
        sections.append(
            f"### Standard Error (stderr):\n\n```\n{stderr_log.strip()}\n```"
        )

    if not sections:
        return "\n### Error Output:\n\nNo error output captured.\n"

    return "\n" + "\n\n".join(sections) + "\n"


# -----------------------------------------------------------------------------
# Error-fix retry loop (Phase 4 of research-grounding)
# -----------------------------------------------------------------------------

ERROR_FIX_SYS_FORMAT = """
You are fixing one specific error in a program modification. The modification
has a recorded intent -- your job is to fix the error while preserving that
intent. Do NOT redesign the modification or try a different approach.

If you don't see a clear, minimal fix for this error, return the parent
program unchanged with a brief comment. You have {n_remaining} fix attempts
remaining after this one.

You MUST respond using a short summary name, description and the full code:

<NAME>
A shortened name summarizing the fix you are applying. Lowercase, no spaces, underscores allowed.
</NAME>

<DESCRIPTION>
Identify the specific cause of the error and describe the minimum change you
made to address it while preserving the original modification's intent.
</DESCRIPTION>

<CODE>
```{language}
# The fixed program here.
```
</CODE>

* Keep the markers "EVOLVE-BLOCK-START" and "EVOLVE-BLOCK-END" in the code. Do not change the code outside of these markers.
* Make the minimum change needed to fix the error while preserving the modification's intent.
* If no clear fix is visible, return the parent unchanged.
* Use the <NAME>, <DESCRIPTION>, and <CODE> delimiters to structure your response. It will be parsed afterwards.
""".rstrip()


ERROR_FIX_ITER_MSG = """# Error-fix Round {round_number}

## Original modification intent
{mutation_intent}

## Parent program (before the failed modification)

```{language}
{parent_code}
```

## Failed candidate (this round's input)

```{language}
{failed_code}
```

## Error from the most recent run

```
{error_message}
```

{traceback_section}{prior_attempts_section}{text_feedback_section}
# Your task

1. Identify the specific cause of THIS error.
2. Make the minimum change needed to fix it while preserving the original
   modification's intent above.
3. If no clear fix is visible, abort by returning the parent unchanged.
""".rstrip()


@dataclass
class AttemptRecord:
    """One round's outcome in an error-fix retry loop.

    Stored as structured rows rather than pasted prompts so the context
    stays small even after many rounds and the model can scan past
    attempts at a glance.
    """

    round_number: int
    model_used: Optional[str]
    summary: str           # Short prose: what this attempt tried to change.
    error_message: str     # The error the attempt resulted in (or "" on success).


def format_prior_attempt_log(attempts: List[AttemptRecord]) -> str:
    """Render the structured attempt log for the error-fix prompt.

    Empty list -> empty string. Each entry is one short row keyed by
    round number so the model can see *what was already tried* and avoid
    repeating itself.
    """
    if not attempts:
        return ""
    rows = ["## Previous fix attempts in this loop"]
    for record in attempts:
        rows.append(
            "- Round {n} (model={model}): {summary} | resulting error: {err}".format(
                n=record.round_number,
                model=record.model_used or "?",
                summary=(record.summary or "(no summary)").strip()[:200],
                err=(record.error_message or "(none)").strip()[:200],
            )
        )
    return "\n" + "\n".join(rows) + "\n"


def format_traceback_section(traceback_text: Optional[str]) -> str:
    """Optional traceback block; empty when no traceback is available."""
    if not traceback_text:
        return ""
    trimmed = traceback_text.strip()
    if not trimmed:
        return ""
    return f"\n## Traceback\n\n```\n{trimmed}\n```\n"

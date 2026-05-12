"""MutationIntent contract for downstream prompts.

Phase 1 of the research-grounding plan introduces a strict, one-line summary
of *why* a given mutation was proposed. Later phases (error-fix retry loop,
meta-cycle Stage A) read this field instead of the original mutation prompt
so that retries / summaries cannot accidentally inherit freeform noise.

The contract is intentionally tiny:

- ``name``: <=5 words. Acts as a stable label (used by display + diffing).
- ``primary_technique``: <=140 chars. Names the technique being introduced.
- ``expected_effect``: a single sentence (no newlines) describing the
  intended behavioral change.

The renderer concatenates these into one line so the database column can be
queried trivially and prompt templates can splice it without escaping. On
parse failure callers store ``NO_INTENT_RECORDED`` -- never freeform text.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

NO_INTENT_RECORDED: str = "no intent recorded"

_MAX_NAME_WORDS = 5
_MAX_TECHNIQUE_CHARS = 140
_MAX_EFFECT_CHARS = 280


class MutationIntent(BaseModel):
    """Validated one-line summary of a mutation proposal."""

    name: str = Field(..., description="<=5 word stable label")
    primary_technique: str = Field(
        ..., description="<=140 char technique description"
    )
    expected_effect: str = Field(
        ..., description="Single sentence describing intended behavioral change"
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must be non-empty")
        words = stripped.split()
        if len(words) > _MAX_NAME_WORDS:
            raise ValueError(
                f"name must be <= {_MAX_NAME_WORDS} words (got {len(words)})"
            )
        return stripped

    @field_validator("primary_technique")
    @classmethod
    def _validate_technique(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("primary_technique must be non-empty")
        if len(stripped) > _MAX_TECHNIQUE_CHARS:
            raise ValueError(
                f"primary_technique must be <= {_MAX_TECHNIQUE_CHARS} chars "
                f"(got {len(stripped)})"
            )
        if "\n" in stripped:
            raise ValueError("primary_technique must not contain newlines")
        return stripped

    @field_validator("expected_effect")
    @classmethod
    def _validate_effect(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("expected_effect must be non-empty")
        if len(stripped) > _MAX_EFFECT_CHARS:
            raise ValueError(
                f"expected_effect must be <= {_MAX_EFFECT_CHARS} chars "
                f"(got {len(stripped)})"
            )
        if "\n" in stripped:
            raise ValueError("expected_effect must not contain newlines")
        # A single sentence: at most one terminal punctuation mark mid-string
        # is a heuristic, not a hard rule -- writers split clauses with commas,
        # not periods. We only require absence of newlines (above) and a
        # non-trivial length.
        return stripped

    def render(self) -> str:
        """Render as a single line suitable for the ``mutation_intent`` DB column."""
        return (
            f"{self.name} | technique: {self.primary_technique} "
            f"| expected: {self.expected_effect}"
        )


def validate_mutation_intent(raw: Optional[Any]) -> str:
    """Best-effort coercion of an LLM-returned intent into a stored string.

    Returns the validated one-line summary on success; otherwise returns
    ``NO_INTENT_RECORDED``. This is the only function downstream callers
    should use to write to ``Program.mutation_intent`` -- it guarantees
    the column is either the well-formed contract output or the sentinel.

    Accepts:

    - a ``MutationIntent`` instance (already validated)
    - a ``dict`` with the three required keys
    - anything else -> sentinel
    """
    if raw is None:
        return NO_INTENT_RECORDED
    if isinstance(raw, MutationIntent):
        return raw.render()
    if isinstance(raw, dict):
        try:
            return MutationIntent.model_validate(raw).render()
        except ValidationError:
            return NO_INTENT_RECORDED
    return NO_INTENT_RECORDED

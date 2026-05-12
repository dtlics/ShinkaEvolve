"""Agentic LLM layer built on top of the openai-agents SDK.

This package replaces the bespoke per-provider query loop in
``shinka.llm.providers`` for OpenAI / Azure OpenAI calls. Non-OpenAI
providers continue to use the legacy path. See ``AGENTIC_REWRITE.md`` at
the repo root for the full migration plan.
"""

from .background_model import (
    BackgroundOpenAIResponsesModel,
    BackgroundPollTimeout,
)
from .robust_runner import RobustRunner

__all__ = [
    "BackgroundOpenAIResponsesModel",
    "BackgroundPollTimeout",
    "RobustRunner",
]

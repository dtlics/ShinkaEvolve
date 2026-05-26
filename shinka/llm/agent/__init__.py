"""Azure background-mode transport for long reasoning calls.

Slimmed to what the orchestrator's deep-research path needs: the
``BackgroundOpenAIResponsesModel`` (submit + poll, resilient to Azure
long-idle-TCP kills) and the DR client. The old agentic proposer
(`AgentLLMClient` + tools) was removed in the orchestrator rewrite — the inner
loop's mutation call lives in `orchestrator/scripts/mutate.py`.
"""

from .background_model import (
    BackgroundOpenAIResponsesModel,
    BackgroundPollTimeout,
)

__all__ = [
    "BackgroundOpenAIResponsesModel",
    "BackgroundPollTimeout",
]

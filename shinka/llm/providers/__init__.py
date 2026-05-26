"""LLM providers — Azure/OpenAI only (non-OpenAI providers removed in the
Azure-only prune). `query_openai` serves both `openai` and `azure_openai`."""

from .openai import query_openai, query_openai_async
from .result import QueryResult

__all__ = [
    "query_openai",
    "query_openai_async",
    "QueryResult",
]

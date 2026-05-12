from typing import List, Optional, Dict
from pydantic import BaseModel
from .client import get_client_llm, get_async_client_llm
from .providers import (
    query_openai,
    query_openai_async,
    QueryResult,
)
import logging

logger = logging.getLogger(__name__)


def query(
    model_name: str,
    msg: str,
    system_msg: str,
    msg_history: List = [],
    output_model: Optional[BaseModel] = None,
    model_posteriors: Optional[Dict[str, float]] = None,
    **kwargs,
) -> QueryResult:
    """Query the LLM (OpenAI or Azure OpenAI only)."""
    client, model_name, provider = get_client_llm(
        model_name, structured_output=output_model is not None
    )
    if provider not in ("openai", "azure_openai"):
        raise ValueError(f"Model {model_name} not supported.")
    result = query_openai(
        client,
        model_name,
        msg,
        system_msg,
        msg_history,
        output_model,
        model_posteriors=model_posteriors,
        **kwargs,
    )
    return result


async def query_async(
    model_name: str,
    msg: str,
    system_msg: str,
    msg_history: List = [],
    output_model: Optional[BaseModel] = None,
    model_posteriors: Optional[Dict[str, float]] = None,
    **kwargs,
) -> QueryResult:
    """Query the LLM asynchronously (OpenAI or Azure OpenAI only)."""
    client, model_name, provider = get_async_client_llm(
        model_name, structured_output=output_model is not None
    )
    if provider not in ("openai", "azure_openai"):
        raise ValueError(f"Model {model_name} not supported.")
    result = await query_openai_async(
        client,
        model_name,
        msg,
        system_msg,
        msg_history,
        output_model,
        model_posteriors=model_posteriors,
        **kwargs,
    )
    return result

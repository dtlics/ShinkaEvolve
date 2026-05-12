"""OpenAI / Azure OpenAI provider, threaded through the bg+poll helpers.

Phase 2b of research-grounding routed everything through ``create_and_poll``;
Phase 3a now also accepts a ``tools`` kwarg + companion ``tool_budget`` /
``tool_context`` and dispatches client-side function tools when the API
returns ``status="requires_action"``.
"""

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from .pricing import calculate_cost, model_exists
from .result import QueryResult
from ..constants import POLL_TIMEOUT_DEFAULT
from ..poll import (
    create_and_poll,
    create_and_poll_async,
    create_and_poll_parse,
    create_and_poll_parse_async,
)
from ..tools import (
    ToolBudget,
    ToolBudgetExceeded,
    ToolSpec,
    lookup_tool_by_name,
    serialize_tools,
)

logger = logging.getLogger(__name__)


def _build_tool_dispatcher(
    tools: Optional[Iterable[ToolSpec]],
    tool_budget: Optional[ToolBudget],
    tool_context: Optional[Dict[str, Any]],
    trace: List[Dict[str, Any]],
):
    """Sync dispatcher that the bg+poll loop hands ``requires_action`` calls to.

    Server-side tools never reach here, so dispatched names should always
    be in our ToolSpec list. Budget violations and unknown tool names emit
    an error payload (the model sees it and decides whether to abort).
    """
    if not tools:
        return None

    def _dispatch(pending):
        outputs = []
        for call in pending:
            name = call.get("name") or ""
            args = call.get("arguments") or {}
            call_id = call.get("call_id")
            tool = lookup_tool_by_name(tools, name)
            if tool is None:
                payload = {"error": f"unknown tool {name!r}"}
            else:
                try:
                    if tool_budget is not None:
                        tool_budget.spend(name)
                    payload = tool.dispatch(args, tool_context or {})
                except ToolBudgetExceeded as exc:
                    payload = {"error": str(exc)}
                except Exception as exc:  # noqa: BLE001
                    payload = {"error": f"dispatch failed: {exc}"}
            trace.append({"name": name, "args": args, "output": payload})
            outputs.append(
                {"tool_call_id": call_id, "output": json.dumps(payload)}
            )
        if tool_budget is not None:
            tool_budget.consume_turn()
        return outputs

    return _dispatch


def _build_tool_dispatcher_async(
    tools: Optional[Iterable[ToolSpec]],
    tool_budget: Optional[ToolBudget],
    tool_context: Optional[Dict[str, Any]],
    trace: List[Dict[str, Any]],
):
    if not tools:
        return None

    async def _dispatch(pending):
        outputs = []
        for call in pending:
            name = call.get("name") or ""
            args = call.get("arguments") or {}
            call_id = call.get("call_id")
            tool = lookup_tool_by_name(tools, name)
            if tool is None:
                payload = {"error": f"unknown tool {name!r}"}
            else:
                try:
                    if tool_budget is not None:
                        tool_budget.spend(name)
                    if hasattr(tool, "dispatch_async"):
                        payload = await tool.dispatch_async(  # type: ignore[attr-defined]
                            args, tool_context or {}
                        )
                    else:
                        payload = tool.dispatch(args, tool_context or {})
                except ToolBudgetExceeded as exc:
                    payload = {"error": str(exc)}
                except Exception as exc:  # noqa: BLE001
                    payload = {"error": f"dispatch failed: {exc}"}
            trace.append({"name": name, "args": args, "output": payload})
            outputs.append(
                {"tool_call_id": call_id, "output": json.dumps(payload)}
            )
        if tool_budget is not None:
            tool_budget.consume_turn()
        return outputs

    return _dispatch


def get_openai_costs(response, model):
    # Get token counts and costs
    in_tokens = response.usage.input_tokens
    try:
        thinking_tokens = response.usage.output_tokens_details.reasoning_tokens
    except Exception:
        thinking_tokens = 0
    all_out_tokens = response.usage.output_tokens
    out_tokens = response.usage.output_tokens - thinking_tokens

    if model_exists(model):
        input_cost, output_cost = calculate_cost(model, in_tokens, all_out_tokens)
    else:
        logger.warning(
            "Model '%s' has no pricing entry. Defaulting query cost to 0.",
            model,
        )
        input_cost, output_cost = 0.0, 0.0
    return {
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "thinking_tokens": thinking_tokens,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "cost": input_cost + output_cost,
    }


def _extract_text_output(response):
    """Pull the assistant text out of a Responses API response.

    The output array can interleave reasoning items and message items; we
    walk it and concatenate any text content we find. Returns ``("", "")``
    if no usable text is present (e.g. ``status="incomplete"`` mid-tool-use,
    which Phase 3 will handle).
    """
    content_text = ""
    thought_text = ""
    output = getattr(response, "output", None) or []
    for item in output:
        item_type = getattr(item, "type", None)
        if item_type == "reasoning":
            summary = getattr(item, "summary", None) or []
            for piece in summary:
                piece_text = getattr(piece, "text", None)
                if piece_text:
                    thought_text = piece_text
                    break
            continue
        content = getattr(item, "content", None) or []
        for piece in content:
            piece_text = getattr(piece, "text", None)
            if piece_text:
                content_text = piece_text
                break
        if content_text:
            break
    return content_text, thought_text


def query_openai(
    client,
    model,
    msg,
    system_msg,
    msg_history,
    output_model,
    model_posteriors=None,
    poll_timeout: float = POLL_TIMEOUT_DEFAULT,
    delete_after: bool = True,
    tools: Optional[Iterable[ToolSpec]] = None,
    tool_budget: Optional[ToolBudget] = None,
    tool_context: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> QueryResult:
    """Query an OpenAI / Azure OpenAI Responses model in bg+poll mode."""
    new_msg_history = msg_history + [{"role": "user", "content": msg}]
    thought = ""
    tool_trace: List[Dict[str, Any]] = []
    extra_create_kwargs: Dict[str, Any] = {}
    serialized_tools = serialize_tools(tools)
    if serialized_tools is not None:
        extra_create_kwargs["tools"] = serialized_tools
        # ``auto`` lets the model decide; we never set ``required`` to avoid
        # unbounded tool loops (research-grounding plan gotcha #7).
        extra_create_kwargs.setdefault("tool_choice", "auto")
        # Force serial dispatch so client-side budgeting actually bites.
        extra_create_kwargs["parallel_tool_calls"] = False
    dispatcher = _build_tool_dispatcher(tools, tool_budget, tool_context, tool_trace)

    if output_model is None:
        response = create_and_poll(
            client,
            poll_timeout=poll_timeout,
            delete_after=delete_after,
            tool_dispatcher=dispatcher,
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            **{**extra_create_kwargs, **kwargs},
        )
        content, thought = _extract_text_output(response)
        if not content:
            # Reasoning models can put the message at output[1]; fall back to
            # the legacy heuristic if the walker didn't surface anything.
            try:
                content = response.output[1].content[0].text
            except Exception:
                content = ""
        new_msg_history.append({"role": "assistant", "content": content})
    else:
        response = create_and_poll_parse(
            client,
            text_format=output_model,
            poll_timeout=poll_timeout,
            delete_after=delete_after,
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            **kwargs,
        )
        content = response.output_parsed
        new_content = ""
        for i in content:
            new_content += i[0] + ":" + i[1] + "\n"
        new_msg_history.append({"role": "assistant", "content": new_content})

    cost_results = get_openai_costs(response, model)
    enriched_kwargs = dict(kwargs)
    enriched_kwargs["response_status"] = getattr(response, "status", None)
    enriched_kwargs["response_id"] = getattr(response, "id", None)
    if tool_trace:
        enriched_kwargs["tool_trace"] = tool_trace

    return QueryResult(
        content=content,
        msg=msg,
        system_msg=system_msg,
        new_msg_history=new_msg_history,
        model_name=model,
        kwargs=enriched_kwargs,
        **cost_results,
        thought=thought,
        model_posteriors=model_posteriors,
        num_tool_calls=len(tool_trace),
    )


async def query_openai_async(
    client,
    model,
    msg,
    system_msg,
    msg_history,
    output_model,
    model_posteriors=None,
    poll_timeout: float = POLL_TIMEOUT_DEFAULT,
    delete_after: bool = True,
    tools: Optional[Iterable[ToolSpec]] = None,
    tool_budget: Optional[ToolBudget] = None,
    tool_context: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> QueryResult:
    """Async mirror of :func:`query_openai`."""
    new_msg_history = msg_history + [{"role": "user", "content": msg}]
    thought = ""
    tool_trace: List[Dict[str, Any]] = []
    extra_create_kwargs: Dict[str, Any] = {}
    serialized_tools = serialize_tools(tools)
    if serialized_tools is not None:
        extra_create_kwargs["tools"] = serialized_tools
        extra_create_kwargs.setdefault("tool_choice", "auto")
        extra_create_kwargs["parallel_tool_calls"] = False
    dispatcher = _build_tool_dispatcher_async(
        tools, tool_budget, tool_context, tool_trace
    )

    if output_model is None:
        response = await create_and_poll_async(
            client,
            poll_timeout=poll_timeout,
            delete_after=delete_after,
            tool_dispatcher=dispatcher,
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            **{**extra_create_kwargs, **kwargs},
        )
        content, thought = _extract_text_output(response)
        if not content:
            try:
                content = response.output[1].content[0].text
            except Exception:
                content = ""
        new_msg_history.append({"role": "assistant", "content": content})
    else:
        response = await create_and_poll_parse_async(
            client,
            text_format=output_model,
            poll_timeout=poll_timeout,
            delete_after=delete_after,
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            **kwargs,
        )
        content = response.output_parsed
        new_content = ""
        for i in content:
            new_content += i[0] + ":" + i[1] + "\n"
        new_msg_history.append({"role": "assistant", "content": new_content})

    cost_results = get_openai_costs(response, model)
    enriched_kwargs = dict(kwargs)
    enriched_kwargs["response_status"] = getattr(response, "status", None)
    enriched_kwargs["response_id"] = getattr(response, "id", None)
    if tool_trace:
        enriched_kwargs["tool_trace"] = tool_trace

    return QueryResult(
        content=content,
        msg=msg,
        system_msg=system_msg,
        new_msg_history=new_msg_history,
        model_name=model,
        kwargs=enriched_kwargs,
        **cost_results,
        thought=thought,
        model_posteriors=model_posteriors,
        num_tool_calls=len(tool_trace),
    )

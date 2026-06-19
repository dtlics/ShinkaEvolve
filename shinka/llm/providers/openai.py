"""OpenAI/Azure Responses-API query implementation.

This is the sole query implementation for the OpenAI/Azure backend: every
``shinka.llm.query`` call dispatches into ``query_openai`` /
``query_openai_async`` unconditionally for each supported provider. (The
former ``AgentLLMClient._query_via_legacy`` path and the OpenAI Agents SDK
Runner were removed in the Azure-only prune.)
"""

import openai
from .pricing import calculate_cost, model_exists
from .result import QueryResult
import logging

logger = logging.getLogger(__name__)


def _extract_message_text(response) -> str:
    """Iterate response.output and return text from the first 'message' item.

    The /v1/responses endpoint returns a list of output items mixing
    'reasoning' (no `.content`, has `.summary`) and 'message' (has `.content`)
    types. Reasoning models typically emit one of each, in either order, but
    can also emit:
      - reasoning-only (model exhausted output budget on reasoning),
      - multiple reasoning items,
      - a single message (non-reasoning models).
    The previous implementation hard-coded `output[0]` then `output[1]` as
    fallbacks, which IndexError'd on reasoning-only or single-item responses.
    Iterating safely is the correct shape.
    """
    for item in response.output:
        if getattr(item, "type", None) == "message":
            content = getattr(item, "content", None)
            if content:
                first = content[0]
                text = getattr(first, "text", None)
                if text is not None:
                    return text
    types = [getattr(item, "type", "?") for item in response.output]
    raise ValueError(
        f"Response has no 'message' item with text content; output items={types}. "
        f"Most common cause: reasoning model exhausted its output token budget "
        f"on reasoning before producing a message. Increase max_output_tokens "
        f"or lower reasoning_effort."
    )


def _extract_thought_text(response) -> str:
    """Return the first 'reasoning' item's summary text, or '' if absent."""
    for item in response.output:
        if getattr(item, "type", None) == "reasoning":
            summary = getattr(item, "summary", None)
            if summary:
                first = summary[0]
                text = getattr(first, "text", None)
                if text:
                    return text
    return ""


def get_openai_costs(response, model):
    # Get token counts and costs
    in_tokens = response.usage.input_tokens
    try:
        thinking_tokens = response.usage.output_tokens_details.reasoning_tokens
    except Exception:
        thinking_tokens = 0
    all_out_tokens = response.usage.output_tokens
    out_tokens = response.usage.output_tokens - thinking_tokens

    # Get actual costs from OpenRouter API if available -- if not use OAI
    cost_details = getattr(response.usage, "cost_details", None)
    if cost_details:
        if isinstance(cost_details, dict):
            input_cost = float(cost_details.get("upstream_inference_input_cost", 0.0))
            output_cost = float(cost_details.get("upstream_inference_output_cost", 0.0))
        else:
            input_cost = float(
                getattr(cost_details, "upstream_inference_input_cost", 0.0) or 0.0
            )
            output_cost = float(
                getattr(cost_details, "upstream_inference_output_cost", 0.0) or 0.0
            )
    elif model_exists(model):
        input_cost, output_cost = calculate_cost(model, in_tokens, all_out_tokens)
    else:
        logger.warning(
            "Model '%s' has no pricing entry and response cost metadata is absent. "
            "Defaulting query cost to 0.",
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


def query_openai(
    client,
    model,
    msg,
    system_msg,
    msg_history,
    output_model,
    model_posteriors=None,
    **kwargs,
) -> QueryResult:
    """Query OpenAI model."""
    new_msg_history = msg_history + [{"role": "user", "content": msg}]
    thought = ""
    if output_model is None:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            **kwargs,
        )
        content = _extract_message_text(response)
        thought = _extract_thought_text(response)
        new_msg_history.append({"role": "assistant", "content": content})
    else:
        response = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            text_format=output_model,
            **kwargs,
        )
        content = response.output_parsed
        new_content = ""
        for i in content:
            new_content += i[0] + ":" + i[1] + "\n"
        new_msg_history.append({"role": "assistant", "content": new_content})

    # Get token counts and costs
    cost_results = get_openai_costs(response, model)

    # Collect all results
    result = QueryResult(
        content=content,
        msg=msg,
        system_msg=system_msg,
        new_msg_history=new_msg_history,
        model_name=model,
        kwargs=kwargs,
        **cost_results,
        thought=thought,
        model_posteriors=model_posteriors,
    )
    return result


async def query_openai_async(
    client,
    model,
    msg,
    system_msg,
    msg_history,
    output_model,
    model_posteriors=None,
    **kwargs,
) -> QueryResult:
    """Query OpenAI model asynchronously."""
    new_msg_history = msg_history + [{"role": "user", "content": msg}]
    thought = ""
    if output_model is None:
        response = await client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            **kwargs,
        )
        content = _extract_message_text(response)
        thought = _extract_thought_text(response)
        new_msg_history.append({"role": "assistant", "content": content})
    else:
        response = await client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            text_format=output_model,
            **kwargs,
        )
        content = response.output_parsed
        new_content = ""
        for i in content:
            new_content += i[0] + ":" + i[1] + "\n"
        new_msg_history.append({"role": "assistant", "content": new_content})
    cost_results = get_openai_costs(response, model)
    result = QueryResult(
        content=content,
        msg=msg,
        system_msg=system_msg,
        new_msg_history=new_msg_history,
        model_name=model,
        kwargs=kwargs,
        **cost_results,
        thought=thought,
        model_posteriors=model_posteriors,
    )
    return result

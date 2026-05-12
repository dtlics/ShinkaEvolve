"""OpenAI / Azure OpenAI provider, threaded through the bg+poll helpers.

Phase 2b of research-grounding: all calls now use ``create_and_poll`` instead
of a blocking foreground ``client.responses.create(...)``. The previous
``@backoff.on_exception`` decorator that retried whole-call failures is gone:
transient errors are caught and retried inside the polling layer.
"""

from .pricing import calculate_cost, model_exists
from .result import QueryResult
from ..constants import POLL_TIMEOUT_DEFAULT
from ..poll import (
    create_and_poll,
    create_and_poll_async,
    create_and_poll_parse,
    create_and_poll_parse_async,
)
import logging

logger = logging.getLogger(__name__)


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
    **kwargs,
) -> QueryResult:
    """Query an OpenAI / Azure OpenAI Responses model in bg+poll mode."""
    new_msg_history = msg_history + [{"role": "user", "content": msg}]
    thought = ""
    if output_model is None:
        response = create_and_poll(
            client,
            poll_timeout=poll_timeout,
            delete_after=delete_after,
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            **kwargs,
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
    **kwargs,
) -> QueryResult:
    """Async mirror of :func:`query_openai`."""
    new_msg_history = msg_history + [{"role": "user", "content": msg}]
    thought = ""
    if output_model is None:
        response = await create_and_poll_async(
            client,
            poll_timeout=poll_timeout,
            delete_after=delete_after,
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                *new_msg_history,
            ],
            **kwargs,
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
    )

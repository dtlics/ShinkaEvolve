from typing import List, Union, Optional
import random
from .providers.pricing import (
    is_reasoning_model,
    has_fixed_temperature,
    requires_reasoning,
)
from .providers.model_resolver import resolve_model_backend
import logging

logger = logging.getLogger(__name__)


def sample_batch_kwargs(
    num_samples: int,
    model_names: Union[List[str], str] = "gpt-5-mini",
    temperatures: Union[List[float], float] = 0.0,
    max_tokens: Union[List[int], int] = 4096,
    reasoning_efforts: Union[List[str], str] = "",
    model_sample_probs: Optional[List[float]] = None,
    unique_filter: bool = False,
):
    """Sample a dictionary of kwargs for a given model."""
    all_kwargs = []
    attempts = 0
    max_attempts = num_samples * 10  # Prevent infinite loops

    while len(all_kwargs) < num_samples and attempts < max_attempts:
        kwargs_dict = sample_model_kwargs(
            model_names=model_names,
            temperatures=temperatures,
            max_tokens=max_tokens,
            reasoning_efforts=reasoning_efforts,
            model_sample_probs=model_sample_probs,
        )

        if unique_filter:
            if kwargs_dict not in all_kwargs:
                all_kwargs.append(kwargs_dict)
        else:
            all_kwargs.append(kwargs_dict)

        attempts += 1

    if len(all_kwargs) < num_samples:
        logger.info(
            f"Could not generate {num_samples} unique kwargs combinations "
            f"after {max_attempts} attempts"
        )
        logger.info(f"Returning {len(all_kwargs)} unique kwargs combinations.")

    return all_kwargs


def sample_model_kwargs(
    model_names: Union[List[str], str] = "gpt-5-mini",
    temperatures: Union[List[float], float] = 0.0,
    max_tokens: Union[List[int], int] = 4096,
    reasoning_efforts: Union[List[str], str] = "",
    model_sample_probs: Optional[List[float]] = None,
):
    """Sample a dictionary of kwargs for OpenAI/Azure OpenAI models."""
    # Make all inputs lists
    if isinstance(model_names, str):
        model_names = [model_names]
    if isinstance(temperatures, float):
        temperatures = [temperatures]
    if isinstance(max_tokens, int):
        max_tokens = [max_tokens]
    if isinstance(reasoning_efforts, str):
        reasoning_efforts = [reasoning_efforts]

    kwargs_dict = {}

    # 1. SAMPLE: model name
    if model_sample_probs is not None:
        if len(model_sample_probs) != len(model_names):
            raise ValueError(
                "model_sample_probs must have the same length as model_names"
            )
        if not abs(sum(model_sample_probs) - 1.0) < 1e-9:
            raise ValueError("model_sample_probs must sum to 1")
        kwargs_dict["model_name"] = random.choices(
            model_names, weights=model_sample_probs, k=1
        )[0]
    else:
        kwargs_dict["model_name"] = random.choice(model_names)

    model_name = kwargs_dict["model_name"]
    resolved_model = resolve_model_backend(model_name)
    api_model_name = resolved_model.api_model_name

    # 2. SAMPLE: reasoning effort
    if is_reasoning_model(api_model_name):
        r_effort = random.choice(reasoning_efforts)
    else:
        r_effort = "disabled"

    # Some reasoning-required models cannot run with reasoning disabled.
    if requires_reasoning(api_model_name) and r_effort == "disabled":
        r_effort = "low"

    # 3. SAMPLE: temperature with possible reasoning restrictions
    # OpenAI/Azure reasoning models require temperature=1.0 (think_temp_fixed).
    if has_fixed_temperature(api_model_name) and r_effort != "disabled":
        kwargs_dict["temperature"] = 1.0
    else:
        # OpenAI reasoning models also expect temperature=1.0 even when reasoning is
        # disabled; only non-reasoning OpenAI/Azure models accept arbitrary temps.
        if is_reasoning_model(api_model_name):
            kwargs_dict["temperature"] = 1.0
        else:
            kwargs_dict["temperature"] = random.choice(temperatures)

    # 4. SET: max_output_tokens + reasoning kwarg for OpenAI/Azure
    kwargs_dict["max_output_tokens"] = random.choice(max_tokens)
    if is_reasoning_model(api_model_name):
        if r_effort == "disabled":
            kwargs_dict["reasoning"] = {"effort": None}
        elif r_effort == "min":
            kwargs_dict["reasoning"] = {"effort": "low"}
        elif r_effort == "max":
            kwargs_dict["reasoning"] = {"effort": "high"}
        else:
            kwargs_dict["reasoning"] = {"effort": r_effort}

        # Auto-summarization for OpenAI reasoning models (Azure surfaces summary too).
        if r_effort != "disabled":
            kwargs_dict["reasoning"]["summary"] = "auto"

    return kwargs_dict

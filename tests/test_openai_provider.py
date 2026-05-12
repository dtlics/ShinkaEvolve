from types import SimpleNamespace

from shinka.llm.providers.openai import get_openai_costs


def _usage(
    *,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
):
    output_details = SimpleNamespace(reasoning_tokens=reasoning_tokens)
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        output_tokens_details=output_details,
    )


def test_get_openai_costs_defaults_to_zero_for_unknown_model():
    response = SimpleNamespace(
        usage=_usage(
            input_tokens=10,
            output_tokens=20,
            reasoning_tokens=5,
        )
    )

    costs = get_openai_costs(response, "not-in-pricing")
    assert costs["input_tokens"] == 10
    assert costs["output_tokens"] == 15
    assert costs["thinking_tokens"] == 5
    assert costs["input_cost"] == 0.0
    assert costs["output_cost"] == 0.0
    assert costs["cost"] == 0.0


def test_get_openai_costs_uses_pricing_table_for_known_model():
    response = SimpleNamespace(
        usage=_usage(
            input_tokens=1_000_000,
            output_tokens=500_000,
            reasoning_tokens=0,
        )
    )

    costs = get_openai_costs(response, "gpt-5-mini")
    # gpt-5-mini: input $0.25/M, output $2.00/M
    assert costs["input_tokens"] == 1_000_000
    assert costs["output_tokens"] == 500_000
    assert costs["thinking_tokens"] == 0
    assert costs["input_cost"] == 0.25
    assert costs["output_cost"] == 1.0
    assert costs["cost"] == 1.25

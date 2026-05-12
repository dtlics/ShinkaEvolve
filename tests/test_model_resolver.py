import pytest

from shinka.llm.providers.model_resolver import resolve_model_backend


def test_resolve_known_pricing_model():
    resolved = resolve_model_backend("gpt-5-mini")
    assert resolved.provider == "openai"
    assert resolved.api_model_name == "gpt-5-mini"
    assert resolved.base_url is None


@pytest.mark.parametrize(
    "model_name",
    ["gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano"],
)
def test_resolve_new_openai_pricing_models(model_name: str):
    resolved = resolve_model_backend(model_name)
    assert resolved.provider == "openai"
    assert resolved.api_model_name == model_name
    assert resolved.base_url is None


def test_resolve_codex_model():
    resolved = resolve_model_backend("gpt-5-codex")
    assert resolved.provider == "openai"
    assert resolved.api_model_name == "gpt-5-codex"


def test_resolve_o3_deep_research_model():
    resolved = resolve_model_backend("o3-deep-research")
    assert resolved.provider == "openai"
    assert resolved.api_model_name == "o3-deep-research"


def test_resolve_azure_prefixed_model():
    resolved = resolve_model_backend("azure-gpt-4.1")
    assert resolved.provider == "azure_openai"
    assert resolved.api_model_name == "gpt-4.1"


def test_resolve_unsupported_model_raises():
    with pytest.raises(ValueError, match="not supported"):
        resolve_model_backend("claude-opus-4-7")


def test_resolve_unknown_model_raises():
    with pytest.raises(ValueError, match="not supported"):
        resolve_model_backend("does-not-exist")

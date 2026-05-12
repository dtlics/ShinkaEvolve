"""Phase 2a of research-grounding — DR Azure client factory.

The deep-research endpoint is a **separate Azure resource** from the
main gpt-* endpoint. ``get_dr_async_client`` reads dedicated env vars
(``AZURE_DR_ENDPOINT`` / ``AZURE_DR_API_KEY`` / ``AZURE_DR_API_VERSION``)
and builds an ``AsyncAzureOpenAI`` pointed at that resource's responses
API base URL.

These tests run offline — we never touch real Azure. They cover URL
canonicalization, env-var failure messages, and DeepResearchModel
cadence defaults.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from shinka.llm.agent.dr_client import (
    DR_API_KEY_ENV,
    DR_ENDPOINT_ENV,
    DR_API_VERSION_ENV,
    DR_MAX_QUEUED_WAIT_SEC,
    DR_POLL_INTERVAL_SEC,
    DR_TIMEOUT,
    DeepResearchModel,
    _build_dr_base_url,
    get_dr_async_client,
)


def test_build_dr_base_url_appends_openai_v1_when_absent() -> None:
    """A bare endpoint must get ``/openai/v1`` appended so AsyncAzureOpenAI's
    base_url ends at the path the Responses API expects."""
    url = _build_dr_base_url(
        "https://dtlics2000-4351-resource.services.ai.azure.com/api/projects/dtlics2000-4351"
    )
    assert url == (
        "https://dtlics2000-4351-resource.services.ai.azure.com"
        "/api/projects/dtlics2000-4351/openai/v1"
    )


def test_build_dr_base_url_strips_trailing_slash() -> None:
    """Trailing slashes are normalized so two equivalent inputs converge."""
    bare = _build_dr_base_url("https://x.services.ai.azure.com/api/projects/p")
    slashed = _build_dr_base_url("https://x.services.ai.azure.com/api/projects/p/")
    assert bare == slashed


def test_build_dr_base_url_idempotent_when_v1_present() -> None:
    """If the user supplies ``...openai/v1`` we don't double-append."""
    url = _build_dr_base_url(
        "https://x.services.ai.azure.com/api/projects/p/openai/v1"
    )
    assert url == "https://x.services.ai.azure.com/api/projects/p/openai/v1"


def test_build_dr_base_url_strips_responses_suffix() -> None:
    """If the user pasted the full responses URL, drop ``/responses`` since
    AsyncAzureOpenAI re-appends it from the create call's path."""
    url = _build_dr_base_url(
        "https://x.services.ai.azure.com/api/projects/p/openai/v1/responses"
    )
    assert url == "https://x.services.ai.azure.com/api/projects/p/openai/v1"


def test_get_dr_async_client_requires_endpoint(monkeypatch) -> None:
    """Missing endpoint is a hard error — the message must point at the
    env-var name so the user knows what to set."""
    monkeypatch.delenv(DR_ENDPOINT_ENV, raising=False)
    monkeypatch.setenv(DR_API_KEY_ENV, "k")
    with pytest.raises(RuntimeError) as exc:
        get_dr_async_client()
    assert DR_ENDPOINT_ENV in str(exc.value)


def test_get_dr_async_client_requires_api_key(monkeypatch) -> None:
    """Missing key is a hard error too."""
    monkeypatch.setenv(DR_ENDPOINT_ENV, "https://x.services.ai.azure.com/api/projects/p")
    monkeypatch.delenv(DR_API_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError) as exc:
        get_dr_async_client()
    assert DR_API_KEY_ENV in str(exc.value)


def test_get_dr_async_client_builds_client_when_env_set(monkeypatch) -> None:
    """With both env vars set, the factory returns ``(client, base_url)``.
    We patch ``AsyncAzureOpenAI`` to avoid making a real connection — the
    test asserts on the kwargs passed to the constructor."""
    monkeypatch.setenv(
        DR_ENDPOINT_ENV,
        "https://dr-resource.services.ai.azure.com/api/projects/dr-proj",
    )
    monkeypatch.setenv(DR_API_KEY_ENV, "dr-key-42")
    monkeypatch.delenv(DR_API_VERSION_ENV, raising=False)

    captured = {}

    def _fake_client(**kwargs):
        captured.update(kwargs)
        return object()

    with patch("openai.AsyncAzureOpenAI", side_effect=_fake_client):
        client, base_url = get_dr_async_client()

    assert client is not None
    assert base_url == (
        "https://dr-resource.services.ai.azure.com/api/projects/dr-proj/openai/v1"
    )
    assert captured["api_key"] == "dr-key-42"
    # api_version defaults to "preview" so o3-deep-research preview surface
    # is reachable.
    assert captured["api_version"] == "preview"
    assert captured["base_url"] == base_url


def test_get_dr_async_client_honors_explicit_api_version(monkeypatch) -> None:
    """When ``AZURE_DR_API_VERSION`` is set, the factory uses it instead
    of the ``preview`` default — useful when the user pins to a GA
    revision."""
    monkeypatch.setenv(
        DR_ENDPOINT_ENV, "https://x.services.ai.azure.com/api/projects/p"
    )
    monkeypatch.setenv(DR_API_KEY_ENV, "k")
    monkeypatch.setenv(DR_API_VERSION_ENV, "2026-04-01-preview")

    captured = {}

    def _fake_client(**kwargs):
        captured.update(kwargs)
        return object()

    with patch("openai.AsyncAzureOpenAI", side_effect=_fake_client):
        get_dr_async_client()

    assert captured["api_version"] == "2026-04-01-preview"


def test_deep_research_model_defaults_match_dr_cadence() -> None:
    """DeepResearchModel must default to DR-tuned cadence: 5s poll
    interval, 30 min wall cap, 10 min max queue. These values are the
    knobs the summarizer relies on; if they drift, DR runs either
    burn rate limits or time out on legitimate work."""
    model = DeepResearchModel(
        model="o3-deep-research",
        openai_client=object(),  # never touched
    )
    assert model.poll_interval_sec == DR_POLL_INTERVAL_SEC == 5.0
    assert model.poll_timeout_sec == DR_TIMEOUT == 1800.0
    assert model.max_queued_wait_sec == DR_MAX_QUEUED_WAIT_SEC == 600.0

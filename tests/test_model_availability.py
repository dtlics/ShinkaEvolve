from __future__ import annotations

from pathlib import Path

import pytest

from shinka.core import EvolutionConfig, ShinkaEvolveRunner
from shinka.database import DatabaseConfig
from shinka.launch import LocalJobConfig
from shinka.model_availability import validate_model_env_access


_PROVIDER_ENV_VARS = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_API_ENDPOINT",
    "AZURE_API_VERSION",
    "OPENAI_API_KEY",
)


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var_name in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(env_var_name, raising=False)


def test_validate_model_env_access_rejects_missing_llm_provider_key(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_provider_env(monkeypatch)

    with pytest.raises(ValueError) as exc_info:
        validate_model_env_access(llm_models=["gpt-5-mini"])

    error = str(exc_info.value)
    assert "gpt-5-mini" in error
    assert "OPENAI_API_KEY" in error


def test_validate_model_env_access_rejects_missing_embedding_provider_key(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_provider_env(monkeypatch)

    with pytest.raises(ValueError) as exc_info:
        validate_model_env_access(embedding_models=["text-embedding-3-small"])

    error = str(exc_info.value)
    assert "text-embedding-3-small" in error
    assert "OPENAI_API_KEY" in error


def test_validate_model_env_access_rejects_incomplete_azure_env(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")

    with pytest.raises(ValueError) as exc_info:
        validate_model_env_access(llm_models=["azure-gpt-4.1"])

    error = str(exc_info.value)
    assert "azure-gpt-4.1" in error
    assert "AZURE_API_ENDPOINT" in error


def test_validate_model_env_access_passes_when_keys_present(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    validate_model_env_access(
        llm_models=["gpt-5-mini"],
        embedding_models=["text-embedding-3-small"],
    )


def test_async_runner_fails_fast_when_requested_model_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _clear_provider_env(monkeypatch)
    results_dir = tmp_path / "results"

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        ShinkaEvolveRunner(
            evo_config=EvolutionConfig(
                llm_models=["gpt-5-mini"],
                llm_dynamic_selection=None,
                meta_rec_interval=None,
                embedding_model=None,
                num_generations=1,
                results_dir=str(results_dir),
            ),
            job_config=LocalJobConfig(),
            db_config=DatabaseConfig(),
            verbose=False,
        )

    assert not results_dir.exists()

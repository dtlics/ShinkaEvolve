from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import shinka.cli.models as cli_models
from shinka.env import load_shinka_dotenv as real_load_shinka_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
LLM_PRICING_CSV = REPO_ROOT / "shinka" / "llm" / "providers" / "pricing.csv"
EMBED_PRICING_CSV = REPO_ROOT / "shinka" / "embed" / "providers" / "pricing.csv"
PROVIDER_ENV_VARS = {
    "azure": ("AZURE_OPENAI_API_KEY", "AZURE_API_ENDPOINT", "AZURE_API_VERSION"),
    "openai": ("OPENAI_API_KEY",),
}


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var_names in PROVIDER_ENV_VARS.values():
        for env_var_name in env_var_names:
            monkeypatch.delenv(env_var_name, raising=False)


def _models_for_provider(provider: str, pricing_csv: Path) -> list[str]:
    with pricing_csv.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        return sorted(
            row["model_name"].strip()
            for row in rows
            if row["provider"].strip() == provider
        )


def _llm_models_for_provider(provider: str) -> list[str]:
    return _models_for_provider(provider, LLM_PRICING_CSV)


def _embedding_models_for_provider(provider: str) -> list[str]:
    return _models_for_provider(provider, EMBED_PRICING_CSV)


def _run_cli(
    capsys: pytest.CaptureFixture[str], argv: list[str] | None = None
) -> tuple[int, list[str] | dict]:
    exit_code = cli_models.main([] if argv is None else argv)
    output = capsys.readouterr().out
    return exit_code, json.loads(output)


def test_shinka_models_help_describes_json_output(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        cli_models.main(["--help"])

    assert exc_info.value.code == 0
    help_output = capsys.readouterr().out
    assert "Inspect current environment variables and discovered .env files" in help_output
    assert "available_providers" in help_output
    assert '"embedding": [...]' in help_output
    assert '"llm": [...]' in help_output
    assert "--verbose" in help_output
    assert "current environment" in help_output
    assert ".env" in help_output


def test_shinka_models_lists_openai_models_when_key_present(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(cli_models, "load_shinka_dotenv", lambda: ())
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    exit_code, payload = _run_cli(capsys)

    expected_embedding_models = _embedding_models_for_provider("openai")
    expected_llm_models = _llm_models_for_provider("openai")
    assert exit_code == 0
    assert payload == {
        "embedding": expected_embedding_models,
        "llm": expected_llm_models,
    }


def test_shinka_models_verbose_prints_full_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(cli_models, "load_shinka_dotenv", lambda: ())
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    exit_code, payload = _run_cli(capsys, ["--verbose"])

    expected_llm_models = _llm_models_for_provider("openai")
    expected_embedding_models = _embedding_models_for_provider("openai")
    assert exit_code == 0
    assert payload == {
        "available_providers": [
            {
                "env_vars": {"OPENAI_API_KEY": True},
                "embedding_models": expected_embedding_models,
                "llm_models": expected_llm_models,
                "provider": "openai",
            }
        ],
        "embedding": expected_embedding_models,
        "llm": expected_llm_models,
    }


def test_shinka_models_requires_full_azure_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(cli_models, "load_shinka_dotenv", lambda: ())
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-azure-key")

    exit_code, payload = _run_cli(capsys)

    assert exit_code == 0
    assert payload == {"embedding": [], "llm": []}


def test_shinka_models_lists_azure_models_when_all_required_env_vars_present(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(cli_models, "load_shinka_dotenv", lambda: ())
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-azure-key")
    monkeypatch.setenv("AZURE_API_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_API_VERSION", "2024-10-01-preview")

    exit_code, payload = _run_cli(capsys)

    expected_embedding_models = _embedding_models_for_provider("azure")
    expected_llm_models = _llm_models_for_provider("azure")
    assert exit_code == 0
    assert payload == {
        "embedding": expected_embedding_models,
        "llm": expected_llm_models,
    }


def test_shinka_models_loads_dotenv_before_checking_provider_availability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    _clear_provider_env(monkeypatch)
    package_root = tmp_path / "package-root"
    launch_dir = tmp_path / "launch-dir"
    package_root.mkdir()
    launch_dir.mkdir()
    (launch_dir / ".env").write_text("OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr(
        cli_models,
        "load_shinka_dotenv",
        lambda: real_load_shinka_dotenv(package_root=package_root, cwd=launch_dir),
    )

    exit_code, payload = _run_cli(capsys)

    assert exit_code == 0
    assert payload == {
        "embedding": _embedding_models_for_provider("openai"),
        "llm": _llm_models_for_provider("openai"),
    }


def test_shinka_models_returns_empty_payload_when_no_provider_keys_are_available(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(cli_models, "load_shinka_dotenv", lambda: ())

    exit_code, payload = _run_cli(capsys)

    assert exit_code == 0
    assert payload == {"embedding": [], "llm": []}


def test_shinka_models_never_prints_api_key_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(cli_models, "load_shinka_dotenv", lambda: ())
    secret_value = "super-secret-openai-key"
    monkeypatch.setenv("OPENAI_API_KEY", secret_value)

    exit_code = cli_models.main([])
    raw_output = capsys.readouterr().out
    payload = json.loads(raw_output)

    expected_embedding_models = _embedding_models_for_provider("openai")
    expected_llm_models = _llm_models_for_provider("openai")
    assert exit_code == 0
    assert secret_value not in raw_output
    assert payload == {
        "embedding": expected_embedding_models,
        "llm": expected_llm_models,
    }

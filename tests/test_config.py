from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from maf_e2e.config import Settings as SettingsModel


def Settings(**kwargs: Any) -> SettingsModel:
    return SettingsModel(**kwargs)


def test_playwright_args_include_mcp_boundaries(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="test-model",
        storage_state_path=tmp_path / "missing.json",
        playwright_allowed_origins=["https://app.example.com"],
        playwright_headless=True,
    )

    args = settings.playwright_args(tmp_path / "output")

    assert args[0] == "@playwright/mcp"
    assert "--isolated" in args
    assert args[args.index("--caps") + 1] == "devtools"
    assert "--headless" in args
    assert "--allowed-origins" in args
    assert "--storage-state" not in args


def test_gemini_developer_api_settings() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="gemini",
        gemini_api_key="secret-key",
        gemini_model="gemini-2.5-flash-lite",
    )

    assert settings.model_provider == "gemini"
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "secret-key"


def test_gemini_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAF_E2E_GEMINI_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="MAF_E2E_GEMINI_API_KEY"):
        Settings(
            _env_file=None,
            model_provider="gemini",
            gemini_model="gemini-2.5-flash-lite",
        )

    with pytest.raises(ValidationError, match="MAF_E2E_GEMINI_API_KEY"):
        Settings(
            _env_file=None,
            model_provider="gemini",
            gemini_api_key="",
            gemini_model="gemini-2.5-flash-lite",
        )


def test_vertex_ai_accepts_adc_configuration() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="gemini",
        gemini_model="gemini-2.5-flash-lite",
        gemini_use_vertex_ai=True,
        gemini_vertex_project="test-project",
        gemini_vertex_location="global",
    )

    assert settings.gemini_use_vertex_ai is True


def test_github_copilot_settings_with_explicit_token() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="github_copilot",
        github_copilot_token="gho_test_token",
        github_copilot_model="gpt-4.1",
        github_copilot_use_gh_cli_token=False,
    )

    assert settings.model_provider == "github_copilot"
    assert settings.github_copilot_model == "gpt-4.1"
    assert settings.github_copilot_token is not None
    assert settings.github_copilot_token.get_secret_value() == "gho_test_token"
    assert settings.github_copilot_use_gh_cli_token is False


def test_github_copilot_requires_token_when_gh_cli_disabled() -> None:
    with pytest.raises(
        ValidationError,
        match="MAF_E2E_GITHUB_COPILOT_TOKEN or MAF_E2E_GITHUB_COPILOT_USE_GH_CLI_TOKEN=true",
    ):
        Settings(
            _env_file=None,
            model_provider="github_copilot",
            github_copilot_model="gpt-4.1",
            github_copilot_use_gh_cli_token=False,
        )


def test_resilience_and_privacy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAF_E2E_SKILL_PATHS", "skills/one, skills/two")
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="test-model",
    )

    assert settings.model_retries == 2
    assert settings.structured_output_retries == 1
    assert settings.trace_content is False
    assert settings.skill_paths == [Path("skills/one"), Path("skills/two")]

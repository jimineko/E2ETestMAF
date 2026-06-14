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
        model_provider="azure_openai",
        model_auth="entra_id",
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


@pytest.mark.parametrize(
    ("provider", "auth", "extra"),
    [
        (
            "azure_openai",
            "api_key",
            {
                "azure_openai_endpoint": "https://example.openai.azure.com",
                "azure_openai_deployment": "model",
                "azure_openai_api_key": "secret",
            },
        ),
        (
            "azure_openai",
            "entra_id",
            {
                "azure_openai_endpoint": "https://example.openai.azure.com",
                "azure_openai_deployment": "model",
            },
        ),
        ("gemini", "api_key", {"gemini_api_key": "secret", "gemini_model": "model"}),
        ("vertex_ai", "api_key", {"gemini_api_key": "secret", "gemini_model": "model"}),
        (
            "vertex_ai",
            "adc",
            {
                "gemini_model": "model",
                "gemini_vertex_project": "project",
                "gemini_vertex_location": "global",
            },
        ),
        ("github_copilot", "subscription", {}),
        ("codex_cli", "subscription", {}),
    ],
)
def test_valid_provider_auth_combinations(
    provider: str, auth: str, extra: dict[str, Any]
) -> None:
    settings = Settings(
        _env_file=None,
        model_provider=provider,
        model_auth=auth,
        **extra,
    )

    assert settings.model_provider == provider
    assert settings.model_auth == auth


@pytest.mark.parametrize(
    ("provider", "auth"),
    [
        ("azure_openai", "subscription"),
        ("gemini", "adc"),
        ("vertex_ai", "entra_id"),
        ("github_copilot", "api_key"),
        ("codex_cli", "entra_id"),
    ],
)
def test_invalid_provider_auth_combinations_are_rejected(provider: str, auth: str) -> None:
    with pytest.raises(ValidationError, match="does not support model auth"):
        Settings(_env_file=None, model_provider=provider, model_auth=auth)


def test_model_auth_is_required() -> None:
    with pytest.raises(ValidationError, match="model_auth"):
        Settings(
            _env_file=None,
            model_provider="azure_openai",
            azure_openai_endpoint="https://example.openai.azure.com",
            azure_openai_deployment="model",
        )


def test_azure_api_key_auth_requires_key() -> None:
    with pytest.raises(ValidationError, match="MAF_E2E_AZURE_OPENAI_API_KEY"):
        Settings(
            _env_file=None,
            model_provider="azure_openai",
            model_auth="api_key",
            azure_openai_endpoint="https://example.openai.azure.com",
            azure_openai_deployment="model",
        )


def test_vertex_adc_requires_project_and_location() -> None:
    with pytest.raises(ValidationError, match="Vertex AI ADC"):
        Settings(
            _env_file=None,
            model_provider="vertex_ai",
            model_auth="adc",
            gemini_model="model",
        )


@pytest.mark.parametrize("provider", ["github_copilot", "codex_cli"])
def test_subscription_cli_providers_are_local_only(provider: str) -> None:
    with pytest.raises(ValidationError, match="is local-only"):
        Settings(
            _env_file=None,
            model_provider=provider,
            model_auth="subscription",
            runtime_environment="container",
        )


def test_resilience_and_privacy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAF_E2E_SKILL_PATHS", "skills/one, skills/two")
    settings = Settings(
        _env_file=None,
        model_provider="azure_openai",
        model_auth="entra_id",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="test-model",
    )

    assert settings.model_retries == 2
    assert settings.structured_output_retries == 1
    assert settings.trace_content is False
    assert settings.skill_paths == [Path("skills/one"), Path("skills/two")]

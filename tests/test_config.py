from pathlib import Path

import pytest
from pydantic import ValidationError

from maf_qa.config import Settings


def test_playwright_args_include_mcp_boundaries(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="test-model",
        storage_state_path=tmp_path / "missing.json",
        playwright_allowed_origins=["https://app.example.com"],
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
    monkeypatch.delenv("MAF_QA_GEMINI_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="MAF_QA_GEMINI_API_KEY"):
        Settings(
            _env_file=None,
            model_provider="gemini",
            gemini_model="gemini-2.5-flash-lite",
        )

    with pytest.raises(ValidationError, match="MAF_QA_GEMINI_API_KEY"):
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


def test_resilience_and_privacy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAF_QA_SKILL_PATHS", "skills/one, skills/two")
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="test-model",
    )

    assert settings.model_retries == 2
    assert settings.structured_output_retries == 1
    assert settings.trace_content is False
    assert settings.skill_paths == [Path("skills/one"), Path("skills/two")]

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from maf_e2e.config import Settings as SettingsModel
from maf_e2e.provider_backend import _build_maf_chat_client as build_maf_chat_client
from maf_e2e.provider_backend import build_provider_backend, resolve_executable
from maf_e2e.runtime import RuntimeResources
from maf_e2e.runtime import (
    _sanitize_mcp_function_schemas as sanitize_mcp_function_schemas,
)
from maf_e2e.runtime import (
    _strip_json_schema_dialect as strip_json_schema_dialect,
)


def Settings(**kwargs: Any) -> SettingsModel:
    return SettingsModel(**kwargs)


def test_azure_api_key_is_passed_without_credential(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "maf_e2e.provider_backend.OpenAIChatClient",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )
    settings = Settings(
        _env_file=None,
        model_provider="azure_openai",
        model_auth="api_key",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="test-model",
        azure_openai_api_key="secret-key",
    )

    _client, credential = build_maf_chat_client(settings)

    assert captured["api_key"] == "secret-key"
    assert "credential" not in captured
    assert captured["azure_endpoint"] == "https://example.openai.azure.com"
    assert credential is None


def test_azure_entra_id_is_passed_without_api_key(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    fake_credential = SimpleNamespace()
    monkeypatch.setattr(
        "maf_e2e.provider_backend.DefaultAzureCredential", lambda: fake_credential
    )
    monkeypatch.setattr(
        "maf_e2e.provider_backend.OpenAIChatClient",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )
    settings = Settings(
        _env_file=None,
        model_provider="azure_openai",
        model_auth="entra_id",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="test-model",
    )

    _client, credential = build_maf_chat_client(settings)

    assert captured["credential"] is fake_credential
    assert "api_key" not in captured
    assert credential is fake_credential


@pytest.mark.parametrize(
    ("provider", "auth", "expected_vertex", "expected_key"),
    [
        ("gemini", "api_key", False, "secret-key"),
        ("vertex_ai", "api_key", True, "secret-key"),
        ("vertex_ai", "adc", True, None),
    ],
)
def test_gemini_provider_constructor_arguments(
    monkeypatch: Any,
    provider: str,
    auth: str,
    expected_vertex: bool,
    expected_key: str | None,
) -> None:
    captured: dict[str, Any] = {}
    fake_google_credential = SimpleNamespace()
    monkeypatch.setattr(
        "maf_e2e.provider_backend.GeminiChatClient",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        "maf_e2e.provider_backend.google_auth_default",
        lambda **kwargs: (fake_google_credential, "project"),
    )
    settings = Settings(
        _env_file=None,
        model_provider=provider,
        model_auth=auth,
        gemini_api_key="secret-key" if auth == "api_key" else None,
        gemini_model="gemini-model",
        gemini_vertex_project="project" if auth == "adc" else None,
        gemini_vertex_location="global" if auth == "adc" else None,
    )

    _client, credential = build_maf_chat_client(settings)

    assert captured["vertexai"] is expected_vertex
    assert captured["api_key"] == expected_key
    if auth == "adc":
        assert captured["credentials"] is fake_google_credential
        assert captured["project"] == "project"
        assert captured["location"] == "global"
    else:
        assert "credentials" not in captured
    if provider == "vertex_ai" and auth == "api_key":
        assert captured["project"] == ""
        assert captured["location"] == ""
    assert credential is None


def test_builds_github_copilot_as_official_maf_agent() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="github_copilot",
        model_auth="subscription",
    )

    backend = build_provider_backend(settings, Path("agents"))

    assert type(backend.agents.discovery).__name__ == "GitHubCopilotAgent"
    assert backend.codex_runtime is None


def test_builds_codex_as_custom_agent() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="codex_cli",
        model_auth="subscription",
    )

    backend = build_provider_backend(settings, Path("agents"))

    assert type(backend.agents.discovery).__name__ == "CodexCLIAgent"
    assert backend.codex_runtime is not None


async def test_blob_credential_is_independent_from_api_key_model_auth(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        model_provider="azure_openai",
        model_auth="api_key",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="model",
        azure_openai_api_key="secret-key",
        blob_account_url="https://example.blob.core.windows.net",
        artifact_root=tmp_path / "artifacts",
    )
    resources = RuntimeResources(settings, "credential-test", target_url=None)
    try:
        assert resources.backend.model_credential is None
        assert resources.blob_credential is not None
    finally:
        await resources.close()


async def test_blob_and_model_entra_credentials_have_separate_lifecycles(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        model_provider="azure_openai",
        model_auth="entra_id",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="model",
        blob_account_url="https://example.blob.core.windows.net",
        artifact_root=tmp_path / "artifacts",
    )
    resources = RuntimeResources(settings, "credential-test", target_url=None)
    try:
        assert resources.backend.model_credential is not None
        assert resources.blob_credential is not None
        assert resources.backend.model_credential is not resources.blob_credential
    finally:
        await resources.close()


def test_secret_keys_are_not_exposed_in_settings_repr() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="azure_openai",
        model_auth="api_key",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_deployment="model",
        azure_openai_api_key="do-not-print-this",
    )

    assert "do-not-print-this" not in repr(settings)


def test_missing_subscription_cli_has_clear_error() -> None:
    with pytest.raises(RuntimeError, match="executable was not found"):
        resolve_executable("definitely-missing-maf-e2e-cli", "Test CLI")


def test_strip_json_schema_dialect_removes_schema_key_recursively() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "payload": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additional_properties": False,
                "propertyNames": {"type": "string"},
            }
        },
    }

    cleaned = strip_json_schema_dialect(schema)

    assert "$schema" not in cleaned
    assert "additionalProperties" not in cleaned
    assert "$schema" not in cleaned["properties"]["payload"]
    assert "additional_properties" not in cleaned["properties"]["payload"]
    assert "propertyNames" not in cleaned["properties"]["payload"]


def test_sanitize_mcp_function_schemas_keeps_each_function_schema() -> None:
    first_schema = {
        "type": "object",
        "properties": {"first": {"type": "string"}},
        "required": ["first"],
    }
    second_schema = {
        "type": "object",
        "properties": {"second": {"type": "string"}},
        "required": ["second"],
    }
    tool = SimpleNamespace(
        functions=[
            SimpleNamespace(parameters=lambda: first_schema),
            SimpleNamespace(parameters=lambda: second_schema),
        ]
    )

    sanitize_mcp_function_schemas(tool)  # type: ignore[arg-type]

    assert tool.functions[0].parameters()["required"] == ["first"]
    assert tool.functions[1].parameters()["required"] == ["second"]


def test_sanitize_mcp_function_schemas_preserves_browser_navigate_url() -> None:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "description": "The URL to navigate to."}
        },
        "required": ["url"],
    }
    tool = SimpleNamespace(functions=[SimpleNamespace(parameters=lambda: schema)])

    sanitize_mcp_function_schemas(tool)  # type: ignore[arg-type]
    cleaned = tool.functions[0].parameters()

    assert "$schema" not in cleaned
    assert "additionalProperties" not in cleaned
    assert cleaned["properties"]["url"]["type"] == "string"
    assert cleaned["required"] == ["url"]

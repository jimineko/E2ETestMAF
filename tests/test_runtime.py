import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
from agent_framework_gemini import GeminiChatClient
from azure.identity.aio import DefaultAzureCredential

from maf_qa.agent_config import load_agent_set
from maf_qa.config import Settings
from maf_qa.runtime import (
    _resolve_github_copilot_token as resolve_github_copilot_token,
)
from maf_qa.runtime import (
    _sanitize_mcp_function_schemas as sanitize_mcp_function_schemas,
)
from maf_qa.runtime import (
    _strip_json_schema_dialect as strip_json_schema_dialect,
)
from maf_qa.runtime import build_chat_client


async def test_builds_azure_openai_client() -> None:
    credential = DefaultAzureCredential()
    try:
        settings = Settings(
            _env_file=None,
            model_provider="azure_openai",
            azure_openai_endpoint="https://example.openai.azure.com",
            azure_openai_deployment="test-model",
        )

        client = build_chat_client(settings, credential)

        assert isinstance(client, OpenAIChatClient)
        agents = load_agent_set(Path("agents"), client, skill_paths=[], model_retries=0)
        assert agents.discovery.client is client  # type: ignore[attr-defined]
    finally:
        await credential.close()


async def test_builds_gemini_client() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="gemini",
        gemini_api_key="secret-key",
        gemini_model="gemini-2.5-flash-lite",
    )

    client = build_chat_client(settings, None)

    assert isinstance(client, GeminiChatClient)
    assert client.model == "gemini-2.5-flash-lite"
    agents = load_agent_set(Path("agents"), client, skill_paths=[], model_retries=0)
    assert agents.discovery.client is client  # type: ignore[attr-defined]


async def test_builds_github_copilot_client_with_explicit_token() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="github_copilot",
        github_copilot_token="gho_test_token",
        github_copilot_model="gpt-4.1",
        github_copilot_use_gh_cli_token=False,
    )

    client = build_chat_client(settings, None)

    assert isinstance(client, OpenAIChatCompletionClient)
    assert client.model == "gpt-4.1"
    agents = load_agent_set(Path("agents"), client, skill_paths=[], model_retries=0)
    assert agents.discovery.client is client  # type: ignore[attr-defined]


def test_resolves_github_copilot_token_from_gh_cli(monkeypatch: Any) -> None:
    settings = Settings(
        _env_file=None,
        model_provider="github_copilot",
        github_copilot_model="gpt-4.1",
        github_copilot_use_gh_cli_token=True,
    )

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["gh", "auth", "token"],
            returncode=0,
            stdout="gho_cli\n",
        )

    monkeypatch.setattr("maf_qa.runtime.subprocess.run", fake_run)

    assert resolve_github_copilot_token(settings) == "gho_cli"


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
            }
        },
    }

    cleaned = strip_json_schema_dialect(schema)

    assert "$schema" not in cleaned
    assert "additionalProperties" not in cleaned
    assert "$schema" not in cleaned["properties"]["payload"]
    assert "additional_properties" not in cleaned["properties"]["payload"]


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

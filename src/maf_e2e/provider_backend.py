from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_framework import SupportsChatGetResponse
from agent_framework.openai import OpenAIChatClient
from agent_framework_gemini import GeminiChatClient
from azure.identity.aio import DefaultAzureCredential
from google.auth import default as google_auth_default

from maf_e2e.agent_config import (
    build_chat_agent_set,
    load_agent_definitions,
    load_skills,
)
from maf_e2e.cli_agents import (
    CodexRuntime,
    create_codex_agent,
    create_github_copilot_agent,
)
from maf_e2e.config import Settings
from maf_e2e.workflow import AgentSet


@dataclass
class ProviderBackend:
    settings: Settings
    agents: AgentSet
    use_native_response_format: bool
    sanitize_mcp_schemas: bool = False
    model_credential: DefaultAzureCredential | None = None
    codex_runtime: CodexRuntime | None = None

    async def start(self) -> None:
        if self.settings.model_provider == "codex_cli":
            executable = resolve_executable(self.settings.codex_cli_path, "Codex CLI")
            await run_preflight_command(
                [executable, "login", "status"],
                timeout_seconds=self.settings.codex_timeout_seconds,
                error_message="Codex CLI is not authenticated; run `codex login`",
            )
        elif self.settings.model_provider == "github_copilot":
            resolve_executable(
                self.settings.github_copilot_cli_path, "GitHub Copilot CLI"
            )
            starter = getattr(self.agents.discovery, "start", None)
            if not callable(starter):
                raise RuntimeError("GitHub Copilot agent backend cannot be started")
            await asyncio.wait_for(
                starter(), timeout=float(self.settings.github_copilot_timeout_seconds)
            )

    async def close(self) -> None:
        if self.settings.model_provider == "github_copilot":
            for role in ("discovery", "generator", "browser", "judge", "safety"):
                stop = getattr(getattr(self.agents, role), "stop", None)
                if callable(stop):
                    await stop()
        if self.codex_runtime is not None:
            await self.codex_runtime.close()
        if self.model_credential is not None:
            await self.model_credential.close()


def build_provider_backend(settings: Settings, config_dir: Path) -> ProviderBackend:
    definitions = load_agent_definitions(config_dir)
    if settings.model_provider in {"azure_openai", "gemini", "vertex_ai"}:
        client, credential = _build_maf_chat_client(settings)
        return ProviderBackend(
            settings=settings,
            agents=build_chat_agent_set(
                definitions,
                client,
                skill_paths=settings.skill_paths,
                model_retries=settings.model_retries,
                trace_content=settings.trace_content,
            ),
            use_native_response_format=settings.model_provider == "azure_openai",
            sanitize_mcp_schemas=settings.model_provider in {"gemini", "vertex_ai"},
            model_credential=credential,
        )

    skills = load_skills(settings.skill_paths)
    agents: dict[str, Any] = {}
    codex_runtime = (
        CodexRuntime(cli_path=settings.codex_cli_path, cwd=str(Path.cwd()))
        if settings.model_provider == "codex_cli"
        else None
    )
    for role, definition in definitions.items():
        context_providers = (
            [skills] if skills is not None and role in {"discovery", "generator"} else []
        )
        if settings.model_provider == "github_copilot":
            agents[role] = create_github_copilot_agent(
                definition=definition,
                cli_path=settings.github_copilot_cli_path,
                model=settings.github_copilot_model,
                timeout_seconds=settings.github_copilot_timeout_seconds,
                context_providers=context_providers,
                model_retries=settings.model_retries,
                trace_content=settings.trace_content,
            )
        elif codex_runtime is not None:
            agents[role] = create_codex_agent(
                definition=definition,
                runtime=codex_runtime,
                model=settings.codex_model,
                timeout_seconds=settings.codex_timeout_seconds,
                max_tool_rounds=settings.codex_max_tool_rounds,
                context_providers=context_providers,
                model_retries=settings.model_retries,
                trace_content=settings.trace_content,
            )
        else:
            raise AssertionError("validated provider was not handled")
    return ProviderBackend(
        settings=settings,
        agents=AgentSet(**agents),
        use_native_response_format=settings.model_provider == "codex_cli",
        codex_runtime=codex_runtime,
    )


def _build_maf_chat_client(
    settings: Settings,
) -> tuple[SupportsChatGetResponse[Any], DefaultAzureCredential | None]:
    if settings.model_provider == "azure_openai":
        common: dict[str, Any] = {
            "model": settings.azure_openai_deployment,
            "azure_endpoint": settings.azure_openai_endpoint,
            "api_version": settings.azure_openai_api_version,
        }
        if settings.model_auth == "api_key":
            assert settings.azure_openai_api_key is not None
            return (
                OpenAIChatClient(
                    **common,
                    api_key=settings.azure_openai_api_key.get_secret_value(),
                ),
                None,
            )
        credential = DefaultAzureCredential()
        return OpenAIChatClient(**common, credential=credential), credential

    api_key = (
        settings.gemini_api_key.get_secret_value()
        if settings.model_auth == "api_key" and settings.gemini_api_key is not None
        else None
    )
    gemini_options: dict[str, Any] = {
        "api_key": api_key,
        "model": settings.gemini_model,
        "vertexai": settings.model_provider == "vertex_ai",
    }
    if settings.model_provider == "vertex_ai" and settings.model_auth == "adc":
        google_credentials, _ = google_auth_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        gemini_options.update(
            project=settings.gemini_vertex_project,
            location=settings.gemini_vertex_location,
            credentials=google_credentials,
        )
    elif settings.model_provider == "vertex_ai":
        # Empty explicit values prevent MAF from substituting GOOGLE_CLOUD_* and
        # accidentally changing Vertex API-key auth into ADC.
        gemini_options.update(project="", location="")
    return (
        GeminiChatClient(**gemini_options),
        None,
    )


def resolve_executable(configured: str, display_name: str) -> str:
    resolved = shutil.which(configured)
    if resolved is None:
        raise RuntimeError(f"{display_name} executable was not found: {configured}")
    return resolved


async def run_preflight_command(
    command: list[str], *, timeout_seconds: int, error_message: str
) -> None:
    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, text=True, check=False)

    try:
        result = await asyncio.wait_for(asyncio.to_thread(run), timeout=float(timeout_seconds))
    except TimeoutError as exc:
        raise RuntimeError(f"CLI preflight timed out: {command[0]}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{error_message}: {detail or 'unknown error'}")

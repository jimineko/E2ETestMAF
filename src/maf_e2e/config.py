from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MAF_E2E_",
        extra="ignore",
    )

    model_provider: Literal[
        "azure_openai", "gemini", "vertex_ai", "github_copilot", "codex_cli"
    ]
    model_auth: Literal["api_key", "entra_id", "adc", "subscription"]
    runtime_environment: Literal["local", "container", "azure"] = "local"

    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2025-04-01-preview"
    azure_openai_api_key: SecretStr | None = None

    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    gemini_vertex_project: str | None = None
    gemini_vertex_location: str | None = None
    github_copilot_cli_path: str = "copilot"
    github_copilot_model: str | None = None
    github_copilot_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    codex_cli_path: str = "codex"
    codex_model: str | None = None
    codex_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    codex_max_tool_rounds: int = Field(default=8, ge=1, le=50)

    target_url: str | None = None
    objective: str = "Validate the critical user journey and report regressions."
    policies: list[str] = Field(default_factory=list)
    max_refinements: int = Field(default=2, ge=0, le=5)
    agent_config_dir: Path = Path("agents")
    skill_paths: Annotated[list[Path], NoDecode] = Field(default_factory=list)
    model_retries: int = Field(default=2, ge=0, le=5)
    structured_output_retries: int = Field(default=1, ge=0, le=2)
    trace_content: bool = False
    codeact_mode: Literal["required", "auto", "disabled"] = "auto"
    codeact_max_code_bytes: int = Field(default=32_768, ge=1_024, le=1_048_576)
    codeact_max_invocations: int = Field(default=6, ge=1, le=50)
    codeact_require_kvm: bool = True
    codeact_allow_file_upload: bool = False
    codeact_allow_destructive_actions: bool = False

    playwright_command: str = "npx"
    playwright_package: str = "@playwright/mcp"
    playwright_browser: Literal["chrome", "firefox", "webkit", "msedge"] = "chrome"
    playwright_headless: bool = True
    playwright_allowed_origins: list[str] = Field(default_factory=list)
    playwright_action_timeout_ms: int = Field(default=10_000, ge=1_000)
    playwright_navigation_timeout_ms: int = Field(default=60_000, ge=1_000)
    storage_state_path: Path = Path("auth/user.json")

    artifact_root: Path = Path("artifacts")
    checkpoint_root: Path = Path("checkpoints")
    blob_account_url: str | None = None
    blob_container: str = "e2e-artifacts"

    otlp_endpoint: str | None = None
    applicationinsights_connection_string: str | None = None
    log_level: str = "INFO"
    devui_host: str = "127.0.0.1"
    devui_port: int = Field(default=8080, ge=1, le=65535)
    devui_auth_token: SecretStr | None = None

    @field_validator("policies", "playwright_allowed_origins", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("skill_paths", mode="before")
    @classmethod
    def split_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(item.strip()) for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_model_configuration(self) -> Self:
        valid_auth = {
            "azure_openai": {"api_key", "entra_id"},
            "gemini": {"api_key"},
            "vertex_ai": {"api_key", "adc"},
            "github_copilot": {"subscription"},
            "codex_cli": {"subscription"},
        }
        if self.model_auth not in valid_auth[self.model_provider]:
            allowed = ", ".join(sorted(valid_auth[self.model_provider]))
            raise ValueError(
                f"{self.model_provider} does not support model auth {self.model_auth!r}; "
                f"allowed: {allowed}"
            )

        if self.model_provider == "azure_openai":
            if not self.azure_openai_endpoint or not self.azure_openai_deployment:
                raise ValueError(
                    "Azure OpenAI requires MAF_E2E_AZURE_OPENAI_ENDPOINT and "
                    "MAF_E2E_AZURE_OPENAI_DEPLOYMENT"
                )
            if self.model_auth == "api_key" and not _has_secret(self.azure_openai_api_key):
                raise ValueError(
                    "Azure OpenAI API key auth requires MAF_E2E_AZURE_OPENAI_API_KEY"
                )
            return self

        if self.model_provider == "gemini":
            if not self.gemini_model:
                raise ValueError("Gemini requires MAF_E2E_GEMINI_MODEL")
            if not _has_secret(self.gemini_api_key):
                raise ValueError("Gemini Developer API requires MAF_E2E_GEMINI_API_KEY")
            return self

        if self.model_provider == "vertex_ai":
            if not self.gemini_model:
                raise ValueError("Vertex AI requires MAF_E2E_GEMINI_MODEL")
            if self.model_auth == "api_key" and not _has_secret(self.gemini_api_key):
                raise ValueError("Vertex AI API key auth requires MAF_E2E_GEMINI_API_KEY")
            if (
                self.model_auth == "adc"
                and (not self.gemini_vertex_project or not self.gemini_vertex_location)
            ):
                raise ValueError(
                    "Vertex AI ADC requires both "
                    "MAF_E2E_GEMINI_VERTEX_PROJECT and MAF_E2E_GEMINI_VERTEX_LOCATION"
                )
            return self

        if self.runtime_environment != "local":
            raise ValueError(
                f"{self.model_provider} is local-only; "
                "MAF_E2E_RUNTIME_ENVIRONMENT must be local"
            )
        if self.model_provider == "github_copilot" and not self.github_copilot_cli_path.strip():
            raise ValueError(
                "GitHub Copilot requires MAF_E2E_GITHUB_COPILOT_CLI_PATH"
            )
        if self.model_provider == "codex_cli" and not self.codex_cli_path.strip():
            raise ValueError("Codex CLI requires MAF_E2E_CODEX_CLI_PATH")
        return self

    def playwright_args(
        self, output_dir: Path, *, default_allowed_origin: str | None = None
    ) -> list[str]:
        args = [
            self.playwright_package,
            "--browser",
            self.playwright_browser,
            "--caps",
            "devtools",
            "--isolated",
            "--output-dir",
            str(output_dir),
            "--save-session",
            "--timeout-action",
            str(self.playwright_action_timeout_ms),
            "--timeout-navigation",
            str(self.playwright_navigation_timeout_ms),
        ]
        if self.playwright_headless:
            args.append("--headless")
        if self.storage_state_path.exists():
            args.extend(["--storage-state", str(self.storage_state_path.resolve())])
        allowed_origins = self.playwright_allowed_origins or (
            [default_allowed_origin] if default_allowed_origin else []
        )
        if allowed_origins:
            args.extend(["--allowed-origins", ";".join(allowed_origins)])
        return args


def _has_secret(value: SecretStr | None) -> bool:
    return bool(value and value.get_secret_value().strip())

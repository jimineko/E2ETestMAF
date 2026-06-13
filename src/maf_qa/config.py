from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MAF_QA_",
        extra="ignore",
    )

    model_provider: Literal["azure_openai", "gemini"] = "azure_openai"

    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2025-04-01-preview"

    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    gemini_use_vertex_ai: bool = False
    gemini_vertex_project: str | None = None
    gemini_vertex_location: str | None = None

    target_url: str | None = None
    objective: str = "Validate the critical user journey and report regressions."
    policies: list[str] = Field(default_factory=list)
    max_refinements: int = Field(default=2, ge=0, le=5)

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
    blob_container: str = "qa-artifacts"

    otlp_endpoint: str | None = None
    applicationinsights_connection_string: str | None = None
    log_level: str = "INFO"

    @field_validator("policies", "playwright_allowed_origins", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_model_configuration(self) -> Self:
        if self.model_provider == "azure_openai":
            if not self.azure_openai_endpoint or not self.azure_openai_deployment:
                raise ValueError(
                    "Azure OpenAI requires MAF_QA_AZURE_OPENAI_ENDPOINT and "
                    "MAF_QA_AZURE_OPENAI_DEPLOYMENT"
                )
            return self

        if not self.gemini_model:
            raise ValueError("Gemini requires MAF_QA_GEMINI_MODEL")
        has_gemini_api_key = bool(
            self.gemini_api_key and self.gemini_api_key.get_secret_value().strip()
        )
        if not self.gemini_use_vertex_ai and not has_gemini_api_key:
            raise ValueError("Gemini Developer API requires MAF_QA_GEMINI_API_KEY")
        if (
            self.gemini_use_vertex_ai
            and not has_gemini_api_key
            and (not self.gemini_vertex_project or not self.gemini_vertex_location)
        ):
            raise ValueError(
                "Vertex AI requires MAF_QA_GEMINI_API_KEY or both "
                "MAF_QA_GEMINI_VERTEX_PROJECT and MAF_QA_GEMINI_VERTEX_LOCATION"
            )
        return self

    def playwright_args(self, output_dir: Path) -> list[str]:
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
        if self.playwright_allowed_origins:
            args.extend(["--allowed-origins", ";".join(self.playwright_allowed_origins)])
        return args

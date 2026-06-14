from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from maf_e2e.config import Settings
from maf_e2e.provider_backend import build_provider_backend

pytestmark = pytest.mark.subscription_cli


class SmokeResult(BaseModel):
    status: str


@pytest.mark.skipif(
    os.getenv("MAF_E2E_RUN_COPILOT_CLI_INTEGRATION") != "1",
    reason="Set MAF_E2E_RUN_COPILOT_CLI_INTEGRATION=1 to consume the Copilot subscription",
)
async def test_authenticated_github_copilot_cli_smoke() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="github_copilot",
        model_auth="subscription",
        github_copilot_cli_path=os.getenv("MAF_E2E_GITHUB_COPILOT_CLI_PATH", "copilot"),
        github_copilot_model=os.getenv("MAF_E2E_GITHUB_COPILOT_MODEL") or None,
        model_retries=0,
    )
    backend = build_provider_backend(settings, Path("agents"))
    try:
        await backend.start()
        response = await backend.agents.judge.run(  # type: ignore[attr-defined]
            "Reply with READY and do not call tools.",
            session=backend.agents.judge.create_session(),
        )
        assert "READY" in response.text.upper()
    finally:
        await backend.close()


@pytest.mark.skipif(
    os.getenv("MAF_E2E_RUN_CODEX_CLI_INTEGRATION") != "1",
    reason="Set MAF_E2E_RUN_CODEX_CLI_INTEGRATION=1 to consume the ChatGPT subscription",
)
async def test_authenticated_codex_cli_smoke() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="codex_cli",
        model_auth="subscription",
        codex_cli_path=os.getenv("MAF_E2E_CODEX_CLI_PATH", "codex"),
        codex_model=os.getenv("MAF_E2E_CODEX_MODEL") or None,
        model_retries=0,
    )
    backend = build_provider_backend(settings, Path("agents"))
    try:
        await backend.start()
        response = await backend.agents.judge.run(  # type: ignore[attr-defined]
            "Return a status of READY.",
            session=backend.agents.judge.create_session(),
            options={"response_format": SmokeResult},
        )
        assert response.value == SmokeResult(status="READY")
    finally:
        await backend.close()

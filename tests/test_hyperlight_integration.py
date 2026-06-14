from __future__ import annotations

import os
from typing import Any

import pytest

from maf_e2e.codeact import AuditedHyperlightCodeActProvider
from maf_e2e.config import Settings
from maf_e2e.runtime import RuntimeResources

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MAF_E2E_RUN_HYPERLIGHT_INTEGRATION") != "1",
        reason="Set MAF_E2E_RUN_HYPERLIGHT_INTEGRATION=1 on a Linux KVM host",
    ),
]


def _settings(**values: Any) -> Settings:
    return Settings(**values)


async def test_hyperlight_calls_multiple_playwright_mcp_tools() -> None:
    target_url = os.environ["MAF_E2E_RAMPART_TARGET_URL"]
    settings = _settings(
        _env_file=None,
        model_provider="github_copilot",
        github_copilot_token="integration-test-token",
        github_copilot_use_gh_cli_token=False,
        target_url=target_url,
        codeact_mode="required",
        codeact_require_kvm=True,
        playwright_allowed_origins=[target_url],
    )
    resources = RuntimeResources(settings, "hyperlight-integration", target_url=target_url)
    try:
        await resources.start()
        assert resources.codeact_error is None
        provider = next(
            provider
            for provider in resources.codeact_providers
            if isinstance(provider, AuditedHyperlightCodeActProvider)
            and provider.stage == "browser"
        )
        execute_code = provider.create_run_tool()
        outputs = await execute_code.invoke(
            arguments={
                "code": (
                    f'call_tool("browser_navigate", url={target_url!r})\n'
                    'snapshot = call_tool("browser_snapshot")\n'
                    'print("MCP_CALLS_OK", bool(snapshot))'
                )
            }
        )
    finally:
        await resources.close()

    assert isinstance(outputs, list)
    assert any(item.text and "MCP_CALLS_OK True" in item.text for item in outputs)
    assert not any(item.type == "error" for item in outputs)

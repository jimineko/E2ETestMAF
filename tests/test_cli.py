from __future__ import annotations

import pytest

from maf_e2e.cli import build_parser


def test_cli_accepts_subscription_agent_backends() -> None:
    parser = build_parser()

    copilot = parser.parse_args(
        ["--model-provider", "github_copilot", "--model-auth", "subscription"]
    )
    codex = parser.parse_args(
        ["--model-provider", "codex_cli", "--model-auth", "subscription"]
    )

    assert copilot.model_provider == "github_copilot"
    assert copilot.model_auth == "subscription"
    assert codex.model_provider == "codex_cli"
    assert codex.model_auth == "subscription"


def test_cli_accepts_vertex_adc() -> None:
    args = build_parser().parse_args(
        ["--model-provider", "vertex_ai", "--model-auth", "adc"]
    )

    assert args.model_provider == "vertex_ai"
    assert args.model_auth == "adc"


def test_cli_rejects_removed_github_models_provider() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--model-provider", "github_models"])

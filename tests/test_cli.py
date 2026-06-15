from __future__ import annotations

import pytest
from regression_helpers import make_fake_node_repository

from maf_e2e.cli import _run, build_parser


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


async def test_regression_command_does_not_require_agent_options(tmp_path) -> None:
    make_fake_node_repository(tmp_path)
    args = build_parser().parse_args(
        ["regression", "--target-repo", str(tmp_path), "--environment", "staging"]
    )

    assert await _run(args) == 0


def test_regression_rejects_production_environment(tmp_path) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["regression", "--target-repo", str(tmp_path), "--environment", "production"]
        )

from __future__ import annotations

import argparse
import asyncio
import sys

from maf_qa.config import Settings
from maf_qa.models import LiteralStatus, QARequest
from maf_qa.runtime import execute
from maf_qa.telemetry import configure_telemetry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run autonomous QA with MAF and Playwright MCP")
    parser.add_argument(
        "--model-provider",
        choices=["azure_openai", "gemini"],
        help="Chat model provider; overrides MAF_QA_MODEL_PROVIDER",
    )
    parser.add_argument("--target-url", help="Absolute target application URL")
    parser.add_argument("--objective", help="Business objective to validate")
    parser.add_argument("--policy", action="append", default=[], help="Business policy; repeatable")
    parser.add_argument("--max-refinements", type=int, help="Self-healing retries, 0-5")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings(model_provider=args.model_provider) if args.model_provider else Settings()
    target_url = args.target_url or settings.target_url
    if not target_url:
        raise ValueError("--target-url or MAF_QA_TARGET_URL is required")
    request = QARequest(
        target_url=target_url,
        objective=args.objective or settings.objective,
        policies=args.policy or settings.policies,
        max_refinements=(
            args.max_refinements if args.max_refinements is not None else settings.max_refinements
        ),
    )
    configure_telemetry(settings)
    report = await execute(settings, request)
    print(report.model_dump_json(indent=2))
    if report.status == LiteralStatus.BLOCKED:
        return 3
    return 0 if report.passed else 2


def main() -> None:
    try:
        exit_code = asyncio.run(_run(build_parser().parse_args()))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

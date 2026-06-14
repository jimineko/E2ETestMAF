from __future__ import annotations

import argparse
import asyncio
import sys

from maf_e2e.config import Settings
from maf_e2e.models import E2ETestRequest, LiteralStatus
from maf_e2e.runtime import execute
from maf_e2e.telemetry import configure_telemetry
from maf_e2e.workflow import load_checkpoint_test_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run autonomous E2E testing with MAF and Playwright MCP"
    )
    parser.add_argument(
        "--model-provider",
        choices=["azure_openai", "gemini", "vertex_ai", "github_copilot", "codex_cli"],
        help="Agent backend; overrides MAF_E2E_MODEL_PROVIDER",
    )
    parser.add_argument(
        "--model-auth",
        choices=["api_key", "entra_id", "adc", "subscription"],
        help="Authentication method; overrides MAF_E2E_MODEL_AUTH",
    )
    parser.add_argument("--target-url", help="Absolute target application URL")
    parser.add_argument("--objective", help="Business objective to validate")
    parser.add_argument("--policy", action="append", default=[], help="Business policy; repeatable")
    parser.add_argument("--max-refinements", type=int, help="Self-healing retries, 0-5")
    parser.add_argument("--resume-run-id", help="Resume a saved run from its browser stage")
    parser.add_argument("--checkpoint-id", help="Specific checkpoint in the saved run chain")
    return parser


async def _run(args: argparse.Namespace) -> int:
    overrides = {
        key: value
        for key, value in {
            "model_provider": args.model_provider,
            "model_auth": args.model_auth,
        }.items()
        if value is not None
    }
    settings = Settings(**overrides)
    resume_plan = None
    if args.resume_run_id:
        resume_plan = await load_checkpoint_test_plan(
            settings.checkpoint_root / args.resume_run_id,
            checkpoint_id=args.checkpoint_id,
        )
        request = resume_plan.discovery.run.request
        if request.run_id != args.resume_run_id:
            raise ValueError("Saved TestPlan run_id does not match --resume-run-id")
    else:
        if args.checkpoint_id:
            raise ValueError("--checkpoint-id requires --resume-run-id")
        target_url = args.target_url or settings.target_url
        if not target_url:
            raise ValueError("--target-url or MAF_E2E_TARGET_URL is required")
        request = E2ETestRequest(
            target_url=target_url,
            objective=args.objective or settings.objective,
            policies=args.policy or settings.policies,
            max_refinements=(
                args.max_refinements
                if args.max_refinements is not None
                else settings.max_refinements
            ),
        )
    configure_telemetry(settings)
    report = await execute(settings, request, resume_plan=resume_plan)
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

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from maf_e2e.approval_store import ApprovalStore
from maf_e2e.asset_store import AssetStore
from maf_e2e.config import OperationalSettings, Settings
from maf_e2e.domain.approval import ApprovalAction
from maf_e2e.domain.assets import TrialRunResult
from maf_e2e.domain.failures import FailureAnalysis, RegressionFailureDiagnostic
from maf_e2e.domain.regression import TargetEnvironment
from maf_e2e.failure_analysis import analyze_failure
from maf_e2e.github_repair import GitHubRepositoryPublisher, publish_repair_pull_request
from maf_e2e.models import E2ETestRequest, LiteralStatus
from maf_e2e.publisher import Publisher
from maf_e2e.regression_runner import RegressionRunner, regression_exit_code
from maf_e2e.repair import RepairService, apply_locator_repairs
from maf_e2e.telemetry import configure_telemetry
from maf_e2e.trial_runner import TrialRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Author, approve, publish, and run Playwright E2E regression assets"
    )
    _add_agent_options(parser)
    parser.add_argument("--target-url", help="Absolute target application URL")
    parser.add_argument("--objective", help="Business objective to validate")
    parser.add_argument("--policy", action="append", default=[], help="Business policy; repeatable")
    parser.add_argument("--max-refinements", type=int, help="Self-healing retries, 0-5")
    parser.add_argument("--resume-run-id", help="Resume a saved legacy run from its browser stage")
    parser.add_argument("--checkpoint-id", help="Specific checkpoint in the saved run chain")

    commands = parser.add_subparsers(dest="command")
    author = commands.add_parser("author", help="Create and trial draft E2E assets")
    _add_agent_options(author)
    author.add_argument("--target-repo", type=Path, required=True)
    author.add_argument("--target-url", required=True)
    author.add_argument("--objective", required=True)
    author.add_argument("--expected-result", action="append", required=True)
    author.add_argument("--business-context", default="")
    author.add_argument("--precondition", action="append", default=[])
    author.add_argument("--test-data", type=_json_object, default={})
    author.add_argument("--policy", action="append", default=[])
    author.add_argument("--prohibited-action", action="append", default=[])
    author.add_argument("--allowed-origin", action="append", default=[])
    author.add_argument("--max-scenarios", type=int, default=5)
    author.add_argument("--max-steps", type=int, default=20)
    author.add_argument("--max-trial-repairs", type=int)

    review = commands.add_parser("review", help="Show review packages")
    _add_repo_and_optional_scenario(review)

    for name, action in (
        ("approve", ApprovalAction.APPROVE),
        ("request-changes", ApprovalAction.REQUEST_CHANGES),
        ("reject", ApprovalAction.REJECT),
    ):
        command = commands.add_parser(name, help=f"Record {action.value} review action")
        _add_repo_and_scenario(command)
        command.add_argument("--reviewer", required=True)
        command.add_argument("--comment")

    publish = commands.add_parser("publish", help="Publish an approved scenario")
    _add_repo_and_scenario(publish)

    regression = commands.add_parser("regression", help="Run ACTIVE assets without an Agent")
    regression.add_argument("--target-repo", type=Path, required=True)
    regression.add_argument(
        "--environment",
        choices=[item.value for item in TargetEnvironment],
        default=TargetEnvironment.LOCAL.value,
    )
    regression.add_argument("--scenario-id", action="append", default=[])

    failure = commands.add_parser("analyze-failure", help="Classify a saved trial failure")
    _add_agent_options(failure)
    failure.add_argument("--trial-result", type=Path, required=True)
    failure.add_argument("--diagnostic", action="append", default=[])
    failure.add_argument("--previous-passed", action="store_true")
    failure.add_argument("--investigate", action="store_true")
    failure.add_argument("--target-url")
    failure.add_argument("--allowed-origin", action="append", default=[])

    repair = commands.add_parser("repair", help="Validate a bounded code-only repair")
    repair.add_argument("--target-repo", type=Path, required=True)
    repair.add_argument("--scenario-id", required=True)
    repair.add_argument("--analysis", type=Path, required=True)
    repair.add_argument("--proposed-code", type=Path)
    repair.add_argument("--diagnostic", type=Path)
    repair.add_argument("--create-pr", action="store_true")
    repair.add_argument("--base-branch")
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.command == "author":
        return await _run_author(args)
    if args.command == "review":
        return _run_review(args)
    if args.command in {"approve", "request-changes", "reject"}:
        return _run_approval(args)
    if args.command == "publish":
        return _run_publish(args)
    if args.command == "regression":
        return await _run_regression(args)
    if args.command == "analyze-failure":
        return await _run_failure_analysis(args)
    if args.command == "repair":
        return await _run_repair(args)
    return await _run_legacy(args)


async def _run_legacy(args: argparse.Namespace) -> int:
    from maf_e2e.runtime import execute
    from maf_e2e.workflow import load_checkpoint_test_plan

    settings = Settings(**_agent_overrides(args))
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


async def _run_author(args: argparse.Namespace) -> int:
    from maf_e2e.runtime import execute_authoring

    settings = Settings(**_agent_overrides(args))
    request = E2ETestRequest(
        target_url=args.target_url,
        objective=args.objective,
        expected_results=args.expected_result,
        business_context=args.business_context,
        preconditions=args.precondition,
        test_data=args.test_data,
        policies=args.policy,
        prohibited_actions=args.prohibited_action,
        allowed_origins=args.allowed_origin,
        max_scenarios=args.max_scenarios,
        max_steps=args.max_steps,
        max_trial_repairs=(
            args.max_trial_repairs
            if args.max_trial_repairs is not None
            else settings.max_trial_repairs
        ),
        target_repository_root=args.target_repo.resolve(strict=True),
    )
    configure_telemetry(settings)
    result = await execute_authoring(settings, request)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "pending_approval" else 3


def _run_review(args: argparse.Namespace) -> int:
    assets = AssetStore(args.target_repo)
    selected = (
        [assets.load_asset(args.scenario_id)] if args.scenario_id else assets.list_drafts()
    )
    packages = []
    for asset in selected:
        item: dict[str, object] = {
            "asset": asset.model_dump(mode="json"),
            "specification": assets.load_specification(asset.scenario_id).model_dump(mode="json"),
            "code": assets.load_source(asset.scenario_id),
        }
        try:
            item["trial"] = assets.load_trial(asset.scenario_id).model_dump(mode="json")
        except FileNotFoundError:
            item["trial"] = None
        packages.append(item)
    print(json.dumps(packages, ensure_ascii=False, indent=2, default=str))
    return 0


def _run_approval(args: argparse.Namespace) -> int:
    action = {
        "approve": ApprovalAction.APPROVE,
        "request-changes": ApprovalAction.REQUEST_CHANGES,
        "reject": ApprovalAction.REJECT,
    }[args.command]
    store = ApprovalStore(AssetStore(args.target_repo))
    approval = store.record(
        args.scenario_id,
        action=action,
        reviewer=args.reviewer,
        comment=args.comment,
    )
    print(approval.model_dump_json(indent=2))
    return 0


def _run_publish(args: argparse.Namespace) -> int:
    assets = AssetStore(args.target_repo)
    path = Publisher(assets, ApprovalStore(assets)).publish(args.scenario_id)
    print(json.dumps({"published_path": str(path)}, indent=2))
    return 0


async def _run_regression(args: argparse.Namespace) -> int:
    settings = OperationalSettings()
    runner = RegressionRunner(args.target_repo, timeout_seconds=settings.regression_timeout_seconds)
    result = await runner.run(
        TargetEnvironment(args.environment),
        scenario_ids=set(args.scenario_id) if args.scenario_id else None,
    )
    print(result.model_dump_json(indent=2))
    return regression_exit_code(result)


async def _run_failure_analysis(args: argparse.Namespace) -> int:
    trial = TrialRunResult.model_validate_json(args.trial_result.read_text(encoding="utf-8"))
    if args.investigate:
        if not args.target_url:
            raise ValueError("--investigate requires --target-url")
        from maf_e2e.runtime import execute_regression_diagnostic

        settings = Settings(**_agent_overrides(args))
        diagnostic = await execute_regression_diagnostic(
            settings,
            trial,
            target_url=args.target_url,
            allowed_origins=args.allowed_origin,
        )
        diagnostic_output = args.trial_result.with_name("regression-diagnostic.json")
        diagnostic_output.write_text(diagnostic.model_dump_json(indent=2), encoding="utf-8")
        analysis = FailureAnalysis(
            scenario_id=trial.scenario_id,
            category=diagnostic.category,
            confidence=diagnostic.confidence,
            evidence=[diagnostic.current_ui_summary, *diagnostic.evidence],
            recommended_action=diagnostic.recommended_action,
        )
    else:
        analysis = analyze_failure(
            trial,
            diagnostic_evidence=args.diagnostic,
            previous_passed=args.previous_passed,
        )
    output = args.trial_result.with_name("failure-analysis.json")
    output.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    print(analysis.model_dump_json(indent=2))
    return 2 if analysis.category.value == "test_maintenance" else 1


async def _run_repair(args: argparse.Namespace) -> int:
    operational = OperationalSettings()
    assets = AssetStore(args.target_repo)
    analysis = FailureAnalysis.model_validate_json(args.analysis.read_text(encoding="utf-8"))
    proposed_spec = None
    if args.proposed_code is not None:
        source = args.proposed_code.read_text(encoding="utf-8")
    elif args.diagnostic is not None:
        diagnostic = RegressionFailureDiagnostic.model_validate_json(
            args.diagnostic.read_text(encoding="utf-8")
        )
        if diagnostic.scenario_id != args.scenario_id:
            raise ValueError("Diagnostic scenario does not match --scenario-id")
        if not diagnostic.locator_replacements:
            raise ValueError("Diagnostic contains no locator replacements")
        approved_spec = assets.load_specification(args.scenario_id)
        proposed_spec = apply_locator_repairs(
            approved_spec, diagnostic.locator_replacements
        )
        from maf_e2e.playwright_codegen import generate_playwright_test

        source = generate_playwright_test(
            proposed_spec, spec_hash_override=approved_spec.spec_hash
        )
    else:
        raise ValueError("repair requires --proposed-code or --diagnostic")
    proposal = await RepairService(assets).propose(
        args.scenario_id, analysis, source, proposed_spec=proposed_spec
    )
    asset = assets.load_asset(args.scenario_id)
    trial = await TrialRunner(
        assets.repository_root, timeout_seconds=operational.trial_timeout_seconds
    ).run(
        args.scenario_id,
        asset.draft_path / "generated.spec.ts",
        artifact_dir=asset.draft_path / "artifacts",
    )
    assets.save_trial(args.scenario_id, trial)
    proposal = proposal.model_copy(
        update={
            "validation_results": [
                *proposal.validation_results,
                f"repair trial: {trial.status}",
            ]
        }
    )
    if trial.status != "passed":
        assets.write_json(
            args.scenario_id, "repair-proposal.json", proposal.model_dump(mode="json")
        )
        print(proposal.model_dump_json(indent=2))
        return 2
    if args.create_pr:
        if asset.published_path is None:
            raise ValueError("Creating a repair PR requires an already published asset")
        published_path = asset.published_path
        if not published_path.is_absolute():
            published_path = assets.repository_root / published_path
        publisher = GitHubRepositoryPublisher(assets.repository_root)
        proposal = await publish_repair_pull_request(
            publisher,
            proposal,
            [published_path],
            file_updates={published_path: source},
            base_branch=args.base_branch or operational.github_base_branch,
            branch_prefix=operational.repair_branch_prefix,
        )
    assets.write_json(args.scenario_id, "repair-proposal.json", proposal.model_dump(mode="json"))
    print(proposal.model_dump_json(indent=2))
    return 0


def _agent_overrides(args: argparse.Namespace) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "model_provider": getattr(args, "model_provider", None),
            "model_auth": getattr(args, "model_auth", None),
        }.items()
        if value is not None
    }


def _add_agent_options(parser: argparse.ArgumentParser) -> None:
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


def _add_repo_and_scenario(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target-repo", type=Path, required=True)
    parser.add_argument("--scenario-id", required=True)


def _add_repo_and_optional_scenario(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target-repo", type=Path, required=True)
    parser.add_argument("--scenario-id")


def _json_object(value: str) -> dict[str, object]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("test data must be a JSON object")
    return payload


def main() -> None:
    args = build_parser().parse_args()
    try:
        exit_code = asyncio.run(_run(args))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        exit_code = 4 if args.command in {"regression", "publish", "repair"} else 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

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
from maf_e2e.domain.assets import GeneratedTestAsset, TrialRunResult
from maf_e2e.domain.failures import FailureAnalysis, RegressionFailureDiagnostic
from maf_e2e.domain.regression import TargetEnvironment
from maf_e2e.domain.specification import TestLifecycleStatus, TestSpecification
from maf_e2e.failure_analysis import analyze_failure
from maf_e2e.github_repair import GitHubRepositoryPublisher, publish_repair_pull_request
from maf_e2e.models import E2ETestRequest, LiteralStatus
from maf_e2e.publisher import Publisher
from maf_e2e.regression_runner import (
    RegressionRunner,
    find_latest_successful_result,
    regression_exit_code,
)
from maf_e2e.repair import RepairService, apply_locator_repairs
from maf_e2e.telemetry import configure_telemetry
from maf_e2e.trial_runner import TrialRunner

ALLOWED_TARGET_ENVIRONMENTS = [item.value for item in TargetEnvironment]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Author, approve, publish, and run Playwright E2E regression assets"
    )
    _add_agent_options(parser)
    parser.add_argument("--target-url", help="Absolute target application URL")
    parser.add_argument("--objective", help="Business objective to validate")
    parser.add_argument("--policy", action="append", default=[], help="Business policy; repeatable")
    parser.add_argument("--max-refinements", type=int, help="Self-healing retries, 0-5")
    parser.add_argument(
        "--environment",
        choices=ALLOWED_TARGET_ENVIRONMENTS,
        default=TargetEnvironment.LOCAL.value,
        help="Target environment for legacy autonomous execution",
    )
    parser.add_argument("--resume-run-id", help="Resume a saved legacy run from its browser stage")
    parser.add_argument("--checkpoint-id", help="Specific checkpoint in the saved run chain")

    commands = parser.add_subparsers(dest="command")
    author = commands.add_parser("author", help="Create and trial draft E2E assets")
    _add_agent_options(author)
    author.add_argument("--target-repo", type=Path, required=True)
    author.add_argument("--target-url", required=True)
    author.add_argument(
        "--environment",
        choices=ALLOWED_TARGET_ENVIRONMENTS,
        default=TargetEnvironment.LOCAL.value,
    )
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
    author.add_argument("--max-pages", type=int, default=20)
    author.add_argument("--max-actions", type=int, default=100)
    author.add_argument("--max-duration-seconds", type=int, default=600)
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

    for name in ("disable", "retire"):
        lifecycle = commands.add_parser(name, help=f"{name.title()} an active scenario")
        _add_repo_and_scenario(lifecycle)
        lifecycle.add_argument("--reviewer", required=True)
        lifecycle.add_argument("--comment")

    new_version = commands.add_parser("new-version", help="Create a new draft spec version")
    _add_repo_and_scenario(new_version)
    new_version.add_argument("--reviewer", required=True)
    new_version.add_argument("--comment")

    regression = commands.add_parser("regression", help="Run ACTIVE assets without an Agent")
    regression.add_argument("--target-repo", type=Path, required=True)
    regression.add_argument(
        "--environment",
        choices=ALLOWED_TARGET_ENVIRONMENTS,
        default=TargetEnvironment.LOCAL.value,
    )
    regression.add_argument("--scenario-id", action="append", default=[])
    regression.add_argument(
        "--no-classify-failures",
        action="store_true",
        help="Run fixed code only and return 1 for any test failure without classification",
    )

    failure = commands.add_parser("analyze-failure", help="Classify a saved trial failure")
    _add_agent_options(failure)
    failure.add_argument("--trial-result", type=Path, required=True)
    failure.add_argument("--target-repo", type=Path)
    failure.add_argument("--diagnostic", action="append", default=[])
    failure.add_argument("--previous-passed", action="store_true")
    failure.add_argument("--investigate", action="store_true")
    failure.add_argument("--target-url")
    failure.add_argument(
        "--environment",
        choices=ALLOWED_TARGET_ENVIRONMENTS,
        default=TargetEnvironment.LOCAL.value,
    )
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
    if args.command in {"disable", "retire"}:
        return _run_lifecycle_status(args)
    if args.command == "new-version":
        return _run_new_version(args)
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
            target_environment=args.environment,
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
        max_pages=args.max_pages,
        max_actions=args.max_actions,
        max_duration_seconds=args.max_duration_seconds,
        target_environment=args.environment,
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
    approvals = ApprovalStore(assets)
    selected = (
        [assets.load_asset(args.scenario_id)] if args.scenario_id else assets.list_drafts()
    )
    packages = []
    for asset in selected:
        specification = assets.load_specification(asset.scenario_id)
        item: dict[str, object] = {
            "asset": asset.model_dump(mode="json"),
            "specification": specification.model_dump(mode="json"),
            "code": assets.load_source(asset.scenario_id),
            "review_history": [
                approval.model_dump(mode="json")
                for approval in approvals.history(asset.scenario_id)
            ],
        }
        try:
            trial = assets.load_trial(asset.scenario_id)
            item["trial"] = trial.model_dump(mode="json")
            item["review_evidence"] = _review_evidence(specification, trial)
        except FileNotFoundError:
            item["trial"] = None
            item["review_evidence"] = _empty_review_evidence(specification)
        packages.append(item)
    print(json.dumps(packages, ensure_ascii=False, indent=2, default=str))
    return 0


def _review_evidence(
    specification: TestSpecification, trial: TrialRunResult
) -> dict[str, object]:
    return {
        **_empty_review_evidence(specification),
        "final_url": trial.final_url,
        "step_results": [step.model_dump(mode="json") for step in trial.step_results],
        "assertion_results": [
            assertion.model_dump(mode="json") for assertion in trial.assertion_results
        ],
        "artifacts": {
            "report_path": trial.report_path,
            "junit_path": trial.junit_path,
            "html_report_path": trial.html_report_path,
            "screenshot_paths": trial.screenshot_paths,
            "trace_path": trial.trace_path,
        },
    }


def _empty_review_evidence(specification: TestSpecification) -> dict[str, object]:
    return {
        "side_effects": specification.cleanup,
        "unexplored_areas": [],
        "prohibited_actions": specification.prohibited_actions,
    }


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


def _run_lifecycle_status(args: argparse.Namespace) -> int:
    status = {
        "disable": TestLifecycleStatus.DISABLED,
        "retire": TestLifecycleStatus.RETIRED,
    }[args.command]
    asset = AssetStore(args.target_repo).set_lifecycle_status(
        args.scenario_id,
        status,
        actor=args.reviewer,
        comment=args.comment,
    )
    print(asset.model_dump_json(indent=2))
    return 0


def _run_new_version(args: argparse.Namespace) -> int:
    asset = AssetStore(args.target_repo).create_new_version(
        args.scenario_id,
        actor=args.reviewer,
        comment=args.comment,
    )
    print(asset.model_dump_json(indent=2))
    return 0


async def _run_regression(args: argparse.Namespace) -> int:
    settings = OperationalSettings()
    runner = RegressionRunner(args.target_repo, timeout_seconds=settings.regression_timeout_seconds)
    result = await runner.run(
        TargetEnvironment(args.environment),
        scenario_ids=set(args.scenario_id) if args.scenario_id else None,
        classify_failures=not args.no_classify_failures,
    )
    print(result.model_dump_json(indent=2))
    return regression_exit_code(result)


async def _run_failure_analysis(args: argparse.Namespace) -> int:
    trial = TrialRunResult.model_validate_json(args.trial_result.read_text(encoding="utf-8"))
    previous_passed = args.previous_passed
    history_evidence: list[str] = []
    if args.target_repo is not None:
        history = find_latest_successful_result(args.target_repo, trial.scenario_id)
        if history is not None:
            run, _result = history
            previous_passed = True
            completed = run.completed_at or run.started_at
            history_evidence.append(
                "Previous passing regression result found: "
                f"run_id={run.run_id}, completed_at={completed.isoformat()}, "
                f"commit={run.git_commit or 'unknown'}"
            )
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
            evidence=[diagnostic.current_ui_summary, *diagnostic.evidence, *history_evidence],
            recommended_action=diagnostic.recommended_action,
        )
    else:
        analysis = analyze_failure(
            trial,
            diagnostic_evidence=[*args.diagnostic, *history_evidence],
            previous_passed=previous_passed,
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
    if proposal.proposed_code_path is None:
        raise ValueError("Repair proposal did not record a candidate code path")
    proposed_code_path = Path(proposal.proposed_code_path)
    if _has_failed_static_validation(proposal.validation_results):
        proposal = proposal.model_copy(
            update={
                "validation_results": [
                    *proposal.validation_results,
                    "target trial: skipped because static validation failed",
                ]
            }
        )
        assets.write_json(
            args.scenario_id, "repair-proposal.json", proposal.model_dump(mode="json")
        )
        print(proposal.model_dump_json(indent=2))
        return 2
    trial_runner = TrialRunner(
        assets.repository_root, timeout_seconds=operational.trial_timeout_seconds
    )
    trial = await trial_runner.run(
        args.scenario_id,
        proposed_code_path,
        artifact_dir=proposed_code_path.parent / "artifacts",
    )
    assets.save_repair_trial(args.scenario_id, proposal.proposal_id, trial)
    proposal = proposal.model_copy(
        update={
            "validation_results": [
                *proposal.validation_results,
                f"target trial {args.scenario_id}: {trial.status}",
            ],
            "artifact_paths": [
                *proposal.artifact_paths,
                *_trial_artifact_paths(trial),
            ],
        }
    )
    if trial.status != "passed":
        assets.write_json(
            args.scenario_id, "repair-proposal.json", proposal.model_dump(mode="json")
        )
        print(proposal.model_dump_json(indent=2))
        return 2
    related_failures: list[str] = []
    for related_asset in _related_active_assets(assets, asset):
        related_path = _published_code_path(assets, related_asset)
        related_trial = await trial_runner.run(
            related_asset.scenario_id,
            related_path,
            artifact_dir=(
                proposed_code_path.parent / "related" / related_asset.scenario_id
            ),
        )
        assets.save_repair_related_trial(
            args.scenario_id,
            proposal.proposal_id,
            related_asset.scenario_id,
            related_trial,
        )
        proposal = proposal.model_copy(
            update={
                "validation_results": [
                    *proposal.validation_results,
                    f"related trial {related_asset.scenario_id}: {related_trial.status}",
                ],
                "artifact_paths": [
                    *proposal.artifact_paths,
                    *_trial_artifact_paths(related_trial),
                ],
            }
        )
        if related_trial.status != "passed":
            related_failures.append(related_asset.scenario_id)
    if related_failures:
        proposal = proposal.model_copy(
            update={
                "validation_results": [
                    *proposal.validation_results,
                    "repair PR skipped: related scenario validation failed for "
                    + ", ".join(related_failures),
                ]
            }
        )
        assets.write_json(
            args.scenario_id, "repair-proposal.json", proposal.model_dump(mode="json")
        )
        print(proposal.model_dump_json(indent=2))
        return 2
    if args.create_pr:
        if asset.published_path is None:
            raise ValueError("Creating a repair PR requires an already published asset")
        published_path = _published_code_path(assets, asset)
        repaired_source = proposal.proposed_code or proposed_code_path.read_text(
            encoding="utf-8"
        )
        publisher = GitHubRepositoryPublisher(assets.repository_root)
        proposal = await publish_repair_pull_request(
            publisher,
            proposal,
            [published_path],
            file_updates={published_path: repaired_source},
            base_branch=args.base_branch or operational.github_base_branch,
            branch_prefix=operational.repair_branch_prefix,
        )
    assets.write_json(args.scenario_id, "repair-proposal.json", proposal.model_dump(mode="json"))
    print(proposal.model_dump_json(indent=2))
    return 0


def _has_failed_static_validation(validation_results: list[str]) -> bool:
    return any(result.endswith(": failed") for result in validation_results)


def _trial_artifact_paths(trial: TrialRunResult) -> list[str]:
    return [
        path
        for path in [
            trial.report_path,
            trial.junit_path,
            trial.html_report_path,
            trial.trace_path,
            *trial.screenshot_paths,
        ]
        if path
    ]


def _related_active_assets(
    assets: AssetStore, target_asset: GeneratedTestAsset
) -> list[GeneratedTestAsset]:
    target_parent = _published_parent(assets, target_asset)
    related: list[GeneratedTestAsset] = []
    for asset in assets.list_active_assets():
        if asset.scenario_id == target_asset.scenario_id:
            continue
        if asset.published_path is None:
            continue
        same_feature = asset.feature == target_asset.feature
        same_published_parent = (
            target_parent is not None and _published_parent(assets, asset) == target_parent
        )
        if same_feature or same_published_parent:
            related.append(asset)
    return related


def _published_parent(assets: AssetStore, asset: GeneratedTestAsset) -> Path | None:
    if asset.published_path is None:
        return None
    return _published_code_path(assets, asset).parent


def _published_code_path(assets: AssetStore, asset: GeneratedTestAsset) -> Path:
    if asset.published_path is None:
        raise ValueError(f"ACTIVE asset has no published path: {asset.scenario_id}")
    published_path = asset.published_path
    if not published_path.is_absolute():
        published_path = assets.repository_root / published_path
    return published_path


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

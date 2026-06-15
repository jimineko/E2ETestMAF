from __future__ import annotations

import json
from pathlib import Path

import pytest
from regression_helpers import make_fake_node_repository, sample_spec

from maf_e2e.approval_store import ApprovalStore
from maf_e2e.asset_store import AssetStore
from maf_e2e.cli import _run, build_parser
from maf_e2e.domain.approval import ApprovalAction
from maf_e2e.domain.assets import AssertionResult, StepResult, TrialRunResult
from maf_e2e.domain.failures import (
    FailureAnalysis,
    FailureCategory,
    LocatorRepair,
    RegressionFailureDiagnostic,
)
from maf_e2e.domain.regression import RegressionRun, ScenarioRunResult, TargetEnvironment
from maf_e2e.domain.specification import LocatorSpec
from maf_e2e.domain.specification import TestLifecycleStatus as LifecycleStatus
from maf_e2e.playwright_codegen import generate_playwright_test
from maf_e2e.publisher import Publisher


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


async def test_review_outputs_structured_trial_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assets = AssetStore(tmp_path)
    spec = sample_spec()
    asset = assets.save_draft(spec, generate_playwright_test(spec))
    assets.save_trial(
        spec.scenario_id,
        TrialRunResult(
            run_id="trial",
            scenario_id=spec.scenario_id,
            code_hash=asset.code_hash,
            status="failed",
            step_results=[
                StepResult(
                    step_id="fill-email",
                    action="fill",
                    status="passed",
                    url="https://example.com/login",
                    locator='{"strategy":"label","value":"Email"}',
                )
            ],
            assertion_results=[
                AssertionResult(
                    assertion_id="heading-visible",
                    status="failed",
                    expected="true",
                    actual="false",
                    url="https://example.com/login",
                    locator='{"strategy":"role","role":"heading","name":"Login"}',
                    error="not visible",
                )
            ],
            final_url="https://example.com/login",
            screenshot_paths=["artifacts/screenshot.png"],
            trace_path="artifacts/trace.zip",
            report_path="artifacts/report.json",
        ),
    )
    args = build_parser().parse_args(
        ["review", "--target-repo", str(tmp_path), "--scenario-id", spec.scenario_id]
    )

    assert await _run(args) == 0

    payload = json.loads(capsys.readouterr().out)
    evidence = payload[0]["review_evidence"]
    assert evidence["final_url"] == "https://example.com/login"
    assert evidence["step_results"][0]["step_id"] == "fill-email"
    assert evidence["assertion_results"][0]["actual"] == "false"
    assert evidence["artifacts"]["trace_path"] == "artifacts/trace.zip"


async def test_analyze_failure_uses_previous_passing_regression_history(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    scenario_id = "login-page-1234567890"
    passed_trial = TrialRunResult(
        run_id="passed",
        scenario_id=scenario_id,
        code_hash="hash",
        status="passed",
        report_path="passed-report.json",
    )
    history = RegressionRun(
        run_id="previous-run",
        repository=repo,
        git_commit="abc123",
        environment=TargetEnvironment.STAGING,
        scenario_results=[
            ScenarioRunResult(
                scenario_id=scenario_id,
                status="passed",
                trial=passed_trial,
            )
        ],
    )
    history_path = repo / ".maf-e2e" / "regression" / "previous-run" / "regression.json"
    history_path.parent.mkdir(parents=True)
    history_path.write_text(history.model_dump_json(indent=2), encoding="utf-8")
    failed_trial_path = tmp_path / "trial-result.json"
    failed_trial_path.write_text(
        TrialRunResult(
            run_id="failed",
            scenario_id=scenario_id,
            code_hash="hash",
            status="failed",
            report_path="failed-report.json",
            error="intermittent retry failed",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        [
            "analyze-failure",
            "--trial-result",
            str(failed_trial_path),
            "--target-repo",
            str(repo),
        ]
    )

    assert await _run(args) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["category"] == FailureCategory.FLAKY_FAILURE.value
    assert any("previous-run" in item for item in payload["evidence"])
    saved = json.loads(failed_trial_path.with_name("failure-analysis.json").read_text())
    assert saved["category"] == FailureCategory.FLAKY_FAILURE.value


def test_regression_rejects_production_environment(tmp_path) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["regression", "--target-repo", str(tmp_path), "--environment", "production"]
        )


def test_author_rejects_production_environment(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "author",
                "--target-repo",
                str(tmp_path),
                "--target-url",
                "https://example.com",
                "--environment",
                "production",
                "--objective",
                "Validate login",
                "--expected-result",
                "Login page is visible",
            ]
        )


def test_analyze_failure_rejects_production_environment(tmp_path: Path) -> None:
    trial = tmp_path / "trial-result.json"
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "analyze-failure",
                "--trial-result",
                str(trial),
                "--investigate",
                "--target-url",
                "https://example.com",
                "--environment",
                "production",
            ]
        )


def test_legacy_rejects_production_environment() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "--target-url",
                "https://example.com",
                "--objective",
                "Validate login",
                "--environment",
                "production",
            ]
        )


def test_author_accepts_discovery_limits(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "author",
            "--target-repo",
            str(tmp_path),
            "--target-url",
            "https://example.com",
            "--environment",
            "staging",
            "--objective",
            "Validate login",
            "--expected-result",
            "Login page is visible",
            "--max-pages",
            "3",
            "--max-actions",
            "12",
            "--max-duration-seconds",
            "45",
        ]
    )

    assert args.max_pages == 3
    assert args.max_actions == 12
    assert args.max_duration_seconds == 45
    assert args.environment == "staging"


async def test_disable_and_retire_update_lifecycle_and_regression_skips_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_fake_node_repository(tmp_path)
    assets = AssetStore(tmp_path)
    disabled_published, _source = _publish_approved_spec(assets)
    retired_scenario_id = "login-retired-1234567890"
    retired_published, _retired_source = _publish_approved_spec(
        assets, retired_scenario_id
    )

    disable_args = build_parser().parse_args(
        [
            "disable",
            "--target-repo",
            str(tmp_path),
            "--scenario-id",
            sample_spec().scenario_id,
            "--reviewer",
            "reviewer@example.com",
            "--comment",
            "Temporarily disabled",
        ]
    )
    retire_args = build_parser().parse_args(
        [
            "retire",
            "--target-repo",
            str(tmp_path),
            "--scenario-id",
            retired_scenario_id,
            "--reviewer",
            "reviewer@example.com",
        ]
    )

    assert await _run(disable_args) == 0
    assert await _run(retire_args) == 0

    disabled_metadata = json.loads(
        (
            tmp_path
            / "e2e"
            / "metadata"
            / "login"
            / f"{sample_spec().scenario_id}.json"
        ).read_text(encoding="utf-8")
    )
    retired_metadata = json.loads(
        (
            tmp_path
            / "e2e"
            / "metadata"
            / "login"
            / f"{retired_scenario_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert disabled_metadata["status"] == LifecycleStatus.DISABLED.value
    assert retired_metadata["status"] == LifecycleStatus.RETIRED.value
    assert disabled_published.exists()
    assert retired_published.exists()
    capsys.readouterr()

    regression_args = build_parser().parse_args(
        ["regression", "--target-repo", str(tmp_path), "--environment", "staging"]
    )
    assert await _run(regression_args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scenario_results"] == []


async def test_request_changes_comment_is_visible_in_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assets = AssetStore(tmp_path)
    spec = sample_spec()
    assets.save_draft(spec, generate_playwright_test(spec))
    ApprovalStore(assets).record(
        spec.scenario_id,
        action=ApprovalAction.REQUEST_CHANGES,
        reviewer="reviewer@example.com",
        comment="Clarify expected redirect.",
    )
    args = build_parser().parse_args(
        ["review", "--target-repo", str(tmp_path), "--scenario-id", spec.scenario_id]
    )

    assert await _run(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["review_history"][0]["comment"] == "Clarify expected redirect."


async def test_new_version_requires_fresh_approval_before_publish(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    assets = AssetStore(tmp_path)
    _published, _source = _publish_approved_spec(assets)
    spec = sample_spec()
    args = build_parser().parse_args(
        [
            "new-version",
            "--target-repo",
            str(tmp_path),
            "--scenario-id",
            spec.scenario_id,
            "--reviewer",
            "reviewer@example.com",
            "--comment",
            "Expected result changed",
        ]
    )

    assert await _run(args) == 0

    draft_asset = assets.load_asset(spec.scenario_id)
    assert draft_asset.spec_version == 2
    assert draft_asset.status == LifecycleStatus.GENERATED
    with pytest.raises(ValueError, match="Specification hash changed"):
        Publisher(assets, ApprovalStore(assets)).publish(spec.scenario_id)


def _publish_approved_spec(
    assets: AssetStore,
    scenario_id: str | None = None,
) -> tuple[Path, str]:
    spec = sample_spec()
    if scenario_id is not None:
        spec = spec.model_copy(update={"scenario_id": scenario_id}).with_hash()
    source = generate_playwright_test(spec)
    asset = assets.save_draft(spec, source)
    assets.save_trial(
        spec.scenario_id,
        TrialRunResult(
            run_id=f"trial-{spec.scenario_id}",
            scenario_id=spec.scenario_id,
            code_hash=asset.code_hash,
            status="passed",
            report_path="report.json",
        ),
    )
    approvals = ApprovalStore(assets)
    approvals.record(
        spec.scenario_id,
        action=ApprovalAction.APPROVE,
        reviewer="reviewer@example.com",
    )
    return Publisher(assets, approvals).publish(spec.scenario_id), source


def _write_repair_inputs(tmp_path: Path, scenario_id: str) -> tuple[Path, Path]:
    analysis_path = tmp_path / f"{scenario_id}-failure-analysis.json"
    analysis_path.write_text(
        FailureAnalysis(
            scenario_id=scenario_id,
            category=FailureCategory.TEST_MAINTENANCE,
            confidence=0.8,
            recommended_action="Update locator",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    diagnostic_path = tmp_path / f"{scenario_id}-regression-diagnostic.json"
    diagnostic_path.write_text(
        RegressionFailureDiagnostic(
            scenario_id=scenario_id,
            category=FailureCategory.TEST_MAINTENANCE,
            confidence=0.8,
            current_ui_summary="Email field now has a test id.",
            recommended_action="Update locator",
            locator_replacements=[
                LocatorRepair(
                    target_id="fill-email",
                    locator=LocatorSpec(strategy="test_id", value="email"),
                )
            ],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return analysis_path, diagnostic_path


def _write_fake_playwright(root: Path, script: str) -> None:
    path = root / "node_modules" / ".bin" / "playwright"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


async def test_repair_diagnostic_create_pr_preserves_published_asset_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_fake_node_repository(tmp_path)
    assets = AssetStore(tmp_path)
    spec = sample_spec()
    source = generate_playwright_test(spec)
    asset = assets.save_draft(spec, source)
    assets.save_trial(
        spec.scenario_id,
        TrialRunResult(
            run_id="trial",
            scenario_id=spec.scenario_id,
            code_hash=asset.code_hash,
            status="passed",
            report_path="report.json",
        ),
    )
    approvals = ApprovalStore(assets)
    approvals.record(
        spec.scenario_id,
        action=ApprovalAction.APPROVE,
        reviewer="reviewer@example.com",
    )
    published = Publisher(assets, approvals).publish(spec.scenario_id)
    approved_asset = assets.load_asset(spec.scenario_id)

    analysis_path = tmp_path / "failure-analysis.json"
    analysis_path.write_text(
        FailureAnalysis(
            scenario_id=spec.scenario_id,
            category=FailureCategory.TEST_MAINTENANCE,
            confidence=0.8,
            recommended_action="Update locator",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    diagnostic_path = tmp_path / "regression-diagnostic.json"
    diagnostic_path.write_text(
        RegressionFailureDiagnostic(
            scenario_id=spec.scenario_id,
            category=FailureCategory.TEST_MAINTENANCE,
            confidence=0.8,
            current_ui_summary="Email field now has a test id.",
            recommended_action="Update locator",
            locator_replacements=[
                LocatorRepair(
                    target_id="fill-email",
                    locator=LocatorSpec(strategy="test_id", value="email"),
                )
            ],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    class FakePublisher:
        latest: FakePublisher | None = None

        def __init__(self, repository_root: Path) -> None:
            self.repository_root = repository_root.resolve(strict=True)
            self.committed_paths: list[Path] = []
            FakePublisher.latest = self

        async def create_branch(self, branch_name: str) -> None:
            self.branch_name = branch_name

        async def commit_files(self, paths: list[Path], message: str) -> str:
            self.committed_paths = paths
            self.commit_message = message
            return "fake-commit"

        async def push(self, branch_name: str) -> None:
            self.pushed_branch = branch_name

        async def create_pull_request(
            self, *, branch_name: str, title: str, body: str, base_branch: str
        ) -> str:
            self.pull_request = {
                "branch_name": branch_name,
                "title": title,
                "body": body,
                "base_branch": base_branch,
            }
            return "https://github.com/example/repo/pull/1"

    monkeypatch.setattr("maf_e2e.cli.GitHubRepositoryPublisher", FakePublisher)

    args = build_parser().parse_args(
        [
            "repair",
            "--target-repo",
            str(tmp_path),
            "--scenario-id",
            spec.scenario_id,
            "--analysis",
            str(analysis_path),
            "--diagnostic",
            str(diagnostic_path),
            "--create-pr",
        ]
    )

    assert await _run(args) == 0

    current_asset = assets.load_asset(spec.scenario_id)
    assert current_asset.status == LifecycleStatus.ACTIVE
    assert current_asset.published_path == approved_asset.published_path
    assert current_asset.spec_hash == approved_asset.spec_hash
    assert current_asset.code_hash == approved_asset.code_hash
    assert current_asset.code_version == approved_asset.code_version
    assert assets.load_source(spec.scenario_id) == source
    assert 'getByTestId("email")' in published.read_text(encoding="utf-8")
    assert FakePublisher.latest is not None
    assert FakePublisher.latest.committed_paths == [published]

    proposal = json.loads(
        (assets.draft_dir(spec.scenario_id) / "repair-proposal.json").read_text(
            encoding="utf-8"
        )
    )
    assert proposal["pull_request_url"] == "https://github.com/example/repo/pull/1"
    assert Path(proposal["proposed_code_path"]).parent.parent.name == "repairs"


async def test_repair_blocks_pr_when_target_trial_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_fake_node_repository(tmp_path)
    _write_fake_playwright(
        tmp_path,
        """#!/bin/sh
case " $* " in
  *" --list "*) exit 0 ;;
esac
printf '{"suites":[]}' > "$PLAYWRIGHT_JSON_OUTPUT_NAME"
printf '<testsuite tests="1" failures="1" />' > "$PLAYWRIGHT_JUNIT_OUTPUT_FILE"
mkdir -p "$PLAYWRIGHT_HTML_OUTPUT_DIR"
printf '<html></html>' > "$PLAYWRIGHT_HTML_OUTPUT_DIR/index.html"
exit 1
""",
    )
    assets = AssetStore(tmp_path)
    published, _source = _publish_approved_spec(assets)
    spec = sample_spec()
    analysis_path, diagnostic_path = _write_repair_inputs(tmp_path, spec.scenario_id)

    class FakePublisher:
        latest: FakePublisher | None = None

        def __init__(self, repository_root: Path) -> None:
            FakePublisher.latest = self

    monkeypatch.setattr("maf_e2e.cli.GitHubRepositoryPublisher", FakePublisher)

    args = build_parser().parse_args(
        [
            "repair",
            "--target-repo",
            str(tmp_path),
            "--scenario-id",
            spec.scenario_id,
            "--analysis",
            str(analysis_path),
            "--diagnostic",
            str(diagnostic_path),
            "--create-pr",
        ]
    )

    assert await _run(args) == 2
    assert FakePublisher.latest is None
    proposal = json.loads(
        (assets.draft_dir(spec.scenario_id) / "repair-proposal.json").read_text(
            encoding="utf-8"
        )
    )
    assert f"target trial {spec.scenario_id}: failed" in proposal["validation_results"]
    assert 'getByTestId("email")' not in published.read_text(encoding="utf-8")


async def test_repair_blocks_pr_when_related_active_scenario_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_fake_node_repository(tmp_path)
    related_scenario_id = "login-related-1234567890"
    _write_fake_playwright(
        tmp_path,
        f"""#!/bin/sh
case " $* " in
  *" --list "*) exit 0 ;;
esac
printf '{{"suites":[]}}' > "$PLAYWRIGHT_JSON_OUTPUT_NAME"
printf '<testsuite tests="1" failures="0" />' > "$PLAYWRIGHT_JUNIT_OUTPUT_FILE"
mkdir -p "$PLAYWRIGHT_HTML_OUTPUT_DIR"
printf '<html></html>' > "$PLAYWRIGHT_HTML_OUTPUT_DIR/index.html"
case " $* " in
  *"{related_scenario_id}.spec.ts"*) exit 1 ;;
esac
exit 0
""",
    )
    assets = AssetStore(tmp_path)
    target_published, _source = _publish_approved_spec(assets)
    _related_published, _related_source = _publish_approved_spec(
        assets, related_scenario_id
    )
    spec = sample_spec()
    analysis_path, diagnostic_path = _write_repair_inputs(tmp_path, spec.scenario_id)

    class FakePublisher:
        latest: FakePublisher | None = None

        def __init__(self, repository_root: Path) -> None:
            FakePublisher.latest = self

    monkeypatch.setattr("maf_e2e.cli.GitHubRepositoryPublisher", FakePublisher)

    args = build_parser().parse_args(
        [
            "repair",
            "--target-repo",
            str(tmp_path),
            "--scenario-id",
            spec.scenario_id,
            "--analysis",
            str(analysis_path),
            "--diagnostic",
            str(diagnostic_path),
            "--create-pr",
        ]
    )

    assert await _run(args) == 2
    assert FakePublisher.latest is None
    proposal = json.loads(
        (assets.draft_dir(spec.scenario_id) / "repair-proposal.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        f"related trial {related_scenario_id}: failed"
        in proposal["validation_results"]
    )
    assert (
        "repair PR skipped: related scenario validation failed for "
        + related_scenario_id
        in proposal["validation_results"]
    )
    assert 'getByTestId("email")' not in target_published.read_text(encoding="utf-8")

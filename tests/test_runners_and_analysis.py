from __future__ import annotations

import json
from base64 import b64encode
from pathlib import Path

import pytest
from regression_helpers import make_fake_node_repository, sample_spec

from maf_e2e.asset_store import AssetStore
from maf_e2e.code_validation import CodeValidator
from maf_e2e.domain.assets import GeneratedTestAsset, TrialRunResult
from maf_e2e.domain.failures import FailureCategory
from maf_e2e.domain.regression import TargetEnvironment
from maf_e2e.domain.specification import (
    TestLifecycleStatus as LifecycleStatus,
)
from maf_e2e.domain.specification import (
    TestSpecification as E2ESpecification,
)
from maf_e2e.failure_analysis import analyze_failure
from maf_e2e.playwright_codegen import generate_playwright_test
from maf_e2e.regression_runner import RegressionRunner, regression_exit_code
from maf_e2e.trial_runner import TrialRunner, _assertion_results, _step_results


async def test_validation_and_trial_use_target_repository_tools(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    store = AssetStore(tmp_path)
    spec = sample_spec()
    asset = store.save_draft(spec, generate_playwright_test(spec))

    validation = await CodeValidator(tmp_path).validate(asset.draft_path / "generated.spec.ts")
    trial = await TrialRunner(tmp_path).run(
        spec.scenario_id,
        asset.draft_path / "generated.spec.ts",
        artifact_dir=asset.draft_path / "artifacts",
    )

    assert validation.passed is True
    assert [check.name for check in validation.checks] == [
        "format",
        "lint",
        "type_check",
        "discovery",
    ]
    assert trial.status == "passed"
    assert Path(trial.report_path).exists()


async def test_regression_runs_only_active_assets_without_agent_settings(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    store = AssetStore(tmp_path)
    spec = sample_spec()
    source = generate_playwright_test(spec)
    asset = store.save_draft(spec, source)
    published = tmp_path / "e2e" / "generated" / "login" / f"{spec.scenario_id}.spec.ts"
    published.parent.mkdir(parents=True)
    published.write_text(source, encoding="utf-8")
    metadata = tmp_path / "e2e" / "metadata" / "login" / f"{spec.scenario_id}.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        asset.model_copy(
            update={"status": LifecycleStatus.ACTIVE, "published_path": published}
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    inactive = GeneratedTestAsset.model_validate(asset.model_dump())
    inactive_metadata = metadata.with_name("inactive.json")
    inactive_metadata.write_text(inactive.model_dump_json(indent=2), encoding="utf-8")

    run = await RegressionRunner(tmp_path).run(TargetEnvironment.STAGING)

    assert [result.scenario_id for result in run.scenario_results] == [spec.scenario_id]
    assert regression_exit_code(run) == 0


async def test_regression_classifies_test_maintenance_as_exit_two(
    tmp_path: Path,
) -> None:
    make_fake_node_repository(tmp_path)
    _write_failing_playwright(tmp_path, "locator waiting for element timed out")
    store = AssetStore(tmp_path)
    spec = sample_spec()
    _publish_active_asset(store, spec)

    run = await RegressionRunner(tmp_path).run(TargetEnvironment.STAGING)

    assert run.scenario_results[0].analysis is not None
    assert run.scenario_results[0].analysis.category == FailureCategory.TEST_MAINTENANCE
    assert regression_exit_code(run) == 2


async def test_regression_keeps_application_defect_as_exit_one(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    _write_failing_playwright(tmp_path, "Expected welcome but received error")
    store = AssetStore(tmp_path)
    spec = sample_spec()
    _publish_active_asset(store, spec)

    run = await RegressionRunner(tmp_path).run(TargetEnvironment.STAGING)

    assert run.scenario_results[0].analysis is not None
    assert run.scenario_results[0].analysis.category == FailureCategory.APPLICATION_DEFECT
    assert regression_exit_code(run) == 1


async def test_regression_can_disable_failure_classification(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    _write_failing_playwright(tmp_path, "locator waiting for element timed out")
    store = AssetStore(tmp_path)
    spec = sample_spec()
    _publish_active_asset(store, spec)

    run = await RegressionRunner(tmp_path).run(
        TargetEnvironment.STAGING, classify_failures=False
    )

    assert run.scenario_results[0].analysis is None
    assert regression_exit_code(run) == 1


def test_failure_analysis_distinguishes_maintenance_and_application_defect() -> None:
    maintenance = TrialRunResult(
        run_id="run",
        scenario_id="scenario",
        code_hash="hash",
        status="failed",
        report_path="report.json",
        error="locator waiting for element timed out",
    )
    defect = maintenance.model_copy(update={"error": "Expected welcome but received error"})

    assert analyze_failure(maintenance).category == FailureCategory.TEST_MAINTENANCE
    assert analyze_failure(defect).category == FailureCategory.APPLICATION_DEFECT


@pytest.mark.parametrize(
    ("error", "previous_passed", "expected"),
    [
        ("Expected welcome but received error", False, FailureCategory.APPLICATION_DEFECT),
        ("locator waiting for element timed out", False, FailureCategory.TEST_MAINTENANCE),
        ("browser closed after timeout", False, FailureCategory.ENVIRONMENT_FAILURE),
        ("401 unauthorized storage state expired", False, FailureCategory.AUTHENTICATION_FAILURE),
        ("Missing test data: user.email", False, FailureCategory.TEST_DATA_FAILURE),
        ("intermittent retry failed", True, FailureCategory.FLAKY_FAILURE),
        ("unclassified failure", False, FailureCategory.UNKNOWN),
    ],
)
def test_failure_analysis_covers_all_categories(
    error: str, previous_passed: bool, expected: FailureCategory
) -> None:
    trial = TrialRunResult(
        run_id="run",
        scenario_id="scenario",
        code_hash="hash",
        status="failed",
        report_path="report.json",
        error=error,
    )

    analysis = analyze_failure(trial, previous_passed=previous_passed)

    assert analysis.category == expected
    if previous_passed:
        assert any("Previous passing" in item for item in analysis.evidence)


def test_trial_report_extracts_assertion_steps() -> None:
    report = {
        "suites": [
            {
                "specs": [
                    {
                        "tests": [
                            {
                                "results": [
                                    {
                                        "steps": [
                                            {"title": "assertion:heading", "duration": 10},
                                            {
                                                "title": "assertion:message",
                                                "error": {"message": "not visible"},
                                            },
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }

    results = _assertion_results(report)

    assert [(item.assertion_id, item.status) for item in results] == [
        ("heading", "passed"),
        ("message", "failed"),
    ]


def test_trial_report_extracts_structured_evidence_attachments() -> None:
    report = {
        "suites": [
            {
                "specs": [
                    {
                        "tests": [
                            {
                                "results": [
                                    {
                                        "attachments": [
                                            _attachment(
                                                "maf-step-results",
                                                [
                                                    {
                                                        "step_id": "fill-email",
                                                        "action": "fill",
                                                        "status": "passed",
                                                        "url": "https://example.com/login",
                                                        "locator": '{"strategy":"label"}',
                                                    }
                                                ],
                                            ),
                                            _attachment(
                                                "maf-assertion-results",
                                                [
                                                    {
                                                        "assertion_id": "message",
                                                        "status": "failed",
                                                        "expected": "Welcome",
                                                        "actual": "Error",
                                                        "url": "https://example.com/login",
                                                        "locator": '{"strategy":"text"}',
                                                        "error": "Text did not match",
                                                    }
                                                ],
                                            ),
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }

    steps = _step_results(report)
    assertions = _assertion_results(report)

    assert steps[0].step_id == "fill-email"
    assert steps[0].url == "https://example.com/login"
    assert assertions[0].assertion_id == "message"
    assert assertions[0].expected == "Welcome"
    assert assertions[0].actual == "Error"
    assert assertions[0].locator == '{"strategy":"text"}'


def _attachment(name: str, payload: object) -> dict[str, str]:
    body = b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return {"name": name, "body": body}


def _publish_active_asset(store: AssetStore, spec: E2ESpecification) -> None:
    source = generate_playwright_test(spec)
    asset = store.save_draft(spec, source)
    published = (
        store.repository_root
        / "e2e"
        / "generated"
        / spec.feature
        / f"{spec.scenario_id}.spec.ts"
    )
    published.parent.mkdir(parents=True)
    published.write_text(source, encoding="utf-8")
    metadata = (
        store.repository_root
        / "e2e"
        / "metadata"
        / spec.feature
        / f"{spec.scenario_id}.json"
    )
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        asset.model_copy(
            update={"status": LifecycleStatus.ACTIVE, "published_path": published}
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )


def _write_failing_playwright(root: Path, stderr: str) -> None:
    path = root / "node_modules" / ".bin" / "playwright"
    path.write_text(
        f"""#!/bin/sh
case " $* " in
  *" --list "*) exit 0 ;;
esac
printf '{{"suites":[]}}' > "$PLAYWRIGHT_JSON_OUTPUT_NAME"
printf '<testsuite tests="1" failures="1" />' > "$PLAYWRIGHT_JUNIT_OUTPUT_FILE"
mkdir -p "$PLAYWRIGHT_HTML_OUTPUT_DIR"
printf '<html></html>' > "$PLAYWRIGHT_HTML_OUTPUT_DIR/index.html"
printf '%s\\n' {json.dumps(stderr)} >&2
exit 1
""",
        encoding="utf-8",
    )
    path.chmod(0o755)

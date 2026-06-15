from __future__ import annotations

from pathlib import Path

from regression_helpers import make_fake_node_repository, sample_spec

from maf_e2e.asset_store import AssetStore
from maf_e2e.code_validation import CodeValidator
from maf_e2e.domain.assets import GeneratedTestAsset, TrialRunResult
from maf_e2e.domain.failures import FailureCategory
from maf_e2e.domain.regression import TargetEnvironment
from maf_e2e.domain.specification import TestLifecycleStatus as LifecycleStatus
from maf_e2e.failure_analysis import analyze_failure
from maf_e2e.playwright_codegen import generate_playwright_test
from maf_e2e.regression_runner import RegressionRunner, regression_exit_code
from maf_e2e.trial_runner import TrialRunner, _assertion_results


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

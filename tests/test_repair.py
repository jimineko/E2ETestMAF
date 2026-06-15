from __future__ import annotations

from pathlib import Path

import pytest
from regression_helpers import make_fake_node_repository, sample_spec

from maf_e2e.asset_store import AssetStore
from maf_e2e.domain.failures import FailureAnalysis, FailureCategory, LocatorRepair
from maf_e2e.domain.specification import LocatorSpec
from maf_e2e.playwright_codegen import generate_playwright_test
from maf_e2e.repair import (
    RepairService,
    SemanticChangeError,
    apply_locator_repairs,
    expected_results_changed,
)


async def test_repair_accepts_code_only_change_for_maintenance(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    assets = AssetStore(tmp_path)
    spec = sample_spec()
    source = generate_playwright_test(spec)
    asset = assets.save_draft(spec, source)
    analysis = FailureAnalysis(
        scenario_id=spec.scenario_id,
        category=FailureCategory.TEST_MAINTENANCE,
        confidence=0.8,
        recommended_action="Update locator",
    )

    proposal = await RepairService(assets).propose(
        spec.scenario_id,
        analysis,
        source.replace('getByLabel("Email")', 'getByTestId("email")'),
    )

    assert proposal.semantic_change_detected is False
    assert proposal.expected_result_changed is False
    assert proposal.base_code_version == asset.code_version
    assert proposal.base_code_hash == asset.code_hash
    assert proposal.proposed_code_hash != asset.code_hash
    assert proposal.diff
    assert any(path.endswith("validation-result.json") for path in proposal.artifact_paths)
    assert proposal.proposed_code_path is not None
    assert Path(proposal.proposed_code_path).exists()
    assert Path(proposal.proposed_code_path).parent.parent.name == "repairs"
    assert assets.load_asset(spec.scenario_id).code_version == asset.code_version
    assert assets.load_source(spec.scenario_id) == source


async def test_repair_rejects_expected_result_change(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    assets = AssetStore(tmp_path)
    spec = sample_spec()
    source = generate_playwright_test(spec)
    assets.save_draft(spec, source)
    changed_assertion = spec.assertions[0].model_copy(update={"expected": "Different"})
    changed_spec = spec.model_copy(update={"assertions": [changed_assertion]})
    analysis = FailureAnalysis(
        scenario_id=spec.scenario_id,
        category=FailureCategory.TEST_MAINTENANCE,
        confidence=0.8,
        recommended_action="Update locator",
    )

    assert expected_results_changed(spec, changed_spec) is True
    with pytest.raises(SemanticChangeError):
        await RepairService(assets).propose(
            spec.scenario_id,
            analysis,
            source.replace("Email", "Email address"),
            proposed_spec=changed_spec,
        )


async def test_proposed_code_rejects_assertion_semantic_change(
    tmp_path: Path,
) -> None:
    make_fake_node_repository(tmp_path)
    assets = AssetStore(tmp_path)
    spec = sample_spec()
    source = generate_playwright_test(spec)
    assets.save_draft(spec, source)
    analysis = FailureAnalysis(
        scenario_id=spec.scenario_id,
        category=FailureCategory.TEST_MAINTENANCE,
        confidence=0.8,
        recommended_action="Update locator",
    )

    with pytest.raises(SemanticChangeError, match=r"assertions|classified"):
        await RepairService(assets).propose(
            spec.scenario_id,
            analysis,
            source.replace("toBeVisible()", "toBeHidden()"),
        )


async def test_proposed_code_rejects_test_data_change(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    assets = AssetStore(tmp_path)
    spec = sample_spec()
    source = generate_playwright_test(spec)
    assets.save_draft(spec, source)
    analysis = FailureAnalysis(
        scenario_id=spec.scenario_id,
        category=FailureCategory.TEST_MAINTENANCE,
        confidence=0.8,
        recommended_action="Update locator",
    )

    with pytest.raises(SemanticChangeError, match=r"assertions|classified"):
        await RepairService(assets).propose(
            spec.scenario_id,
            analysis,
            source.replace("user@example.com", "attacker@example.com"),
        )


async def test_structured_diagnostic_repair_can_update_assertion_locator(
    tmp_path: Path,
) -> None:
    make_fake_node_repository(tmp_path)
    assets = AssetStore(tmp_path)
    spec = sample_spec()
    source = generate_playwright_test(spec)
    assets.save_draft(spec, source)
    changed = apply_locator_repairs(
        spec,
        [
            LocatorRepair(
                target_id="heading-visible",
                locator=LocatorSpec(strategy="test_id", value="login-heading"),
            )
        ],
    )
    analysis = FailureAnalysis(
        scenario_id=spec.scenario_id,
        category=FailureCategory.TEST_MAINTENANCE,
        confidence=0.8,
        recommended_action="Update locator",
    )

    proposal = await RepairService(assets).propose(
        spec.scenario_id,
        analysis,
        generate_playwright_test(changed, spec_hash_override=spec.spec_hash),
        proposed_spec=changed,
    )

    assert proposal.proposed_code is not None
    assert 'getByTestId("login-heading")' in proposal.proposed_code
    assert assets.load_source(spec.scenario_id) == source


def test_locator_only_spec_change_does_not_change_expected_result() -> None:
    spec = sample_spec()
    changed_step = spec.steps[1].model_copy(
        update={"locator": LocatorSpec(strategy="test_id", value="email")}
    )
    changed = spec.model_copy(update={"steps": [spec.steps[0], changed_step]})

    assert expected_results_changed(spec, changed) is False


def test_diagnostic_locator_repair_keeps_approved_spec_hash_in_generated_code() -> None:
    spec = sample_spec()
    candidate = apply_locator_repairs(
        spec,
        [
            LocatorRepair(
                target_id="fill-email",
                locator=LocatorSpec(strategy="test_id", value="email"),
            )
        ],
    )

    source = generate_playwright_test(candidate, spec_hash_override=spec.spec_hash)

    assert 'getByTestId("email")' in source
    assert f"// spec_hash: {spec.spec_hash}" in source

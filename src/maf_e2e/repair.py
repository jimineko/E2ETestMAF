from __future__ import annotations

import difflib
from uuid import uuid4

from maf_e2e.asset_store import AssetStore
from maf_e2e.code_validation import CodeValidator
from maf_e2e.domain.failures import FailureAnalysis, FailureCategory, LocatorRepair
from maf_e2e.domain.repair import RepairProposal
from maf_e2e.domain.specification import TestLifecycleStatus, TestSpecification


class SemanticChangeError(ValueError):
    pass


class RepairService:
    def __init__(self, assets: AssetStore) -> None:
        self.assets = assets

    async def propose(
        self,
        scenario_id: str,
        analysis: FailureAnalysis,
        proposed_code: str,
        *,
        proposed_spec: TestSpecification | None = None,
    ) -> RepairProposal:
        if analysis.category != FailureCategory.TEST_MAINTENANCE:
            raise ValueError("Repair is allowed only for TEST_MAINTENANCE")
        if analysis.scenario_id != scenario_id:
            raise ValueError("Failure analysis scenario does not match the requested repair")
        asset = self.assets.load_asset(scenario_id)
        approved_spec = self.assets.load_specification(scenario_id)
        candidate_spec = proposed_spec or approved_spec
        expected_changed = _assertion_semantics(approved_spec) != _assertion_semantics(
            candidate_spec
        )
        semantic_changed = (
            approved_spec.objective != candidate_spec.objective
            or _step_semantics(approved_spec) != _step_semantics(candidate_spec)
            or expected_changed
        )
        if expected_changed or semantic_changed:
            raise SemanticChangeError("Repair would change approved scenario semantics")
        original = self.assets.load_source(scenario_id)
        changed_files = [str(asset.draft_path / "generated.spec.ts")]
        diff = list(
            difflib.unified_diff(
                original.splitlines(),
                proposed_code.splitlines(),
                fromfile="approved",
                tofile="proposed",
                lineterm="",
            )
        )
        if not diff:
            raise ValueError("Repair proposal does not change the generated code")
        repaired = self.assets.save_draft(
            approved_spec.model_copy(update={"status": TestLifecycleStatus.REPAIR_PENDING}),
            proposed_code,
            code_version=asset.code_version + 1,
        )
        validator = CodeValidator(self.assets.repository_root)
        validation = await validator.validate(repaired.draft_path / "generated.spec.ts")
        self.assets.save_validation(scenario_id, validation)
        return RepairProposal(
            proposal_id=uuid4().hex,
            scenario_id=scenario_id,
            spec_version=asset.spec_version,
            base_code_version=asset.code_version,
            reason=analysis.recommended_action,
            changed_files=changed_files,
            semantic_change_detected=False,
            expected_result_changed=False,
            confidence=analysis.confidence,
            validation_results=[
                f"{check.name}: {'passed' if check.passed else 'failed'}"
                for check in validation.checks
            ],
            proposed_code=proposed_code,
        )


def expected_results_changed(before: TestSpecification, after: TestSpecification) -> bool:
    return _assertion_semantics(before) != _assertion_semantics(after)


def apply_locator_repairs(
    spec: TestSpecification, replacements: list[LocatorRepair]
) -> TestSpecification:
    by_id = {replacement.target_id: replacement.locator for replacement in replacements}
    steps = [
        step.model_copy(update={"locator": by_id[step.step_id]})
        if step.step_id in by_id
        else step
        for step in spec.steps
    ]
    assertions = [
        assertion.model_copy(update={"locator": by_id[assertion.assertion_id]})
        if assertion.assertion_id in by_id
        else assertion
        for assertion in spec.assertions
    ]
    return spec.model_copy(update={"steps": steps, "assertions": assertions})


def _assertion_semantics(spec: TestSpecification) -> list[tuple[str, object, str]]:
    return [
        (assertion.type, assertion.expected, assertion.source_expected_result)
        for assertion in spec.assertions
    ]


def _step_semantics(
    spec: TestSpecification,
) -> list[tuple[str, str, str | None, str | None, str | None]]:
    return [
        (step.step_id, step.action, step.target, step.value_ref, step.value)
        for step in spec.steps
    ]

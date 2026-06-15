from __future__ import annotations

import difflib
from uuid import uuid4

from maf_e2e.asset_store import AssetStore
from maf_e2e.code_validation import CodeValidator
from maf_e2e.domain.failures import FailureAnalysis, FailureCategory, LocatorRepair
from maf_e2e.domain.repair import RepairProposal
from maf_e2e.domain.specification import TestSpecification
from maf_e2e.playwright_codegen import generate_playwright_test, generated_code_hash


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
        _guard_proposed_code(
            original,
            proposed_code,
            approved_spec=approved_spec,
            proposed_spec=proposed_spec,
        )
        repair_target = asset.published_path or (asset.draft_path / "generated.spec.ts")
        changed_files = [str(repair_target)]
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
        proposal_id = uuid4().hex
        candidate_path = self.assets.save_repair_candidate(
            scenario_id, proposal_id, proposed_code
        )
        validator = CodeValidator(self.assets.repository_root)
        validation = await validator.validate(candidate_path)
        self.assets.save_repair_validation(scenario_id, proposal_id, validation)
        validated_code = candidate_path.read_text(encoding="utf-8")
        repair_dir = candidate_path.parent
        return RepairProposal(
            proposal_id=proposal_id,
            scenario_id=scenario_id,
            spec_version=asset.spec_version,
            base_code_version=asset.code_version,
            reason=analysis.recommended_action,
            changed_files=changed_files,
            diff=diff,
            base_code_hash=generated_code_hash(original),
            proposed_code_hash=generated_code_hash(validated_code),
            semantic_change_detected=False,
            expected_result_changed=False,
            confidence=analysis.confidence,
            validation_results=[
                f"{check.name}: {'passed' if check.passed else 'failed'}"
                for check in validation.checks
            ],
            artifact_paths=[str(repair_dir / "validation-result.json")],
            proposed_code=validated_code,
            proposed_code_path=str(candidate_path),
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


def _guard_proposed_code(
    approved_code: str,
    proposed_code: str,
    *,
    approved_spec: TestSpecification,
    proposed_spec: TestSpecification | None,
) -> None:
    if proposed_spec is not None:
        expected = generate_playwright_test(
            proposed_spec, spec_hash_override=approved_spec.spec_hash
        )
        if proposed_code.strip() != expected.strip():
            raise SemanticChangeError(
                "Structured repair code must match deterministic generator output"
            )
        return
    if _normalize_for_code_only_repair(approved_code) != _normalize_for_code_only_repair(
        proposed_code
    ):
        raise SemanticChangeError(
            "Proposed code changes approved assertions or cannot be classified safely"
        )


def _normalize_for_code_only_repair(source: str) -> str:
    lines = source.splitlines()
    normalized: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("// step: "):
            step_id = stripped.removeprefix("// step: ")
            if index + 1 >= len(lines):
                raise SemanticChangeError("Malformed generated step block")
            opener = lines[index + 1]
            expected_opener = f'{indent}await test.step("step:{step_id}", async () => {{'
            if opener != expected_opener:
                raise SemanticChangeError("Malformed generated step block")
            end = index + 2
            expected_closer = f"{indent}}});"
            while end < len(lines) and lines[end] != expected_closer:
                end += 1
            if end >= len(lines):
                raise SemanticChangeError("Malformed generated step block")
            normalized.extend(
                [
                    line,
                    opener,
                    f"    __MAF_E2E_REPAIRABLE_STEP_BODY__:{step_id}",
                    lines[end],
                ]
            )
            index = end + 1
            continue
        normalized.append(line)
        index += 1
    return "\n".join(normalized)

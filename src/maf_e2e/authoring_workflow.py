from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from agent_framework import Executor, Workflow, WorkflowBuilder, WorkflowContext, handler
from pydantic import BaseModel, Field

from maf_e2e.agents import run_structured
from maf_e2e.asset_store import AssetStore
from maf_e2e.code_validation import CodeValidator
from maf_e2e.domain.assets import GeneratedTestAsset, TrialRunResult, ValidationResult
from maf_e2e.domain.specification import (
    AssertionSpec,
    LocatorSpec,
    StructuredStep,
    TestSpecification,
    stable_scenario_id,
)
from maf_e2e.executors import DiscoveryExecutor, OrchestratorExecutor, SessionExecutor
from maf_e2e.models import DiscoveryReport, E2ETestRequest, FailureKind, RunContext, StageFailure
from maf_e2e.playwright_codegen import generate_playwright_test
from maf_e2e.trial_runner import TrialRunner
from maf_e2e.workflow import AgentSet


class SpecificationDraft(BaseModel):
    scenario_id: str | None = None
    feature: str = "generated"
    name: str
    objective: str
    priority: int = Field(default=1, ge=1, le=3)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[StructuredStep] = Field(min_length=1)
    assertions: list[AssertionSpec] = Field(min_length=1)
    test_data: dict[str, Any] = Field(default_factory=dict)
    cleanup: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"


class SpecificationDrafts(BaseModel):
    scenarios: list[SpecificationDraft] = Field(min_length=1)


class SpecificationBatch(BaseModel):
    discovery: DiscoveryReport
    specifications: list[TestSpecification]
    repair_attempt: int = 0


class GeneratedBatch(BaseModel):
    specifications: SpecificationBatch
    assets: list[GeneratedTestAsset]


class ValidatedBatch(BaseModel):
    generated: GeneratedBatch
    validation_results: list[ValidationResult]


class TrialBatch(BaseModel):
    validated: ValidatedBatch
    trial_results: list[TrialRunResult]


class LocatorReplacement(BaseModel):
    target_id: str
    locator: LocatorSpec


class TrialDiagnostic(BaseModel):
    scenario_id: str
    summary: str
    locator_replacements: list[LocatorReplacement] = Field(default_factory=list)
    semantic_change_detected: bool = False
    expected_result_changed: bool = False


class DiagnosticBatch(BaseModel):
    trial: TrialBatch
    diagnostics: list[TrialDiagnostic]


class AuthoringDecision(BaseModel):
    trial: TrialBatch
    action: Literal["complete", "diagnose", "blocked"]
    reason: str


class AuthoringResult(BaseModel):
    run_id: str
    status: Literal["pending_approval", "blocked"]
    scenario_ids: list[str] = Field(default_factory=list)
    draft_paths: list[Path] = Field(default_factory=list)
    trial_results: list[TrialRunResult] = Field(default_factory=list)
    reason: str = ""


class SpecificationGeneratorExecutor(SessionExecutor):
    @handler
    async def generate(
        self,
        discovery: DiscoveryReport,
        ctx: WorkflowContext[SpecificationBatch | StageFailure],
    ) -> None:
        request = discovery.run.request
        prompt = f"""Create structured Playwright test specifications from this discovery.
Target URL: {request.target_url}
Objective: {request.objective}
Expected results: {request.expected_results}
Business context: {request.business_context}
Preconditions: {request.preconditions}
Test data: {request.test_data}
Policies: {request.policies}
Prohibited actions: {request.prohibited_actions}
Maximum scenarios: {request.max_scenarios}
Maximum steps per scenario: {request.max_steps}
Discovery: {discovery.findings.model_dump_json()}
Use only the supported structured actions and assertions from the response schema. Prefer role,
label, text, and test-id locators. Keep expected-result wording in source_expected_result.
"""
        try:
            drafts = await run_structured(
                self.agent,
                prompt,
                SpecificationDrafts,
                self.session_for(discovery.run.run_id),
                retries=self.structured_retries,
                run_id=discovery.run.run_id,
                stage=self.id,
                attempt=1,
                use_native_response_format=self.use_native_response_format,
            )
            specifications = [
                _materialize_specification(draft, request)
                for draft in drafts.scenarios[: request.max_scenarios]
            ]
            await ctx.send_message(
                SpecificationBatch(discovery=discovery, specifications=specifications)
            )
        except Exception as exc:
            await self.send_failure(discovery, discovery.run, 1, exc, ctx)


class PlaywrightCodeGeneratorExecutor(Executor):
    def __init__(self, repository_root: Path) -> None:
        self.assets = AssetStore(repository_root)
        super().__init__(id="playwright_codegen")

    @handler
    async def generate(
        self,
        batch: SpecificationBatch,
        ctx: WorkflowContext[GeneratedBatch | StageFailure],
    ) -> None:
        try:
            assets = []
            for spec in batch.specifications:
                existing_version = 0
                with suppress(FileNotFoundError):
                    existing_version = self.assets.load_asset(spec.scenario_id).code_version
                assets.append(
                    self.assets.save_draft(
                        spec,
                        generate_playwright_test(spec),
                        code_version=existing_version + 1,
                    )
                )
            await ctx.send_message(GeneratedBatch(specifications=batch, assets=assets))
        except Exception as exc:
            await ctx.send_message(
                _deterministic_failure(
                    batch.discovery.run, self.id, batch, exc, FailureKind.CONFIGURATION
                )
            )


class CodeValidationExecutor(Executor):
    def __init__(self, repository_root: Path, timeout_seconds: int) -> None:
        self.assets = AssetStore(repository_root)
        self.validator = CodeValidator(repository_root, timeout_seconds=timeout_seconds)
        super().__init__(id="code_validation")

    @handler
    async def validate(
        self,
        generated: GeneratedBatch,
        ctx: WorkflowContext[ValidatedBatch | StageFailure],
    ) -> None:
        try:
            results = []
            for asset in generated.assets:
                result = await self.validator.validate(asset.draft_path / "generated.spec.ts")
                self.assets.save_validation(asset.scenario_id, result)
                results.append(result)
            await ctx.send_message(
                ValidatedBatch(generated=generated, validation_results=results)
            )
        except Exception as exc:
            await ctx.send_message(
                _deterministic_failure(
                    generated.specifications.discovery.run,
                    self.id,
                    generated,
                    exc,
                    FailureKind.CONFIGURATION,
                )
            )


class TrialRunExecutor(Executor):
    def __init__(self, repository_root: Path, timeout_seconds: int) -> None:
        self.assets = AssetStore(repository_root)
        self.runner = TrialRunner(repository_root, timeout_seconds=timeout_seconds)
        super().__init__(id="trial_run")

    @handler
    async def run_trial(
        self, validated: ValidatedBatch, ctx: WorkflowContext[TrialBatch | StageFailure]
    ) -> None:
        try:
            results = []
            for asset, validation in zip(
                validated.generated.assets, validated.validation_results, strict=True
            ):
                if not validation.passed:
                    result = TrialRunResult(
                        run_id=validated.generated.specifications.discovery.run.run_id,
                        scenario_id=asset.scenario_id,
                        code_hash=asset.code_hash,
                        status="blocked",
                        report_path=str(asset.draft_path / "validation-result.json"),
                        error="Code validation failed",
                    )
                else:
                    result = await self.runner.run(
                        asset.scenario_id,
                        asset.draft_path / "generated.spec.ts",
                        artifact_dir=asset.draft_path / "artifacts",
                    )
                self.assets.save_trial(asset.scenario_id, result)
                results.append(result)
            await ctx.send_message(TrialBatch(validated=validated, trial_results=results))
        except Exception as exc:
            await ctx.send_message(
                _deterministic_failure(
                    validated.generated.specifications.discovery.run,
                    self.id,
                    validated,
                    exc,
                    FailureKind.PLAYWRIGHT,
                )
            )


class TrialJudgeExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="trial_judge")

    @handler
    async def judge(self, trial: TrialBatch, ctx: WorkflowContext[AuthoringDecision]) -> None:
        request = trial.validated.generated.specifications.discovery.run.request
        if all(result.status == "passed" for result in trial.trial_results):
            action: Literal["complete", "diagnose", "blocked"] = "complete"
            reason = "All generated code passed validation and the standard Playwright runner."
        elif any(result.status == "blocked" for result in trial.trial_results):
            action = "blocked"
            reason = "Code validation or trial execution was blocked."
        elif trial.validated.generated.specifications.repair_attempt < request.max_trial_repairs:
            action = "diagnose"
            reason = "Trial failed and is eligible for bounded locator repair."
        else:
            action = "blocked"
            reason = "Maximum trial repair attempts reached."
        await ctx.send_message(AuthoringDecision(trial=trial, action=action, reason=reason))


class TrialFailureDiagnosticExecutor(SessionExecutor):
    @handler
    async def diagnose(
        self,
        decision: AuthoringDecision,
        ctx: WorkflowContext[DiagnosticBatch | StageFailure],
    ) -> None:
        run = decision.trial.validated.generated.specifications.discovery.run
        diagnostics = []
        try:
            for result in decision.trial.trial_results:
                if result.status == "passed":
                    continue
                prompt = f"""Investigate this failed generated Playwright trial against
{run.request.target_url}.
Scenario: {result.scenario_id}
Failure: {result.error}
Use browser tools only to inspect the current UI and propose locator replacements. Do not change
the objective, expected results, assertion meaning, business rules, or test data. Return an empty
replacement list when the failure is not a locator issue.
"""
                diagnostic = await run_structured(
                    self.agent,
                    prompt,
                    TrialDiagnostic,
                    self.session_for(run.run_id),
                    retries=self.structured_retries,
                    tools=self.tools,
                    run_id=run.run_id,
                    stage=self.id,
                    attempt=decision.trial.validated.generated.specifications.repair_attempt + 1,
                    use_native_response_format=self.use_native_response_format,
                )
                diagnostics.append(diagnostic)
            await ctx.send_message(DiagnosticBatch(trial=decision.trial, diagnostics=diagnostics))
        except Exception as exc:
            await self.send_failure(decision, run, 1, exc, ctx)


class DraftCodeRepairExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="draft_code_repair")

    @handler
    async def repair(
        self, batch: DiagnosticBatch, ctx: WorkflowContext[SpecificationBatch]
    ) -> None:
        current = batch.trial.validated.generated.specifications
        by_id = {diagnostic.scenario_id: diagnostic for diagnostic in batch.diagnostics}
        repaired = []
        for spec in current.specifications:
            diagnostic = by_id.get(spec.scenario_id)
            if diagnostic is None:
                repaired.append(spec)
                continue
            if diagnostic.semantic_change_detected or diagnostic.expected_result_changed:
                repaired.append(spec)
                continue
            replacements = {
                item.target_id: item.locator for item in diagnostic.locator_replacements
            }
            repaired.append(_replace_locators(spec, replacements))
        await ctx.send_message(
            SpecificationBatch(
                discovery=current.discovery,
                specifications=repaired,
                repair_attempt=current.repair_attempt + 1,
            )
        )


class AuthoringFinalizerExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="authoring_finalizer")

    @handler
    async def finalize_decision(
        self, decision: AuthoringDecision, ctx: WorkflowContext[Any, AuthoringResult]
    ) -> None:
        generated = decision.trial.validated.generated
        await ctx.yield_output(
            AuthoringResult(
                run_id=generated.specifications.discovery.run.run_id,
                status="pending_approval" if decision.action == "complete" else "blocked",
                scenario_ids=[asset.scenario_id for asset in generated.assets],
                draft_paths=[asset.draft_path for asset in generated.assets],
                trial_results=decision.trial.trial_results,
                reason=decision.reason,
            )
        )

    @handler
    async def finalize_failure(
        self, failure: StageFailure, ctx: WorkflowContext[Any, AuthoringResult]
    ) -> None:
        await ctx.yield_output(
            AuthoringResult(
                run_id=failure.run.run_id,
                status="blocked",
                reason=f"{failure.stage}: {failure.message}",
            )
        )


def build_authoring_workflow(
    agents: AgentSet,
    repository_root: Path,
    *,
    discovery_tools: list[Any] | None = None,
    diagnostic_tools: list[Any] | None = None,
    structured_retries: int = 1,
    use_native_response_format: bool = True,
    validation_timeout_seconds: int = 120,
    trial_timeout_seconds: int = 300,
) -> Workflow:
    orchestrator = OrchestratorExecutor()
    discovery = DiscoveryExecutor(
        "authoring_discovery",
        agents.discovery,
        structured_retries=structured_retries,
        tools=discovery_tools,
        use_native_response_format=use_native_response_format,
    )
    specification = SpecificationGeneratorExecutor(
        "specification_generator",
        agents.generator,
        structured_retries=structured_retries,
        use_native_response_format=use_native_response_format,
    )
    codegen = PlaywrightCodeGeneratorExecutor(repository_root)
    validation = CodeValidationExecutor(repository_root, validation_timeout_seconds)
    trial = TrialRunExecutor(repository_root, trial_timeout_seconds)
    judge = TrialJudgeExecutor()
    diagnostic = TrialFailureDiagnosticExecutor(
        "trial_failure_diagnostic",
        agents.browser,
        structured_retries=structured_retries,
        tools=diagnostic_tools,
        use_native_response_format=use_native_response_format,
    )
    repair = DraftCodeRepairExecutor()
    finalizer = AuthoringFinalizerExecutor()
    return (
        WorkflowBuilder(
            start_executor=orchestrator,
            name="regression-asset-authoring-v1",
            description="Discover, generate, validate, trial, diagnose, and package E2E assets.",
            output_from=[finalizer],
            max_iterations=80,
        )
        .add_edge(orchestrator, discovery)
        .add_edge(
            discovery,
            specification,
            condition=lambda value: isinstance(value, DiscoveryReport),
        )
        .add_edge(discovery, finalizer, condition=lambda value: isinstance(value, StageFailure))
        .add_edge(
            specification,
            codegen,
            condition=lambda value: isinstance(value, SpecificationBatch),
        )
        .add_edge(specification, finalizer, condition=lambda value: isinstance(value, StageFailure))
        .add_edge(codegen, validation, condition=lambda value: isinstance(value, GeneratedBatch))
        .add_edge(codegen, finalizer, condition=lambda value: isinstance(value, StageFailure))
        .add_edge(
            validation, trial, condition=lambda value: isinstance(value, ValidatedBatch)
        )
        .add_edge(validation, finalizer, condition=lambda value: isinstance(value, StageFailure))
        .add_edge(trial, judge, condition=lambda value: isinstance(value, TrialBatch))
        .add_edge(trial, finalizer, condition=lambda value: isinstance(value, StageFailure))
        .add_edge(judge, finalizer, condition=lambda value: value.action in {"complete", "blocked"})
        .add_edge(judge, diagnostic, condition=lambda value: value.action == "diagnose")
        .add_edge(diagnostic, repair, condition=lambda value: isinstance(value, DiagnosticBatch))
        .add_edge(diagnostic, finalizer, condition=lambda value: isinstance(value, StageFailure))
        .add_edge(repair, codegen)
        .build()
    )


def _materialize_specification(
    draft: SpecificationDraft, request: E2ETestRequest
) -> TestSpecification:
    scenario_id = draft.scenario_id or stable_scenario_id(draft.name, draft.objective)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", scenario_id):
        scenario_id = stable_scenario_id(draft.name, draft.objective)
    feature = re.sub(r"[^a-z0-9]+", "-", draft.feature.lower()).strip("-") or "generated"
    return TestSpecification(
        scenario_id=scenario_id,
        feature=feature,
        name=draft.name,
        objective=draft.objective,
        target_url=request.target_url,
        priority=draft.priority,
        preconditions=draft.preconditions or request.preconditions,
        steps=draft.steps[: request.max_steps],
        assertions=draft.assertions,
        test_data={**request.test_data, **draft.test_data},
        cleanup=draft.cleanup,
        prohibited_actions=draft.prohibited_actions or request.prohibited_actions,
        risk_level=draft.risk_level,
    ).with_hash()


def _replace_locators(
    spec: TestSpecification, replacements: dict[str, LocatorSpec]
) -> TestSpecification:
    steps = [
        step.model_copy(update={"locator": replacements[step.step_id]})
        if step.step_id in replacements
        else step
        for step in spec.steps
    ]
    assertions = [
        assertion.model_copy(update={"locator": replacements[assertion.assertion_id]})
        if assertion.assertion_id in replacements
        else assertion
        for assertion in spec.assertions
    ]
    return spec.model_copy(
        update={"steps": steps, "assertions": assertions, "spec_hash": ""}
    ).with_hash()


def _deterministic_failure(
    run: RunContext,
    stage: str,
    stage_input: BaseModel,
    exc: Exception,
    kind: FailureKind,
) -> StageFailure:
    return StageFailure(
        run=run,
        stage=stage,
        attempt=1,
        kind=kind,
        exception_type=type(exc).__name__,
        message=str(exc),
        retryable=False,
        input_type=type(stage_input).__name__,
        stage_input=stage_input.model_dump(mode="json"),
    )

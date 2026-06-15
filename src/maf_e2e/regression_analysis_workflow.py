from __future__ import annotations

from typing import Any

from agent_framework import Executor, Workflow, WorkflowBuilder, WorkflowContext, handler
from pydantic import BaseModel

from maf_e2e.agents import AgentRunner, run_structured
from maf_e2e.domain.assets import TrialRunResult
from maf_e2e.domain.failures import (
    FailureCategory,
    RegressionFailureDiagnostic,
)
from maf_e2e.executors import SessionExecutor
from maf_e2e.models import E2ETestRequest, RunContext, StageFailure


class RegressionDiagnosticRequest(BaseModel):
    target_url: str
    trial: TrialRunResult


class RegressionFailureDiagnosticExecutor(SessionExecutor):
    @handler
    async def diagnose(
        self,
        request: RegressionDiagnosticRequest,
        ctx: WorkflowContext[RegressionFailureDiagnostic | StageFailure],
    ) -> None:
        run = RunContext(
            request=E2ETestRequest(
                run_id=request.trial.run_id,
                target_url=request.target_url,
                objective=f"Diagnose regression failure {request.trial.scenario_id}",
            ),
            run_id=request.trial.run_id,
        )
        prompt = f"""Investigate this failed approved Playwright regression test.
Target: {request.target_url}
Scenario: {request.trial.scenario_id}
Failure: {request.trial.error}
Console errors: {request.trial.console_errors}
Network errors: {request.trial.network_errors}
Inspect the current UI conservatively and classify the failure as application_defect,
test_maintenance, environment_failure, authentication_failure, test_data_failure,
flaky_failure, or unknown. For test maintenance, return locator replacements keyed by the
structured step or assertion id. Do not modify expected results or perform destructive actions.
"""
        try:
            diagnostic = await run_structured(
                self.agent,
                prompt,
                RegressionFailureDiagnostic,
                self.session_for(request.trial.run_id),
                retries=self.structured_retries,
                tools=self.tools,
                run_id=request.trial.run_id,
                stage=self.id,
                attempt=1,
                use_native_response_format=self.use_native_response_format,
            )
            await ctx.send_message(diagnostic)
        except Exception as exc:
            await self.send_failure(request, run, 1, exc, ctx)


class RegressionAnalysisFinalizer(Executor):
    def __init__(self) -> None:
        super().__init__(id="regression_analysis_finalizer")

    @handler
    async def finalize_diagnostic(
        self,
        diagnostic: RegressionFailureDiagnostic,
        ctx: WorkflowContext[Any, RegressionFailureDiagnostic],
    ) -> None:
        await ctx.yield_output(diagnostic)

    @handler
    async def finalize_failure(
        self,
        failure: StageFailure,
        ctx: WorkflowContext[Any, RegressionFailureDiagnostic],
    ) -> None:
        await ctx.yield_output(
            RegressionFailureDiagnostic(
                scenario_id=str(failure.stage_input.get("trial", {}).get("scenario_id", "unknown")),
                category=FailureCategory.UNKNOWN,
                confidence=0,
                current_ui_summary="Regression failure investigation was blocked.",
                evidence=[failure.message],
                recommended_action="Resolve the diagnostic runtime failure and retry analysis.",
            )
        )


def build_regression_analysis_workflow(
    agent: AgentRunner,
    *,
    tools: list[Any] | None = None,
    structured_retries: int = 1,
    use_native_response_format: bool = True,
) -> Workflow:
    diagnostic = RegressionFailureDiagnosticExecutor(
        "regression_failure_diagnostic",
        agent,
        structured_retries=structured_retries,
        tools=tools,
        use_native_response_format=use_native_response_format,
    )
    finalizer = RegressionAnalysisFinalizer()
    return (
        WorkflowBuilder(
            start_executor=diagnostic,
            name="regression-failure-analysis-v1",
            description="Investigate and classify a failed approved Playwright regression.",
            output_from=[finalizer],
            max_iterations=10,
        )
        .add_edge(diagnostic, finalizer)
        .build()
    )

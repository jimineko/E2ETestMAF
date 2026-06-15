from __future__ import annotations

from typing import Any

from agent_framework import AgentResponse, AgentSession

from maf_e2e.domain.assets import TrialRunResult
from maf_e2e.domain.failures import FailureCategory, RegressionFailureDiagnostic
from maf_e2e.regression_analysis_workflow import (
    RegressionDiagnosticRequest,
    build_regression_analysis_workflow,
)


class FakeAgent:
    def create_session(self, *, session_id: str | None = None) -> AgentSession:
        return AgentSession(session_id=session_id)

    async def run(
        self,
        messages: str,
        *,
        session: AgentSession | None = None,
        options: dict[str, Any] | None = None,
        tools: list[Any] | None = None,
    ) -> AgentResponse[Any]:
        del messages, session, options, tools
        return AgentResponse(
            value=RegressionFailureDiagnostic(
                scenario_id="login",
                category=FailureCategory.TEST_MAINTENANCE,
                confidence=0.9,
                current_ui_summary="The email field now uses a test id.",
                evidence=["Approved locator no longer matches"],
                recommended_action="Update only the locator.",
            )
        )


async def test_regression_analysis_runs_as_maf_workflow() -> None:
    workflow = build_regression_analysis_workflow(FakeAgent())
    request = RegressionDiagnosticRequest(
        target_url="https://example.com",
        trial=TrialRunResult(
            run_id="run",
            scenario_id="login",
            code_hash="hash",
            status="failed",
            report_path="report.json",
            error="Locator did not match",
        ),
    )

    result = await workflow.run(request)

    diagnostic = result.get_outputs()[0]
    assert isinstance(diagnostic, RegressionFailureDiagnostic)
    assert diagnostic.category == FailureCategory.TEST_MAINTENANCE

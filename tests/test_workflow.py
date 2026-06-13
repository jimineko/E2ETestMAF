from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework import AgentResponse, AgentSession, FileCheckpointStorage

from maf_qa.models import (
    BrowserRunOutput,
    DiscoveryFindings,
    GeneratedPlan,
    HumanReviewResponse,
    JudgeOutput,
    LiteralStatus,
    PageObservation,
    PolicyResult,
    QAReport,
    QARequest,
    SafetyOutput,
    SecurityFinding,
    Severity,
    StepResult,
)
from maf_qa.models import (
    TestScenario as ScenarioModel,
)
from maf_qa.workflow import CHECKPOINT_TYPES, AgentSet, build_qa_workflow


class FakeAgent:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.session_ids: list[str | None] = []

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
        del messages, options, tools
        self.session_ids.append(session.session_id if session is not None else None)
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if isinstance(output, Exception):
            raise output
        return AgentResponse(value=output)


async def test_workflow_refines_once_then_completes(tmp_path: Path) -> None:
    discovery = FakeAgent(
        [
            DiscoveryFindings(
                pages=[PageObservation(url="https://example.com", title="Home")],
                user_flows=["Open home"],
            )
        ]
    )
    generator = FakeAgent(
        [
            GeneratedPlan(
                scenarios=[
                    ScenarioModel(
                        name="home",
                        goal="Home page is usable",
                        steps=["Navigate home"],
                        expected_results=["Page loads"],
                    )
                ]
            )
        ]
    )
    browser = FakeAgent(
        [
            BrowserRunOutput(
                steps=[
                    StepResult(
                        scenario="home",
                        step="Navigate home",
                        status=LiteralStatus.PASSED,
                        evidence="Visible heading",
                    )
                ],
                summary="Executed",
            )
        ]
    )
    judge = FakeAgent(
        [
            JudgeOutput(
                passed=False,
                score=60,
                defects=["Evidence was incomplete"],
                rationale="The visible heading was not captured.",
                retry_advice="Capture the visible heading text.",
            ),
            JudgeOutput(
                passed=True,
                score=95,
                policy_results=[PolicyResult(policy="Page loads", passed=True, evidence="Heading")],
                rationale="The heading provides sufficient evidence.",
            ),
        ]
    )
    safety = FakeAgent([SafetyOutput(passed=True)])
    workflow = build_qa_workflow(
        AgentSet(discovery, generator, browser, judge, safety),
        tmp_path / "checkpoints",
    )

    result = await workflow.run(
        QARequest(
            target_url="https://example.com",
            objective="Validate home",
            max_refinements=1,
        )
    )

    outputs = result.get_outputs()
    assert len(outputs) == 1
    assert isinstance(outputs[0], QAReport)
    assert outputs[0].passed is True
    assert outputs[0].attempts == 2
    assert judge.calls == 2
    assert generator.calls == 2

    storage = FileCheckpointStorage(
        tmp_path / "checkpoints",
        allowed_checkpoint_types=CHECKPOINT_TYPES,
    )
    checkpoints = await storage.list_checkpoints(workflow_name="autonomous-web-qa-v2")
    assert checkpoints


async def test_retry_limit_produces_failed_report(tmp_path: Path) -> None:
    agents = _basic_agents(
        judge_outputs=[
            JudgeOutput(
                passed=False,
                score=40,
                defects=["Still broken"],
                rationale="Required evidence is missing.",
                retry_advice="Try another locator.",
            )
        ]
    )
    workflow = build_qa_workflow(agents, tmp_path / "checkpoints")

    result = await workflow.run(
        QARequest(target_url="https://example.com", objective="Validate", max_refinements=0)
    )

    report = result.get_outputs()[0]
    assert isinstance(report, QAReport)
    assert report.status == LiteralStatus.FAILED
    assert report.passed is False


async def test_high_safety_finding_produces_blocked_report(tmp_path: Path) -> None:
    agents = _basic_agents(
        safety_outputs=[
            SafetyOutput(
                passed=False,
                findings=[
                    SecurityFinding(
                        severity=Severity.HIGH,
                        title="Secret exposure",
                        evidence="A secret-like value was visible.",
                        recommendation="Remove it from the page.",
                    )
                ],
            )
        ]
    )
    workflow = build_qa_workflow(agents, tmp_path / "checkpoints")

    result = await workflow.run(QARequest(target_url="https://example.com", objective="Validate"))

    report = result.get_outputs()[0]
    assert isinstance(report, QAReport)
    assert report.status == LiteralStatus.BLOCKED


async def test_stage_failure_produces_blocked_report(tmp_path: Path) -> None:
    agents = _basic_agents(discovery_outputs=[TimeoutError("provider timed out")])
    workflow = build_qa_workflow(agents, tmp_path / "checkpoints")

    result = await workflow.run(QARequest(target_url="https://example.com", objective="Validate"))

    report = result.get_outputs()[0]
    assert isinstance(report, QAReport)
    assert report.status == LiteralStatus.BLOCKED
    assert report.failures[0].exception_type == "TimeoutError"
    assert "provider timed out" not in report.failures[0].message


async def test_devui_human_abort_resumes_to_blocked_report(tmp_path: Path) -> None:
    agents = _basic_agents(
        safety_outputs=[
            SafetyOutput(
                passed=False,
                findings=[
                    SecurityFinding(
                        severity=Severity.CRITICAL,
                        title="Critical signal",
                        evidence="Observed evidence",
                        recommendation="Stop the run",
                    )
                ],
            )
        ]
    )
    workflow = build_qa_workflow(agents, tmp_path / "checkpoints", interactive=True)
    initial = await workflow.run(QARequest(target_url="https://example.com", objective="Validate"))
    requests = initial.get_request_info_events()

    assert len(requests) == 1
    resumed = await workflow.run(
        responses={requests[0].request_id: HumanReviewResponse(action="abort", note="Reviewed")}
    )
    report = resumed.get_outputs()[0]
    assert isinstance(report, QAReport)
    assert report.status == LiteralStatus.BLOCKED
    assert report.review_history[0].note == "Reviewed"


async def test_devui_human_retry_resumes_saved_stage_input(tmp_path: Path) -> None:
    agents = _basic_agents(
        safety_outputs=[
            SafetyOutput(
                passed=False,
                findings=[
                    SecurityFinding(
                        severity=Severity.HIGH,
                        title="Needs review",
                        evidence="Initial evidence",
                        recommendation="Retry once",
                    )
                ],
            ),
            SafetyOutput(passed=True),
        ]
    )
    workflow = build_qa_workflow(agents, tmp_path / "checkpoints", interactive=True)
    initial = await workflow.run(QARequest(target_url="https://example.com", objective="Validate"))
    request = initial.get_request_info_events()[0]

    resumed = await workflow.run(
        responses={request.request_id: HumanReviewResponse(action="retry", note="Retry approved")}
    )

    report = resumed.get_outputs()[0]
    assert isinstance(report, QAReport)
    assert report.status == LiteralStatus.PASSED
    assert report.review_history[0].note == "Retry approved"


async def test_sessions_are_isolated_between_runs(tmp_path: Path) -> None:
    agents = _basic_agents()
    workflow = build_qa_workflow(agents, tmp_path / "checkpoints")
    await workflow.run(QARequest(target_url="https://example.com", objective="First"))
    await workflow.run(QARequest(target_url="https://example.com", objective="Second"))

    discovery = agents.discovery
    assert isinstance(discovery, FakeAgent)
    assert len(set(discovery.session_ids)) == 2


def _basic_agents(
    *,
    discovery_outputs: list[Any] | None = None,
    judge_outputs: list[Any] | None = None,
    safety_outputs: list[Any] | None = None,
) -> AgentSet:
    discovery = FakeAgent(
        discovery_outputs
        or [
            DiscoveryFindings(
                pages=[PageObservation(url="https://example.com", title="Home")],
                user_flows=["Open home"],
            )
        ]
    )
    generator = FakeAgent(
        [
            GeneratedPlan(
                scenarios=[
                    ScenarioModel(
                        name="home",
                        goal="Home works",
                        steps=["Navigate"],
                        expected_results=["Loaded"],
                    )
                ]
            )
        ]
    )
    browser = FakeAgent([BrowserRunOutput(summary="Executed")])
    judge = FakeAgent(
        judge_outputs or [JudgeOutput(passed=True, score=100, rationale="All evidence passed.")]
    )
    safety = FakeAgent(safety_outputs or [SafetyOutput(passed=True)])
    return AgentSet(discovery, generator, browser, judge, safety)

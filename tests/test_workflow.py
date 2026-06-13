from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework import AgentResponse, AgentSession, FileCheckpointStorage

from maf_qa.models import (
    BrowserRunOutput,
    DiscoveryFindings,
    GeneratedPlan,
    JudgeOutput,
    LiteralStatus,
    PageObservation,
    PolicyResult,
    QAReport,
    QARequest,
    SafetyOutput,
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

    def create_session(self, *, session_id: str | None = None) -> AgentSession:
        return AgentSession(session_id=session_id)

    async def run(
        self,
        messages: str,
        *,
        session: AgentSession | None = None,
        options: dict[str, Any] | None = None,
    ) -> AgentResponse[Any]:
        del messages, session, options
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
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
                retry_advice="Capture the visible heading text.",
            ),
            JudgeOutput(
                passed=True,
                score=95,
                policy_results=[PolicyResult(policy="Page loads", passed=True, evidence="Heading")],
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
    checkpoints = await storage.list_checkpoints(workflow_name="autonomous-web-qa-v1")
    assert checkpoints

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_framework import FileCheckpointStorage, Workflow, WorkflowBuilder

from maf_qa.agents import AgentRunner
from maf_qa.executors import (
    BrowserExecutor,
    DecisionExecutor,
    DiscoveryExecutor,
    FinalizerExecutor,
    GeneratorExecutor,
    HumanReviewExecutor,
    JudgeExecutor,
    OrchestratorExecutor,
    SafetyExecutor,
)
from maf_qa.models import Decision, ExecutionResult, NextAction, StageFailure, StageRetry

CHECKPOINT_TYPES = [
    f"maf_qa.models:{name}"
    for name in (
        "Severity",
        "Decision",
        "FailureKind",
        "QARequest",
        "RunContext",
        "StageFailure",
        "PageObservation",
        "DiscoveryFindings",
        "DiscoveryReport",
        "TestScenario",
        "GeneratedPlan",
        "TestPlan",
        "LiteralStatus",
        "StepResult",
        "BrowserRunOutput",
        "ExecutionResult",
        "PolicyResult",
        "JudgeOutput",
        "QualityAssessment",
        "SecurityFinding",
        "SafetyOutput",
        "SafetyAssessment",
        "NextAction",
        "HumanReviewRequest",
        "HumanReviewResponse",
        "StageRetry",
        "QAReport",
    )
]


@dataclass(frozen=True)
class AgentSet:
    discovery: AgentRunner
    generator: AgentRunner
    browser: AgentRunner
    judge: AgentRunner
    safety: AgentRunner


def build_qa_workflow(
    agents: AgentSet,
    checkpoint_root: Path,
    *,
    tools: list[Any] | None = None,
    structured_retries: int = 1,
    use_native_response_format: bool = True,
    interactive: bool = False,
) -> Workflow:
    orchestrator = OrchestratorExecutor()
    discovery = DiscoveryExecutor(
        "discovery",
        agents.discovery,
        structured_retries=structured_retries,
        tools=tools,
        use_native_response_format=use_native_response_format,
    )
    generator = GeneratorExecutor(
        "generator",
        agents.generator,
        structured_retries=structured_retries,
        use_native_response_format=use_native_response_format,
    )
    browser = BrowserExecutor(
        "browser",
        agents.browser,
        structured_retries=structured_retries,
        tools=tools,
        use_native_response_format=use_native_response_format,
    )
    judge = JudgeExecutor(
        "judge",
        agents.judge,
        structured_retries=structured_retries,
        use_native_response_format=use_native_response_format,
    )
    safety = SafetyExecutor(
        "safety",
        agents.safety,
        structured_retries=structured_retries,
        use_native_response_format=use_native_response_format,
    )
    decision = DecisionExecutor()
    finalizer = FinalizerExecutor()

    checkpoint_root.mkdir(parents=True, exist_ok=True)
    storage = FileCheckpointStorage(
        storage_path=checkpoint_root,
        allowed_checkpoint_types=CHECKPOINT_TYPES,
    )
    builder = (
        WorkflowBuilder(
            start_executor=orchestrator,
            name="autonomous-web-qa-v2",
            description="Discover, plan, execute, validate, refine, escalate, and report web QA.",
            checkpoint_storage=storage,
            output_from=[finalizer],
            max_iterations=80,
        )
        .add_edge(orchestrator, discovery)
        .add_edge(discovery, generator, condition=_not_failure)
        .add_edge(discovery, decision, condition=_is_failure)
        .add_edge(generator, browser, condition=_not_failure)
        .add_edge(generator, decision, condition=_is_failure)
        .add_edge(browser, judge, condition=_is_execution)
        .add_edge(browser, safety, condition=_is_execution)
        .add_edge(browser, decision, condition=_is_failure)
        .add_fan_in_edges([judge, safety], decision)
        .add_edge(decision, generator, condition=_should_retry)
    )
    if interactive:
        human_review = HumanReviewExecutor()
        builder = (
            builder.add_edge(decision, finalizer, condition=_should_complete)
            .add_edge(decision, human_review, condition=_should_escalate)
            .add_edge(human_review, finalizer, condition=_is_action)
            .add_edge(human_review, discovery, condition=_retry_discovery)
            .add_edge(human_review, generator, condition=_retry_generator)
            .add_edge(human_review, browser, condition=_retry_browser)
            .add_edge(human_review, judge, condition=_retry_judge)
            .add_edge(human_review, safety, condition=_retry_safety)
        )
    else:
        builder = builder.add_edge(decision, finalizer, condition=_should_finalize)
    return builder.build()


def _is_failure(message: object) -> bool:
    return isinstance(message, StageFailure)


def _not_failure(message: object) -> bool:
    return not isinstance(message, StageFailure)


def _is_execution(message: object) -> bool:
    return isinstance(message, ExecutionResult)


def _should_retry(action: NextAction) -> bool:
    return action.decision == Decision.RETRY


def _should_complete(action: NextAction) -> bool:
    return action.decision == Decision.COMPLETE


def _should_escalate(action: NextAction) -> bool:
    return action.decision == Decision.ESCALATE


def _should_finalize(action: NextAction) -> bool:
    return action.decision in {Decision.COMPLETE, Decision.ESCALATE}


def _is_action(message: object) -> bool:
    return isinstance(message, NextAction)


def _retry_discovery(message: object) -> bool:
    return isinstance(message, StageRetry) and message.stage == "discovery"


def _retry_generator(message: object) -> bool:
    return isinstance(message, StageRetry) and message.stage == "generator"


def _retry_browser(message: object) -> bool:
    return isinstance(message, StageRetry) and message.stage == "browser"


def _retry_judge(message: object) -> bool:
    return isinstance(message, StageRetry) and message.stage == "judge"


def _retry_safety(message: object) -> bool:
    return isinstance(message, StageRetry) and message.stage == "safety"

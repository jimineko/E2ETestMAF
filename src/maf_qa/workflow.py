from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_framework import FileCheckpointStorage, Workflow, WorkflowBuilder

from maf_qa.agents import AgentRunner
from maf_qa.executors import (
    BrowserExecutor,
    DiscoveryExecutor,
    FinalizerExecutor,
    GeneratorExecutor,
    JudgeExecutor,
    OrchestratorExecutor,
    RefinerExecutor,
    SafetyExecutor,
)
from maf_qa.models import Decision, NextAction

CHECKPOINT_TYPES = [
    "maf_qa.models:Severity",
    "maf_qa.models:Decision",
    "maf_qa.models:QARequest",
    "maf_qa.models:RunContext",
    "maf_qa.models:PageObservation",
    "maf_qa.models:DiscoveryFindings",
    "maf_qa.models:DiscoveryReport",
    "maf_qa.models:TestScenario",
    "maf_qa.models:GeneratedPlan",
    "maf_qa.models:TestPlan",
    "maf_qa.models:LiteralStatus",
    "maf_qa.models:StepResult",
    "maf_qa.models:BrowserRunOutput",
    "maf_qa.models:ExecutionResult",
    "maf_qa.models:PolicyResult",
    "maf_qa.models:JudgeOutput",
    "maf_qa.models:QualityAssessment",
    "maf_qa.models:SecurityFinding",
    "maf_qa.models:SafetyOutput",
    "maf_qa.models:SafetyAssessment",
    "maf_qa.models:NextAction",
    "maf_qa.models:QAReport",
]


@dataclass(frozen=True)
class AgentSet:
    discovery: AgentRunner
    generator: AgentRunner
    browser: AgentRunner
    judge: AgentRunner
    safety: AgentRunner


def build_qa_workflow(agents: AgentSet, checkpoint_root: Path) -> Workflow:
    orchestrator = OrchestratorExecutor()
    discovery = DiscoveryExecutor("discovery", agents.discovery)
    generator = GeneratorExecutor("generator", agents.generator)
    browser = BrowserExecutor("browser", agents.browser)
    judge = JudgeExecutor("judge", agents.judge)
    safety = SafetyExecutor("safety", agents.safety)
    refiner = RefinerExecutor()
    finalizer = FinalizerExecutor()

    checkpoint_root.mkdir(parents=True, exist_ok=True)
    storage = FileCheckpointStorage(
        storage_path=checkpoint_root,
        allowed_checkpoint_types=CHECKPOINT_TYPES,
    )

    return (
        WorkflowBuilder(
            start_executor=orchestrator,
            name="autonomous-web-qa-v1",
            description="Discover, plan, execute, validate, refine, and report web QA.",
            checkpoint_storage=storage,
            output_from=[finalizer],
            max_iterations=50,
        )
        .add_edge(orchestrator, discovery)
        .add_edge(discovery, generator)
        .add_edge(generator, browser)
        .add_fan_out_edges(browser, [judge, safety])
        .add_fan_in_edges([judge, safety], refiner)
        .add_edge(refiner, generator, condition=_should_retry)
        .add_edge(refiner, finalizer, condition=_should_complete)
        .build()
    )


def _should_retry(action: NextAction) -> bool:
    return action.decision == Decision.RETRY


def _should_complete(action: NextAction) -> bool:
    return action.decision == Decision.COMPLETE

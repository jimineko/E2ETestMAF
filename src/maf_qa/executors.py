from __future__ import annotations

from typing import Any

from agent_framework import AgentSession, Executor, WorkflowContext, handler

from maf_qa.agents import AgentRunner, run_structured
from maf_qa.models import (
    BrowserRunOutput,
    Decision,
    DiscoveryFindings,
    DiscoveryReport,
    ExecutionResult,
    GeneratedPlan,
    JudgeOutput,
    NextAction,
    QAReport,
    QARequest,
    QualityAssessment,
    RunContext,
    SafetyAssessment,
    SafetyOutput,
    TestPlan,
)


class SessionExecutor(Executor):
    def __init__(self, executor_id: str, agent: AgentRunner) -> None:
        self.agent = agent
        self.session = agent.create_session(session_id=f"{executor_id}-session")
        super().__init__(id=executor_id)

    async def on_checkpoint_save(self) -> dict[str, Any]:
        return {"agent_session": self.session.to_dict()}

    async def on_checkpoint_restore(self, state: dict[str, Any]) -> None:
        session = state.get("agent_session")
        if isinstance(session, dict):
            self.session = AgentSession.from_dict(session)


class OrchestratorExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="orchestrator")

    @handler
    async def start(self, request: QARequest, ctx: WorkflowContext[RunContext]) -> None:
        await ctx.send_message(RunContext(request=request, run_id=request.run_id))


class DiscoveryExecutor(SessionExecutor):
    @handler
    async def discover(self, run: RunContext, ctx: WorkflowContext[DiscoveryReport]) -> None:
        prompt = f"""Explore this web application for QA planning.
Target: {run.request.target_url}
Objective: {run.request.objective}
Policies: {run.request.policies or ['No additional policies']}
Return the discovered pages, user flows, authentication requirement, and risks.
"""
        findings = await run_structured(self.agent, prompt, DiscoveryFindings, self.session)
        await ctx.send_message(DiscoveryReport(run=run, findings=findings))


class GeneratorExecutor(SessionExecutor):
    @handler
    async def generate(
        self,
        message: DiscoveryReport | NextAction,
        ctx: WorkflowContext[TestPlan],
    ) -> None:
        if isinstance(message, DiscoveryReport):
            discovery = message
            attempt = 1
            refinement = None
        else:
            discovery = message.plan.discovery
            attempt = message.plan.attempt + 1
            refinement = message.retry_instruction

        prompt = f"""Create the next end-to-end test plan.
Target: {discovery.run.request.target_url}
Objective: {discovery.run.request.objective}
Policies: {discovery.run.request.policies}
Discovered topology: {discovery.findings.model_dump_json()}
Attempt: {attempt}
Refinement instruction: {refinement or 'None; create the initial plan.'}
"""
        generated = await run_structured(self.agent, prompt, GeneratedPlan, self.session)
        await ctx.send_message(
            TestPlan(
                discovery=discovery,
                generated=generated,
                attempt=attempt,
                refinement_instruction=refinement,
            )
        )


class BrowserExecutor(SessionExecutor):
    @handler
    async def execute_plan(self, plan: TestPlan, ctx: WorkflowContext[ExecutionResult]) -> None:
        prompt = f"""Execute this plan against {plan.discovery.run.request.target_url}.
Plan: {plan.generated.model_dump_json()}
Attempt: {plan.attempt}
Return step-level evidence and all observed errors. Start and stop Playwright tracing.
"""
        output = await run_structured(self.agent, prompt, BrowserRunOutput, self.session)
        await ctx.send_message(ExecutionResult(plan=plan, output=output))


class JudgeExecutor(SessionExecutor):
    @handler
    async def judge(
        self,
        execution: ExecutionResult,
        ctx: WorkflowContext[QualityAssessment],
    ) -> None:
        request = execution.plan.discovery.run.request
        prompt = f"""Judge this browser execution.
Objective: {request.objective}
Policies: {request.policies}
Execution evidence: {execution.output.model_dump_json()}
"""
        result = await run_structured(self.agent, prompt, JudgeOutput, self.session)
        await ctx.send_message(QualityAssessment(execution=execution, result=result))


class SafetyExecutor(SessionExecutor):
    @handler
    async def review(
        self,
        execution: ExecutionResult,
        ctx: WorkflowContext[SafetyAssessment],
    ) -> None:
        prompt = f"""Passively review this browser execution for security and privacy signals.
Target: {execution.plan.discovery.run.request.target_url}
Evidence: {execution.output.model_dump_json()}
"""
        result = await run_structured(self.agent, prompt, SafetyOutput, self.session)
        await ctx.send_message(SafetyAssessment(execution=execution, result=result))


class RefinerExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="refiner")

    @handler
    async def refine(
        self,
        assessments: list[QualityAssessment | SafetyAssessment],
        ctx: WorkflowContext[NextAction],
    ) -> None:
        quality = next(item for item in assessments if isinstance(item, QualityAssessment))
        safety = next(item for item in assessments if isinstance(item, SafetyAssessment))
        plan = quality.execution.plan
        max_attempts = plan.discovery.run.request.max_refinements + 1
        retryable = (
            not quality.result.passed
            and plan.attempt < max_attempts
            and bool(quality.result.retry_advice)
        )
        decision = Decision.RETRY if retryable else Decision.COMPLETE
        await ctx.send_message(
            NextAction(
                plan=plan,
                quality=quality,
                safety=safety,
                decision=decision,
                retry_instruction=quality.result.retry_advice if retryable else None,
            )
        )


class FinalizerExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="finalizer")

    @handler
    async def finalize(self, action: NextAction, ctx: WorkflowContext[Any, QAReport]) -> None:
        request = action.plan.discovery.run.request
        passed = action.quality.result.passed and action.safety.result.passed
        report = QAReport(
            run_id=action.plan.discovery.run.run_id,
            target_url=request.target_url,
            passed=passed,
            score=action.quality.result.score,
            attempts=action.plan.attempt,
            summary=action.plan.generated.scenarios[0].goal
            if passed
            else "; ".join(action.quality.result.defects)
            or action.quality.execution.output.summary,
            policy_results=action.quality.result.policy_results,
            security_findings=action.safety.result.findings,
            artifact_uris=action.quality.execution.output.artifact_paths,
        )
        await ctx.yield_output(report)

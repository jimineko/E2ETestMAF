from typing import Any

from agent_framework import AgentSession, Executor, WorkflowContext, handler, response_handler
from pydantic import BaseModel

from maf_qa.agents import AgentRunner, run_structured
from maf_qa.middleware import classify_failure, exception_status_code, is_quota_error, is_transient_error
from maf_qa.models import (
    BrowserRunOutput,
    Decision,
    DiscoveryFindings,
    DiscoveryReport,
    ExecutionResult,
    GeneratedPlan,
    HumanReviewRequest,
    HumanReviewResponse,
    JudgeOutput,
    LiteralStatus,
    NextAction,
    QAReport,
    QARequest,
    QualityAssessment,
    RunContext,
    SafetyAssessment,
    SafetyOutput,
    Severity,
    StageFailure,
    StageRetry,
    TestPlan,
)


class SessionExecutor(Executor):
    def __init__(
        self,
        executor_id: str,
        agent: AgentRunner,
        *,
        structured_retries: int,
        tools: list[Any] | None = None,
    ) -> None:
        self.agent = agent
        self.structured_retries = structured_retries
        self.tools = tools
        self.sessions: dict[str, AgentSession] = {}
        super().__init__(id=executor_id)

    def session_for(self, run_id: str) -> AgentSession:
        if run_id not in self.sessions:
            self.sessions[run_id] = self.agent.create_session(
                session_id=f"{self.id}-{run_id}-session"
            )
        return self.sessions[run_id]

    async def on_checkpoint_save(self) -> dict[str, Any]:
        return {"agent_sessions": {key: value.to_dict() for key, value in self.sessions.items()}}

    async def on_checkpoint_restore(self, state: dict[str, Any]) -> None:
        sessions = state.get("agent_sessions")
        if isinstance(sessions, dict):
            self.sessions = {
                str(key): AgentSession.from_dict(value)
                for key, value in sessions.items()
                if isinstance(value, dict)
            }

    async def send_failure(
        self,
        message: BaseModel,
        run: RunContext,
        attempt: int,
        exc: Exception,
        ctx: WorkflowContext[Any],
    ) -> None:
        await ctx.send_message(
            StageFailure(
                run=run,
                stage=self.id,
                attempt=attempt,
                kind=classify_failure(exc, stage=self.id),
                exception_type=type(exc).__name__,
                message=_safe_exception_message(exc),
                retryable=is_transient_error(exc),
                input_type=type(message).__name__,
                stage_input=message.model_dump(mode="json"),
            )
        )


class OrchestratorExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="orchestrator")

    @handler
    async def start(self, request: QARequest, ctx: WorkflowContext[RunContext]) -> None:
        await ctx.send_message(RunContext(request=request, run_id=request.run_id))


class DiscoveryExecutor(SessionExecutor):
    @handler
    async def discover(
        self, message: RunContext | StageRetry, ctx: WorkflowContext[DiscoveryReport | StageFailure]
    ) -> None:
        run = (
            RunContext.model_validate(message.stage_input)
            if isinstance(message, StageRetry)
            else message
        )
        review_history = message.review_history if isinstance(message, StageRetry) else []
        prompt = f"""Explore this web application for QA planning.
Target: {run.request.target_url}
Objective: {run.request.objective}
Policies: {run.request.policies or ["No additional policies"]}
Return the discovered pages, user flows, authentication requirement, and risks.
"""
        try:
            findings = await run_structured(
                self.agent,
                prompt,
                DiscoveryFindings,
                self.session_for(run.run_id),
                retries=self.structured_retries,
                tools=self.tools,
                run_id=run.run_id,
                stage=self.id,
                attempt=1,
            )
            await ctx.send_message(
                DiscoveryReport(run=run, findings=findings, review_history=review_history)
            )
        except Exception as exc:
            await self.send_failure(run, run, 1, exc, ctx)


class GeneratorExecutor(SessionExecutor):
    @handler
    async def generate(
        self,
        raw_message: DiscoveryReport | NextAction | StageRetry,
        ctx: WorkflowContext[TestPlan | StageFailure],
    ) -> None:
        message: DiscoveryReport | NextAction
        if isinstance(raw_message, StageRetry):
            model = DiscoveryReport if raw_message.input_type == "DiscoveryReport" else NextAction
            message = model.model_validate(raw_message.stage_input)
            review_history = raw_message.review_history
        else:
            message = raw_message
            review_history = (
                message.review_history if isinstance(message, (DiscoveryReport, NextAction)) else []
            )
        if isinstance(message, DiscoveryReport):
            discovery = message
            attempt = 1
            refinement = None
        else:
            if message.plan is None:
                raise RuntimeError("Generator retry requires an existing test plan")
            discovery = message.plan.discovery
            attempt = message.plan.attempt + 1
            refinement = message.retry_instruction

        prompt = f"""Create the next end-to-end test plan.
Target: {discovery.run.request.target_url}
Objective: {discovery.run.request.objective}
Policies: {discovery.run.request.policies}
Discovered topology: {discovery.findings.model_dump_json()}
Attempt: {attempt}
Refinement instruction: {refinement or "None; create the initial plan."}
"""
        try:
            generated = await run_structured(
                self.agent,
                prompt,
                GeneratedPlan,
                self.session_for(discovery.run.run_id),
                retries=self.structured_retries,
                run_id=discovery.run.run_id,
                stage=self.id,
                attempt=attempt,
            )
            await ctx.send_message(
                TestPlan(
                    discovery=discovery,
                    generated=generated,
                    attempt=attempt,
                    refinement_instruction=refinement,
                    review_history=review_history,
                )
            )
        except Exception as exc:
            await self.send_failure(message, discovery.run, attempt, exc, ctx)


class BrowserExecutor(SessionExecutor):
    @handler
    async def execute_plan(
        self, message: TestPlan | StageRetry, ctx: WorkflowContext[ExecutionResult | StageFailure]
    ) -> None:
        plan = (
            TestPlan.model_validate(message.stage_input)
            if isinstance(message, StageRetry)
            else message
        )
        if isinstance(message, StageRetry):
            plan = plan.model_copy(update={"review_history": message.review_history})
        prompt = f"""Execute this plan against {plan.discovery.run.request.target_url}.
Plan: {plan.generated.model_dump_json()}
Attempt: {plan.attempt}
Return step-level evidence and all observed errors. Start and stop Playwright tracing.
"""
        try:
            output = await run_structured(
                self.agent,
                prompt,
                BrowserRunOutput,
                self.session_for(plan.discovery.run.run_id),
                retries=self.structured_retries,
                tools=self.tools,
                run_id=plan.discovery.run.run_id,
                stage=self.id,
                attempt=plan.attempt,
            )
            await ctx.send_message(ExecutionResult(plan=plan, output=output))
        except Exception as exc:
            await self.send_failure(plan, plan.discovery.run, plan.attempt, exc, ctx)


class JudgeExecutor(SessionExecutor):
    @handler
    async def judge(
        self,
        message: ExecutionResult | StageRetry,
        ctx: WorkflowContext[QualityAssessment | StageFailure],
    ) -> None:
        execution = (
            ExecutionResult.model_validate(message.stage_input)
            if isinstance(message, StageRetry)
            else message
        )
        request = execution.plan.discovery.run.request
        prompt = f"""Judge this browser execution.
Objective: {request.objective}
Policies: {request.policies}
Execution evidence: {execution.output.model_dump_json()}
Return a concise evidence-based rationale, not hidden chain-of-thought.
"""
        try:
            result = await run_structured(
                self.agent,
                prompt,
                JudgeOutput,
                self.session_for(execution.plan.discovery.run.run_id),
                retries=self.structured_retries,
                run_id=execution.plan.discovery.run.run_id,
                stage=self.id,
                attempt=execution.plan.attempt,
            )
            await ctx.send_message(QualityAssessment(execution=execution, result=result))
        except Exception as exc:
            await self.send_failure(
                execution, execution.plan.discovery.run, execution.plan.attempt, exc, ctx
            )


class SafetyExecutor(SessionExecutor):
    @handler
    async def review(
        self,
        message: ExecutionResult | StageRetry,
        ctx: WorkflowContext[SafetyAssessment | StageFailure],
    ) -> None:
        execution = (
            ExecutionResult.model_validate(message.stage_input)
            if isinstance(message, StageRetry)
            else message
        )
        prompt = f"""Passively review this browser execution for security and privacy signals.
Target: {execution.plan.discovery.run.request.target_url}
Evidence: {execution.output.model_dump_json()}
"""
        try:
            result = await run_structured(
                self.agent,
                prompt,
                SafetyOutput,
                self.session_for(execution.plan.discovery.run.run_id),
                retries=self.structured_retries,
                run_id=execution.plan.discovery.run.run_id,
                stage=self.id,
                attempt=execution.plan.attempt,
            )
            await ctx.send_message(SafetyAssessment(execution=execution, result=result))
        except Exception as exc:
            await self.send_failure(
                execution, execution.plan.discovery.run, execution.plan.attempt, exc, ctx
            )


class DecisionExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="decision")

    @handler
    async def decide_failure(self, failure: StageFailure, ctx: WorkflowContext[NextAction]) -> None:
        await ctx.send_message(
            NextAction(run=failure.run, decision=Decision.ESCALATE, failure=failure)
        )

    @handler
    async def decide_assessments(
        self,
        assessments: list[QualityAssessment | SafetyAssessment | StageFailure],
        ctx: WorkflowContext[NextAction],
    ) -> None:
        failure = next((item for item in assessments if isinstance(item, StageFailure)), None)
        quality = next((item for item in assessments if isinstance(item, QualityAssessment)), None)
        safety = next((item for item in assessments if isinstance(item, SafetyAssessment)), None)
        if failure is not None:
            plan = _plan_from_assessments(quality, safety)
            await ctx.send_message(
                NextAction(
                    run=failure.run,
                    plan=plan,
                    quality=quality,
                    safety=safety,
                    decision=Decision.ESCALATE,
                    failure=failure,
                    review_history=plan.review_history if plan is not None else [],
                )
            )
            return
        if quality is None or safety is None:
            raise RuntimeError("Decision requires both quality and safety assessments")
        plan = quality.execution.plan
        max_attempts = plan.discovery.run.request.max_refinements + 1
        severe = any(
            finding.severity in {Severity.HIGH, Severity.CRITICAL}
            for finding in safety.result.findings
        )
        retryable = (
            not quality.result.passed
            and plan.attempt < max_attempts
            and bool(quality.result.retry_advice)
        )
        decision = (
            Decision.ESCALATE if severe else Decision.RETRY if retryable else Decision.COMPLETE
        )
        await ctx.send_message(
            NextAction(
                run=plan.discovery.run,
                plan=plan,
                quality=quality,
                safety=safety,
                decision=decision,
                retry_instruction=quality.result.retry_advice if retryable else None,
                review_history=plan.review_history,
            )
        )


class HumanReviewExecutor(Executor):
    def __init__(self) -> None:
        self.actions: dict[str, NextAction] = {}
        self.retry_counts: dict[str, int] = {}
        super().__init__(id="human_review")

    @handler
    async def request_review(
        self, action: NextAction, ctx: WorkflowContext[StageRetry | NextAction]
    ) -> None:
        self.actions[action.run.run_id] = action
        stage = action.failure.stage if action.failure is not None else "browser"
        reason = (
            action.failure.message if action.failure is not None else "High severity safety finding"
        )
        await ctx.request_info(
            HumanReviewRequest(run_id=action.run.run_id, stage=stage, reason=reason),
            HumanReviewResponse,
        )

    @response_handler
    async def handle_review(
        self,
        request: HumanReviewRequest,
        response: HumanReviewResponse,
        ctx: WorkflowContext[StageRetry],
    ) -> None:
        action = self.actions[request.run_id]
        action.review_history.append(response)
        if response.action == "abort":
            await ctx.send_message(action)  # type: ignore[arg-type]
            return
        count = self.retry_counts.get(request.run_id, 0) + 1
        self.retry_counts[request.run_id] = count
        if count > action.run.request.max_refinements + 1:
            action.review_history.append(
                HumanReviewResponse(action="abort", note="Human retry limit exhausted")
            )
            await ctx.send_message(action)  # type: ignore[arg-type]
            return
        await ctx.send_message(_stage_retry(action))

    async def on_checkpoint_save(self) -> dict[str, Any]:
        return {
            "actions": {key: value.model_dump(mode="json") for key, value in self.actions.items()},
            "retry_counts": self.retry_counts,
        }

    async def on_checkpoint_restore(self, state: dict[str, Any]) -> None:
        actions = state.get("actions", {})
        if isinstance(actions, dict):
            self.actions = {
                str(key): NextAction.model_validate(value)
                for key, value in actions.items()
                if isinstance(value, dict)
            }
        retry_counts = state.get("retry_counts", {})
        if isinstance(retry_counts, dict):
            self.retry_counts = {str(key): int(value) for key, value in retry_counts.items()}


class FinalizerExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="finalizer")

    @handler
    async def finalize(self, action: NextAction, ctx: WorkflowContext[Any, QAReport]) -> None:
        request = action.run.request
        quality = action.quality
        safety = action.safety
        passed = bool(
            action.decision == Decision.COMPLETE
            and quality is not None
            and safety is not None
            and quality.result.passed
            and safety.result.passed
        )
        status = (
            LiteralStatus.BLOCKED
            if action.decision == Decision.ESCALATE
            else LiteralStatus.PASSED
            if passed
            else LiteralStatus.FAILED
        )
        summary = _report_summary(action, status)
        report = QAReport(
            run_id=action.run.run_id,
            target_url=request.target_url,
            status=status,
            passed=passed,
            score=quality.result.score if quality is not None else 0,
            attempts=action.plan.attempt
            if action.plan is not None
            else action.failure.attempt
            if action.failure
            else 1,
            summary=summary,
            policy_results=quality.result.policy_results if quality is not None else [],
            security_findings=safety.result.findings if safety is not None else [],
            failures=[action.failure] if action.failure is not None else [],
            review_history=action.review_history,
            artifact_uris=(quality.execution.output.artifact_paths if quality is not None else []),
        )
        await ctx.yield_output(report)


def _safe_exception_message(exc: Exception) -> str:
    if is_quota_error(exc):
        return f"{type(exc).__name__} (quota_exceeded)"
    status = exception_status_code(exc)
    suffix = f" (status={status})" if isinstance(status, int) else ""
    return f"{type(exc).__name__}{suffix}"


def _plan_from_assessments(
    quality: QualityAssessment | None, safety: SafetyAssessment | None
) -> TestPlan | None:
    if quality is not None:
        return quality.execution.plan
    if safety is not None:
        return safety.execution.plan
    return None


def _stage_retry(action: NextAction) -> StageRetry:
    if action.failure is not None:
        if action.failure.stage in {"judge", "safety"} and action.plan is not None:
            return StageRetry(
                stage="browser",
                input_type="TestPlan",
                stage_input=action.plan.model_dump(mode="json"),
                review_history=action.review_history,
            )
        return StageRetry(
            stage=action.failure.stage,
            input_type=action.failure.input_type,
            stage_input=action.failure.stage_input,
            review_history=action.review_history,
        )
    if action.plan is None:
        raise RuntimeError("Safety retry requires a test plan")
    return StageRetry(
        stage="browser",
        input_type="TestPlan",
        stage_input=action.plan.model_dump(mode="json"),
        review_history=action.review_history,
    )


def _report_summary(action: NextAction, status: LiteralStatus) -> str:
    if status == LiteralStatus.BLOCKED:
        return action.failure.message if action.failure is not None else "Human review required"
    if action.quality is None:
        return "QA run did not produce a quality assessment"
    if action.quality.result.passed:
        return action.quality.result.rationale
    return "; ".join(action.quality.result.defects) or action.quality.result.rationale

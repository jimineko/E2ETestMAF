from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Decision(StrEnum):
    RETRY = "retry"
    COMPLETE = "complete"
    ESCALATE = "escalate"


class QARequest(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    target_url: str
    objective: str
    policies: list[str] = Field(default_factory=list)
    max_refinements: int = Field(default=2, ge=0, le=5)

    @field_validator("target_url")
    @classmethod
    def validate_target_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("target_url must be an absolute HTTP(S) URL")
        return value.rstrip("/")


class RunContext(BaseModel):
    request: QARequest
    run_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FailureKind(StrEnum):
    MODEL_TRANSIENT = "model_transient"
    MODEL_QUOTA = "model_quota"
    MODEL_PERMANENT = "model_permanent"
    STRUCTURED_OUTPUT = "structured_output"
    PLAYWRIGHT = "playwright"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class StageFailure(BaseModel):
    run: RunContext
    stage: str
    attempt: int = Field(ge=1)
    kind: FailureKind
    exception_type: str
    message: str
    retryable: bool = False
    input_type: str
    stage_input: dict[str, Any]


class PageObservation(BaseModel):
    url: str
    title: str = ""
    purpose: str = ""
    interactive_elements: list[str] = Field(default_factory=list)


class DiscoveryFindings(BaseModel):
    pages: list[PageObservation] = Field(default_factory=list)
    user_flows: list[str] = Field(default_factory=list)
    auth_required: bool = False
    risks: list[str] = Field(default_factory=list)
    next_step_hints: list[str] = Field(default_factory=list)


class DiscoveryReport(BaseModel):
    run: RunContext
    findings: DiscoveryFindings
    review_history: list[HumanReviewResponse] = Field(default_factory=list)


class TestScenario(BaseModel):
    name: str
    goal: str
    steps: list[str]
    expected_results: list[str]
    execution_notes: list[str] = Field(default_factory=list)
    priority: int = Field(default=1, ge=1, le=3)


class GeneratedPlan(BaseModel):
    scenarios: list[TestScenario] = Field(min_length=1)
    test_data_notes: list[str] = Field(default_factory=list)
    handoff_hints: list[str] = Field(default_factory=list)


class TestPlan(BaseModel):
    discovery: DiscoveryReport
    generated: GeneratedPlan
    attempt: int = Field(default=1, ge=1)
    refinement_instruction: str | None = None
    review_history: list[HumanReviewResponse] = Field(default_factory=list)


class LiteralStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class StepResult(BaseModel):
    scenario: str
    step: str
    status: LiteralStatus
    evidence: str = ""


class BrowserRunOutput(BaseModel):
    steps: list[StepResult] = Field(default_factory=list)
    console_errors: list[str] = Field(default_factory=list)
    network_errors: list[str] = Field(default_factory=list)
    accessibility_notes: list[str] = Field(default_factory=list)
    follow_up_hints: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    summary: str


class ExecutionResult(BaseModel):
    plan: TestPlan
    output: BrowserRunOutput


class PolicyResult(BaseModel):
    policy: str
    passed: bool
    evidence: str


class JudgeOutput(BaseModel):
    passed: bool
    score: int = Field(ge=0, le=100)
    policy_results: list[PolicyResult] = Field(default_factory=list)
    defects: list[str] = Field(default_factory=list)
    rationale: str
    retry_advice: str | None = None


class QualityAssessment(BaseModel):
    execution: ExecutionResult
    result: JudgeOutput


class SecurityFinding(BaseModel):
    severity: Severity
    title: str
    evidence: str
    recommendation: str


class SafetyOutput(BaseModel):
    passed: bool
    findings: list[SecurityFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SafetyAssessment(BaseModel):
    execution: ExecutionResult
    result: SafetyOutput


class NextAction(BaseModel):
    run: RunContext
    plan: TestPlan | None = None
    quality: QualityAssessment | None = None
    safety: SafetyAssessment | None = None
    decision: Decision
    retry_instruction: str | None = None
    failure: StageFailure | None = None
    review_history: list[HumanReviewResponse] = Field(default_factory=list)


def _default_review_actions() -> list[Literal["retry", "abort"]]:
    return ["retry", "abort"]


class HumanReviewRequest(BaseModel):
    run_id: str
    stage: str
    reason: str
    allowed_actions: list[Literal["retry", "abort"]] = Field(
        default_factory=_default_review_actions
    )


class HumanReviewResponse(BaseModel):
    action: Literal["retry", "abort"]
    note: str | None = None


class StageRetry(BaseModel):
    stage: str
    input_type: str
    stage_input: dict[str, Any]
    review_history: list[HumanReviewResponse] = Field(default_factory=list)


class QAReport(BaseModel):
    run_id: str
    target_url: str
    status: LiteralStatus
    passed: bool
    score: int
    attempts: int
    summary: str
    policy_results: list[PolicyResult]
    security_findings: list[SecurityFinding]
    failures: list[StageFailure] = Field(default_factory=list)
    review_history: list[HumanReviewResponse] = Field(default_factory=list)
    artifact_uris: list[str] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

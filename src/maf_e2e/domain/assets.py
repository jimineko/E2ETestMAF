from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from maf_e2e.domain.specification import TestLifecycleStatus


class ValidationCheck(BaseModel):
    name: Literal["format", "lint", "type_check", "discovery"]
    command: list[str]
    passed: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = Field(ge=0)


class ValidationResult(BaseModel):
    passed: bool
    checks: list[ValidationCheck]
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AssertionResult(BaseModel):
    assertion_id: str
    status: Literal["passed", "failed", "skipped"]
    expected: str = ""
    actual: str = ""
    error: str | None = None


class TrialRunResult(BaseModel):
    run_id: str
    scenario_id: str
    code_hash: str
    status: Literal["passed", "failed", "blocked"]
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    screenshot_paths: list[str] = Field(default_factory=list)
    trace_path: str | None = None
    console_errors: list[str] = Field(default_factory=list)
    network_errors: list[str] = Field(default_factory=list)
    report_path: str
    junit_path: str | None = None
    html_report_path: str | None = None
    error: str | None = None
    duration_seconds: float = Field(default=0, ge=0)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GeneratedTestAsset(BaseModel):
    scenario_id: str
    spec_version: int = Field(ge=1)
    code_version: int = Field(ge=1)
    feature: str
    draft_path: Path
    published_path: Path | None = None
    spec_hash: str
    code_hash: str
    generator_version: str
    validated: bool = False
    status: TestLifecycleStatus = TestLifecycleStatus.GENERATED
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

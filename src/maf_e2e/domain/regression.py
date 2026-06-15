from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from maf_e2e.domain.assets import TrialRunResult


class TargetEnvironment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"


class ScenarioRunResult(BaseModel):
    scenario_id: str
    status: Literal["passed", "failed", "blocked"]
    trial: TrialRunResult


class RegressionRun(BaseModel):
    run_id: str
    repository: Path
    git_commit: str = ""
    environment: TargetEnvironment
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    scenario_results: list[ScenarioRunResult] = Field(default_factory=list)
    report_path: Path | None = None

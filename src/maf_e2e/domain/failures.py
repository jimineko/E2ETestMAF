from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from maf_e2e.domain.specification import LocatorSpec


class FailureCategory(StrEnum):
    APPLICATION_DEFECT = "application_defect"
    TEST_MAINTENANCE = "test_maintenance"
    ENVIRONMENT_FAILURE = "environment_failure"
    AUTHENTICATION_FAILURE = "authentication_failure"
    TEST_DATA_FAILURE = "test_data_failure"
    FLAKY_FAILURE = "flaky_failure"
    UNKNOWN = "unknown"


class FailureAnalysis(BaseModel):
    scenario_id: str
    category: FailureCategory
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    recommended_action: str


class LocatorRepair(BaseModel):
    target_id: str
    locator: LocatorSpec


class RegressionFailureDiagnostic(BaseModel):
    scenario_id: str
    category: FailureCategory
    confidence: float = Field(ge=0, le=1)
    current_ui_summary: str
    evidence: list[str] = Field(default_factory=list)
    recommended_action: str
    locator_replacements: list[LocatorRepair] = Field(default_factory=list)

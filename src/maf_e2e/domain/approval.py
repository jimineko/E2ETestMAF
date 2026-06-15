from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


class ScenarioApproval(BaseModel):
    scenario_id: str
    spec_version: int = Field(ge=1)
    code_version: int = Field(ge=1)
    action: ApprovalAction
    reviewer: str = Field(min_length=1)
    comment: str | None = None
    spec_hash: str
    code_hash: str
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

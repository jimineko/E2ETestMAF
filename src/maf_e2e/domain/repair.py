from __future__ import annotations

from pydantic import BaseModel, Field


class RepairProposal(BaseModel):
    proposal_id: str
    scenario_id: str
    spec_version: int = Field(ge=1)
    base_code_version: int = Field(ge=1)
    reason: str
    changed_files: list[str] = Field(default_factory=list)
    diff: list[str] = Field(default_factory=list)
    base_code_hash: str
    proposed_code_hash: str
    semantic_change_detected: bool
    expected_result_changed: bool
    confidence: float = Field(ge=0, le=1)
    validation_results: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    proposed_code: str | None = None
    proposed_code_path: str | None = None
    branch_name: str | None = None
    pull_request_url: str | None = None

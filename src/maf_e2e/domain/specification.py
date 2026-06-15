from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from maf_e2e.domain.hashing import model_hash, sha256_text


class TestLifecycleStatus(StrEnum):
    DRAFT = "draft"
    GENERATED = "generated"
    VALIDATING = "validating"
    TRIAL_PASSED = "trial_passed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ACTIVE = "active"
    REPAIR_PENDING = "repair_pending"
    DISABLED = "disabled"
    RETIRED = "retired"
    REJECTED = "rejected"


class LocatorSpec(BaseModel):
    strategy: Literal["role", "label", "text", "test_id", "css", "xpath"]
    role: str | None = None
    name: str | None = None
    value: str | None = None

    @model_validator(mode="after")
    def validate_locator(self) -> Self:
        if self.strategy == "role" and not self.role:
            raise ValueError("role locator requires role")
        if self.strategy in {"label", "text", "test_id", "css", "xpath"} and not self.value:
            raise ValueError(f"{self.strategy} locator requires value")
        return self


class StructuredStep(BaseModel):
    step_id: str
    action: Literal[
        "navigate",
        "click",
        "fill",
        "select",
        "press",
        "check",
        "uncheck",
        "upload",
        "wait",
    ]
    target: str | None = None
    locator: LocatorSpec | None = None
    value_ref: str | None = None
    value: str | None = None

    @model_validator(mode="after")
    def validate_action_inputs(self) -> Self:
        if self.action == "navigate" and not self.target:
            raise ValueError("navigate requires target")
        if self.action != "navigate" and self.locator is None:
            raise ValueError(f"{self.action} requires locator")
        if self.action in {"fill", "select", "press", "upload"} and not (
            self.value_ref or self.value
        ):
            raise ValueError(f"{self.action} requires value_ref or value")
        return self


class AssertionSpec(BaseModel):
    assertion_id: str
    type: Literal[
        "visible",
        "hidden",
        "enabled",
        "disabled",
        "text_equals",
        "text_contains",
        "url_matches",
        "value_equals",
        "count_equals",
    ]
    locator: LocatorSpec | None = None
    expected: Any = True
    source_expected_result: str

    @model_validator(mode="after")
    def validate_assertion(self) -> Self:
        if self.type != "url_matches" and self.locator is None:
            raise ValueError(f"{self.type} requires locator")
        return self


class TestSpecification(BaseModel):
    scenario_id: str
    version: int = Field(default=1, ge=1)
    feature: str = "generated"
    name: str
    objective: str
    target_url: str
    priority: int = Field(default=1, ge=1, le=3)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[StructuredStep] = Field(min_length=1)
    assertions: list[AssertionSpec] = Field(min_length=1)
    test_data: dict[str, Any] = Field(default_factory=dict)
    cleanup: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    status: TestLifecycleStatus = TestLifecycleStatus.DRAFT
    spec_hash: str = ""

    @field_validator("target_url")
    @classmethod
    def validate_target_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("target_url must be an absolute HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("scenario_id", "feature")
    @classmethod
    def validate_safe_identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
            raise ValueError("identifier must use lowercase letters, digits, and hyphens")
        return value

    def calculated_hash(self) -> str:
        return model_hash(self, exclude={"spec_hash", "status"})

    def with_hash(self) -> TestSpecification:
        return self.model_copy(update={"spec_hash": self.calculated_hash()})


def stable_scenario_id(name: str, objective: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "scenario"
    base = base[:48].rstrip("-")
    suffix = sha256_text(f"{name}\n{objective}")[:10]
    return f"{base}-{suffix}"

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class AuthoringRequest(BaseModel):
    target_repository_root: Path
    target_url: str
    objective: str
    expected_results: list[str] = Field(min_length=1)
    business_context: str = ""
    preconditions: list[str] = Field(default_factory=list)
    test_data: dict[str, Any] = Field(default_factory=dict)
    policies: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    max_scenarios: int = Field(default=5, ge=1, le=20)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_trial_repairs: int = Field(default=2, ge=0, le=5)

    @field_validator("target_url")
    @classmethod
    def validate_target_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("target_url must be an absolute HTTP(S) URL")
        return value.rstrip("/")

"""Autonomous E2E testing using Microsoft Agent Framework and Playwright MCP."""

from typing import TYPE_CHECKING, Any

from maf_e2e.domain import (
    GeneratedTestAsset,
    RegressionRun,
    RepairProposal,
    ScenarioApproval,
    TestSpecification,
    TrialRunResult,
)
from maf_e2e.models import E2ETestReport, E2ETestRequest

if TYPE_CHECKING:
    from maf_e2e.runtime import E2ETestRuntime
    from maf_e2e.workflow import build_e2e_test_workflow

__all__ = [
    "E2ETestReport",
    "E2ETestRequest",
    "E2ETestRuntime",
    "GeneratedTestAsset",
    "RegressionRun",
    "RepairProposal",
    "ScenarioApproval",
    "TestSpecification",
    "TrialRunResult",
    "build_e2e_test_workflow",
]


def __getattr__(name: str) -> Any:
    if name == "E2ETestRuntime":
        from maf_e2e.runtime import E2ETestRuntime

        return E2ETestRuntime
    if name == "build_e2e_test_workflow":
        from maf_e2e.workflow import build_e2e_test_workflow

        return build_e2e_test_workflow
    raise AttributeError(name)

from maf_e2e.domain.approval import ApprovalAction, ScenarioApproval
from maf_e2e.domain.assets import (
    AssertionResult,
    GeneratedTestAsset,
    StepResult,
    TrialRunResult,
    ValidationCheck,
    ValidationResult,
)
from maf_e2e.domain.failures import (
    FailureAnalysis,
    FailureCategory,
    LocatorRepair,
    RegressionFailureDiagnostic,
)
from maf_e2e.domain.regression import RegressionRun, ScenarioRunResult, TargetEnvironment
from maf_e2e.domain.repair import RepairProposal
from maf_e2e.domain.requests import AuthoringRequest
from maf_e2e.domain.specification import (
    AssertionSpec,
    LocatorSpec,
    StructuredStep,
    TestLifecycleStatus,
    TestSpecification,
    stable_scenario_id,
)

__all__ = [
    "ApprovalAction",
    "AssertionResult",
    "AssertionSpec",
    "AuthoringRequest",
    "FailureAnalysis",
    "FailureCategory",
    "GeneratedTestAsset",
    "LocatorRepair",
    "LocatorSpec",
    "RegressionFailureDiagnostic",
    "RegressionRun",
    "RepairProposal",
    "ScenarioApproval",
    "ScenarioRunResult",
    "StepResult",
    "StructuredStep",
    "TargetEnvironment",
    "TestLifecycleStatus",
    "TestSpecification",
    "TrialRunResult",
    "ValidationCheck",
    "ValidationResult",
    "stable_scenario_id",
]

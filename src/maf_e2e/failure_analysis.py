from __future__ import annotations

from maf_e2e.domain.assets import TrialRunResult
from maf_e2e.domain.failures import FailureAnalysis, FailureCategory


def analyze_failure(
    result: TrialRunResult,
    *,
    diagnostic_evidence: list[str] | None = None,
    previous_passed: bool = False,
) -> FailureAnalysis:
    evidence = [item for item in [result.error, *(diagnostic_evidence or [])] if item]
    text = "\n".join(evidence).lower()
    category, confidence, action = _classify(text, previous_passed=previous_passed)
    if previous_passed:
        confidence = min(confidence + 0.05, 0.95)
        if not any("previous passing" in item.lower() for item in evidence):
            evidence.append("Previous passing result is available for this scenario.")
    return FailureAnalysis(
        scenario_id=result.scenario_id,
        category=category,
        confidence=confidence,
        evidence=evidence[:20],
        recommended_action=action,
    )


def _classify(
    text: str, *, previous_passed: bool
) -> tuple[FailureCategory, float, str]:
    maintenance_tokens = ("strict mode violation", "locator", "waiting for", "no element")
    if any(token in text for token in maintenance_tokens):
        return (
            FailureCategory.TEST_MAINTENANCE,
            0.75,
            "Investigate current UI locators without changing expected behavior.",
        )
    if any(token in text for token in ("timeout", "econnrefused", "dns", "browser closed")):
        return (
            FailureCategory.ENVIRONMENT_FAILURE,
            0.8,
            "Restore the target environment and rerun the same code hash.",
        )
    authentication_tokens = ("401", "403", "unauthorized", "storage state", "session expired")
    if any(token in text for token in authentication_tokens):
        return (
            FailureCategory.AUTHENTICATION_FAILURE,
            0.85,
            "Refresh the test authentication state and rerun.",
        )
    if any(token in text for token in ("fixture", "missing test data", "not found in test data")):
        return (
            FailureCategory.TEST_DATA_FAILURE,
            0.8,
            "Restore or recreate the required test data.",
        )
    if previous_passed and any(token in text for token in ("intermittent", "retry", "flaky")):
        return (
            FailureCategory.FLAKY_FAILURE,
            0.7,
            "Collect repeated runs before changing the test.",
        )
    if any(token in text for token in ("expect(", "expected", "received", "assertion")):
        return (
            FailureCategory.APPLICATION_DEFECT,
            0.65,
            "Review the application behavior against the approved expected result.",
        )
    return FailureCategory.UNKNOWN, 0.3, "Collect trace and current UI evidence for human triage."

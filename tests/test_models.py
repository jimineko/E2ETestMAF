from maf_e2e.models import (
    BrowserRunOutput,
    DiscoveryFindings,
    GeneratedPlan,
    LiteralStatus,
    StepResult,
)
from maf_e2e.models import (
    TestScenario as ScenarioModel,
)


def test_handoff_fields_default_to_empty_lists() -> None:
    discovery = DiscoveryFindings()
    scenario = ScenarioModel(name="n", goal="g", steps=["s"], expected_results=["e"])
    generated = GeneratedPlan(scenarios=[scenario])
    browser = BrowserRunOutput(summary="done")

    assert discovery.next_step_hints == []
    assert scenario.execution_notes == []
    assert generated.handoff_hints == []
    assert browser.follow_up_hints == []


def test_handoff_fields_round_trip_through_dump() -> None:
    discovery = DiscoveryFindings(next_step_hints=["inspect login"])
    scenario = ScenarioModel(
        name="login",
        goal="Verify login flow",
        steps=["Open login", "Submit credentials"],
        expected_results=["User lands on dashboard"],
        execution_notes=["Prefer test id selectors"],
    )
    generated = GeneratedPlan(scenarios=[scenario], handoff_hints=["Keep tracing enabled"])
    browser = BrowserRunOutput(
        steps=[
            StepResult(
                scenario="login",
                step="Open login",
                status=LiteralStatus.PASSED,
                evidence="Loaded",
            )
        ],
        follow_up_hints=["Check post-login redirect"],
        summary="Executed",
    )

    assert DiscoveryFindings.model_validate(discovery.model_dump()).next_step_hints == [
        "inspect login"
    ]
    assert GeneratedPlan.model_validate(generated.model_dump()).handoff_hints == [
        "Keep tracing enabled"
    ]
    assert BrowserRunOutput.model_validate(browser.model_dump()).follow_up_hints == [
        "Check post-login redirect"
    ]

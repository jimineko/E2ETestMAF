from maf_e2e.models import (
    BrowserRunOutput,
    DiscoveryFindings,
    E2ETestRequest,
    GeneratedPlan,
    LiteralStatus,
    PageTransition,
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
    assert discovery.transitions == []
    assert discovery.required_test_data == []
    assert discovery.unexplored_areas == []
    assert discovery.console_errors == []
    assert discovery.network_errors == []
    assert discovery.destructive_action_risks == []
    assert scenario.execution_notes == []
    assert generated.handoff_hints == []
    assert browser.follow_up_hints == []


def test_handoff_fields_round_trip_through_dump() -> None:
    discovery = DiscoveryFindings(
        transitions=[
            PageTransition(
                source_url="https://example.com",
                target_url="https://example.com/login",
                trigger="Login link",
                method="link",
            )
        ],
        required_test_data=["user.email"],
        unexplored_areas=["settings page requires auth"],
        console_errors=["ReferenceError: missing"],
        network_errors=["GET /api/profile 500"],
        destructive_action_risks=["Delete account button not clicked"],
        next_step_hints=["inspect login"],
    )
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
    assert DiscoveryFindings.model_validate(discovery.model_dump()).transitions[
        0
    ].trigger == "Login link"
    assert DiscoveryFindings.model_validate(discovery.model_dump()).network_errors == [
        "GET /api/profile 500"
    ]
    assert GeneratedPlan.model_validate(generated.model_dump()).handoff_hints == [
        "Keep tracing enabled"
    ]
    assert BrowserRunOutput.model_validate(browser.model_dump()).follow_up_hints == [
        "Check post-login redirect"
    ]


def test_authoring_request_discovery_limits_round_trip() -> None:
    request = E2ETestRequest(
        target_url="https://example.com",
        objective="Validate login",
        max_pages=3,
        max_actions=12,
        max_duration_seconds=45,
    )

    loaded = E2ETestRequest.model_validate(request.model_dump())

    assert loaded.max_pages == 3
    assert loaded.max_actions == 12
    assert loaded.max_duration_seconds == 45

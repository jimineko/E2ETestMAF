from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework import AgentResponse, AgentSession
from regression_helpers import make_fake_node_repository

from maf_e2e.authoring_workflow import (
    AuthoringResult,
    LocatorReplacement,
    SpecificationDraft,
    SpecificationDrafts,
    TrialDiagnostic,
    build_authoring_workflow,
)
from maf_e2e.domain.specification import AssertionSpec, LocatorSpec, StructuredStep
from maf_e2e.models import DiscoveryFindings, E2ETestRequest, PageObservation
from maf_e2e.workflow import AgentSet


class FakeAgent:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.calls = 0

    def create_session(self, *, session_id: str | None = None) -> AgentSession:
        return AgentSession(session_id=session_id)

    async def run(
        self,
        messages: str,
        *,
        session: AgentSession | None = None,
        options: dict[str, Any] | None = None,
        tools: list[Any] | None = None,
    ) -> AgentResponse[Any]:
        del messages, session, options, tools
        self.calls += 1
        return AgentResponse(value=self.output)


async def test_authoring_workflow_produces_reviewable_trialled_draft(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    discovery = FakeAgent(_discovery())
    generator = FakeAgent(_drafts())
    unused = FakeAgent(DiscoveryFindings())
    workflow = build_authoring_workflow(
        AgentSet(discovery, generator, unused, unused, unused),
        tmp_path,
    )

    result = await workflow.run(
        E2ETestRequest(
            target_url="https://example.com",
            objective="Show login",
            expected_results=["Login heading is visible"],
            target_repository_root=tmp_path,
        )
    )

    output = result.get_outputs()[0]
    assert isinstance(output, AuthoringResult)
    assert output.status == "pending_approval"
    assert len(output.scenario_ids) == 1
    assert output.trial_results[0].status == "passed"
    assert (output.draft_paths[0] / "generated.spec.ts").exists()


async def test_authoring_trial_failure_uses_bounded_diagnostic_loop(tmp_path: Path) -> None:
    make_fake_node_repository(tmp_path)
    playwright = tmp_path / "node_modules" / ".bin" / "playwright"
    playwright.write_text(
        "#!/bin/sh\ncase \" $* \" in *\" --list \"*) exit 0 ;; esac\nexit 1\n",
        encoding="utf-8",
    )
    playwright.chmod(0o755)
    diagnostic = FakeAgent(
        TrialDiagnostic(
            scenario_id="login-page-6d8bf73a18",
            summary="Heading locator changed",
            locator_replacements=[
                LocatorReplacement(
                    target_id="heading",
                    locator=LocatorSpec(strategy="test_id", value="login-heading"),
                )
            ],
        )
    )
    workflow = build_authoring_workflow(
        AgentSet(FakeAgent(_discovery()), FakeAgent(_drafts()), diagnostic, diagnostic, diagnostic),
        tmp_path,
    )

    result = await workflow.run(
        E2ETestRequest(
            target_url="https://example.com",
            objective="Show login",
            expected_results=["Login heading is visible"],
            target_repository_root=tmp_path,
            max_trial_repairs=1,
        )
    )

    output = result.get_outputs()[0]
    assert isinstance(output, AuthoringResult)
    assert output.status == "blocked"
    assert output.reason == "Maximum trial repair attempts reached."
    assert diagnostic.calls == 1


def _discovery() -> DiscoveryFindings:
    return DiscoveryFindings(
        pages=[PageObservation(url="https://example.com/login", title="Login")],
        user_flows=["Open login"],
    )


def _drafts() -> SpecificationDrafts:
    return SpecificationDrafts(
        scenarios=[
            SpecificationDraft(
                scenario_id="login-page-6d8bf73a18",
                name="Login page",
                objective="Show login",
                steps=[StructuredStep(step_id="navigate", action="navigate", target="/login")],
                assertions=[
                    AssertionSpec(
                        assertion_id="heading",
                        type="visible",
                        locator=LocatorSpec(strategy="role", role="heading", name="Login"),
                        source_expected_result="Login heading is visible",
                    )
                ],
            )
        ]
    )

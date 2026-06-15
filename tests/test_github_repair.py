from __future__ import annotations

import pytest

from maf_e2e.domain.repair import RepairProposal
from maf_e2e.github_repair import _pull_request_body, _validate_branch


def test_repair_pull_request_body_requires_human_review() -> None:
    proposal = RepairProposal(
        proposal_id="proposal",
        scenario_id="login-page",
        spec_version=1,
        base_code_version=1,
        reason="Locator changed",
        semantic_change_detected=False,
        expected_result_changed=False,
        confidence=0.8,
        validation_results=["lint: passed", "repair trial: passed"],
    )

    body = _pull_request_body(proposal)

    assert "never merged automatically" in body
    assert "Expected result changed: `False`" in body


@pytest.mark.parametrize("branch", ["../escape", "-option", "bad branch"])
def test_unsafe_repair_branch_is_rejected(branch: str) -> None:
    with pytest.raises(ValueError):
        _validate_branch(branch)


def test_valid_repair_branch_is_accepted() -> None:
    _validate_branch("agent/e2e-repair/login-20260615")

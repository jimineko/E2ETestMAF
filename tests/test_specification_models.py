from __future__ import annotations

from regression_helpers import sample_spec

from maf_e2e.domain.hashing import model_hash, sha256_text
from maf_e2e.domain.specification import TestLifecycleStatus as LifecycleStatus
from maf_e2e.domain.specification import stable_scenario_id


def test_scenario_and_spec_hashes_are_deterministic() -> None:
    first = sample_spec()
    second = sample_spec()

    assert stable_scenario_id("Login", "Show login") == stable_scenario_id(
        "Login", "Show login"
    )
    assert first.spec_hash == second.spec_hash
    assert first.calculated_hash() == second.calculated_hash()
    assert model_hash(first, exclude={"spec_hash", "status"}) == first.spec_hash
    assert sha256_text("source") == sha256_text("source")


def test_lifecycle_status_does_not_change_spec_hash() -> None:
    spec = sample_spec()

    active = spec.model_copy(update={"status": LifecycleStatus.ACTIVE})

    assert active.calculated_hash() == spec.spec_hash

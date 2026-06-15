from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from regression_helpers import sample_spec

from maf_e2e.approval_store import ApprovalStore
from maf_e2e.asset_store import AssetStore
from maf_e2e.domain.approval import ApprovalAction
from maf_e2e.domain.assets import TrialRunResult, ValidationResult
from maf_e2e.domain.specification import TestLifecycleStatus as LifecycleStatus
from maf_e2e.playwright_codegen import generate_playwright_test
from maf_e2e.publisher import Publisher


def test_approval_requires_same_passing_code_and_publish_is_hash_locked(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    spec = sample_spec()
    source = generate_playwright_test(spec)
    asset = store.save_draft(
        spec,
        source,
        generation_provider="gemini",
        generation_model="gemini-2.5-flash-lite",
    )
    approvals = ApprovalStore(store)

    with pytest.raises(FileNotFoundError):
        approvals.record(
            spec.scenario_id,
            action=ApprovalAction.APPROVE,
            reviewer="reviewer@example.com",
        )

    store.save_trial(
        spec.scenario_id,
        TrialRunResult(
            run_id="trial",
            scenario_id=spec.scenario_id,
            code_hash=asset.code_hash,
            status="passed",
            report_path="report.json",
        ),
    )
    approval = approvals.record(
        spec.scenario_id,
        action=ApprovalAction.APPROVE,
        reviewer="reviewer@example.com",
    )
    published = Publisher(store, approvals).publish(spec.scenario_id)

    assert approval.code_hash == asset.code_hash
    assert published.read_text(encoding="utf-8") == source
    assert store.load_asset(spec.scenario_id).status == LifecycleStatus.ACTIVE
    assert store.load_asset(spec.scenario_id).published_path == published.relative_to(tmp_path)
    assert store.load_asset(spec.scenario_id).generation_provider == "gemini"
    assert store.load_asset(spec.scenario_id).generation_model == "gemini-2.5-flash-lite"
    published_metadata = (
        tmp_path / "e2e" / "metadata" / spec.feature / f"{spec.scenario_id}.json"
    ).read_text(encoding="utf-8")
    assert "review_history_path" in published_metadata

    published.unlink()
    (asset.draft_path / "generated.spec.ts").write_text(source + "// changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Code hash changed"):
        Publisher(store, approvals).publish(spec.scenario_id)


def test_publish_before_approval_and_path_traversal_are_rejected(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    spec = sample_spec()
    store.save_draft(spec, generate_playwright_test(spec))

    with pytest.raises(ValueError, match="requires the latest review"):
        Publisher(store, ApprovalStore(store)).publish(spec.scenario_id)
    with pytest.raises(ValueError, match="escapes"):
        store._safe_child(store.draft_root, "..", "..", "outside")


def test_reject_moves_draft_out_of_active_workspace(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    spec = sample_spec()
    store.save_draft(spec, generate_playwright_test(spec))

    ApprovalStore(store).record(
        spec.scenario_id,
        action=ApprovalAction.REJECT,
        reviewer="reviewer@example.com",
    )

    assert not (store.draft_root / spec.scenario_id).exists()
    assert list(store.rejected_root.glob(f"{spec.scenario_id}-*"))


def test_validation_refreshes_hash_after_formatter_changes_source(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    spec = sample_spec()
    asset = store.save_draft(spec, generate_playwright_test(spec))
    changed = store.load_source(spec.scenario_id) + "// formatted\n"
    (asset.draft_path / "generated.spec.ts").write_text(changed, encoding="utf-8")

    refreshed = store.save_validation(
        spec.scenario_id, ValidationResult(passed=True, checks=[])
    )

    assert refreshed.code_hash != asset.code_hash


def test_expired_draft_is_archived_by_retention_policy(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    spec = sample_spec()
    asset = store.save_draft(spec, generate_playwright_test(spec))
    store.save_metadata(
        asset.model_copy(update={"updated_at": datetime.now(UTC) - timedelta(days=31)})
    )

    moved = store.prune_expired(30)

    assert len(moved) == 1
    assert moved[0].parent == store.expired_root

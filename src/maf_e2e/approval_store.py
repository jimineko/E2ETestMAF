from __future__ import annotations

import json
from datetime import UTC, datetime

from maf_e2e.asset_store import AssetStore
from maf_e2e.domain.approval import ApprovalAction, ScenarioApproval
from maf_e2e.domain.specification import TestLifecycleStatus
from maf_e2e.playwright_codegen import generated_code_hash


class ApprovalStore:
    def __init__(self, assets: AssetStore) -> None:
        self.assets = assets

    def record(
        self,
        scenario_id: str,
        *,
        action: ApprovalAction,
        reviewer: str,
        comment: str | None = None,
    ) -> ScenarioApproval:
        asset = self.assets.load_asset(scenario_id)
        spec = self.assets.load_specification(scenario_id)
        source = self.assets.load_source(scenario_id)
        spec_hash = spec.calculated_hash()
        code_hash = generated_code_hash(source)
        if spec_hash != asset.spec_hash or code_hash != asset.code_hash:
            raise ValueError("Draft hashes no longer match metadata; regenerate before review")
        if action == ApprovalAction.APPROVE:
            trial = self.assets.load_trial(scenario_id)
            if trial.status != "passed" or trial.code_hash != code_hash:
                raise ValueError("Approval requires a passing trial of the same code hash")
        approval = ScenarioApproval(
            scenario_id=scenario_id,
            spec_version=asset.spec_version,
            code_version=asset.code_version,
            action=action,
            reviewer=reviewer,
            comment=comment,
            spec_hash=spec_hash,
            code_hash=code_hash,
        )
        history = self.history(scenario_id)
        history.append(approval)
        self.assets.write_json(
            scenario_id,
            "approvals.json",
            [item.model_dump(mode="json") for item in history],
        )
        status = {
            ApprovalAction.APPROVE: TestLifecycleStatus.APPROVED,
            ApprovalAction.REQUEST_CHANGES: TestLifecycleStatus.GENERATED,
            ApprovalAction.REJECT: TestLifecycleStatus.REJECTED,
        }[action]
        self.assets.save_metadata(
            asset.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
        )
        if action == ApprovalAction.REJECT:
            self.assets.reject(scenario_id)
        return approval

    def history(self, scenario_id: str) -> list[ScenarioApproval]:
        path = self.assets.draft_dir(scenario_id) / "approvals.json"
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [ScenarioApproval.model_validate(item) for item in payload]

    def latest(self, scenario_id: str) -> ScenarioApproval | None:
        history = self.history(scenario_id)
        return history[-1] if history else None

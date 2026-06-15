from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from maf_e2e.approval_store import ApprovalStore
from maf_e2e.asset_store import AssetStore
from maf_e2e.domain.approval import ApprovalAction
from maf_e2e.domain.specification import TestLifecycleStatus
from maf_e2e.playwright_codegen import generated_code_hash


class Publisher:
    def __init__(self, assets: AssetStore, approvals: ApprovalStore) -> None:
        self.assets = assets
        self.approvals = approvals

    def publish(self, scenario_id: str) -> Path:
        asset = self.assets.load_asset(scenario_id)
        approval = self.approvals.latest(scenario_id)
        if approval is None or approval.action != ApprovalAction.APPROVE:
            raise ValueError("Publish requires the latest review action to be approve")
        spec = self.assets.load_specification(scenario_id)
        source = self.assets.load_source(scenario_id)
        if spec.calculated_hash() != approval.spec_hash:
            raise ValueError("Specification hash changed after approval")
        if generated_code_hash(source) != approval.code_hash:
            raise ValueError("Code hash changed after approval")
        if asset.spec_hash != approval.spec_hash or asset.code_hash != approval.code_hash:
            raise ValueError("Approved hashes do not match draft metadata")

        root = self.assets.repository_root / "e2e"
        code_path = _published_path(root, "generated", asset.feature, f"{scenario_id}.spec.ts")
        spec_path = _published_path(
            root, "specs", asset.feature, f"{scenario_id}.v{asset.spec_version}.yaml"
        )
        metadata_path = _published_path(root, "metadata", asset.feature, f"{scenario_id}.json")
        published_relative = code_path.relative_to(self.assets.repository_root)
        published = asset.model_copy(
            update={
                "published_path": published_relative,
                "status": TestLifecycleStatus.ACTIVE,
                "updated_at": datetime.now(UTC),
            }
        )
        portable_metadata = published.model_copy(
            update={"draft_path": asset.draft_path.relative_to(self.assets.repository_root)}
        )
        _atomic_publish(code_path, source)
        _atomic_publish(spec_path, spec.model_dump_json(indent=2))
        _atomic_publish(metadata_path, portable_metadata.model_dump_json(indent=2))
        self.assets.save_metadata(published)
        return code_path


def _published_path(root: Path, *parts: str) -> Path:
    resolved_root = root.resolve()
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError("Published path escapes e2e root")
    return candidate


def _atomic_publish(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise

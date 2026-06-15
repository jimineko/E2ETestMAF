from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from maf_e2e.domain.assets import GeneratedTestAsset, TrialRunResult, ValidationResult
from maf_e2e.domain.specification import TestLifecycleStatus, TestSpecification
from maf_e2e.playwright_codegen import GENERATOR_VERSION, generated_code_hash


class AssetStore:
    def __init__(self, target_repository_root: Path) -> None:
        self.repository_root = target_repository_root.resolve(strict=True)
        if not self.repository_root.is_dir():
            raise ValueError("target repository root must be a directory")
        self.workspace_root = self.repository_root / ".maf-e2e"
        self.draft_root = self.workspace_root / "drafts"
        self.rejected_root = self.workspace_root / "rejected"
        self.expired_root = self.workspace_root / "expired"

    def save_draft(
        self,
        spec: TestSpecification,
        source: str,
        *,
        code_version: int = 1,
    ) -> GeneratedTestAsset:
        hashed = spec.with_hash()
        draft_dir = self._safe_child(self.draft_root, hashed.scenario_id)
        draft_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(draft_dir / "specification.yaml", hashed.model_dump_json(indent=2))
        _atomic_write(draft_dir / "generated.spec.ts", source)
        (draft_dir / "artifacts").mkdir(exist_ok=True)
        asset = GeneratedTestAsset(
            scenario_id=hashed.scenario_id,
            spec_version=hashed.version,
            code_version=code_version,
            feature=hashed.feature,
            draft_path=draft_dir,
            spec_hash=hashed.spec_hash,
            code_hash=generated_code_hash(source),
            generator_version=GENERATOR_VERSION,
            status=TestLifecycleStatus.GENERATED,
        )
        self.save_metadata(asset)
        return asset

    def load_specification(self, scenario_id: str) -> TestSpecification:
        path = self.draft_dir(scenario_id) / "specification.yaml"
        return TestSpecification.model_validate_json(path.read_text(encoding="utf-8"))

    def load_source(self, scenario_id: str) -> str:
        return (self.draft_dir(scenario_id) / "generated.spec.ts").read_text(encoding="utf-8")

    def load_asset(self, scenario_id: str) -> GeneratedTestAsset:
        path = self.draft_dir(scenario_id) / "metadata.json"
        return GeneratedTestAsset.model_validate_json(path.read_text(encoding="utf-8"))

    def save_metadata(self, asset: GeneratedTestAsset) -> None:
        path = self._safe_child(asset.draft_path, "metadata.json")
        _atomic_write(path, asset.model_dump_json(indent=2))

    def save_validation(self, scenario_id: str, result: ValidationResult) -> GeneratedTestAsset:
        draft_dir = self.draft_dir(scenario_id)
        _atomic_write(draft_dir / "validation-result.json", result.model_dump_json(indent=2))
        source_hash = generated_code_hash(
            (draft_dir / "generated.spec.ts").read_text(encoding="utf-8")
        )
        asset = self.load_asset(scenario_id).model_copy(
            update={
                "validated": result.passed,
                "code_hash": source_hash,
                "status": (
                    TestLifecycleStatus.VALIDATING
                    if not result.passed
                    else TestLifecycleStatus.GENERATED
                ),
                "updated_at": datetime.now(UTC),
            }
        )
        self.save_metadata(asset)
        return asset

    def save_trial(self, scenario_id: str, result: TrialRunResult) -> GeneratedTestAsset:
        draft_dir = self.draft_dir(scenario_id)
        _atomic_write(draft_dir / "trial-result.json", result.model_dump_json(indent=2))
        asset = self.load_asset(scenario_id).model_copy(
            update={
                "status": (
                    TestLifecycleStatus.PENDING_APPROVAL
                    if result.status == "passed"
                    else TestLifecycleStatus.GENERATED
                ),
                "updated_at": datetime.now(UTC),
            }
        )
        self.save_metadata(asset)
        return asset

    def load_trial(self, scenario_id: str) -> TrialRunResult:
        path = self.draft_dir(scenario_id) / "trial-result.json"
        return TrialRunResult.model_validate_json(path.read_text(encoding="utf-8"))

    def write_json(self, scenario_id: str, filename: str, value: Any) -> None:
        if Path(filename).name != filename:
            raise ValueError("filename must not contain a path")
        _atomic_write(
            self.draft_dir(scenario_id) / filename,
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
        )

    def reject(self, scenario_id: str) -> Path:
        draft_dir = self.draft_dir(scenario_id)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = self._safe_child(self.rejected_root, f"{scenario_id}-{timestamp}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(draft_dir), destination)
        return destination

    def draft_dir(self, scenario_id: str) -> Path:
        path = self._safe_child(self.draft_root, scenario_id)
        if not path.is_dir():
            raise FileNotFoundError(f"Draft not found: {scenario_id}")
        return path

    def list_drafts(self) -> list[GeneratedTestAsset]:
        if not self.draft_root.exists():
            return []
        return [
            GeneratedTestAsset.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.draft_root.glob("*/metadata.json"))
        ]

    def prune_expired(self, retention_days: int) -> list[Path]:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        moved: list[Path] = []
        for asset in self.list_drafts():
            if asset.updated_at >= cutoff or asset.status == TestLifecycleStatus.ACTIVE:
                continue
            destination = self._safe_child(self.expired_root, asset.scenario_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(asset.draft_path), destination)
            moved.append(destination)
        return moved

    def _safe_child(self, parent: Path, *parts: str) -> Path:
        candidate = parent.joinpath(*parts).resolve()
        workspace = self.workspace_root.resolve()
        if not candidate.is_relative_to(workspace):
            raise ValueError("Path escapes the target repository workspace")
        return candidate


def _atomic_write(path: Path, content: str) -> None:
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

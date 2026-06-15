from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from maf_e2e.domain.assets import GeneratedTestAsset
from maf_e2e.domain.regression import RegressionRun, ScenarioRunResult, TargetEnvironment
from maf_e2e.domain.specification import TestLifecycleStatus
from maf_e2e.process import run_process
from maf_e2e.trial_runner import TrialRunner


class RegressionRunner:
    def __init__(self, repository_root: Path, *, timeout_seconds: int = 600) -> None:
        self.repository_root = repository_root.resolve(strict=True)
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        environment: TargetEnvironment,
        *,
        scenario_ids: set[str] | None = None,
    ) -> RegressionRun:
        run_id = uuid4().hex
        output_root = self.repository_root / ".maf-e2e" / "regression" / run_id
        output_root.mkdir(parents=True, exist_ok=True)
        run = RegressionRun(
            run_id=run_id,
            repository=self.repository_root,
            git_commit=await self._git_commit(),
            environment=environment,
        )
        trial_runner = TrialRunner(self.repository_root, timeout_seconds=self.timeout_seconds)
        for metadata_path in sorted((self.repository_root / "e2e" / "metadata").glob("**/*.json")):
            asset = GeneratedTestAsset.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
            if asset.status != TestLifecycleStatus.ACTIVE:
                continue
            if scenario_ids is not None and asset.scenario_id not in scenario_ids:
                continue
            if asset.published_path is None:
                raise ValueError(f"ACTIVE asset has no published path: {asset.scenario_id}")
            code_path = asset.published_path
            if not code_path.is_absolute():
                code_path = self.repository_root / code_path
            trial = await trial_runner.run(
                asset.scenario_id,
                code_path,
                artifact_dir=output_root / asset.scenario_id,
            )
            run.scenario_results.append(
                ScenarioRunResult(scenario_id=asset.scenario_id, status=trial.status, trial=trial)
            )
        run.completed_at = datetime.now(UTC)
        run.report_path = output_root / "regression.json"
        run.report_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        return run

    async def _git_commit(self) -> str:
        result = await run_process(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repository_root,
            timeout_seconds=10,
            output_limit_bytes=10_000,
        )
        return result.stdout.strip() if result.exit_code == 0 else ""


def regression_exit_code(run: RegressionRun) -> int:
    if any(result.status == "blocked" for result in run.scenario_results):
        return 3
    if any(result.status == "failed" for result in run.scenario_results):
        return 1
    return 0


def load_regression_run(path: Path) -> RegressionRun:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RegressionRun.model_validate(payload)

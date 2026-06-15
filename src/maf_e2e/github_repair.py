from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from maf_e2e.domain.repair import RepairProposal
from maf_e2e.process import ProcessResult, run_process


class RepositoryPublisher(Protocol):
    repository_root: Path

    async def create_branch(self, branch_name: str) -> None: ...

    async def commit_files(self, paths: list[Path], message: str) -> str: ...

    async def push(self, branch_name: str) -> None: ...

    async def create_pull_request(
        self, *, branch_name: str, title: str, body: str, base_branch: str
    ) -> str: ...


class GitHubRepositoryPublisher:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve(strict=True)

    async def create_branch(self, branch_name: str) -> None:
        _validate_branch(branch_name)
        await self._required(["git", "switch", "-c", branch_name])

    async def commit_files(self, paths: list[Path], message: str) -> str:
        relative: list[str] = []
        for path in paths:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.repository_root):
                raise ValueError("Cannot commit a file outside the target repository")
            relative.append(str(resolved.relative_to(self.repository_root)))
        await self._required(["git", "add", "--", *relative])
        await self._required(["git", "commit", "-m", message])
        result = await self._required(["git", "rev-parse", "HEAD"])
        return result.stdout.strip()

    async def push(self, branch_name: str) -> None:
        _validate_branch(branch_name)
        await self._required(["git", "push", "-u", "origin", branch_name], timeout=300)

    async def create_pull_request(
        self, *, branch_name: str, title: str, body: str, base_branch: str
    ) -> str:
        _validate_branch(branch_name)
        result = await self._required(
            [
                "gh",
                "pr",
                "create",
                "--head",
                branch_name,
                "--base",
                base_branch,
                "--title",
                title,
                "--body",
                body,
            ],
            timeout=300,
        )
        return str(result.stdout.strip().splitlines()[-1])

    async def _required(
        self, command: list[str], *, timeout: int = 120
    ) -> ProcessResult:
        result = await run_process(
            command,
            cwd=self.repository_root,
            timeout_seconds=timeout,
            output_limit_bytes=1_000_000,
        )
        if result.exit_code != 0 or result.timed_out:
            raise RuntimeError(result.stderr or result.stdout or f"Command failed: {command[0]}")
        return result


async def publish_repair_pull_request(
    publisher: RepositoryPublisher,
    proposal: RepairProposal,
    paths: list[Path],
    *,
    file_updates: dict[Path, str] | None = None,
    base_branch: str = "main",
    branch_prefix: str = "agent/e2e-repair",
) -> RepairProposal:
    date = datetime.now(UTC).strftime("%Y%m%d")
    branch = f"{branch_prefix}/{proposal.scenario_id}-{date}"
    await publisher.create_branch(branch)
    for path, content in (file_updates or {}).items():
        resolved = path.resolve()
        if not resolved.is_relative_to(publisher.repository_root):
            raise ValueError("Cannot update a file outside the target repository")
        resolved.write_text(content, encoding="utf-8")
    await publisher.commit_files(paths, f"Repair E2E scenario {proposal.scenario_id}")
    await publisher.push(branch)
    body = _pull_request_body(proposal)
    url = await publisher.create_pull_request(
        branch_name=branch,
        base_branch=base_branch,
        title=f"Repair E2E scenario: {proposal.scenario_id}",
        body=body,
    )
    return proposal.model_copy(update={"branch_name": branch, "pull_request_url": url})


def _pull_request_body(proposal: RepairProposal) -> str:
    validations = "\n".join(f"- {item}" for item in proposal.validation_results) or "- None"
    return f"""## E2E repair

Scenario: `{proposal.scenario_id}`
Reason: {proposal.reason}
Confidence: {proposal.confidence:.2f}
Expected result changed: `{proposal.expected_result_changed}`
Semantic change detected: `{proposal.semantic_change_detected}`

## Validation

{validations}

This pull request requires human review and is never merged automatically.
"""


def _validate_branch(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", value) or ".." in value or value.startswith("-"):
        raise ValueError("Unsafe git branch name")

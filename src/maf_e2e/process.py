from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic


@dataclass(frozen=True)
class ProcessResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False


async def run_process(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    output_limit_bytes: int = 1_000_000,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    started = monotonic()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        timed_out = True
        process.kill()
        stdout, stderr = await process.communicate()
    return ProcessResult(
        command=list(command),
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout=_bounded_decode(stdout, output_limit_bytes),
        stderr=_bounded_decode(stderr, output_limit_bytes),
        duration_seconds=monotonic() - started,
        timed_out=timed_out,
    )


def _bounded_decode(value: bytes, limit: int) -> str:
    if len(value) <= limit:
        return value.decode("utf-8", errors="replace")
    suffix = f"\n... output truncated ({len(value) - limit} bytes omitted)"
    return value[:limit].decode("utf-8", errors="replace") + suffix

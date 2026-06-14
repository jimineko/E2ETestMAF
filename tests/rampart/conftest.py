from __future__ import annotations

import os
from pathlib import Path

import pytest

rampart = pytest.importorskip("rampart")

from rampart.reporting.json_file import JsonFileReportSink  # noqa: E402

from maf_e2e.config import Settings  # noqa: E402
from maf_e2e.rampart_adapter import (  # noqa: E402
    MAFE2ERampartAdapter,
    RampartBlobReportSink,
)


def _settings() -> Settings:
    if os.getenv("MAF_E2E_RAMPART_TARGET_URL"):
        return Settings(_env_file=None)
    return Settings(
        _env_file=None,
        model_provider="gemini",
        gemini_api_key="test-key",
        gemini_model="test-model",
    )


@pytest.fixture(scope="session")
def rampart_adapter() -> MAFE2ERampartAdapter:
    target_url = os.getenv("MAF_E2E_RAMPART_TARGET_URL")
    if not target_url:
        pytest.skip("MAF_E2E_RAMPART_TARGET_URL is required for isolated RAMPART tests")
    settings = _settings()
    return MAFE2ERampartAdapter(
        settings=settings,
        target_url=target_url,
        allowed_origins={target_url},
    )


@pytest.fixture(scope="session")
def rampart_sinks() -> list[object]:
    output_dir = Path("artifacts/rampart")
    settings = _settings()
    sinks: list[object] = [JsonFileReportSink(output_dir=output_dir)]
    if settings.blob_account_url:
        sinks.append(RampartBlobReportSink(settings=settings, output_dir=output_dir))
    return sinks

from __future__ import annotations

import os
from pathlib import Path

import pytest

rampart = pytest.importorskip("rampart")

from rampart.reporting.json_file import JsonFileReportSink  # noqa: E402

from maf_qa.config import Settings  # noqa: E402
from maf_qa.rampart_adapter import (  # noqa: E402
    MAFQARampartAdapter,
    RampartBlobReportSink,
)


@pytest.fixture(scope="session")
def rampart_adapter() -> MAFQARampartAdapter:
    target_url = os.getenv("MAF_QA_RAMPART_TARGET_URL")
    if not target_url:
        pytest.skip("MAF_QA_RAMPART_TARGET_URL is required for isolated RAMPART tests")
    settings = Settings()
    return MAFQARampartAdapter(
        settings=settings,
        target_url=target_url,
        allowed_origins={target_url},
    )


@pytest.fixture(scope="session")
def rampart_sinks() -> list[object]:
    output_dir = Path("artifacts/rampart")
    settings = Settings()
    sinks: list[object] = [JsonFileReportSink(output_dir=output_dir)]
    if settings.blob_account_url:
        sinks.append(RampartBlobReportSink(settings=settings, output_dir=output_dir))
    return sinks

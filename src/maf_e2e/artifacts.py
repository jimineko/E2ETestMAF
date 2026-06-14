from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path

from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob.aio import BlobServiceClient


def archive_run(run_dir: Path) -> Path:
    archive_base = run_dir.parent / run_dir.name
    archive_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=run_dir))
    return archive_path


def blob_uri(account_url: str, container_name: str, run_id: str, filename: str) -> str:
    return f"{account_url.rstrip('/')}/{container_name}/{run_id}/{filename}"


async def upload_artifacts(
    paths: list[Path],
    *,
    account_url: str,
    container_name: str,
    credential: AsyncTokenCredential,
    run_id: str,
) -> list[str]:
    uris: list[str] = []
    async with BlobServiceClient(account_url=account_url, credential=credential) as service:
        container = service.get_container_client(container_name)
        with suppress(ResourceExistsError):
            await container.create_container()
        for path in paths:
            blob_name = f"{run_id}/{path.name}"
            with path.open("rb") as handle:
                await container.upload_blob(blob_name, handle, overwrite=True)
            uris.append(blob_uri(account_url, container_name, run_id, path.name))
    return uris

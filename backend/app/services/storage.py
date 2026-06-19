"""On-disk storage of raw uploaded PDFs.

Phase 1 only persists the raw file; the ingestion pipeline (Phase 2) will read
it back to parse + chunk + embed. Kept in ``services/`` because it's business
logic (no FastAPI imports), so it stays unit-testable without spinning up a
router.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings


async def save_uploaded_file(file: UploadFile, document_id: int) -> Path:
    """Stream ``UploadFile`` to ``{storage_path}/{document_id}.pdf`` and return
    the absolute path. Reads in chunks so large PDFs don't load fully into RAM.
    """
    dest = settings.document_file_path(document_id)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # UploadFile is a SpooledTemporaryFile-backed stream; iterate in modest
    # chunks rather than awaiting read() with no limit.
    written = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)  # 1 MiB
            if not chunk:
                break
            out.write(chunk)
            written += len(chunk)
    return dest, written  # type: ignore[return-value]


def delete_document_file(document_id: int) -> None:
    """Best-effort delete of a document's raw PDF. Missing file is a no-op —
    the DB row is the source of truth, the file is secondary."""
    path = settings.document_file_path(document_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # If it's a directory or some other FS oddity, don't blow up the
        # DELETE request; the DB row is what matters.
        shutil.rmtree(path, ignore_errors=True)

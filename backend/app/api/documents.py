"""Documents API router.

CRUD over the ``documents`` table for Phase 1. Ingestion (parsing/chunking/
embedding) is explicitly out of scope here — Phase 1 just persists the raw
upload and the metadata row. The handlers stay thin: parse request -> call a
service -> shape the response.
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import get_session
from app.db.models import Document
from app.models.documents import DocumentList, DocumentRead
from app.services.ingestion import process_document
from app.services.storage import delete_document_file, save_uploaded_file

router = APIRouter(prefix="/documents", tags=["documents"])

# First 5 bytes of any PDF file — "%PDF-". Used as the real check (content-type
# can be spoofed by the client), content-type is a secondary sanity filter.
PDF_MAGIC = b"%PDF"


def _is_pdf(file: UploadFile) -> bool:
    """Cheap sniff: peek the first bytes for the PDF magic header."""
    file.file.seek(0)
    head = file.file.read(len(PDF_MAGIC))
    file.file.seek(0)
    return head == PDF_MAGIC


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new PDF",
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> DocumentRead:
    """Accept a PDF upload, store it on disk, create a ``documents`` row with
    ``status='uploaded'``, return its metadata immediately, then kick off the
    ingestion pipeline (parse → chunk → embed → store) as a background task.

    The response is sent before ingestion runs — the frontend polls
    ``GET /api/documents/{id}`` to watch ``uploaded → processing → ready``.
    """
    # --- Validation: must be a PDF (content-type OR magic bytes) ---
    ctype = (file.content_type or "").lower()
    looks_like_pdf_ct = ctype == "application/pdf" or ctype.endswith("pdf")
    if not (looks_like_pdf_ct and _is_pdf(file)):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF.")

    # --- Validation: size limit (stream-check, since we haven't read it all) ---
    # We enforce after streaming to disk below by checking written length; but
    # reject obviously-oversize clients up front when Content-Length is present.
    file.file.seek(0, 2)
    declared = file.file.tell()
    file.file.seek(0)
    if declared and declared > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large: limit is {settings.MAX_UPLOAD_SIZE_MB} MB "
                f"({settings.max_upload_size_bytes} bytes)."
            ),
        )

    # --- Insert the row first so we have the id to name the on-disk file ---
    document = Document(
        filename=file.filename or "upload.pdf",
        file_path="",  # filled in after we know the id
        status="uploaded",
    )
    session.add(document)
    await session.flush()  # assigns document.id without committing

    # --- Persist the raw file to {storage_path}/{id}.pdf ---
    path, written = await save_uploaded_file(file, document.id)
    document.file_path = str(path)
    document.file_size_bytes = written

    await session.commit()
    await session.refresh(document)

    # --- Kick off ingestion AFTER the response is returned. The task opens
    # its own session (the request session is closed by then), so we only need
    # to pass the id. See app/services/ingestion.py.
    background_tasks.add_task(process_document, document.id)

    return DocumentRead.model_validate(document)


@router.get(
    "",
    response_model=DocumentList,
    summary="List all documents",
)
async def list_documents(
    session: AsyncSession = Depends(get_session),
) -> DocumentList:
    """Return every document, newest upload first (matches the frontend's
    expected display order)."""
    stmt = select(Document).order_by(Document.uploaded_at.desc())
    result = await session.execute(stmt)
    docs = result.scalars().all()
    return DocumentList(documents=[DocumentRead.model_validate(d) for d in docs])


@router.get(
    "/{document_id}",
    response_model=DocumentRead,
    summary="Get a single document",
)
async def get_document(
    document_id: int,
    session: AsyncSession = Depends(get_session),
) -> DocumentRead:
    """Lighter-weight than the list; useful for targeted status polling while a
    single document is still processing."""
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"No document with id {document_id}.")
    return DocumentRead.model_validate(document)


@router.delete(
    "/{document_id}",
    summary="Delete a document and everything derived from it",
)
async def delete_document(
    document_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete the ``documents`` row (cascades to ``chunks``) and the raw file.
    Returns 404 if the id doesn't exist so the frontend can surface a real
    error if it's out of sync — not a silent success."""
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"No document with id {document_id}.")

    file_path = document.file_path
    await session.delete(document)  # FK ondelete=CASCADE drops chunks too
    await session.commit()

    delete_document_file(document_id)
    # Defensive: also remove by the stored path if it differs from the id-based name.
    if file_path and file_path != str(settings.document_file_path(document_id)):
        try:
            from pathlib import Path

            Path(file_path).unlink(missing_ok=True)
        except OSError:
            pass

    # Explicit empty 204 response. Returning None (implicit) with status_code=204
    # works on newer FastAPI but asserts on 0.115.x ("204 must not have a response
    # body"). Returning Response(status_code=204) is correct on both.
    return Response(status_code=status.HTTP_204_NO_CONTENT)

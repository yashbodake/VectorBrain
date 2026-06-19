"""Pydantic schemas for the documents resource.

One Pydantic model per boundary, per the coding conventions (no raw dicts
crossing layers). ``from_attributes=True`` lets us build these directly from
ORM instances in the route handlers.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    """Single document as returned by the API. Includes ``error_message`` so a
    failed ingest (Phase 2+) can be surfaced to the frontend."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    page_count: int | None = None
    file_size_bytes: int | None = None
    status: str
    error_message: str | None = None
    uploaded_at: datetime
    processed_at: datetime | None = None


class DocumentList(BaseModel):
    """Envelope for ``GET /api/documents``."""

    documents: list[DocumentRead] = Field(default_factory=list)

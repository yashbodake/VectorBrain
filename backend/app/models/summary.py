"""Pydantic schemas for chapter summaries."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    section_index: int
    title: str
    page_start: int | None = None
    page_end: int | None = None
    summary: str
    created_at: datetime

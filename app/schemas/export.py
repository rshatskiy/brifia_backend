"""Schemas for /api/v1/meetings/{id}/export and /share/email."""
import uuid
from typing import Literal
from pydantic import BaseModel, Field


ExportFormat = Literal["pdf", "docx"]


class ShareEmailRequest(BaseModel):
    """Send the rendered protocol to a list of meeting participants by
    email. Only participants with a non-empty email address are accepted;
    others are filtered out server-side and silently dropped from the
    delivery list (the response reports actual deliveries)."""
    participant_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=50)
    format: ExportFormat = "pdf"
    message: str | None = Field(default=None, max_length=2000)


class ShareEmailResponse(BaseModel):
    sent_to: list[str]
    skipped: list[str]

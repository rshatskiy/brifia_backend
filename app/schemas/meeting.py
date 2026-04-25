from pydantic import BaseModel
from datetime import datetime
from uuid import UUID


class MeetingCreate(BaseModel):
    # Optional client-supplied id. When present, makes meeting creation idempotent
    # by this UUID (acts as local_meeting_uuid). When absent, server generates one.
    # Lets the mobile client commit to a single identifier at recording start —
    # before any network round-trip can fail — and reuse it across registration,
    # upload initiation, and transcription.
    id: UUID | None = None
    title: str | None = None
    status: str = "pending_upload"
    duration_seconds: int | None = None
    local_filename: str | None = None
    series_id: UUID | None = None
    prompt_id: UUID | None = None


class MeetingCreateInternal(BaseModel):
    # Used by faster-whisper to register a meeting if the client failed to.
    id: UUID
    user_id: UUID
    title: str | None = None
    status: str = "pending_upload"
    duration_seconds: int | None = None
    local_filename: str | None = None


class MeetingUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    duration_seconds: int | None = None
    transcript: str | None = None
    transcript_json: str | None = None
    protocol: str | None = None
    tasks_json: str | None = None
    series_id: UUID | None = None
    prompt_id: UUID | None = None


class MeetingListItem(BaseModel):
    id: UUID
    title: str | None
    status: str
    duration_seconds: int | None
    local_filename: str | None
    series_id: UUID | None
    prompt_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MeetingDetail(MeetingListItem):
    transcript: str | None
    protocol: str | None
    tasks_json: str | None

    model_config = {"from_attributes": True}


class MeetingTranscriptResponse(BaseModel):
    meeting_id: UUID
    transcript_json: str | None

    model_config = {"from_attributes": True}


class MeetingStatusResponse(BaseModel):
    meeting_id: UUID
    status: str
    title: str | None
    has_transcript: bool
    has_transcript_json: bool
    has_protocol: bool
    has_tasks: bool
    created_at: datetime
    updated_at: datetime


class MeetingCountsResponse(BaseModel):
    total: int
    no_series: int
    by_series: dict[str, int]

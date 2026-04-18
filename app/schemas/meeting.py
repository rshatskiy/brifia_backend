from pydantic import BaseModel
from datetime import datetime
from uuid import UUID


class MeetingCreate(BaseModel):
    title: str | None = None
    status: str = "pending_upload"
    duration_seconds: int | None = None
    local_filename: str | None = None
    series_id: UUID | None = None
    prompt_id: UUID | None = None


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

"""Pydantic schemas for /api/v1/participants and /meetings/{id}/speakers."""
import uuid
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, EmailStr


class ParticipantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=64)
    role: str | None = Field(None, max_length=255)
    note: str | None = None


class ParticipantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=64)
    role: str | None = Field(None, max_length=255)
    note: str | None = None


class ParticipantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str | None = None
    phone: str | None = None
    role: str | None = None
    note: str | None = None
    created_at: datetime
    updated_at: datetime
    # Series-aware fields populated when series_id query param was provided:
    meetings_in_series: int | None = None
    last_seen_in_series: datetime | None = None


class ParticipantMerge(BaseModel):
    absorb_id: uuid.UUID  # this id will be deleted, its data moved into the URL's id


class ParticipantWithMeetings(ParticipantOut):
    """Detail response — extends with recent meetings and series list."""
    recent_meetings: list[dict] = Field(default_factory=list)
    # [{"id": uuid, "title": str | None, "created_at": datetime}]
    series: list[dict] = Field(default_factory=list)
    # [{"id": uuid, "title": str | None, "meetings_count": int}]


class MeetingSpeakerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    speaker_label: str
    participant: ParticipantOut | None = None
    speaking_seconds: int | None = None
    name_suggestions: list[dict] = Field(default_factory=list)


class MeetingSpeakerBind(BaseModel):
    """Body for PUT /meetings/{id}/speakers/{label}."""
    participant_id: uuid.UUID | None  # null = unbind
    accepted_suggestion: bool | None = None  # for LLM-suggestion accept_rate metric

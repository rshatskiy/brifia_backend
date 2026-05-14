from pydantic import BaseModel, field_validator
from datetime import datetime
from uuid import UUID

from app.constants.meeting_status import MeetingStatus

# Canonical state machine — the only values internal endpoints can move
# the meeting through (worker progress, complete, etc.).
_CANONICAL_STATUSES = frozenset(s.value for s in MeetingStatus)

# Statuses that the current production mobile client pushes from
# BackgroundUploadService.toMeetingStatus() and related paths, but which
# are NOT part of the canonical state machine. Accepted here so the
# validator doesn't 422 existing TestFlight/App Store builds. They mostly
# fold into pending_upload / error for routing purposes — the
# PendingUploadWatcher on the client keys off 'upload_failed' specifically
# to auto-retry stuck uploads, which is why we can't just drop them.
#
# These should be removed once the cleaned-up client (cancelUpload no
# longer pushes status, toMeetingStatus mapped to canonical values) has
# rolled out widely. Until then this list is the contract.
_LEGACY_CLIENT_STATUSES = frozenset({
    "upload_failed",      # client: chunked upload returned failure
    "upload_cancelled",   # client: explicit user cancel (legacy)
    "upload_initiating",  # client: pre-/initiate transient
    "upload_completing",  # client: post-chunks pre-/complete transient
})

_ALLOWED_STATUSES = _CANONICAL_STATUSES | _LEGACY_CLIENT_STATUSES


def _validate_status_value(v: str | None) -> str | None:
    """Guards the public PUT /meetings/{id} endpoint from being fed
    arbitrary status strings by buggy or outdated clients. The trigger
    was 'upload_cancelled_by_user' surfacing literally in the meeting
    card — pushed by a stray cancelUpload() path on a stop-button race.

    Accepts canonical MeetingStatus values plus a transitional allowlist
    for statuses that current production clients actively send. Anything
    else returns 422.
    """
    if v is None:
        return v
    if v not in _ALLOWED_STATUSES:
        raise ValueError(
            f"Invalid meeting status '{v}'. Allowed: {sorted(_ALLOWED_STATUSES)}"
        )
    return v


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

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str | None) -> str | None:
        return _validate_status_value(v)


class MeetingCreateInternal(BaseModel):
    # Used by faster-whisper to register a meeting if the client failed to.
    id: UUID
    user_id: UUID
    title: str | None = None
    status: str = "pending_upload"
    duration_seconds: int | None = None
    local_filename: str | None = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str | None) -> str | None:
        return _validate_status_value(v)


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

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str | None) -> str | None:
        return _validate_status_value(v)


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

    # Counts of rows in meeting_tasks for this meeting. Server-computed
    # so the client can render badges and series statistics without having
    # to fetch the task list. Defaults are 0 so newly-created meetings and
    # meetings whose worker hasn't fanned out yet round-trip cleanly.
    tasks_count: int = 0
    open_tasks_count: int = 0

    model_config = {"from_attributes": True}


class MeetingDetail(MeetingListItem):
    transcript: str | None
    protocol: str | None
    # Legacy blob — kept on the wire while clients migrate to meeting_tasks
    # rows + tasks_count/open_tasks_count above. Will be dropped after the
    # mobile cut-over (see project_tasks_json_migration_blockers memory).
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

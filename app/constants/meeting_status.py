"""Canonical state machine for Meeting.status.

Single source of truth — Pydantic schemas, routers, and the Flutter client
all reference these values. Adding a new status without updating both ends
should fail loudly (assert in dev) per the design spec.
"""
from enum import Enum


class MeetingStatus(str, Enum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADING = "uploading"
    QUEUED = "queued"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    TRANSCRIPTION_EMPTY = "transcription_empty"
    ERROR = "error"


ALL_STATUSES: tuple[str, ...] = tuple(s.value for s in MeetingStatus)

TERMINAL_STATUSES: frozenset[str] = frozenset({
    MeetingStatus.COMPLETED.value,
    MeetingStatus.TRANSCRIPTION_EMPTY.value,
    MeetingStatus.ERROR.value,
})

# Statuses that processing_jobs/stalekeeper considers "in flight"
IN_FLIGHT_STATUSES: frozenset[str] = frozenset({
    MeetingStatus.QUEUED.value,
    MeetingStatus.TRANSCRIBING.value,
    MeetingStatus.ANALYZING.value,
})

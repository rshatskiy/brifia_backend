"""Pydantic schemas for /internal/jobs endpoints.

Body shapes for the worker on the transcription machine. All endpoints
authenticated via X-API-Key (FASTER_WHISPER_API_KEY), not user JWT.
"""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


# --- Request payloads from worker → api2 ---

class JobCreate(BaseModel):
    """Posted by faster-whisper FastAPI after merging chunks."""
    meeting_id: uuid.UUID
    audio_local_path: str = Field(..., min_length=1, max_length=1024)
    priority: Literal["realtime", "background"] = "realtime"
    expected_duration_seconds: int | None = None


class JobProgress(BaseModel):
    """Worker reports stage transition (transcribing → analyzing)."""
    stage: Literal["transcribing", "analyzing"]


class SpeakerOut(BaseModel):
    """Per-speaker payload inside JobComplete."""
    label: str = Field(..., pattern=r"^SPEAKER_(\d+|UNKNOWN)$")
    speaking_seconds: int | None = None
    name_suggestions: list[dict] = Field(default_factory=list)
    # name_suggestions: [{"name": str, "confidence": float, "evidence": str}]


class JobComplete(BaseModel):
    """Worker finished — full payload of results."""
    transcript_json: str
    transcript: str | None = None
    protocol: str | None = None
    tasks_json: str | None = None
    duration_seconds: int
    speakers: list[SpeakerOut] = Field(default_factory=list)


class JobFail(BaseModel):
    """Worker hit an error."""
    error_message: str = Field(..., min_length=1, max_length=2000)
    retriable: bool


# --- Response payloads from api2 → worker ---

class JobClaimResponse(BaseModel):
    """Worker received a job to process."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    meeting_id: uuid.UUID
    audio_local_path: str
    priority: str
    attempts: int
    expected_duration_seconds: int | None = None
    # Joined fields for the worker to work standalone:
    user_id: uuid.UUID
    prompt_text: str | None = None
    prompt_model: str | None = None

"""Internal API for faster-whisper server.

Authenticated via shared API key (FASTER_WHISPER_API_KEY),
not user JWT tokens.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.meeting import Meeting
from app.config import get_settings
from app.websocket_manager import ws_manager
from app.schemas.meeting import MeetingUpdate, MeetingStatusResponse

router = APIRouter(prefix="/internal", tags=["internal"])


async def verify_api_key(x_api_key: str = Header(...)):
    settings = get_settings()
    if x_api_key != settings.faster_whisper_api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


@router.put("/meetings/{meeting_id}", dependencies=[Depends(verify_api_key)])
async def update_meeting_from_whisper(
    meeting_id: uuid.UUID,
    body: MeetingUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Called by faster-whisper to save transcription results."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(meeting, field, value)
    await db.commit()
    await db.refresh(meeting)

    # Notify the user via WebSocket
    await ws_manager.notify_user(str(meeting.user_id), "meeting.updated", {
        "id": str(meeting.id),
        "status": meeting.status,
    })

    return {"status": "ok"}


@router.get("/meetings/{meeting_id}/status", dependencies=[Depends(verify_api_key)])
async def get_meeting_status_internal(
    meeting_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Called by faster-whisper to check meeting status."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return MeetingStatusResponse(
        meeting_id=m.id,
        status=m.status,
        title=m.title,
        has_transcript=bool(m.transcript),
        has_transcript_json=bool(m.transcript_json),
        has_protocol=bool(m.protocol),
        has_tasks=bool(m.tasks_json),
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get("/meetings/{meeting_id}/prompt", dependencies=[Depends(verify_api_key)])
async def get_meeting_prompt(
    meeting_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get the prompt associated with a meeting (for analysis)."""
    from app.models.prompt import Prompt

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if not meeting.prompt_id:
        # Return default prompt
        result = await db.execute(
            select(Prompt).where(Prompt.use_case == "meeting_protocol", Prompt.is_active == True).limit(1)
        )
        prompt = result.scalar_one_or_none()
    else:
        result = await db.execute(select(Prompt).where(Prompt.id == meeting.prompt_id))
        prompt = result.scalar_one_or_none()

    if not prompt:
        return {"prompt_text": None, "model": "deepseek-chat"}

    return {"prompt_text": prompt.prompt_text, "model": prompt.model}

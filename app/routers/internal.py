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
from app.models.user import User
from app.models.profile import Profile
from app.config import get_settings
from app.websocket_manager import ws_manager
from app.schemas.meeting import MeetingUpdate, MeetingStatusResponse, MeetingCreateInternal, MeetingDetail

router = APIRouter(prefix="/internal", tags=["internal"])

COMPLETED_STATUS = "completed"


async def verify_api_key(x_api_key: str = Header(...)):
    settings = get_settings()
    if x_api_key != settings.faster_whisper_api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


async def _charge_usage_if_first_completion(
    db: AsyncSession,
    meeting: Meeting,
    was_completed: bool,
) -> None:
    """When a meeting flips into 'completed' for the first time, bill its
    duration against the user's plan minutes. Idempotent — once the meeting
    is already 'completed', subsequent updates do nothing.

    Counter selection has to match the client's account screen, which
    discriminates by plan *name* containing 'бесплатный' rather than by
    presence of current_plan_id. Free users do have current_plan_id set
    (the "Бесплатный" plan is itself a Plan row with a 300-minute limit),
    so the older "is current_plan_id null?" check sent free users' minutes
    into the paid bucket and the UI showed zero usage.
    """
    if was_completed:
        return
    if meeting.status != COMPLETED_STATUS:
        return
    if not meeting.duration_seconds or meeting.duration_seconds <= 0:
        return

    profile_q = await db.execute(
        select(Profile).where(Profile.user_id == meeting.user_id)
    )
    profile = profile_q.scalar_one_or_none()
    if profile is None:
        return

    is_free = True
    if profile.current_plan_id is not None:
        from app.models.plan import Plan
        plan_q = await db.execute(
            select(Plan.name).where(Plan.id == profile.current_plan_id)
        )
        plan_name = plan_q.scalar_one_or_none()
        if plan_name is not None and "бесплатный" not in plan_name.lower():
            is_free = False

    minutes = (meeting.duration_seconds + 59) // 60
    if is_free:
        profile.free_minutes_used = (profile.free_minutes_used or 0) + minutes
    else:
        profile.paid_minutes_used_this_cycle = (
            profile.paid_minutes_used_this_cycle or 0
        ) + minutes


@router.post("/meetings", response_model=MeetingDetail, status_code=201, dependencies=[Depends(verify_api_key)])
async def create_meeting_internal(
    body: MeetingCreateInternal,
    db: AsyncSession = Depends(get_db),
):
    """Server-to-server meeting creation, used by faster-whisper as a safety net
    when the mobile client's auth died before it could register the meeting itself.

    Idempotent on `id`: repeated calls with the same UUID return the existing
    meeting. Returns 409 if the id exists for a different user (defends against
    misrouted calls).
    """
    user_result = await db.execute(select(User).where(User.id == body.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing_result = await db.execute(select(Meeting).where(Meeting.id == body.id))
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        if existing.user_id != body.user_id:
            raise HTTPException(status_code=409, detail="Meeting id already exists for another user")
        return existing

    meeting = Meeting(
        id=body.id,
        user_id=body.user_id,
        title=body.title,
        status=body.status,
        duration_seconds=body.duration_seconds,
        local_filename=body.local_filename,
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)

    await ws_manager.notify_user(str(body.user_id), "meeting.created", {"id": str(meeting.id)})
    return meeting


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

    was_completed = meeting.status == COMPLETED_STATUS
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(meeting, field, value)
    await _charge_usage_if_first_completion(db, meeting, was_completed)
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

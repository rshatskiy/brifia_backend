import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from app.database import get_db
from app.models.user import User
from app.models.meeting import Meeting
from app.auth import get_current_user
from app.schemas.meeting import (
    MeetingCreate, MeetingUpdate, MeetingListItem,
    MeetingDetail, MeetingTranscriptResponse, MeetingStatusResponse,
)
from app.websocket_manager import ws_manager

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])


@router.get("", response_model=list[MeetingListItem])
async def list_meetings(
    offset: int = Query(0, ge=0),
    limit: int = Query(15, ge=1, le=100),
    series_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(Meeting)
        .where(Meeting.user_id == user.id)
        .order_by(Meeting.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if series_id:
        q = q.where(Meeting.series_id == series_id)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=MeetingDetail, status_code=201)
async def create_meeting(
    body: MeetingCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Idempotent when the client supplies an id — repeated calls with the same
    # id return the existing meeting. Lets the mobile client retry registration
    # safely after network errors without creating duplicates.
    if body.id is not None:
        existing = await db.execute(
            select(Meeting).where(Meeting.id == body.id)
        )
        existing_meeting = existing.scalar_one_or_none()
        if existing_meeting is not None:
            if existing_meeting.user_id != user.id:
                raise HTTPException(status_code=409, detail="Meeting id already exists")
            return existing_meeting

    meeting = Meeting(user_id=user.id, **body.model_dump(exclude_unset=True))
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)

    await ws_manager.notify_user(str(user.id), "meeting.created", {"id": str(meeting.id)})
    return meeting


@router.get("/{meeting_id}", response_model=MeetingDetail)
async def get_meeting(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user.id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.get("/{meeting_id}/transcript", response_model=MeetingTranscriptResponse)
async def get_transcript(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Meeting.id, Meeting.transcript_json)
        .where(Meeting.id == meeting_id, Meeting.user_id == user.id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return MeetingTranscriptResponse(meeting_id=row[0], transcript_json=row[1])


@router.get("/{meeting_id}/status", response_model=MeetingStatusResponse)
async def get_meeting_status(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user.id)
    )
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


@router.put("/{meeting_id}", response_model=MeetingDetail)
async def update_meeting(
    meeting_id: uuid.UUID,
    body: MeetingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user.id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(meeting, field, value)
    await db.commit()
    await db.refresh(meeting)

    await ws_manager.notify_user(str(user.id), "meeting.updated", {
        "id": str(meeting.id),
        "status": meeting.status,
    })
    return meeting


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user.id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    await db.delete(meeting)
    await db.commit()

    await ws_manager.notify_user(str(user.id), "meeting.deleted", {"id": str(meeting_id)})

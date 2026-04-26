import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from app.database import get_db
from app.models.user import User
from app.models.meeting import Meeting
from app.models.participant import MeetingSpeaker, Participant
from app.auth import get_current_user
from app.routers.internal import _charge_usage_if_first_completion, COMPLETED_STATUS
from app.schemas.meeting import (
    MeetingCreate, MeetingUpdate, MeetingListItem,
    MeetingDetail, MeetingTranscriptResponse, MeetingStatusResponse,
    MeetingCountsResponse,
)
from app.schemas.participant import MeetingSpeakerOut, ParticipantOut
from app.websocket_manager import ws_manager

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])


@router.get("/counts", response_model=MeetingCountsResponse)
async def get_meeting_counts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Counters for the meetings list header and series tabs.

    Returns the user's total, the number of meetings without a series
    (the "Новые" tab), and per-series counts. Replaces a client-side
    sum-of-series approximation that always under-counted because
    meetings without a series were ignored.
    """
    total_q = await db.execute(
        select(func.count(Meeting.id)).where(Meeting.user_id == user.id)
    )
    total = total_q.scalar() or 0

    no_series_q = await db.execute(
        select(func.count(Meeting.id)).where(
            Meeting.user_id == user.id,
            Meeting.series_id.is_(None),
        )
    )
    no_series = no_series_q.scalar() or 0

    by_series_q = await db.execute(
        select(Meeting.series_id, func.count(Meeting.id))
        .where(Meeting.user_id == user.id, Meeting.series_id.is_not(None))
        .group_by(Meeting.series_id)
    )
    by_series = {str(sid): count for sid, count in by_series_q.all()}

    return MeetingCountsResponse(
        total=total,
        no_series=no_series,
        by_series=by_series,
    )


@router.get("", response_model=list[MeetingListItem])
async def list_meetings(
    offset: int = Query(0, ge=0),
    limit: int = Query(15, ge=1, le=100),
    series_id: uuid.UUID | None = None,
    no_series: bool = Query(False, description="Filter to meetings with no series (the 'Новые' tab)"),
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
    elif no_series:
        q = q.where(Meeting.series_id.is_(None))
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

    was_completed = meeting.status == COMPLETED_STATUS
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(meeting, field, value)
    # If a client manually flips a meeting into completed (rare — usually the
    # transcription pipeline does this via /internal), still charge the usage.
    await _charge_usage_if_first_completion(db, meeting, was_completed)
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


@router.get("/{meeting_id}/speakers", response_model=list[MeetingSpeakerOut])
async def list_meeting_speakers(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Authz check
    meeting_q = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user.id)
    )
    if meeting_q.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    rows = await db.execute(
        select(MeetingSpeaker, Participant)
        .outerjoin(Participant, Participant.id == MeetingSpeaker.participant_id)
        .where(MeetingSpeaker.meeting_id == meeting_id)
        .order_by(MeetingSpeaker.speaker_label.asc())
    )
    out = []
    for sp, p in rows.all():
        item = MeetingSpeakerOut(
            speaker_label=sp.speaker_label,
            participant=ParticipantOut.model_validate(p) if p is not None else None,
            speaking_seconds=sp.speaking_seconds,
            name_suggestions=sp.name_suggestions or [],
        )
        out.append(item)
    return out

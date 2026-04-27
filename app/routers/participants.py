"""Participants CRUD + speaker bindings.

Hybrid model from spec:
- Global pool per user (`participants`)
- Per-series association via `participant_series` (auto-tracked)
- Per-meeting binding to speakers (`meeting_speakers`)

GET ?series_id=X returns participants ordered by series presence
(meetings_count DESC, then everyone else by name ASC) so the mobile
client doesn't need to sort.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, literal, update as sa_update
from app.database import get_db
from app.models.user import User
from app.models.meeting import Meeting
from app.models.series import Series
from app.models.participant import Participant, ParticipantSeriesLink, MeetingSpeaker
from app.auth import get_current_user
from app.schemas.participant import (
    ParticipantOut, ParticipantCreate, ParticipantWithMeetings,
    ParticipantUpdate, ParticipantMerge,
)
from app.websocket_manager import ws_manager

router = APIRouter(prefix="/api/v1/participants", tags=["participants"])


@router.get("", response_model=list[ParticipantOut])
async def list_participants(
    series_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, min_length=1, max_length=255),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's participants.

    With series_id: participants in that series first (sorted by meetings_count
    DESC), then the rest by name. With q: ILIKE filter on name applied on top.
    """
    if series_id is not None:
        # LEFT JOIN to participant_series so we can sort by presence in this series
        stmt = (
            select(Participant, ParticipantSeriesLink.meetings_count, ParticipantSeriesLink.first_seen_at)
            .outerjoin(
                ParticipantSeriesLink,
                (ParticipantSeriesLink.participant_id == Participant.id)
                & (ParticipantSeriesLink.series_id == series_id),
            )
            .where(Participant.user_id == user.id)
            .order_by(
                # In-series first (meetings_count NOT NULL ranks higher)
                ParticipantSeriesLink.meetings_count.desc().nullslast(),
                Participant.name.asc(),
            )
        )
    else:
        # No series filter — return literal nulls for the JOIN columns to keep
        # the result-row shape (Participant, mcount, lseen) consistent.
        stmt = (
            select(Participant, literal(None).label("mcount"), literal(None).label("lseen"))
            .where(Participant.user_id == user.id)
            .order_by(Participant.name.asc())
        )

    if q:
        stmt = stmt.where(Participant.name.ilike(f"%{q}%"))

    result = await db.execute(stmt)
    rows = result.all()
    out = []
    for p, mcount, lseen in rows:
        item = ParticipantOut.model_validate(p)
        if series_id is not None:
            item.meetings_in_series = int(mcount) if mcount is not None else 0
            item.last_seen_in_series = lseen
        out.append(item)
    return out


@router.post("", response_model=ParticipantOut, status_code=201)
async def create_participant(
    body: ParticipantCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = Participant(
        user_id=user.id,
        name=body.name.strip(),
        email=body.email,
        phone=body.phone,
        role=body.role,
        note=body.note,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    await ws_manager.notify_user(str(user.id), "participant.created", {"id": str(p.id), "name": p.name})
    return p


@router.get("/{participant_id}", response_model=ParticipantWithMeetings)
async def get_participant(
    participant_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p_q = await db.execute(
        select(Participant).where(Participant.id == participant_id, Participant.user_id == user.id)
    )
    p = p_q.scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Participant not found")

    # Recent 10 meetings via meeting_speakers join
    recent_q = await db.execute(
        select(Meeting.id, Meeting.title, Meeting.created_at)
        .join(MeetingSpeaker, MeetingSpeaker.meeting_id == Meeting.id)
        .where(MeetingSpeaker.participant_id == participant_id)
        .order_by(Meeting.created_at.desc())
        .limit(10)
    )
    recent = [{"id": str(mid), "title": title, "created_at": ca} for mid, title, ca in recent_q.all()]

    # Series list via participant_series join
    series_q = await db.execute(
        select(Series.id, Series.name, ParticipantSeriesLink.meetings_count)
        .join(ParticipantSeriesLink, ParticipantSeriesLink.series_id == Series.id)
        .where(ParticipantSeriesLink.participant_id == participant_id)
        .order_by(ParticipantSeriesLink.meetings_count.desc())
    )
    series_list = [
        {"id": str(sid), "title": name, "meetings_count": mc}
        for sid, name, mc in series_q.all()
    ]

    out = ParticipantWithMeetings.model_validate(p)
    out.recent_meetings = recent
    out.series = series_list
    return out


@router.patch("/{participant_id}", response_model=ParticipantOut)
async def update_participant(
    participant_id: uuid.UUID,
    body: ParticipantUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p_q = await db.execute(
        select(Participant).where(Participant.id == participant_id, Participant.user_id == user.id)
    )
    p = p_q.scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Participant not found")

    payload = body.model_dump(exclude_unset=True)
    if "name" in payload:
        payload["name"] = payload["name"].strip()
        if not payload["name"]:
            raise HTTPException(status_code=422, detail="name cannot be empty")
    for k, v in payload.items():
        setattr(p, k, v)
    await db.commit()
    await db.refresh(p)
    await ws_manager.notify_user(str(user.id), "participant.updated", {"id": str(p.id)})
    return p


@router.delete("/{participant_id}", status_code=204)
async def delete_participant(
    participant_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p_q = await db.execute(
        select(Participant).where(Participant.id == participant_id, Participant.user_id == user.id)
    )
    p = p_q.scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Participant not found")
    await db.delete(p)  # cascades on participant_series, SET NULL on meeting_speakers
    await db.commit()
    await ws_manager.notify_user(str(user.id), "participant.deleted", {"id": str(participant_id)})
    return None


@router.post("/{participant_id}/merge", response_model=ParticipantOut)
async def merge_participants(
    participant_id: uuid.UUID,
    body: ParticipantMerge,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Merge `body.absorb_id` into `participant_id`.

    The absorb_id participant is deleted, its meeting_speaker bindings and
    series associations are reassigned to participant_id. Atomic — single
    transaction.
    """
    if participant_id == body.absorb_id:
        raise HTTPException(status_code=400, detail="Cannot merge a participant into itself")

    # Verify both belong to current user
    keep_q = await db.execute(
        select(Participant).where(Participant.id == participant_id, Participant.user_id == user.id)
    )
    keep = keep_q.scalar_one_or_none()
    absorb_q = await db.execute(
        select(Participant).where(Participant.id == body.absorb_id, Participant.user_id == user.id)
    )
    absorb = absorb_q.scalar_one_or_none()
    if keep is None or absorb is None:
        raise HTTPException(status_code=404, detail="One or both participants not found")

    # Reassign meeting_speakers
    await db.execute(
        sa_update(MeetingSpeaker)
        .where(MeetingSpeaker.participant_id == body.absorb_id)
        .values(participant_id=participant_id)
    )

    # Merge participant_series — sum meetings_count for overlapping series, copy others
    absorb_links_q = await db.execute(
        select(ParticipantSeriesLink).where(ParticipantSeriesLink.participant_id == body.absorb_id)
    )
    absorb_links = absorb_links_q.scalars().all()
    for link in absorb_links:
        existing_q = await db.execute(
            select(ParticipantSeriesLink).where(
                ParticipantSeriesLink.participant_id == participant_id,
                ParticipantSeriesLink.series_id == link.series_id,
            )
        )
        existing = existing_q.scalar_one_or_none()
        if existing is not None:
            existing.meetings_count += link.meetings_count
            if link.first_seen_at < existing.first_seen_at:
                existing.first_seen_at = link.first_seen_at
            await db.delete(link)
        else:
            # PK is composite (participant_id, series_id) — to "transfer" a
            # link, delete the absorb-side row and insert a fresh one for keep-side.
            db.add(ParticipantSeriesLink(
                participant_id=participant_id,
                series_id=link.series_id,
                first_seen_at=link.first_seen_at,
                meetings_count=link.meetings_count,
            ))
            await db.delete(link)

    await db.delete(absorb)
    await db.commit()
    await db.refresh(keep)
    await ws_manager.notify_user(str(user.id), "participant.merged", {
        "kept_id": str(participant_id),
        "absorbed_id": str(body.absorb_id),
    })
    return keep

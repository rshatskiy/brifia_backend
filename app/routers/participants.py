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
from sqlalchemy import select, literal
from app.database import get_db
from app.models.user import User
from app.models.participant import Participant, ParticipantSeriesLink
from app.auth import get_current_user
from app.schemas.participant import ParticipantOut

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

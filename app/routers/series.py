import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.user import User
from app.models.series import Series
from app.models.meeting import Meeting
from app.auth import get_current_user
from app.schemas.series import SeriesCreate, SeriesUpdate, SeriesResponse

router = APIRouter(prefix="/api/v1/series", tags=["series"])


async def _enrich_with_count(series_list: list[Series], user_id: uuid.UUID, db: AsyncSession) -> list[dict]:
    series_ids = [s.id for s in series_list]
    if not series_ids:
        return []

    counts_q = (
        select(Meeting.series_id, func.count(Meeting.id))
        .where(Meeting.series_id.in_(series_ids), Meeting.user_id == user_id)
        .group_by(Meeting.series_id)
    )
    counts_result = await db.execute(counts_q)
    count_map = dict(counts_result.all())

    result = []
    for s in series_list:
        data = SeriesResponse.model_validate(s).model_dump()
        data["meeting_count"] = count_map.get(s.id, 0)
        result.append(data)
    return result


@router.get("", response_model=list[SeriesResponse])
async def list_series(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Series)
        .where(Series.user_id == user.id, Series.is_archived == False)
        .order_by(Series.sort_order, Series.created_at)
    )
    series_list = result.scalars().all()
    return await _enrich_with_count(series_list, user.id, db)


@router.post("", response_model=SeriesResponse, status_code=201)
async def create_series(
    body: SeriesCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    series = Series(user_id=user.id, **body.model_dump())
    db.add(series)
    await db.commit()
    await db.refresh(series)
    resp = SeriesResponse.model_validate(series).model_dump()
    resp["meeting_count"] = 0
    return resp


@router.put("/{series_id}", response_model=SeriesResponse)
async def update_series(
    series_id: uuid.UUID,
    body: SeriesUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Series).where(Series.id == series_id, Series.user_id == user.id)
    )
    series = result.scalar_one_or_none()
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")

    update_data = body.model_dump(exclude_unset=True)
    if "is_archived" in update_data and update_data["is_archived"]:
        update_data["archived_at"] = datetime.now(timezone.utc)

    for field, value in update_data.items():
        setattr(series, field, value)
    await db.commit()
    await db.refresh(series)

    enriched = await _enrich_with_count([series], user.id, db)
    return enriched[0]


@router.delete("/{series_id}", status_code=204)
async def delete_series(
    series_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Series).where(Series.id == series_id, Series.user_id == user.id)
    )
    series = result.scalar_one_or_none()
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    await db.delete(series)
    await db.commit()

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.models.profile import Profile
from app.models.plan import Plan
from app.auth import get_current_user
from app.schemas.profile import ProfileUpdate, ProfileResponse, AccountUsageResponse

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])


@router.get("/me", response_model=AccountUsageResponse)
async def get_my_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    plan_name = "Бесплатный"
    plan_minutes_limit = None
    plan_price_rub = None

    if profile.current_plan_id:
        plan_result = await db.execute(select(Plan).where(Plan.id == profile.current_plan_id))
        plan = plan_result.scalar_one_or_none()
        if plan:
            plan_name = plan.name
            plan_minutes_limit = plan.minutes_limit
            plan_price_rub = float(plan.price_rub)

    return AccountUsageResponse(
        profile=ProfileResponse.model_validate(profile),
        plan_name=plan_name,
        plan_minutes_limit=plan_minutes_limit,
        plan_price_rub=plan_price_rub,
    )


@router.put("/me", response_model=ProfileResponse)
async def update_my_profile(
    body: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile

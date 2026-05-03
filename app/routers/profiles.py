from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.user import User
from app.models.profile import Profile
from app.models.plan import Plan
from app.auth import get_current_user
from app.routers.internal import reset_free_cycle_if_needed
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
    is_free_plan = True
    plan = None
    needs_commit = False

    if profile.current_plan_id:
        plan_result = await db.execute(select(Plan).where(Plan.id == profile.current_plan_id))
        plan = plan_result.scalar_one_or_none()

    # Fallback for legacy/half-migrated profiles that have current_plan_id NULL.
    # The rest of the system assumes every user is on the free plan by default;
    # without this fallback the mobile UI computes "0 of 0 minutes" and the
    # account screen looks broken (see internal.py charging notes).
    if plan is None:
        free_plan_result = await db.execute(
            select(Plan)
            .where(func.lower(Plan.name).like("%бесплат%"))
            .where(Plan.active.is_(True))
            .order_by(Plan.price_rub.asc())
            .limit(1)
        )
        plan = free_plan_result.scalar_one_or_none()
        if plan is not None and profile.current_plan_id is None:
            # Self-heal: persist the default so future reads (and minute
            # charging) stop relying on the fallback.
            profile.current_plan_id = plan.id
            needs_commit = True

    if plan is not None:
        plan_name = plan.name
        plan_minutes_limit = plan.minutes_limit
        plan_price_rub = float(plan.price_rub)
        if "бесплатный" not in plan.name.lower():
            is_free_plan = False

    # Lazy monthly reset for free users so the UI never shows a stale
    # "60/60 used" right after a calendar month boundary even if the user
    # hasn't recorded yet this month.
    if is_free_plan and reset_free_cycle_if_needed(profile):
        needs_commit = True

    if needs_commit:
        await db.commit()
        await db.refresh(profile)

    return AccountUsageResponse(
        profile=ProfileResponse.model_validate(profile),
        email=user.email,
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

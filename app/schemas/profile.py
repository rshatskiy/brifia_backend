from pydantic import BaseModel
from datetime import datetime
from uuid import UUID


class ProfileUpdate(BaseModel):
    full_name: str | None = None
    company_name: str | None = None
    position: str | None = None
    avatar_url: str | None = None


class ProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    full_name: str | None
    company_name: str | None
    position: str | None
    avatar_url: str | None
    current_plan_id: UUID | None
    subscription_active_until: datetime | None
    free_minutes_used: int
    paid_minutes_used_this_cycle: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AccountUsageResponse(BaseModel):
    profile: ProfileResponse
    plan_name: str
    plan_minutes_limit: int | None
    plan_price_rub: float | None

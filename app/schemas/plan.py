from pydantic import BaseModel
from uuid import UUID


class PlanResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    minutes_limit: int | None
    price_rub: float
    duration_days: int
    active: bool

    model_config = {"from_attributes": True}

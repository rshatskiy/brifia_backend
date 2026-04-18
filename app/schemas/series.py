from pydantic import BaseModel
from datetime import datetime
from uuid import UUID


class SeriesCreate(BaseModel):
    name: str
    description: str | None = None
    color: str = "#3B82F6"
    icon: str = "\U0001F4C1"


class SeriesUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    is_archived: bool | None = None
    sort_order: int | None = None


class SeriesResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    color: str
    icon: str
    is_archived: bool
    sort_order: int
    meeting_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

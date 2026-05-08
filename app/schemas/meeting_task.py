"""Pydantic schemas for /api/v1/tasks endpoints."""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


TaskStatus = Literal["open", "done", "cancelled"]
TaskPriority = Literal["high", "medium", "low"]


class TaskOut(BaseModel):
    """Single task as returned by GET endpoints."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    meeting_id: uuid.UUID
    title: str
    description: str | None = None
    status: TaskStatus
    priority: TaskPriority | None = None
    due_date: datetime | None = None
    assignee_participant_id: uuid.UUID | None = None
    bitrix_task_id: str | None = None
    position: int
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    """Body for POST /meetings/{meeting_id}/tasks — creating a task by hand
    (in addition to LLM-extracted ones)."""
    title: str = Field(..., min_length=1, max_length=1024)
    description: str | None = None
    status: TaskStatus = "open"
    priority: TaskPriority | None = None
    due_date: datetime | None = None
    assignee_participant_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    """Body for PATCH /tasks/{task_id} — partial update; null due_date or
    null assignee_participant_id explicitly unset (so distinguish from
    "not present" via model_dump(exclude_unset=True) on the route)."""
    title: str | None = Field(default=None, min_length=1, max_length=1024)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: datetime | None = None
    assignee_participant_id: uuid.UUID | None = None
    # Set when the client successfully exports to Bitrix24 — stored so
    # future inbound webhook can map back to the local task.
    bitrix_task_id: str | None = None

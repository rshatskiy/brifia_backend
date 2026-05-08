"""CRUD for first-class meeting tasks.

Two route trees:
- /api/v1/meetings/{meeting_id}/tasks — list + create
- /api/v1/tasks/{task_id} — get + update + delete

Plus a global filter endpoint /api/v1/tasks for cross-meeting views like
"My open tasks" and "Overdue this week" — bare minimum for the dashboard
and "Living tasks" UI.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, asc, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.meeting import Meeting
from app.models.meeting_task import MeetingTask
from app.models.participant import Participant
from app.auth import get_current_user
from app.schemas.meeting_task import TaskOut, TaskCreate, TaskUpdate
from app.websocket_manager import ws_manager


# Mounted twice — once per meeting (nested resource) and once standalone
# (for cross-meeting filtering and direct id lookup).
meeting_tasks_router = APIRouter(prefix="/api/v1/meetings", tags=["tasks"])
tasks_router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


async def _verify_meeting_owned(
    db: AsyncSession, meeting_id: uuid.UUID, user_id: uuid.UUID
) -> Meeting:
    q = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
    )
    meeting = q.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


async def _load_task_owned(
    db: AsyncSession, task_id: uuid.UUID, user_id: uuid.UUID
) -> MeetingTask:
    """Load task and verify it belongs to the current user via the
    meeting → user_id relation."""
    q = await db.execute(
        select(MeetingTask, Meeting)
        .join(Meeting, Meeting.id == MeetingTask.meeting_id)
        .where(MeetingTask.id == task_id, Meeting.user_id == user_id)
    )
    row = q.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return row[0]


@meeting_tasks_router.get(
    "/{meeting_id}/tasks", response_model=list[TaskOut]
)
async def list_meeting_tasks(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """All tasks of a single meeting, ordered by position then created_at.
    Open tasks first, then done/cancelled at the bottom."""
    await _verify_meeting_owned(db, meeting_id, user.id)
    rows = await db.execute(
        select(MeetingTask)
        .where(MeetingTask.meeting_id == meeting_id)
        .order_by(
            # Open at the top, done/cancelled at bottom (CASE expression)
            (MeetingTask.status == "open").desc(),
            asc(MeetingTask.position),
            asc(MeetingTask.created_at),
        )
    )
    return [TaskOut.model_validate(t) for t in rows.scalars().all()]


@meeting_tasks_router.post(
    "/{meeting_id}/tasks", response_model=TaskOut, status_code=201
)
async def create_meeting_task(
    meeting_id: uuid.UUID,
    body: TaskCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually create a task on this meeting (in addition to LLM-extracted
    ones). New task goes to the end of the list."""
    await _verify_meeting_owned(db, meeting_id, user.id)

    # Validate assignee belongs to this user
    if body.assignee_participant_id is not None:
        await _verify_participant_owned(db, body.assignee_participant_id, user.id)

    # Append: position = max(existing) + 1
    max_pos_q = await db.execute(
        select(MeetingTask.position)
        .where(MeetingTask.meeting_id == meeting_id)
        .order_by(desc(MeetingTask.position))
        .limit(1)
    )
    max_pos = max_pos_q.scalar()
    next_position = (max_pos + 1) if max_pos is not None else 0

    task = MeetingTask(
        meeting_id=meeting_id,
        title=body.title.strip(),
        description=body.description,
        status=body.status,
        priority=body.priority,
        due_date=body.due_date,
        assignee_participant_id=body.assignee_participant_id,
        position=next_position,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    await ws_manager.notify_user(str(user.id), "task.created", {
        "meeting_id": str(meeting_id),
        "task_id": str(task.id),
    })
    return TaskOut.model_validate(task)


@tasks_router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = await _load_task_owned(db, task_id, user.id)
    return TaskOut.model_validate(task)


@tasks_router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    body: TaskUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partial update. Pass null for due_date / assignee_participant_id
    to explicitly unset them. Fields not present in the body are left
    untouched (Pydantic exclude_unset)."""
    task = await _load_task_owned(db, task_id, user.id)
    payload = body.model_dump(exclude_unset=True)

    # Validate assignee if changing
    if "assignee_participant_id" in payload and payload["assignee_participant_id"] is not None:
        await _verify_participant_owned(db, payload["assignee_participant_id"], user.id)

    if "title" in payload:
        title = (payload["title"] or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="title cannot be empty")
        task.title = title
    if "description" in payload:
        task.description = payload["description"]
    if "status" in payload and payload["status"] is not None:
        task.status = payload["status"]
    if "priority" in payload:
        task.priority = payload["priority"]
    if "due_date" in payload:
        task.due_date = payload["due_date"]
    if "assignee_participant_id" in payload:
        task.assignee_participant_id = payload["assignee_participant_id"]
    if "bitrix_task_id" in payload:
        task.bitrix_task_id = payload["bitrix_task_id"]

    await db.commit()
    await db.refresh(task)
    await ws_manager.notify_user(str(user.id), "task.updated", {
        "meeting_id": str(task.meeting_id),
        "task_id": str(task.id),
    })
    return TaskOut.model_validate(task)


@tasks_router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = await _load_task_owned(db, task_id, user.id)
    meeting_id = task.meeting_id
    await db.delete(task)
    await db.commit()
    await ws_manager.notify_user(str(user.id), "task.deleted", {
        "meeting_id": str(meeting_id),
        "task_id": str(task_id),
    })
    return None


@tasks_router.get("", response_model=list[TaskOut])
async def list_user_tasks(
    status: Literal["open", "done", "cancelled", "all"] = "open",
    overdue: bool = False,
    assignee_participant_id: uuid.UUID | None = None,
    series_id: uuid.UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cross-meeting task listing for dashboard / "My open" / "Overdue"
    views. Always scoped to current user via meetings.user_id."""
    stmt = (
        select(MeetingTask)
        .join(Meeting, Meeting.id == MeetingTask.meeting_id)
        .where(Meeting.user_id == user.id)
    )
    if status != "all":
        stmt = stmt.where(MeetingTask.status == status)
    if overdue:
        stmt = stmt.where(
            and_(
                MeetingTask.due_date.is_not(None),
                MeetingTask.due_date < datetime.now(timezone.utc),
                MeetingTask.status == "open",
            )
        )
    if assignee_participant_id is not None:
        stmt = stmt.where(
            MeetingTask.assignee_participant_id == assignee_participant_id
        )
    if series_id is not None:
        stmt = stmt.where(Meeting.series_id == series_id)

    # Overdue first by deadline, then open by deadline asc, then by created_at
    stmt = stmt.order_by(
        MeetingTask.due_date.asc().nullslast(),
        MeetingTask.created_at.desc(),
    ).limit(limit)
    rows = await db.execute(stmt)
    return [TaskOut.model_validate(t) for t in rows.scalars().all()]


async def _verify_participant_owned(
    db: AsyncSession, participant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    q = await db.execute(
        select(Participant.id).where(
            Participant.id == participant_id, Participant.user_id == user_id
        )
    )
    if q.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Participant not found")

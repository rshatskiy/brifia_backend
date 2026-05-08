import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


VALID_STATUSES = ("open", "done", "cancelled")


class MeetingTask(Base):
    """First-class task extracted from a meeting. Replaces ad-hoc storage
    in meetings.tasks_json — workers still emit tasks_json for backwards
    compatibility, /jobs/complete fans them out into rows here.

    Status lifecycle: open → done (or cancelled). Living-task features
    (due_date, assignee, dashboard filters) all hang off this row.
    """
    __tablename__ = "meeting_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 'open' | 'done' | 'cancelled' — enforced by check constraint
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assignee_participant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("participants.id", ondelete="SET NULL"), nullable=True
    )

    # Set when client successfully exports to Bitrix24. Needed for future
    # inbound webhook synchronization (out of MVP scope, but stored now so
    # we don't have to migrate later).
    bitrix_task_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # LLM-emitted urgency. 'high' / 'medium' / 'low' or null for legacy rows.
    # Used for visual badges in exports + sortable filters in dashboards.
    priority: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # Preserves order from the LLM-extracted tasks array
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'done', 'cancelled')",
            name="ck_meeting_tasks_status",
        ),
        CheckConstraint(
            "priority IS NULL OR priority IN ('high', 'medium', 'low')",
            name="ck_meeting_tasks_priority",
        ),
    )

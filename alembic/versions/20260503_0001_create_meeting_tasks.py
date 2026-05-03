"""create meeting_tasks table — first-class living tasks

Replaces ad-hoc storage of tasks in meetings.tasks_json with a proper
relational table. Workers continue to write tasks_json (no worker change
needed for MVP); the /jobs/complete handler fans them out into rows here.

Backfills existing tasks_json content into the new table so the API can
serve from a single source of truth from day one.

New schema:
- id (UUID PK)
- meeting_id (UUID FK to meetings, cascade delete)
- title, description (text)
- status: 'open' | 'done' | 'cancelled'
- due_date (timestamptz, nullable)
- assignee_participant_id (UUID FK to participants, SET NULL on delete)
- bitrix_task_id (text, nullable) — populated when client successfully
  exports to Bitrix; needed for future inbound webhook synchronization
- position (int) — preserves order from the LLM-extracted tasks_json array
- created_at / updated_at

Indexes for the views the dashboard ("My open tasks", "Overdue this week")
will eventually query.

Revision ID: 20260503_0001
Revises: 20260502_0002
Create Date: 2026-05-03
"""
import json
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260503_0001"
down_revision = "20260502_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="open",
        ),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "assignee_participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("bitrix_task_id", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done', 'cancelled')",
            name="ck_meeting_tasks_status",
        ),
    )

    # Indexes for common access paths
    op.create_index(
        "ix_meeting_tasks_meeting_id",
        "meeting_tasks",
        ["meeting_id", "position"],
    )
    op.create_index(
        "ix_meeting_tasks_assignee_status_due",
        "meeting_tasks",
        ["assignee_participant_id", "status", "due_date"],
        postgresql_where=sa.text("status = 'open'"),
    )

    # Backfill — read every meetings.tasks_json and create rows. Default
    # status='open', no due_date, no assignee. Title and description copied
    # verbatim. Position from the array index.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, tasks_json FROM meetings WHERE tasks_json IS NOT NULL AND tasks_json != ''")
    ).fetchall()
    now = datetime.now(timezone.utc)
    inserted = 0
    for meeting_id, tasks_json in rows:
        try:
            data = json.loads(tasks_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, list):
            continue
        for position, task in enumerate(data):
            if not isinstance(task, dict):
                continue
            title = (task.get("title") or "").strip()
            if not title:
                continue
            description = task.get("description")
            # Old JSON shape stored isCompleted as a soft 'done' marker
            status = "done" if task.get("isCompleted") else "open"
            bind.execute(
                sa.text("""
                    INSERT INTO meeting_tasks (
                        id, meeting_id, title, description, status, position,
                        created_at, updated_at
                    ) VALUES (
                        :id, :meeting_id, :title, :description, :status, :position,
                        :created_at, :updated_at
                    )
                """),
                {
                    "id": uuid.uuid4(),
                    "meeting_id": meeting_id,
                    "title": title[:1024],
                    "description": description,
                    "status": status,
                    "position": position,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            inserted += 1

    print(f"  -> backfilled {inserted} tasks from {len(rows)} meetings")


def downgrade() -> None:
    op.drop_index("ix_meeting_tasks_assignee_status_due", table_name="meeting_tasks")
    op.drop_index("ix_meeting_tasks_meeting_id", table_name="meeting_tasks")
    op.drop_table("meeting_tasks")

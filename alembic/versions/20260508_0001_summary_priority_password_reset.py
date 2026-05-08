"""add Meeting.summary, MeetingTask.priority, password_reset_tokens

Three changes bundled because they're all tied to the same release:

1. `meetings.summary` — text column. Filled by DeepSeek alongside the
   protocol; rendered on PDF/DOCX cover as a 2-3 sentence executive
   summary. Nullable for backward compatibility — existing meetings
   stay without one until re-analysis.

2. `meeting_tasks.priority` — varchar(8) with CHECK constraint
   ('high'|'medium'|'low' OR NULL). Driven by LLM judgment; used to
   render urgency badges in the tasks table.

3. `password_reset_tokens` — already created on prod via SQLAlchemy
   `metadata.create_all()` at startup, but recorded here so a fresh
   bring-up from migrations gets the same schema. Wrapped in a
   table-existence check so re-running on prod is a no-op.

Revision ID: 20260508_0001
Revises: 20260503_0001
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "20260508_0001"
down_revision = "20260503_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. meetings.summary
    op.add_column(
        "meetings",
        sa.Column("summary", sa.Text(), nullable=True),
    )

    # 2. meeting_tasks.priority + check constraint
    op.add_column(
        "meeting_tasks",
        sa.Column("priority", sa.String(length=8), nullable=True),
    )
    op.create_check_constraint(
        "ck_meeting_tasks_priority",
        "meeting_tasks",
        "priority IS NULL OR priority IN ('high', 'medium', 'low')",
    )

    # 3. password_reset_tokens — only create if not already present
    # (prod has it from SQLAlchemy create_all on startup).
    bind = op.get_bind()
    if "password_reset_tokens" not in inspect(bind).get_table_names():
        op.create_table(
            "password_reset_tokens",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "token_hash", sa.String(length=128), unique=True, nullable=False
            ),
            sa.Column(
                "expires_at", sa.DateTime(timezone=True), nullable=False
            ),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_password_reset_tokens_user_id",
            "password_reset_tokens",
            ["user_id"],
        )
        op.create_index(
            "ix_password_reset_tokens_token_hash",
            "password_reset_tokens",
            ["token_hash"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "password_reset_tokens" in inspect(bind).get_table_names():
        op.drop_index("ix_password_reset_tokens_token_hash", "password_reset_tokens")
        op.drop_index("ix_password_reset_tokens_user_id", "password_reset_tokens")
        op.drop_table("password_reset_tokens")

    op.drop_constraint(
        "ck_meeting_tasks_priority", "meeting_tasks", type_="check"
    )
    op.drop_column("meeting_tasks", "priority")
    op.drop_column("meetings", "summary")

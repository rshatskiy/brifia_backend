"""create processing_jobs

Revision ID: 20260426_0002
Revises: 20260426_0001
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260426_0002"
down_revision = "20260426_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="realtime"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("audio_local_path", sa.String(length=1024), nullable=False),
        sa.Column("expected_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_processing_jobs_meeting_id", "processing_jobs", ["meeting_id"])
    op.create_index(
        "ix_processing_jobs_pending_lookup",
        "processing_jobs",
        ["status", "priority", "created_at"],
    )
    op.create_index("ix_processing_jobs_claimed_lookup", "processing_jobs", ["claimed_by", "status"])


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_claimed_lookup", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_pending_lookup", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_meeting_id", table_name="processing_jobs")
    op.drop_table("processing_jobs")

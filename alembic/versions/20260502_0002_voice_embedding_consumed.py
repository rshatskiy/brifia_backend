"""voice embedding transient delivery — consumed_at + drop server profile table

Per legal review (152-FZ biometrics), the canonical store of voice
fingerprints moves to user device. Server keeps embedding only as a
short-lived delivery channel:

- meeting_speakers.embedding stays as the transport column
- meeting_speakers.embedding_consumed_at marks when client ACK'd receipt;
  cron prunes any embedding still set 24h after the meeting completed,
  whether or not it was consumed (defense in depth)
- participant_voice_profile is dropped — server never stores aggregated
  fingerprints again

Revision ID: 20260502_0002
Revises: 20260502_0001
Create Date: 2026-05-02
"""
from alembic import op
import sqlalchemy as sa


revision = "20260502_0002"
down_revision = "20260502_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_speakers",
        sa.Column("embedding_consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Index supports the cron cleanup query (WHERE embedding IS NOT NULL
    # AND (embedding_consumed_at IS NOT NULL OR meeting_speakers.created_at < threshold))
    op.create_index(
        "ix_meeting_speakers_embedding_cleanup",
        "meeting_speakers",
        ["embedding_consumed_at", "created_at"],
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )
    op.drop_table("participant_voice_profile")


def downgrade() -> None:
    op.create_table(
        "participant_voice_profile",
        sa.Column(
            "participant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("embedding", sa.dialects.postgresql.ARRAY(sa.REAL()), nullable=False),
        sa.Column("samples_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.drop_index("ix_meeting_speakers_embedding_cleanup", table_name="meeting_speakers")
    op.drop_column("meeting_speakers", "embedding_consumed_at")

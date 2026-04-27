"""create participants tables + pg_trgm

Revision ID: 20260426_0003
Revises: 20260426_0002
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260426_0003"
down_revision = "20260426_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # btree_gin lets us combine btree (user_id UUID) with gin_trgm (name)
    # in a single index — needed for the ix_participants_name_trgm GIN below.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")

    op.create_table(
        "participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("trim(name) != ''", name="ck_participants_name_not_empty"),
    )
    op.create_index("ix_participants_user_id_name", "participants", ["user_id", "name"])
    op.execute(
        "CREATE INDEX ix_participants_name_trgm ON participants USING gin (user_id, name gin_trgm_ops)"
    )

    op.create_table(
        "participant_series",
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("meetings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("participant_id", "series_id"),
    )
    op.create_index(
        "ix_participant_series_lookup", "participant_series",
        ["series_id", sa.text("meetings_count DESC")],
    )

    op.create_table(
        "meeting_speakers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("speaker_label", sa.String(length=32), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("speaking_seconds", sa.Integer(), nullable=True),
        sa.Column("name_suggestions", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("meeting_id", "speaker_label", name="uq_meeting_speakers_label"),
    )
    op.create_index("ix_meeting_speakers_meeting_id", "meeting_speakers", ["meeting_id"])
    op.create_index("ix_meeting_speakers_participant_id", "meeting_speakers", ["participant_id"])


def downgrade() -> None:
    op.drop_index("ix_meeting_speakers_participant_id", table_name="meeting_speakers")
    op.drop_index("ix_meeting_speakers_meeting_id", table_name="meeting_speakers")
    op.drop_table("meeting_speakers")
    op.drop_index("ix_participant_series_lookup", table_name="participant_series")
    op.drop_table("participant_series")
    op.execute("DROP INDEX IF EXISTS ix_participants_name_trgm")
    op.drop_index("ix_participants_user_id_name", table_name="participants")
    op.drop_table("participants")
    # Don't drop pg_trgm — may be used by other parts later

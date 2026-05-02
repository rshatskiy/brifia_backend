"""add voice profile infrastructure

Adds:
- meeting_speakers.embedding (FLOAT4[]) — per-meeting per-speaker centroid
  from pyannote community-1 (wespeaker ResNet34, 128-dim, L2-normed).
- participant_voice_profile table — aggregated per-participant embedding,
  updated via running mean on every speaker→participant binding.

Revision ID: 20260502_0001
Revises: 20260501_0001
Create Date: 2026-05-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260502_0001"
down_revision = "20260501_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_speakers",
        sa.Column("embedding", postgresql.ARRAY(sa.REAL()), nullable=True),
    )
    op.create_table(
        "participant_voice_profile",
        sa.Column(
            "participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("embedding", postgresql.ARRAY(sa.REAL()), nullable=False),
        sa.Column("samples_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("participant_voice_profile")
    op.drop_column("meeting_speakers", "embedding")

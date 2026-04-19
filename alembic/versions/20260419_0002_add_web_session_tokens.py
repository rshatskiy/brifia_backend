"""add web_session_tokens

Revision ID: 20260419_0002
Revises: 20260419_0001
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260419_0002"
down_revision = "20260419_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_session_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ip_created", postgresql.INET(), nullable=True),
        sa.Column("ip_used", postgresql.INET(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_web_session_tokens_token_hash"),
    )
    op.create_index("ix_web_session_tokens_user_id", "web_session_tokens", ["user_id"])
    op.create_index("ix_web_session_tokens_expires_at", "web_session_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_web_session_tokens_expires_at", table_name="web_session_tokens")
    op.drop_index("ix_web_session_tokens_user_id", table_name="web_session_tokens")
    op.drop_table("web_session_tokens")

"""create bitrix_integrations table

Revision ID: 20260501_0001
Revises: 20260426_0003
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260501_0001"
down_revision = "20260426_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bitrix_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("portal_url", sa.String(length=256), nullable=False),
        sa.Column("bitrix_user_id", sa.String(length=64), nullable=True),
        sa.Column("member_id", sa.String(length=128), nullable=True),
        sa.Column("access_token", sa.String(length=512), nullable=False),
        sa.Column("refresh_token", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "portal_url", name="uq_bitrix_user_portal"),
    )
    op.create_index("ix_bitrix_integrations_user_id", "bitrix_integrations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_bitrix_integrations_user_id", table_name="bitrix_integrations")
    op.drop_table("bitrix_integrations")

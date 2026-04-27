"""add meeting status fields

Revision ID: 20260426_0001
Revises: 20260425_0001
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa


revision = "20260426_0001"
down_revision = "20260425_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "meetings",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meetings", "processing_started_at")
    op.drop_column("meetings", "error_message")

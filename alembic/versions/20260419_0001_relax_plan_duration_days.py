"""relax plans.duration_days to nullable

Revision ID: 20260419_0001
Revises:
Create Date: 2026-04-19

The "Бесплатный" plan legitimately has no duration (NULL).
Also serves as the baseline Alembic revision; existing tables
are assumed to match models as of this commit.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260419_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "plans",
        "duration_days",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade():
    op.alter_column(
        "plans",
        "duration_days",
        existing_type=sa.Integer(),
        nullable=False,
    )

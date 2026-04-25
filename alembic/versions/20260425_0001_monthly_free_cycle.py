"""monthly free-minutes cycle + 300->60 plan limit + cleanup paid bucket for free users

Revision ID: 20260425_0001
Revises: 20260419_0002
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa


revision = "20260425_0001"
down_revision = "20260419_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Track when the current free-tier billing month started for each profile.
    # Read paths reset free_minutes_used when this drifts into a previous
    # calendar month — see app/routers/internal.py and profiles.py.
    op.add_column(
        "profiles",
        sa.Column("free_minutes_period_start", sa.DateTime(timezone=True), nullable=True),
    )

    # Switch the free plan from 300 lifetime minutes to 60 per month.
    # Existing paid plans untouched.
    op.execute(
        """
        UPDATE plans
        SET minutes_limit = 60, updated_at = now()
        WHERE LOWER(name) LIKE '%бесплат%'
        """
    )

    # Rollout cleanup: every free profile gets a fresh start this month.
    # - zero free_minutes_used so no one is locked out by historical usage
    #   that was logged against a 300-minute lifetime budget
    # - zero paid_minutes_used_this_cycle (legacy migration noise from the
    #   Supabase import: free users had non-zero paid bucket values)
    # - anchor period_start to the first of the current month
    op.execute(
        """
        UPDATE profiles
        SET free_minutes_used = 0,
            paid_minutes_used_this_cycle = 0,
            free_minutes_period_start = DATE_TRUNC('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
            updated_at = now()
        WHERE current_plan_id IN (
            SELECT id FROM plans WHERE LOWER(name) LIKE '%бесплат%'
        ) OR current_plan_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("profiles", "free_minutes_period_start")
    op.execute(
        """
        UPDATE plans
        SET minutes_limit = 300, updated_at = now()
        WHERE LOWER(name) LIKE '%бесплат%'
        """
    )

"""payments_log.processed_at + unique index on yookassa_payment_id

Adds an explicit application-time stamp the webhook handler can flip
after a successful state transition, so YooKassa retries don't re-apply
the same payment twice (no double bump of subscription_active_until).

The unique partial index on yookassa_payment_id makes the idempotency
lookup constant-time and rejects accidental dup inserts at the DB layer.

Revision ID: 20260511_0001
Revises: 20260508_0001
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa


revision = "20260511_0001"
down_revision = "20260508_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "payments_log",
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_payments_log_yookassa_payment_id_unique",
        "payments_log",
        ["yookassa_payment_id"],
        unique=True,
        postgresql_where=sa.text("yookassa_payment_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("ix_payments_log_yookassa_payment_id_unique", table_name="payments_log")
    op.drop_column("payments_log", "processed_at")

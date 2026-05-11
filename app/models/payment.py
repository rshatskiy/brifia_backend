import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_method_id: Mapped[str] = mapped_column(String(256), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PaymentLog(Base):
    __tablename__ = "payments_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    yookassa_payment_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    # Marked once the webhook fully applies a status transition. NULL while
    # pending; set to now() on successful application of the matching
    # YooKassa event. Webhook handler short-circuits on a non-NULL value
    # to make their delivery retries idempotent.
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

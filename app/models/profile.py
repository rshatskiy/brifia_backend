import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    position: Mapped[str | None] = mapped_column(String(256), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    current_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    subscription_active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    free_minutes_used: Mapped[int] = mapped_column(Integer, default=0)
    # Calendar-month boundary: if this is in a previous month relative to the
    # current charge, free_minutes_used is reset to 0 first. Lazy reset — also
    # applied on every /profiles/me read so the UI stays truthful even for
    # users who haven't recorded yet this month.
    free_minutes_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_minutes_used_this_cycle: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="profile")
    plan: Mapped["Plan | None"] = relationship()

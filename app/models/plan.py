import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    minutes_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_rub: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=30)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

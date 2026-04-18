import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, BigInteger
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    meeting_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # pending, uploading, assembling, processing, completed, error
    uploaded_chunks: Mapped[dict] = mapped_column(JSONB, default=dict)
    # {chunk_number: {"size": bytes}} — без path, path вычисляется
    expected_total_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upload_dir: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

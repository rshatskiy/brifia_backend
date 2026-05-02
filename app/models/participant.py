import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text, ForeignKey, CheckConstraint, UniqueConstraint, REAL
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from app.database import Base


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("trim(name) != ''", name="ck_participants_name_not_empty"),
    )


class ParticipantSeriesLink(Base):
    __tablename__ = "participant_series"

    participant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), primary_key=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    meetings_count: Mapped[int] = mapped_column(Integer, default=0)


class MeetingSpeaker(Base):
    __tablename__ = "meeting_speakers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    speaker_label: Mapped[str] = mapped_column(String(32), nullable=False)
    participant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("participants.id", ondelete="SET NULL"), nullable=True
    )
    speaking_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name_suggestions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 256-dim L2-normed wespeaker centroid from pyannote community-1.
    # TRANSIENT delivery channel: server holds it only until the client has
    # downloaded it (or 24h elapses). Per 152-FZ biometric review, the
    # canonical store of voice fingerprints lives on user device.
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(REAL), nullable=True)
    # Set when the client ACK'd it has saved the embedding into its local
    # encrypted SQLite. Cron also prunes embeddings >24h old regardless.
    embedding_consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("meeting_id", "speaker_label", name="uq_meeting_speakers_label"),
    )


# ParticipantVoiceProfile removed — server no longer stores aggregated voice
# profiles. See migration 20260502_0002 and config.voice_profiles_server_matching.

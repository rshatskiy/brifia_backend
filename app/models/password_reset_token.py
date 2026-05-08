"""One-time tokens for the email-driven password reset flow.

We store only a SHA-256 hash of the raw token (so a DB leak doesn't
hand attackers ready-to-use reset URLs). The email contains the raw
token; the reset endpoint hashes its input and looks it up here.
Tokens are single-use: once consumed, `used_at` is stamped and the
record stops matching active queries.
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex of the raw token sent in the email — never the raw value.
    token_hash: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Set when the token has been redeemed; subsequent uses fail.
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

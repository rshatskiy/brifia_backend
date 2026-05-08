"""Send a rendered protocol to selected meeting participants by email.

Delegates the SMTP / Jinja work to `email_service.send_email`, so the
email body is the brand-aligned `email/protocol_share.html` template
rather than plain text.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.meeting import Meeting
from app.models.participant import Participant
from app.models.user import User
from app.services.email_service import Attachment, send_email
from app.services.protocol_export import (
    _ru_duration,
    _ru_meeting_date,
    render_for_format,
)

logger = logging.getLogger(__name__)


async def send_protocol_email(
    db: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    user_id: uuid.UUID,
    participant_ids: list[uuid.UUID],
    fmt: str,
    message: str | None,
    sender_name: str | None,
) -> tuple[list[str], list[str]]:
    """Render the protocol once, then deliver an HTML+attachment email
    per recipient. Returns (sent_addresses, skipped_participant_ids).

    Raises 503 when SMTP is not configured so the route surfaces this
    explicitly (rather than silently dropping every recipient into
    `skipped`)."""

    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from:
        raise HTTPException(
            status_code=503,
            detail="Email delivery is not configured on the server.",
        )

    # Authz: meeting must belong to caller
    m_q = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
    )
    meeting = m_q.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Resolve sender's email so participants can reply directly to them
    u_q = await db.execute(select(User.email).where(User.id == user_id))
    sender_email: str | None = u_q.scalar_one_or_none()

    # Resolve participants — only those owned by caller
    p_q = await db.execute(
        select(Participant).where(
            Participant.id.in_(participant_ids),
            Participant.user_id == user_id,
        )
    )
    participants = list(p_q.scalars().all())
    sent: list[str] = []
    skipped: list[str] = []

    deliverable = [p for p in participants if (p.email or "").strip()]
    skipped.extend(str(p.id) for p in participants if not (p.email or "").strip())
    found_ids = {p.id for p in participants}
    skipped.extend(str(pid) for pid in participant_ids if pid not in found_ids)

    if not deliverable:
        return sent, skipped

    # Render the document once and reuse for every recipient
    data, filename, content_type = await render_for_format(
        db, meeting_id, user_id, fmt
    )

    title = (meeting.title or "Встреча").strip()
    meeting_date = _ru_meeting_date(meeting.created_at)
    duration = _ru_duration(meeting.duration_seconds)
    format_label = "PDF" if fmt == "pdf" else "Word"
    subject = f"Протокол встречи · {title}"

    attachment = Attachment(
        data=data,
        filename=filename,
        content_type=content_type,
    )

    for p in deliverable:
        recipient_name = (
            p.name.strip().split()[0] if p.name and p.name.strip() else None
        )
        ok = await send_email(
            to=p.email,
            subject=subject,
            template_name="email/protocol_share.html",
            context={
                "meeting_title": title,
                "meeting_date": meeting_date,
                "duration": duration if duration != "—" else None,
                "speakers_count": None,
                "recipient_name": recipient_name,
                "sender_name": sender_name,
                "custom_message": (message or "").strip() or None,
                "format_label": format_label,
                "filename": filename,
            },
            attachments=[attachment],
            sender_name=sender_name,
            reply_to=sender_email,
        )
        if ok:
            sent.append(p.email)
        else:
            skipped.append(str(p.id))

    return sent, skipped

"""Send a rendered protocol to selected meeting participants by email.

Uses aiosmtplib so we don't block the event loop. SMTP credentials come
from the standard env-driven Settings; if they're missing, the function
raises a 503 to keep failures explicit.
"""
from __future__ import annotations

import logging
import uuid
from email.message import EmailMessage

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.meeting import Meeting
from app.models.participant import Participant
from app.services.protocol_export import render_for_format

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
    """Render the protocol once, then deliver to each participant who has
    an email. Returns (sent_addresses, skipped_participant_ids_as_str).
    """
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

    # Resolve participants — only those owned by caller AND with email
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
    not_deliverable = [str(p.id) for p in participants if not (p.email or "").strip()]
    skipped.extend(not_deliverable)
    # Plus IDs that didn't resolve (deleted, foreign user) — return them too
    found_ids = {p.id for p in participants}
    for pid in participant_ids:
        if pid not in found_ids:
            skipped.append(str(pid))

    if not deliverable:
        return sent, skipped

    # Render once and reuse
    data, filename, content_type = await render_for_format(db, meeting_id, user_id, fmt)

    subject = f"Протокол встречи: {(meeting.title or 'без названия').strip()}"
    body_text = (message or _default_body(meeting, sender_name)).strip()

    import aiosmtplib  # imported lazily; falls back to ImportError if missing
    use_tls = bool(settings.smtp_use_tls)
    start_tls = bool(settings.smtp_use_starttls)

    for p in deliverable:
        msg = EmailMessage()
        msg["From"] = (
            f"{sender_name} <{settings.smtp_from}>"
            if sender_name else settings.smtp_from
        )
        msg["To"] = p.email
        msg["Subject"] = subject
        msg.set_content(body_text)
        msg.add_attachment(
            data,
            maintype=content_type.split("/", 1)[0],
            subtype=content_type.split("/", 1)[1],
            filename=filename,
        )
        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username or None,
                password=settings.smtp_password or None,
                use_tls=use_tls,
                start_tls=start_tls,
                timeout=30,
            )
            sent.append(p.email)
        except Exception as e:  # noqa: BLE001 — surfaced to caller via metrics/log
            logger.exception(
                "smtp_send_failed meeting=%s participant=%s email=%s err=%s",
                meeting_id, p.id, p.email, e,
            )
            skipped.append(str(p.id))

    return sent, skipped


def _default_body(meeting: Meeting, sender_name: str | None) -> str:
    title = (meeting.title or "встречи").strip()
    who = sender_name or "Команда Brifia"
    return (
        f"Здравствуйте!\n\n"
        f"Во вложении — протокол {title}. "
        f"Документ сгенерирован автоматически на основе аудиозаписи и проверен ведущим.\n\n"
        f"С уважением,\n{who}\n"
        f"— отправлено через Brifia"
    )

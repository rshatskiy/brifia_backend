"""Protocol export and email-share endpoints.

POST /api/v1/meetings/{id}/export?format=pdf|docx
    → 200 with binary attachment (file) of the rendered protocol.

POST /api/v1/meetings/{id}/share/email
    → 202 with {sent_to: [...], skipped: [...]}.
"""
import urllib.parse
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.export import ExportFormat, ShareEmailRequest, ShareEmailResponse
from app.services.protocol_email import send_protocol_email
from app.services.protocol_export import render_for_format

router = APIRouter(prefix="/api/v1/meetings", tags=["exports"])


@router.post("/{meeting_id}/export")
async def export_protocol(
    meeting_id: uuid.UUID,
    format: ExportFormat = Query("pdf"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Render the meeting protocol and stream it back as a downloadable
    file. The client typically saves to disk and hands off to the system
    share sheet (share_plus on Flutter)."""
    data, filename, content_type = await render_for_format(
        db, meeting_id, user.id, format
    )
    # RFC 5987 quoting so non-ASCII filenames survive the trip through
    # mobile/web clients.
    quoted = urllib.parse.quote(filename, safe="")
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="protocol.{format}"; '
                f"filename*=UTF-8''{quoted}"
            ),
            "Content-Length": str(len(data)),
        },
    )


@router.post(
    "/{meeting_id}/share/email",
    response_model=ShareEmailResponse,
    status_code=202,
)
async def share_protocol_via_email(
    meeting_id: uuid.UUID,
    body: ShareEmailRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send the rendered protocol to the listed participants. Only ids
    owned by the calling user with a non-empty email are delivered;
    everything else lands in `skipped`."""
    # Pull the caller's display name once for the From header
    from sqlalchemy import select
    from app.models.profile import Profile
    sender_name: str | None = None
    p_q = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = p_q.scalar_one_or_none()
    if profile and (profile.full_name or "").strip():
        sender_name = profile.full_name.strip()
    elif user.email:
        sender_name = user.email

    sent, skipped = await send_protocol_email(
        db,
        meeting_id=meeting_id,
        user_id=user.id,
        participant_ids=body.participant_ids,
        fmt=body.format,
        message=body.message,
        sender_name=sender_name,
    )
    return ShareEmailResponse(sent_to=sent, skipped=skipped)

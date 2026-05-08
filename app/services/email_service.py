"""Generic transactional email engine — render Jinja template and send via SMTP.

All caller-facing functions return `bool` and never raise on SMTP failure;
they log the exception and return False. Combined with `fire_and_forget()`
this lets request handlers schedule emails without ever blocking on or
failing because of mail delivery problems.

Email-friendly conventions enforced here:
- Both plain-text and HTML alternatives are attached (RFC 2046 multipart),
  improving deliverability with strict spam filters and accessibility
  for clients that prefer text.
- The HTML alternative is rendered via Jinja with autoescape on, so any
  user-supplied context value gets escaped automatically.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings

logger = logging.getLogger(__name__)


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


@dataclass
class Attachment:
    data: bytes
    filename: str
    content_type: str  # e.g. "application/pdf"


async def send_email(
    *,
    to: str | Iterable[str],
    subject: str,
    template_name: str,
    context: dict,
    plain_text: str | None = None,
    attachments: list[Attachment] | None = None,
    sender_name: str | None = None,
    reply_to: str | None = None,
) -> bool:
    """Render `template_name` with `context`, deliver to `to` via SMTP.

    Returns True on success, False on misconfiguration or SMTP failure.
    Never raises — caller is responsible for treating the boolean as the
    delivery acknowledgement.
    """
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from:
        logger.warning("smtp_not_configured: skipping email subject=%r to=%s", subject, to)
        return False

    try:
        template = _jinja.get_template(template_name)
        html = template.render(subject=subject, **context)
    except Exception:
        logger.exception("email_template_render_failed: template=%s", template_name)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = (
        f"{sender_name} <{settings.smtp_from}>" if sender_name else settings.smtp_from
    )
    msg["To"] = to if isinstance(to, str) else ", ".join(list(to))
    if reply_to:
        msg["Reply-To"] = reply_to

    # Plain-text alternative first (per RFC 2046 §5.1.4 the LAST part is
    # preferred — so HTML must come second). Plain text body either passed
    # explicitly or derived by stripping tags from the rendered HTML.
    msg.set_content(plain_text or _html_to_text(html))
    msg.add_alternative(html, subtype="html")

    if attachments:
        for a in attachments:
            try:
                maintype, subtype = a.content_type.split("/", 1)
            except ValueError:
                maintype, subtype = "application", "octet-stream"
            msg.add_attachment(
                a.data, maintype=maintype, subtype=subtype, filename=a.filename
            )

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
            use_tls=bool(settings.smtp_use_tls),
            start_tls=bool(settings.smtp_use_starttls),
            timeout=30,
        )
        logger.info("email_sent: subject=%r to=%s", subject, to)
        return True
    except Exception:
        logger.exception("email_send_failed: subject=%r to=%s", subject, to)
        return False


def fire_and_forget(coro) -> asyncio.Task:
    """Schedule `coro` on the running event loop without blocking the
    caller. Any exception inside the coroutine is logged but never
    propagates — failures don't bring the request down."""
    async def _wrapped():
        try:
            await coro
        except Exception:
            logger.exception("email_fire_and_forget_unhandled")

    return asyncio.create_task(_wrapped())


# ─────────────────────────────────────────────────────────────────────


_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_BLOCK_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_WHITESPACE_RUN_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    """Quick-and-dirty HTML → plain text fallback. Good enough for the
    text/plain alternative — most users won't ever read it. Strips
    style/script blocks first so their CSS doesn't leak into the output."""
    text = _STYLE_BLOCK_RE.sub("", html)
    text = _TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&zwnj;", "")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&laquo;", "«")
        .replace("&raquo;", "»")
    )
    lines = [_WHITESPACE_RUN_RE.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()

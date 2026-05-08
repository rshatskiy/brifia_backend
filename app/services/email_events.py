"""High-level transactional email helpers — one async function per event.

Each function builds the right context for its template and forwards to
`send_email`. Routes call them inside `fire_and_forget()` so the request
never blocks on SMTP latency.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.config import get_settings
from app.services.email_service import send_email

logger = logging.getLogger(__name__)


_RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _ru_long(d: datetime) -> str:
    """`8 мая 2026` — used for receipts and human-readable dates."""
    return f"{d.day} {_RU_MONTHS[d.month - 1]} {d.year}"


def _first_name(full_name: str | None) -> str | None:
    if not full_name:
        return None
    head = full_name.strip().split()
    return head[0] if head else None


def _plural_days(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "дня"
    return "дней"


def _format_amount(amount: float | int) -> str:
    """`1 990` (thin-space thousands separator, no decimals for round)."""
    if abs(amount - round(amount)) < 0.01:
        return f"{int(round(amount)):,}".replace(",", " ")
    return f"{amount:,.2f}".replace(",", " ")


# ─────────────────────────────────────────────────────────────────────


async def send_welcome(to: str, *, name: str | None) -> bool:
    return await send_email(
        to=to,
        subject="Добро пожаловать в Brifia 👋",
        template_name="email/welcome.html",
        context={"name": _first_name(name)},
        sender_name="Команда Brifia",
    )


async def send_payment_success(
    to: str,
    *,
    name: str | None,
    plan_name: str,
    amount: float,
    active_until: datetime,
    payment_id: str | None,
    minutes_limit: int | None = None,
) -> bool:
    return await send_email(
        to=to,
        subject=f"Оплата прошла · тариф «{plan_name}»",
        template_name="email/payment_success.html",
        context={
            "name": _first_name(name),
            "plan_name": plan_name,
            "amount": _format_amount(amount),
            "active_until": _ru_long(active_until),
            "payment_id": payment_id,
            "minutes_limit": minutes_limit,
        },
        sender_name="Команда Brifia",
    )


async def send_password_reset(
    to: str,
    *,
    name: str | None,
    reset_token: str,
    ttl_minutes: int = 30,
) -> bool:
    settings = get_settings()
    base = (settings.web_base_url or "https://brifia.ru").rstrip("/")
    reset_url = f"{base}/reset-password?token={reset_token}"
    return await send_email(
        to=to,
        subject="Сброс пароля Brifia",
        template_name="email/password_reset.html",
        context={
            "name": _first_name(name),
            "email": to,
            "reset_url": reset_url,
            "ttl_minutes": ttl_minutes,
        },
        sender_name="Brifia",
    )


async def send_subscription_expiring(
    to: str,
    *,
    name: str | None,
    plan_name: str,
    active_until: datetime,
    days_left: int,
) -> bool:
    return await send_email(
        to=to,
        subject=(
            "Подписка Brifia скоро закончится"
            if days_left > 0
            else "Подписка Brifia закончилась"
        ),
        template_name="email/subscription_expiring.html",
        context={
            "name": _first_name(name),
            "plan_name": plan_name,
            "active_until": _ru_long(active_until),
            "days_left": days_left,
            "days_word": _plural_days(days_left),
        },
        sender_name="Команда Brifia",
    )

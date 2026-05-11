import logging
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.database import get_db
from app.models.user import User
from app.models.profile import Profile
from app.models.plan import Plan
from app.models.payment import PaymentMethod, PaymentLog
from app.auth import get_current_user
from app.schemas.payment import CreatePaymentRequest, CreatePaymentResponse
from app.config import get_settings
from app.services.email_events import send_payment_success
from app.services.email_service import fire_and_forget
from app.services.yookassa_security import _is_yookassa_ip, client_ip_from_headers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.post("/create", response_model=CreatePaymentResponse)
async def create_payment(
    body: CreatePaymentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()

    plan_result = await db.execute(select(Plan).where(Plan.id == body.plan_id, Plan.active == True))
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    from yookassa import Configuration, Payment

    Configuration.account_id = settings.yookassa_shop_id
    Configuration.secret_key = settings.yookassa_secret_key

    receipt_description = f"Подписка Brifia: {plan.name}"[:128]
    payment = Payment.create({
        "amount": {"value": str(plan.price_rub), "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": body.return_url or settings.payment_success_url,
        },
        "capture": True,
        "description": f"Подписка {plan.name}",
        "metadata": {
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
        "save_payment_method": True,
        "receipt": {
            "customer": {"email": user.email},
            "items": [
                {
                    "description": receipt_description,
                    "quantity": "1.00",
                    "amount": {"value": str(plan.price_rub), "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }
            ],
        },
    })

    log = PaymentLog(
        user_id=user.id,
        yookassa_payment_id=payment.id,
        status="pending",
        amount=float(plan.price_rub),
        plan_id=plan.id,
    )
    db.add(log)
    await db.commit()

    return CreatePaymentResponse(
        confirmation_url=payment.confirmation.confirmation_url,
        payment_id=payment.id,
    )


@router.post("/webhook")
async def yookassa_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive HTTP-notification from YooKassa.

    Defence in depth, in order:

      1. Source IP must be inside YooKassa's published allowlist
         (defeats trivially-forged requests from anywhere on the net).
      2. We independently fetch the payment via YooKassa's REST API and
         require its `.status` to match the event — protects against
         forged bodies if (1) is ever bypassed by a network anomaly.
      3. `payments_log.processed_at` gates idempotency so YooKassa's
         own retries don't re-apply the same payment a second time
         (no double bump of subscription_active_until).
    """
    # ---- Layer 1: IP allowlist -------------------------------------
    src_ip = client_ip_from_headers(
        request.headers.get("X-Real-IP"),
        request.headers.get("X-Forwarded-For"),
    )
    if not _is_yookassa_ip(src_ip):
        # Don't tell the attacker whether the header was missing vs wrong.
        logger.warning("payments.webhook: rejected request from non-YooKassa IP %r", src_ip)
        raise HTTPException(status_code=403, detail="Forbidden")

    # ---- Parse body ------------------------------------------------
    body = await request.json()
    event = body.get("event")
    payment_obj = body.get("object", {})
    payment_id = payment_obj.get("id")
    metadata = payment_obj.get("metadata", {})
    user_id = metadata.get("user_id")
    plan_id = metadata.get("plan_id")

    if not payment_id or not user_id:
        raise HTTPException(status_code=400, detail="Missing data")

    # ---- Layer 3: idempotency check (cheap, do before API call) ----
    existing_log = (await db.execute(
        select(PaymentLog).where(PaymentLog.yookassa_payment_id == payment_id)
    )).scalar_one_or_none()
    if existing_log is not None and existing_log.processed_at is not None:
        logger.info("payments.webhook: duplicate delivery for %s — already processed", payment_id)
        return {"status": "duplicate"}

    # ---- Layer 2: independently verify status via YooKassa API ----
    settings = get_settings()
    try:
        from yookassa import Configuration, Payment as YooKassaPayment
        Configuration.account_id = settings.yookassa_shop_id
        Configuration.secret_key = settings.yookassa_secret_key
        verified = YooKassaPayment.find_one(payment_id)
    except Exception as exc:
        # YooKassa API down / network glitch — refuse to act on unverified body.
        # 200 so they keep retrying; once their API comes back we'll process.
        logger.warning("payments.webhook: verify_failed for %s: %s", payment_id, exc)
        return {"status": "verify_failed"}

    if event == "payment.succeeded" and verified.status != "succeeded":
        logger.warning(
            "payments.webhook: status_mismatch payment=%s claimed=succeeded actual=%s",
            payment_id, verified.status,
        )
        return {"status": "status_mismatch", "expected": "succeeded", "actual": verified.status}
    if event == "payment.canceled" and verified.status != "canceled":
        logger.warning(
            "payments.webhook: status_mismatch payment=%s claimed=canceled actual=%s",
            payment_id, verified.status,
        )
        return {"status": "status_mismatch", "expected": "canceled", "actual": verified.status}

    user_uuid = uuid.UUID(user_id)
    now = datetime.now(timezone.utc)

    if event == "payment.succeeded":
        plan_result = await db.execute(select(Plan).where(Plan.id == uuid.UUID(plan_id)))
        plan = plan_result.scalar_one_or_none()
        if not plan:
            # Ack so YooKassa stops retrying; nothing to activate.
            logger.warning("payments.webhook: plan_not_found plan_id=%s payment=%s", plan_id, payment_id)
            return {"status": "ignored", "reason": "plan_not_found"}

        active_until = now + timedelta(days=plan.duration_days)

        profile_result = await db.execute(select(Profile).where(Profile.user_id == user_uuid))
        profile = profile_result.scalar_one_or_none()
        if profile:
            profile.current_plan_id = plan.id
            profile.subscription_active_until = active_until
            profile.paid_minutes_used_this_cycle = 0

        # Save payment method for auto-renewal
        pm_id = payment_obj.get("payment_method", {}).get("id")
        if pm_id:
            existing = await db.execute(
                select(PaymentMethod).where(PaymentMethod.user_id == user_uuid)
            )
            pm = existing.scalars().first()
            if pm:
                pm.payment_method_id = pm_id
                pm.last_used_at = now
            else:
                db.add(PaymentMethod(
                    user_id=user_uuid,
                    payment_method_id=pm_id,
                    last_used_at=now,
                ))

        # Update payment log + mark processed for idempotency.
        if existing_log:
            existing_log.status = "succeeded"
            existing_log.processed_at = now

        await db.commit()

        # Receipt email — fire-and-forget so a slow SMTP doesn't block YooKassa's
        # webhook timeout. The webhook only needs a 200 to mark this payment
        # delivered on their side.
        user_q = await db.execute(
            select(User.email, Profile.full_name)
            .join(Profile, Profile.user_id == User.id, isouter=True)
            .where(User.id == user_uuid)
        )
        row = user_q.one_or_none()
        if row and row[0]:
            user_email, full_name = row
            amount_value = payment_obj.get("amount", {}).get("value")
            try:
                amount_float = float(amount_value) if amount_value else float(plan.price_rub)
            except (TypeError, ValueError):
                amount_float = float(plan.price_rub)
            fire_and_forget(send_payment_success(
                user_email,
                name=full_name,
                plan_name=plan.name,
                amount=amount_float,
                active_until=active_until,
                payment_id=payment_id,
                minutes_limit=getattr(plan, "minutes_limit", None),
            ))

    elif event == "payment.canceled":
        if existing_log:
            existing_log.status = "canceled"
            existing_log.processed_at = now
            await db.commit()

    return {"status": "ok"}


@router.post("/cancel")
async def cancel_subscription(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Delete payment method (prevents auto-renewal)
    await db.execute(delete(PaymentMethod).where(PaymentMethod.user_id == user.id))

    # Downgrade to free plan (keep active_until so user can use until expiry)
    profile_result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = profile_result.scalar_one_or_none()
    if profile:
        profile.current_plan_id = None

    await db.commit()
    return {"message": "Subscription cancelled"}

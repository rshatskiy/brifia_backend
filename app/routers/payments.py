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
    body = await request.json()
    event = body.get("event")
    payment_obj = body.get("object", {})
    payment_id = payment_obj.get("id")
    metadata = payment_obj.get("metadata", {})
    user_id = metadata.get("user_id")
    plan_id = metadata.get("plan_id")

    if not payment_id or not user_id:
        raise HTTPException(status_code=400, detail="Missing data")

    user_uuid = uuid.UUID(user_id)

    if event == "payment.succeeded":
        plan_result = await db.execute(select(Plan).where(Plan.id == uuid.UUID(plan_id)))
        plan = plan_result.scalar_one_or_none()
        if not plan:
            raise HTTPException(status_code=400, detail="Plan not found")

        profile_result = await db.execute(select(Profile).where(Profile.user_id == user_uuid))
        profile = profile_result.scalar_one_or_none()
        if profile:
            profile.current_plan_id = plan.id
            profile.subscription_active_until = datetime.now(timezone.utc) + timedelta(days=plan.duration_days)
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
                pm.last_used_at = datetime.now(timezone.utc)
            else:
                db.add(PaymentMethod(
                    user_id=user_uuid,
                    payment_method_id=pm_id,
                    last_used_at=datetime.now(timezone.utc),
                ))

        # Update payment log
        log_result = await db.execute(
            select(PaymentLog).where(PaymentLog.yookassa_payment_id == payment_id)
        )
        log = log_result.scalar_one_or_none()
        if log:
            log.status = "succeeded"

        await db.commit()

    elif event == "payment.canceled":
        log_result = await db.execute(
            select(PaymentLog).where(PaymentLog.yookassa_payment_id == payment_id)
        )
        log = log_result.scalar_one_or_none()
        if log:
            log.status = "canceled"
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

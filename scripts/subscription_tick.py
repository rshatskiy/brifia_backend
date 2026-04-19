import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select
from yookassa import Configuration, Payment

from app.config import get_settings
from app.models.user import User
from app.models.profile import Profile
from app.models.plan import Plan
from app.models.payment import PaymentMethod, PaymentLog


async def tick() -> None:
    settings = get_settings()
    Configuration.account_id = settings.yookassa_shop_id
    Configuration.secret_key = settings.yookassa_secret_key

    engine = create_async_engine(settings.database_url)
    async with AsyncSession(engine) as db:
        now = datetime.now(timezone.utc)

        # 1) Try to renew subscriptions ending within the next 24 hours
        due = await db.execute(
            select(Profile).where(
                Profile.current_plan_id.is_not(None),
                Profile.subscription_active_until.is_not(None),
                Profile.subscription_active_until > now,
                Profile.subscription_active_until <= now + timedelta(days=1),
            )
        )
        for profile in due.scalars().all():
            pm_row = await db.execute(select(PaymentMethod).where(PaymentMethod.user_id == profile.user_id))
            pm = pm_row.scalar_one_or_none()
            if not pm:
                print(f'[tick] no payment method user={profile.user_id}')
                continue

            plan_row = await db.execute(select(Plan).where(Plan.id == profile.current_plan_id))
            plan = plan_row.scalar_one_or_none()
            if not plan:
                continue

            user_row = await db.execute(select(User).where(User.id == profile.user_id))
            user = user_row.scalar_one_or_none()
            if not user:
                continue

            idem_key = f'renew-{profile.user_id}-{profile.subscription_active_until.date().isoformat()}'
            desc = f'Подписка Brifia: {plan.name}'[:128]
            try:
                payment = Payment.create({
                    'amount': {'value': str(plan.price_rub), 'currency': 'RUB'},
                    'payment_method_id': pm.payment_method_id,
                    'capture': True,
                    'description': f'Продление {plan.name}',
                    'metadata': {
                        'user_id': str(profile.user_id),
                        'plan_id': str(plan.id),
                    },
                    'receipt': {
                        'customer': {'email': user.email},
                        'items': [{
                            'description': desc,
                            'quantity': '1.00',
                            'amount': {'value': str(plan.price_rub), 'currency': 'RUB'},
                            'vat_code': 1,
                            'payment_mode': 'full_payment',
                            'payment_subject': 'service',
                        }],
                    },
                }, idem_key)
            except Exception as e:
                print(f'[tick] charge failed user={profile.user_id}: {e}')
                continue

            exists = await db.execute(
                select(PaymentLog).where(PaymentLog.yookassa_payment_id == payment.id)
            )
            if not exists.scalar_one_or_none():
                db.add(PaymentLog(
                    user_id=profile.user_id,
                    yookassa_payment_id=payment.id,
                    status='pending',
                    amount=float(plan.price_rub),
                    plan_id=plan.id,
                ))
            print(f'[tick] charge user={profile.user_id} payment={payment.id} status={payment.status}')

        # 2) Downgrade profiles whose subscription fully expired more than 1 day ago
        expired = await db.execute(
            select(Profile).where(
                Profile.current_plan_id.is_not(None),
                Profile.subscription_active_until.is_not(None),
                Profile.subscription_active_until < now - timedelta(days=1),
            )
        )
        for profile in expired.scalars().all():
            profile.current_plan_id = None
            print(f'[tick] expired user={profile.user_id}')

        await db.commit()

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(tick())

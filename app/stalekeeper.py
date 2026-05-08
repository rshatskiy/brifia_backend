"""Background tasks for processing_jobs hygiene.

Runs as APScheduler jobs inside the FastAPI process (single uvicorn worker
expected — multi-worker would need a leader-election story). All operations
are idempotent so accidental double-execution is safe.
"""
import logging
from datetime import datetime, timedelta, timezone, date
from sqlalchemy import select, func, update as sa_update, cast, Date
from app.database import async_session
from app.metrics import processing_jobs_pending
from app.models.processing_job import ProcessingJob
from app.models.meeting import Meeting
from app.models.participant import MeetingSpeaker
from app.models.profile import Profile
from app.models.plan import Plan
from app.models.user import User
from app.constants.meeting_status import MeetingStatus, IN_FLIGHT_STATUSES
from app.routers.internal import _set_meeting_status
from app.services.email_events import send_subscription_expiring

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_MINUTES = 5
ORPHAN_TIMEOUT_HOURS = 2
EMBEDDING_RETENTION_HOURS = 24


async def revive_stalled_jobs() -> int:
    """Revert claimed jobs whose worker stopped heartbeating.

    For each stalled job, increment attempts. If attempts < max_attempts
    → bounce to pending and set Meeting.status='queued' (silent retry).
    Else → mark failed and Meeting.status='error'.
    Pushes WebSocket meeting.updated via _set_meeting_status helper.
    """
    threshold = datetime.now(timezone.utc) - timedelta(minutes=HEARTBEAT_TIMEOUT_MINUTES)
    revived = 0
    try:
        async with async_session() as db:
            # SELECT FOR UPDATE serializes against concurrent worker heartbeats
            stalled_q = await db.execute(
                select(ProcessingJob)
                .where(
                    ProcessingJob.status == "claimed",
                    ProcessingJob.heartbeat_at < threshold,
                )
                .with_for_update()
            )
            stalled = stalled_q.scalars().all()
            for job in stalled:
                job.attempts += 1
                meeting_q = await db.execute(select(Meeting).where(Meeting.id == job.meeting_id))
                meeting = meeting_q.scalar_one()
                if job.attempts < job.max_attempts:
                    job.status = "pending"
                    job.claimed_by = None
                    job.claimed_at = None
                    job.heartbeat_at = None
                    await _set_meeting_status(db, meeting, MeetingStatus.QUEUED.value)
                else:
                    job.status = "failed"
                    job.error_message = "worker timeout"
                    await _set_meeting_status(
                        db, meeting, MeetingStatus.ERROR.value, error_message="worker timeout"
                    )
                revived += 1
            if revived:
                await db.commit()
                logger.warning("stalekeeper: revived %d stalled jobs", revived)
    except Exception:
        logger.exception("stalekeeper: revive_stalled_jobs failed")
        raise
    return revived


async def expire_orphan_meetings() -> int:
    """Catch meetings that are stuck in queued/transcribing/analyzing
    without any matching processing_job row (safety net for cases where
    a job somehow got deleted). Pushes WebSocket meeting.updated.
    """
    threshold = datetime.now(timezone.utc) - timedelta(hours=ORPHAN_TIMEOUT_HOURS)
    expired = 0
    try:
        async with async_session() as db:
            stuck_q = await db.execute(
                select(Meeting).where(
                    Meeting.status.in_(list(IN_FLIGHT_STATUSES)),
                    Meeting.updated_at < threshold,
                )
            )
            stuck = stuck_q.scalars().all()
            for m in stuck:
                await _set_meeting_status(db, m, MeetingStatus.ERROR.value, error_message="timeout")
                expired += 1
            if expired:
                await db.commit()
                logger.warning("stalekeeper: expired %d orphan meetings", expired)
    except Exception:
        logger.exception("stalekeeper: expire_orphan_meetings failed")
        raise
    return expired


async def update_queue_gauges() -> None:
    """Refresh Prometheus gauges from current DB state."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(ProcessingJob.priority, func.count(ProcessingJob.id))
                .where(ProcessingJob.status == "pending")
                .group_by(ProcessingJob.priority)
            )
            counts = {p: c for p, c in result.all()}
        processing_jobs_pending.labels(priority="realtime").set(counts.get("realtime", 0))
        processing_jobs_pending.labels(priority="background").set(counts.get("background", 0))
    except Exception:
        logger.exception("stalekeeper: update_queue_gauges failed")
        # Don't re-raise — gauge update is best-effort, shouldn't kill scheduler


async def expire_speaker_embeddings() -> None:
    """Delete voice embeddings >24h old whether or not the client consumed
    them. Defense in depth for 152-FZ biometric data minimization: even if
    a client never came back to ACK consumption, we don't keep biometrics
    around forever."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=EMBEDDING_RETENTION_HOURS)
    try:
        async with async_session() as db:
            result = await db.execute(
                sa_update(MeetingSpeaker)
                .where(
                    MeetingSpeaker.embedding.isnot(None),
                    MeetingSpeaker.created_at < cutoff,
                )
                .values(embedding=None)
            )
            await db.commit()
            cleared = result.rowcount or 0
            if cleared:
                logger.info("expired %d speaker embeddings (>%dh old)", cleared, EMBEDDING_RETENTION_HOURS)
    except Exception:
        logger.exception("stalekeeper: expire_speaker_embeddings failed")


async def warn_subscriptions_expiring() -> int:
    """Daily cron: notify users whose paid subscription is 3 days from
    expiry, and once more when it has just expired (day-0).

    Idempotency relies on running once per calendar day with a
    `date(active_until) == today + N` check. If the cron skips a day,
    that day's cohort misses the notification — acceptable for a non-
    critical reminder. Sending duplicates within a single day is the
    only thing we actively prevent (via the date-equality query, which
    matches only the cohort whose active_until *date* falls on a
    specific calendar day).
    """
    sent = 0
    today = datetime.now(timezone.utc).date()

    async def _send_for_offset(offset_days: int) -> int:
        target = today + timedelta(days=offset_days)
        delivered = 0
        async with async_session() as db:
            q = await db.execute(
                select(Profile, User, Plan)
                .join(User, User.id == Profile.user_id)
                .join(Plan, Plan.id == Profile.current_plan_id)
                .where(
                    Profile.subscription_active_until.is_not(None),
                    cast(Profile.subscription_active_until, Date) == target,
                    Plan.price_rub > 0,  # only paid plans need a reminder
                )
            )
            for profile, user, plan in q.all():
                if not user.email:
                    continue
                try:
                    ok = await send_subscription_expiring(
                        user.email,
                        name=profile.full_name,
                        plan_name=plan.name,
                        active_until=profile.subscription_active_until,
                        days_left=offset_days,
                    )
                    if ok:
                        delivered += 1
                except Exception:
                    logger.exception(
                        "expiry_warning_send_failed user=%s offset=%d",
                        user.id, offset_days,
                    )
        return delivered

    try:
        sent += await _send_for_offset(3)
        sent += await _send_for_offset(0)
        if sent:
            logger.info("stalekeeper: sent %d expiry warnings", sent)
    except Exception:
        logger.exception("stalekeeper: warn_subscriptions_expiring failed")
    return sent


def attach_to_app(app):
    """Register stalekeeper jobs into FastAPI lifespan via APScheduler.

    Called from app.main lifespan after the engine is up. Wrapped in
    try/except so partial init doesn't leave the scheduler running
    without a way to shut it down.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    scheduler = AsyncIOScheduler()
    scheduler.add_job(revive_stalled_jobs, "interval", minutes=1, id="revive_stalled_jobs")
    scheduler.add_job(expire_orphan_meetings, "interval", hours=1, id="expire_orphan_meetings")
    scheduler.add_job(update_queue_gauges, "interval", seconds=15, id="update_queue_gauges")
    scheduler.add_job(expire_speaker_embeddings, "interval", hours=1, id="expire_speaker_embeddings")
    # Once a day at 09:00 UTC (~12:00 MSK) — far enough into the working
    # day that the email lands in an active inbox.
    scheduler.add_job(
        warn_subscriptions_expiring,
        CronTrigger(hour=9, minute=0),
        id="warn_subscriptions_expiring",
    )
    try:
        scheduler.start()
        app.state.stalekeeper_scheduler = scheduler
    except Exception:
        # If start() somehow succeeded but assignment failed, shut it down
        # so we don't leak a running scheduler.
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        raise

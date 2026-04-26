"""Background tasks for processing_jobs hygiene.

Runs as APScheduler jobs inside the FastAPI process (single uvicorn worker
expected — multi-worker would need a leader-election story). All operations
are idempotent so accidental double-execution is safe.
"""
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from app.database import async_session
from app.metrics import processing_jobs_pending
from app.models.processing_job import ProcessingJob
from app.models.meeting import Meeting
from app.constants.meeting_status import MeetingStatus, IN_FLIGHT_STATUSES
from app.routers.internal import _set_meeting_status

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_MINUTES = 5
ORPHAN_TIMEOUT_HOURS = 2


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


def attach_to_app(app):
    """Register stalekeeper jobs into FastAPI lifespan via APScheduler.

    Called from app.main lifespan after the engine is up. Wrapped in
    try/except so partial init doesn't leave the scheduler running
    without a way to shut it down.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(revive_stalled_jobs, "interval", minutes=1, id="revive_stalled_jobs")
    scheduler.add_job(expire_orphan_meetings, "interval", hours=1, id="expire_orphan_meetings")
    scheduler.add_job(update_queue_gauges, "interval", seconds=15, id="update_queue_gauges")
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

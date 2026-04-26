"""Background tasks for processing_jobs hygiene.

Runs as APScheduler jobs inside the FastAPI process (single uvicorn worker
expected — multi-worker would need a leader-election story). All operations
are idempotent so accidental double-execution is safe.
"""
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.database import async_session
from app.models.processing_job import ProcessingJob
from app.models.meeting import Meeting
from app.constants.meeting_status import MeetingStatus, IN_FLIGHT_STATUSES

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_MINUTES = 5
ORPHAN_TIMEOUT_HOURS = 2


async def revive_stalled_jobs() -> int:
    """Revert claimed jobs whose worker stopped heartbeating.

    For each stalled job, increment attempts. If attempts < max_attempts
    → bounce to pending and set Meeting.status='queued' (silent retry).
    Else → mark failed and Meeting.status='error'.
    """
    threshold = datetime.now(timezone.utc) - timedelta(minutes=HEARTBEAT_TIMEOUT_MINUTES)
    revived = 0
    async with async_session() as db:
        stalled_q = await db.execute(
            select(ProcessingJob).where(
                ProcessingJob.status == "claimed",
                ProcessingJob.heartbeat_at < threshold,
            )
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
                meeting.status = MeetingStatus.QUEUED.value
                meeting.error_message = None
            else:
                job.status = "failed"
                job.error_message = "worker timeout"
                meeting.status = MeetingStatus.ERROR.value
                meeting.error_message = "worker timeout"
            revived += 1
        if revived:
            await db.commit()
            logger.warning("stalekeeper: revived %d stalled jobs", revived)
    return revived


async def expire_orphan_meetings() -> int:
    """Catch meetings that are stuck in queued/transcribing/analyzing
    without any matching processing_job row (safety net for cases where
    a job somehow got deleted).
    """
    threshold = datetime.now(timezone.utc) - timedelta(hours=ORPHAN_TIMEOUT_HOURS)
    expired = 0
    async with async_session() as db:
        stuck_q = await db.execute(
            select(Meeting).where(
                Meeting.status.in_(list(IN_FLIGHT_STATUSES)),
                Meeting.updated_at < threshold,
            )
        )
        stuck = stuck_q.scalars().all()
        for m in stuck:
            m.status = MeetingStatus.ERROR.value
            m.error_message = "timeout"
            expired += 1
        if expired:
            await db.commit()
            logger.warning("stalekeeper: expired %d orphan meetings", expired)
    return expired


def attach_to_app(app):
    """Register stalekeeper jobs into FastAPI lifespan via APScheduler.

    Called from app.main lifespan after the engine is up.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(revive_stalled_jobs, "interval", minutes=1, id="revive_stalled_jobs")
    scheduler.add_job(expire_orphan_meetings, "interval", hours=1, id="expire_orphan_meetings")
    scheduler.start()
    app.state.stalekeeper_scheduler = scheduler

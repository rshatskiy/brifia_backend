"""One-shot backfill of meeting_tasks rows from legacy meetings.tasks_json.

Background: Phase 1 of the "living tasks" migration shipped first-class
meeting_tasks rows + a fanout in /jobs/complete that converts the
LLM-emitted tasks_json blob into rows. New meetings get fanned out
automatically. Old meetings (anything completed before the fanout
shipped) still only have data in meetings.tasks_json.

This script picks up exactly those: meetings whose tasks_json is non-empty
AND who have zero rows in meeting_tasks. For each, it runs the same
_sync_meeting_tasks_from_payload that /jobs/complete uses, so semantics
stay identical between freshly-completed and back-filled meetings.

Usage:
    python -m scripts.backfill_meeting_tasks --dry-run     # report only
    python -m scripts.backfill_meeting_tasks               # write
    python -m scripts.backfill_meeting_tasks --limit 500   # cap

Idempotent: re-running after a successful pass is a no-op (the WHERE
clause excludes meetings that already have rows).

Per-meeting commit. A failure mid-run stops the script but doesn't roll
back what's already been written; you can re-run safely from where it
stopped.
"""
import argparse
import asyncio
import sys
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select, exists, func

from app.config import get_settings
from app.models.meeting import Meeting
from app.models.meeting_task import MeetingTask
# Reuse the exact same fanout logic as /jobs/complete so backfilled
# meetings end up indistinguishable from freshly processed ones.
from app.routers.internal import _sync_meeting_tasks_from_payload


async def _candidates(db: AsyncSession, limit: int | None):
    """Meetings with non-empty tasks_json AND zero meeting_tasks rows."""
    has_rows = (
        select(MeetingTask.id)
        .where(MeetingTask.meeting_id == Meeting.id)
        .exists()
    )
    q = (
        select(Meeting)
        .where(
            Meeting.tasks_json.is_not(None),
            Meeting.tasks_json != "",
            ~has_rows,
        )
        .order_by(Meeting.created_at.asc())
    )
    if limit is not None:
        q = q.limit(limit)
    res = await db.execute(q)
    return res.scalars().all()


async def run(dry_run: bool, limit: int | None) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)

    processed = 0
    skipped = 0
    failed = 0

    try:
        async with AsyncSession(engine) as db:
            meetings = await _candidates(db, limit)
            total = len(meetings)
            print(f"[backfill] candidates: {total}"
                  f"{' (DRY RUN)' if dry_run else ''}")

            for i, meeting in enumerate(meetings, 1):
                try:
                    if dry_run:
                        # Just report what would happen.
                        size = len(meeting.tasks_json or "")
                        print(f"[backfill] {i}/{total} would process "
                              f"meeting={meeting.id} tasks_json={size}b")
                        skipped += 1
                        continue

                    await _sync_meeting_tasks_from_payload(
                        db, meeting.id, meeting.tasks_json
                    )
                    await db.commit()

                    # Verify rows actually appeared (catches silent fanout
                    # no-ops from unparseable JSON).
                    count = await db.execute(
                        select(func.count(MeetingTask.id))
                        .where(MeetingTask.meeting_id == meeting.id)
                    )
                    n = count.scalar() or 0
                    if n == 0:
                        # Most likely cause: tasks_json was unparseable
                        # (returns None from the parser → no rows added).
                        # Not an error, just unrecoverable legacy data.
                        skipped += 1
                        print(f"[backfill] {i}/{total} meeting={meeting.id} "
                              f"unparseable tasks_json — skipped")
                    else:
                        processed += 1
                        print(f"[backfill] {i}/{total} meeting={meeting.id} "
                              f"+{n} task rows")
                except Exception as e:
                    await db.rollback()
                    failed += 1
                    print(f"[backfill] {i}/{total} meeting={meeting.id} "
                          f"FAILED: {e!r}")
    finally:
        await engine.dispose()

    print(f"[backfill] done: processed={processed} "
          f"skipped={skipped} failed={failed}")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report candidates without writing")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap number of meetings processed")
    args = ap.parse_args()
    failed = asyncio.run(run(args.dry_run, args.limit))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Internal API for faster-whisper server.

Authenticated via shared API key (FASTER_WHISPER_API_KEY),
not user JWT tokens.
"""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from app.database import get_db
from app.models.meeting import Meeting
from app.models.user import User
from app.models.profile import Profile
from app.config import get_settings
from app.websocket_manager import ws_manager
from app.schemas.meeting import MeetingUpdate, MeetingStatusResponse, MeetingCreateInternal, MeetingDetail
from app.constants.meeting_status import MeetingStatus, IN_FLIGHT_STATUSES, TERMINAL_STATUSES
from app.models.processing_job import ProcessingJob
from app.models.participant import MeetingSpeaker
from app.services.voice_profile import (
    AUTO_BIND_THRESHOLD,
    HIGH_SUGGESTION_THRESHOLD,
    MEDIUM_SUGGESTION_THRESHOLD,
    load_user_profiles,
    match_speaker,
    update_voice_profile,
)
import logging

logger = logging.getLogger(__name__)
from app.schemas.processing_job import JobCreate, JobClaimResponse, JobProgress, JobComplete, JobFail
from app.models.prompt import Prompt
from app.metrics import (
    processing_jobs_failures_total,
    processing_jobs_retries_total,
    processing_jobs_duration_seconds,
)

router = APIRouter(prefix="/internal", tags=["internal"])

COMPLETED_STATUS = "completed"


def _current_month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def reset_free_cycle_if_needed(profile: Profile) -> bool:
    """Lazy monthly reset for the free tier. If the profile's recorded
    period anchor is older than the current calendar month, zero the
    counter and re-anchor. Returns True if a reset happened (caller
    should commit). Idempotent — calling twice in the same month is a no-op.
    """
    month_start = _current_month_start()
    anchor = profile.free_minutes_period_start
    if anchor is None or anchor < month_start:
        profile.free_minutes_used = 0
        profile.free_minutes_period_start = month_start
        return True
    return False


async def verify_api_key(x_api_key: str = Header(...)):
    settings = get_settings()
    if x_api_key != settings.faster_whisper_api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


async def _set_meeting_status(
    db: AsyncSession,
    meeting: Meeting,
    new_status: str,
    *,
    error_message: str | None = None,
    set_processing_started: bool = False,
) -> None:
    """Single helper for any internal status change.

    Updates Meeting.status, optionally error_message and processing_started_at,
    then pushes meeting.updated via WebSocket. Does NOT commit — caller manages
    the transaction. Pairs with _charge_usage_if_first_completion which is
    invoked separately when status flips to completed.
    """
    if new_status not in {s.value for s in MeetingStatus}:
        raise ValueError(f"Unknown meeting status: {new_status}")

    meeting.status = new_status
    if error_message is not None:
        meeting.error_message = error_message
    elif new_status not in TERMINAL_STATUSES and meeting.error_message:
        # Clearing error_message on retry path
        meeting.error_message = None
    if set_processing_started:
        meeting.processing_started_at = datetime.now(timezone.utc)

    await ws_manager.notify_user(
        str(meeting.user_id),
        "meeting.updated",
        {
            "id": str(meeting.id),
            "status": new_status,
            "error_message": meeting.error_message,
        },
    )


async def _charge_usage_if_first_completion(
    db: AsyncSession,
    meeting: Meeting,
    was_completed: bool,
) -> None:
    """When a meeting flips into 'completed' for the first time, bill its
    duration against the user's plan minutes. Idempotent — once the meeting
    is already 'completed', subsequent updates do nothing.

    Counter selection has to match the client's account screen, which
    discriminates by plan *name* containing 'бесплатный' rather than by
    presence of current_plan_id. Free users do have current_plan_id set
    (the "Бесплатный" plan is itself a Plan row with a 300-minute limit),
    so the older "is current_plan_id null?" check sent free users' minutes
    into the paid bucket and the UI showed zero usage.
    """
    if was_completed:
        return
    if meeting.status != COMPLETED_STATUS:
        return
    if not meeting.duration_seconds or meeting.duration_seconds <= 0:
        return

    profile_q = await db.execute(
        select(Profile).where(Profile.user_id == meeting.user_id)
    )
    profile = profile_q.scalar_one_or_none()
    if profile is None:
        return

    is_free = True
    if profile.current_plan_id is not None:
        from app.models.plan import Plan
        plan_q = await db.execute(
            select(Plan.name).where(Plan.id == profile.current_plan_id)
        )
        plan_name = plan_q.scalar_one_or_none()
        if plan_name is not None and "бесплатный" not in plan_name.lower():
            is_free = False

    minutes = (meeting.duration_seconds + 59) // 60
    if is_free:
        # Roll the cycle if we crossed a calendar month before charging.
        reset_free_cycle_if_needed(profile)
        profile.free_minutes_used = (profile.free_minutes_used or 0) + minutes
    else:
        profile.paid_minutes_used_this_cycle = (
            profile.paid_minutes_used_this_cycle or 0
        ) + minutes


@router.post("/meetings", response_model=MeetingDetail, status_code=201, dependencies=[Depends(verify_api_key)])
async def create_meeting_internal(
    body: MeetingCreateInternal,
    db: AsyncSession = Depends(get_db),
):
    """Server-to-server meeting creation, used by faster-whisper as a safety net
    when the mobile client's auth died before it could register the meeting itself.

    Idempotent on `id`: repeated calls with the same UUID return the existing
    meeting. Returns 409 if the id exists for a different user (defends against
    misrouted calls).
    """
    user_result = await db.execute(select(User).where(User.id == body.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing_result = await db.execute(select(Meeting).where(Meeting.id == body.id))
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        if existing.user_id != body.user_id:
            raise HTTPException(status_code=409, detail="Meeting id already exists for another user")
        return existing

    meeting = Meeting(
        id=body.id,
        user_id=body.user_id,
        title=body.title,
        status=body.status,
        duration_seconds=body.duration_seconds,
        local_filename=body.local_filename,
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)

    await ws_manager.notify_user(str(body.user_id), "meeting.created", {"id": str(meeting.id)})
    return meeting


@router.put("/meetings/{meeting_id}", dependencies=[Depends(verify_api_key)])
async def update_meeting_from_whisper(
    meeting_id: uuid.UUID,
    body: MeetingUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Called by faster-whisper to save transcription results."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    was_completed = meeting.status == COMPLETED_STATUS
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(meeting, field, value)
    await _charge_usage_if_first_completion(db, meeting, was_completed)
    await db.commit()
    await db.refresh(meeting)

    # Notify the user via WebSocket
    await ws_manager.notify_user(str(meeting.user_id), "meeting.updated", {
        "id": str(meeting.id),
        "status": meeting.status,
    })

    return {"status": "ok"}


@router.get("/meetings/{meeting_id}/status", dependencies=[Depends(verify_api_key)])
async def get_meeting_status_internal(
    meeting_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Called by faster-whisper to check meeting status."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return MeetingStatusResponse(
        meeting_id=m.id,
        status=m.status,
        title=m.title,
        has_transcript=bool(m.transcript),
        has_transcript_json=bool(m.transcript_json),
        has_protocol=bool(m.protocol),
        has_tasks=bool(m.tasks_json),
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get("/meetings/{meeting_id}/prompt", dependencies=[Depends(verify_api_key)])
async def get_meeting_prompt(
    meeting_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get the prompt associated with a meeting (for analysis)."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if not meeting.prompt_id:
        # Return default prompt
        result = await db.execute(
            select(Prompt).where(Prompt.use_case == "meeting_protocol", Prompt.is_active == True).limit(1)
        )
        prompt = result.scalar_one_or_none()
    else:
        result = await db.execute(select(Prompt).where(Prompt.id == meeting.prompt_id))
        prompt = result.scalar_one_or_none()

    if not prompt:
        return {"prompt_text": None, "model": "deepseek-chat"}

    return {"prompt_text": prompt.prompt_text, "model": prompt.model}


@router.post("/jobs", status_code=201, dependencies=[Depends(verify_api_key)])
async def create_job(
    body: JobCreate,
    db: AsyncSession = Depends(get_db),
):
    """Called by faster-whisper FastAPI after merging chunks.

    Idempotent: if a pending or claimed job already exists for this meeting,
    return it instead of creating a duplicate.
    """
    meeting_q = await db.execute(select(Meeting).where(Meeting.id == body.meeting_id))
    meeting = meeting_q.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    existing_q = await db.execute(
        select(ProcessingJob).where(
            ProcessingJob.meeting_id == body.meeting_id,
            ProcessingJob.status.in_(["pending", "claimed"]),
        )
    )
    existing = existing_q.scalar_one_or_none()
    if existing is not None:
        return {"job_id": str(existing.id), "duplicate": True}

    job = ProcessingJob(
        meeting_id=body.meeting_id,
        audio_local_path=body.audio_local_path,
        priority=body.priority,
        expected_duration_seconds=body.expected_duration_seconds,
    )
    db.add(job)
    await _set_meeting_status(db, meeting, MeetingStatus.QUEUED.value)
    await db.commit()
    await db.refresh(job)
    return {"job_id": str(job.id), "duplicate": False}


@router.post("/jobs/claim", dependencies=[Depends(verify_api_key)])
async def claim_job(
    worker_id: str,  # Query param
    db: AsyncSession = Depends(get_db),
):
    """Atomic SELECT FOR UPDATE SKIP LOCKED + UPDATE claimed.

    Returns null when the queue is empty. Worker should sleep+retry.
    Worker_id is opaque — used for heartbeat tracking and operational
    visibility ("which worker is doing what").
    """
    # SELECT FOR UPDATE SKIP LOCKED — atomic across concurrent workers
    job_q = await db.execute(
        select(ProcessingJob)
        .where(ProcessingJob.status == "pending")
        .order_by(
            # 'realtime' before 'background' — alphabetical works because b < r,
            # we want realtime first, so reverse:
            ProcessingJob.priority.desc(),
            ProcessingJob.created_at.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = job_q.scalar_one_or_none()
    if job is None:
        return None

    # Mutate it
    now = datetime.now(timezone.utc)
    job.status = "claimed"
    job.claimed_by = worker_id
    job.claimed_at = now
    job.heartbeat_at = now

    # Load meeting + prompt for the response
    meeting_q = await db.execute(select(Meeting).where(Meeting.id == job.meeting_id))
    meeting = meeting_q.scalar_one()

    prompt_text, prompt_model = None, None
    if meeting.prompt_id is not None:
        p_q = await db.execute(select(Prompt).where(Prompt.id == meeting.prompt_id))
        p = p_q.scalar_one_or_none()
        if p is not None:
            prompt_text, prompt_model = p.prompt_text, p.model
    if prompt_text is None:
        # Fallback to default
        p_q = await db.execute(
            select(Prompt).where(Prompt.use_case == "meeting_protocol", Prompt.is_active == True).limit(1)
        )
        p = p_q.scalar_one_or_none()
        if p is not None:
            prompt_text, prompt_model = p.prompt_text, p.model

    # First claim — set processing_started_at; re-claim does NOT reset it
    set_started = meeting.processing_started_at is None
    await _set_meeting_status(
        db, meeting, MeetingStatus.TRANSCRIBING.value, set_processing_started=set_started
    )
    await db.commit()
    await db.refresh(job)

    return JobClaimResponse(
        id=job.id,
        meeting_id=job.meeting_id,
        audio_local_path=job.audio_local_path,
        priority=job.priority,
        attempts=job.attempts,
        expected_duration_seconds=job.expected_duration_seconds,
        user_id=meeting.user_id,
        prompt_text=prompt_text,
        prompt_model=prompt_model,
    )


@router.post("/jobs/{job_id}/heartbeat", dependencies=[Depends(verify_api_key)])
async def job_heartbeat(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Worker pings every 30s to prove it's still alive.

    No-ops on already-terminal jobs (won't fight with concurrent /complete).
    """
    result = await db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id, ProcessingJob.status == "claimed")
        .values(heartbeat_at=datetime.now(timezone.utc))
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Job not claimed or unknown")
    await db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/progress", dependencies=[Depends(verify_api_key)])
async def job_progress(
    job_id: uuid.UUID,
    body: JobProgress,
    db: AsyncSession = Depends(get_db),
):
    """Worker reports a stage transition.

    Updates Meeting.status (which pushes WebSocket meeting.updated)
    so the client sees 'Расшифровываем...' / 'Анализируем...'.
    """
    job_q = await db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
    job = job_q.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "claimed":
        raise HTTPException(status_code=409, detail=f"Job not in claimed state (got {job.status})")

    meeting_q = await db.execute(select(Meeting).where(Meeting.id == job.meeting_id))
    meeting = meeting_q.scalar_one()
    new_status = MeetingStatus.TRANSCRIBING.value if body.stage == "transcribing" else MeetingStatus.ANALYZING.value

    await _set_meeting_status(db, meeting, new_status)
    await db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/complete", dependencies=[Depends(verify_api_key)])
async def job_complete(
    job_id: uuid.UUID,
    body: JobComplete,
    db: AsyncSession = Depends(get_db),
):
    """Worker successfully finished — apply results to the meeting."""
    job_q = await db.execute(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .with_for_update()
    )
    job = job_q.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("claimed", "pending"):
        # Idempotent: completing an already-done job returns ok
        if job.status == "done":
            return {"ok": True, "duplicate": True}
        raise HTTPException(status_code=409, detail=f"Job not completable from {job.status}")

    meeting_q = await db.execute(select(Meeting).where(Meeting.id == job.meeting_id))
    meeting = meeting_q.scalar_one()

    was_completed = meeting.status == MeetingStatus.COMPLETED.value
    if body.title:
        meeting.title = body.title
    meeting.transcript_json = body.transcript_json
    meeting.transcript = body.transcript
    meeting.protocol = body.protocol
    meeting.tasks_json = body.tasks_json
    meeting.duration_seconds = body.duration_seconds

    final_status = (
        MeetingStatus.TRANSCRIPTION_EMPTY.value
        if not body.transcript or not body.transcript.strip()
        else MeetingStatus.COMPLETED.value
    )
    await _set_meeting_status(db, meeting, final_status)
    await _charge_usage_if_first_completion(db, meeting, was_completed)

    job.status = "done"

    # Persist speakers — idempotent via delete-then-insert
    new_speakers: list[MeetingSpeaker] = []
    if body.speakers:
        await db.execute(
            delete(MeetingSpeaker).where(MeetingSpeaker.meeting_id == meeting.id)
        )
        for sp in body.speakers:
            ms = MeetingSpeaker(
                meeting_id=meeting.id,
                speaker_label=sp.label,
                speaking_seconds=sp.speaking_seconds,
                name_suggestions=sp.name_suggestions or None,
                embedding=sp.embedding,
            )
            db.add(ms)
            new_speakers.append(ms)

        # Voice matching against this user's existing voice profiles.
        # Wrapped in try/except so a matching failure never blocks job completion.
        try:
            await _apply_voice_matching(db, meeting.user_id, meeting.id, new_speakers)
        except Exception as e:
            logger.warning("voice matching failed for meeting=%s: %s", meeting.id, e)

    # Total processing duration (queued → completed) for the metric
    if meeting.processing_started_at is not None:
        total_seconds = (datetime.now(timezone.utc) - meeting.processing_started_at).total_seconds()
        processing_jobs_duration_seconds.labels(stage="total").observe(total_seconds)

    await db.commit()
    return {"ok": True, "duplicate": False}


async def _apply_voice_matching(
    db: AsyncSession,
    user_id: uuid.UUID,
    meeting_id: uuid.UUID,
    new_speakers: list[MeetingSpeaker],
) -> None:
    """Voice match every new speaker against the user's voice profiles.
    Auto-bind at >=AUTO_BIND_THRESHOLD (with dedup so one participant maps
    to at most one speaker in this meeting). Append voice suggestions
    (high/medium) to name_suggestions for non-auto-bound speakers."""
    profiles = await load_user_profiles(db, user_id)
    if not profiles:
        return

    # Compute matches once per speaker
    matches_per_speaker: dict[str, list[tuple[uuid.UUID, str, float]]] = {}
    for ms in new_speakers:
        if ms.embedding is None:
            continue
        matches_per_speaker[ms.speaker_label] = match_speaker(ms.embedding, profiles)

    # Dedup auto-bind: if multiple speakers want same participant, only the
    # one with max similarity wins (tiebreak: max speaking_seconds).
    ms_by_label = {ms.speaker_label: ms for ms in new_speakers}
    candidates_per_pid: dict[uuid.UUID, list[tuple[str, float]]] = {}
    for label, matches in matches_per_speaker.items():
        if matches and matches[0][2] >= AUTO_BIND_THRESHOLD:
            candidates_per_pid.setdefault(matches[0][0], []).append((label, matches[0][2]))

    auto_bind_winners: dict[str, uuid.UUID] = {}   # speaker_label -> participant_id
    auto_bind_losers: dict[str, uuid.UUID] = {}    # speaker_label -> participant_id (still suggestion)
    for pid, contenders in candidates_per_pid.items():
        sorted_contenders = sorted(
            contenders,
            key=lambda x: (x[1], ms_by_label[x[0]].speaking_seconds or 0),
            reverse=True,
        )
        auto_bind_winners[sorted_contenders[0][0]] = pid
        for label, _ in sorted_contenders[1:]:
            auto_bind_losers[label] = pid

    # Apply auto-binds + immediately update voice profiles (running mean)
    for label, pid in auto_bind_winners.items():
        ms = ms_by_label[label]
        ms.participant_id = pid
        sim = next(s for s in matches_per_speaker[label] if s[0] == pid)[2]
        try:
            await update_voice_profile(db, pid, ms.embedding)
        except Exception as e:
            logger.warning("voice_profile update failed for participant=%s: %s", pid, e)
        logger.info(
            "voice auto-bind: speaker=%s -> participant=%s sim=%.3f meeting=%s",
            label, pid, sim, meeting_id,
        )

    # Build suggestions for speakers NOT auto-bound (auto-bound ones don't
    # need suggestions — picker doesn't show them for bound speakers).
    auto_bound_pids = set(auto_bind_winners.values())
    for ms in new_speakers:
        if ms.speaker_label in auto_bind_winners:
            continue
        matches = matches_per_speaker.get(ms.speaker_label, [])
        if not matches:
            continue

        existing_suggestions = list(ms.name_suggestions or [])
        voice_suggestions: list[dict] = []
        for i, (pid, name, sim) in enumerate(matches):
            # Top match was auto-bind candidate but lost dedup → demote
            if i == 0 and sim >= AUTO_BIND_THRESHOLD and ms.speaker_label in auto_bind_losers:
                sim = max(MEDIUM_SUGGESTION_THRESHOLD, sim - 0.05)
            # Skip participants already auto-bound to another speaker (one person = one chip)
            if pid in auto_bound_pids and auto_bind_losers.get(ms.speaker_label) != pid:
                continue
            if sim >= HIGH_SUGGESTION_THRESHOLD:
                evidence_type = "voice_high"
                evidence_label = "🎯 совпадение голоса (high)"
            elif sim >= MEDIUM_SUGGESTION_THRESHOLD:
                evidence_type = "voice_medium"
                evidence_label = "🎯 совпадение голоса (medium)"
            else:
                continue  # below threshold, skip rest (sorted DESC)
            voice_suggestions.append({
                "name": name,
                "confidence": round(sim, 3),
                "evidence": evidence_label,
                "evidence_type": evidence_type,
                "participant_id": str(pid),
            })

        # Limit to top-3 voice matches to avoid noise
        voice_suggestions = voice_suggestions[:3]

        # Dedup with LLM by name (voice is more reliable)
        voice_names = {v["name"].lower() for v in voice_suggestions}
        existing_filtered = [
            s for s in existing_suggestions
            if str(s.get("name", "")).lower() not in voice_names
        ]
        combined = voice_suggestions + existing_filtered
        combined.sort(key=lambda s: s.get("confidence", 0), reverse=True)
        ms.name_suggestions = combined or None


@router.post("/jobs/{job_id}/fail", dependencies=[Depends(verify_api_key)])
async def job_fail(
    job_id: uuid.UUID,
    body: JobFail,
    db: AsyncSession = Depends(get_db),
):
    """Worker hit an error.

    Retriable + budget remaining → bounce back to pending (silent retry,
    Meeting.status returns to 'queued'). Otherwise terminal failure with
    Meeting.status='error'.
    """
    job_q = await db.execute(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .with_for_update()
    )
    job = job_q.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("done", "failed"):
        return {"ok": True, "duplicate": True}

    meeting_q = await db.execute(select(Meeting).where(Meeting.id == job.meeting_id))
    meeting = meeting_q.scalar_one()

    job.attempts += 1
    if body.retriable and job.attempts < job.max_attempts:
        # Silent retry: job → pending, meeting → queued
        job.status = "pending"
        processing_jobs_retries_total.inc()
        job.claimed_by = None
        job.claimed_at = None
        job.heartbeat_at = None
        job.error_message = body.error_message  # for diagnostics, not user-visible
        await _set_meeting_status(db, meeting, MeetingStatus.QUEUED.value)
    else:
        # Terminal
        job.status = "failed"
        job.error_message = body.error_message
        # Classify failure reason for the metric
        msg_lower = body.error_message.lower()
        if "timeout" in msg_lower:
            reason = "worker_timeout"
        elif "whisper" in msg_lower:
            reason = "whisper"
        elif "pyannote" in msg_lower:
            reason = "pyannote"
        elif "deepseek" in msg_lower:
            reason = "deepseek"
        else:
            reason = "unknown"
        processing_jobs_failures_total.labels(reason=reason).inc()
        await _set_meeting_status(
            db, meeting, MeetingStatus.ERROR.value, error_message=body.error_message
        )

    await db.commit()
    return {"ok": True, "duplicate": False, "retried": job.status == "pending"}

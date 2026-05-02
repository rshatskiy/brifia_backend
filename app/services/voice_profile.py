"""Voice profile matching for speaker identification across meetings.

Embeddings are 128-dim wespeaker centroids from pyannote community-1, stored
L2-normalized. Cosine similarity between two L2-normed vectors is just their
dot product, so matching is one numpy.dot call per (speaker, profile) pair.

Profiles aggregate via running mean on every successful binding (manual or
auto). Re-normalization after each update keeps cosine semantics.
"""
import logging
import uuid

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.participant import Participant, ParticipantVoiceProfile

logger = logging.getLogger(__name__)


# Confidence thresholds (calibrated for L2-normed wespeaker 128-dim).
AUTO_BIND_THRESHOLD = 0.95
HIGH_SUGGESTION_THRESHOLD = 0.80
MEDIUM_SUGGESTION_THRESHOLD = 0.65


async def load_user_profiles(
    db: AsyncSession, user_id: uuid.UUID
) -> list[tuple[uuid.UUID, str, np.ndarray]]:
    """Returns list of (participant_id, participant_name, embedding) for every
    participant of `user_id` that has a voice profile. Empty list if none."""
    rows = await db.execute(
        select(
            ParticipantVoiceProfile.participant_id,
            Participant.name,
            ParticipantVoiceProfile.embedding,
        )
        .join(Participant, Participant.id == ParticipantVoiceProfile.participant_id)
        .where(Participant.user_id == user_id)
    )
    return [
        (pid, name, np.asarray(emb, dtype=np.float32))
        for pid, name, emb in rows.all()
    ]


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """For L2-normed vectors, cosine similarity = dot product. Inputs MUST
    already be L2-normalized — we do not re-normalize here."""
    return float(np.dot(a, b))


def match_speaker(
    speaker_embedding: list[float],
    profiles: list[tuple[uuid.UUID, str, np.ndarray]],
) -> list[tuple[uuid.UUID, str, float]]:
    """Returns matches sorted by similarity DESC. Caller decides what to do
    with each based on AUTO_BIND/HIGH/MEDIUM thresholds."""
    if not profiles or speaker_embedding is None:
        return []
    spk = np.asarray(speaker_embedding, dtype=np.float32)
    out = [
        (pid, name, cosine_similarity(spk, emb))
        for pid, name, emb in profiles
    ]
    out.sort(key=lambda x: x[2], reverse=True)
    return out


async def update_voice_profile(
    db: AsyncSession,
    participant_id: uuid.UUID,
    new_embedding: list[float],
) -> None:
    """Insert-or-update profile via running mean. The new sample MUST already
    be L2-normalized. Re-normalizes the combined vector to keep cosine
    semantics. No-op (with warning) if new_embedding is empty/invalid."""
    if not new_embedding:
        return
    new_arr = np.asarray(new_embedding, dtype=np.float32)
    if new_arr.size == 0:
        return

    existing_q = await db.execute(
        select(ParticipantVoiceProfile).where(
            ParticipantVoiceProfile.participant_id == participant_id
        )
    )
    existing = existing_q.scalar_one_or_none()
    if existing is None:
        # Create with this single sample. Already L2-normed by worker.
        db.add(ParticipantVoiceProfile(
            participant_id=participant_id,
            embedding=new_arr.tolist(),
            samples_count=1,
        ))
        await db.flush()
        logger.info("voice profile created for participant=%s (samples=1)", participant_id)
        return

    # Running mean: combine, then re-normalize.
    old_arr = np.asarray(existing.embedding, dtype=np.float32)
    n = existing.samples_count or 1
    combined = (old_arr * n + new_arr) / (n + 1)
    norm = float(np.linalg.norm(combined))
    if norm > 0:
        combined = combined / norm
    existing.embedding = combined.tolist()
    existing.samples_count = n + 1
    await db.flush()
    logger.info(
        "voice profile updated for participant=%s (samples=%d)",
        participant_id, existing.samples_count,
    )

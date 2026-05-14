"""Upload state management endpoints.

These endpoints are called by the faster-whisper server to persist
upload state in PostgreSQL instead of in-memory dict.
Authenticated via shared API key (same as /internal/*).
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models.upload import Upload
from app.config import get_settings

router = APIRouter(prefix="/internal/uploads", tags=["uploads"])


async def verify_api_key(x_api_key: str = Header(...)):
    settings = get_settings()
    if x_api_key != settings.faster_whisper_api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


class UploadCreateRequest(BaseModel):
    user_id: str
    meeting_id: str
    file_name: str
    total_size_bytes: int
    upload_dir: str


class UploadCreateResponse(BaseModel):
    upload_id: str
    is_existing: bool = False
    uploaded_chunks: dict = {}
    # Path to the chunk storage dir picked at first initiate. Returned on
    # resume so faster-whisper rebinds existing chunks to the SAME directory
    # — previously it minted a fresh uuid dir on every initiate and built
    # chunk paths under that empty dir, making assembly fail with
    # "Chunk 0 missing" when /complete fired. Stays None on fresh uploads.
    upload_dir: str | None = None


class ChunkRecordRequest(BaseModel):
    chunk_number: int
    size: int


class UploadCompleteRequest(BaseModel):
    expected_total_chunks: int


class UploadStatusResponse(BaseModel):
    upload_id: str
    user_id: str
    meeting_id: str
    file_name: str
    total_size_bytes: int
    status: str
    uploaded_chunks: dict
    expected_total_chunks: int | None

    model_config = {"from_attributes": True}


@router.post("", response_model=UploadCreateResponse, status_code=201,
             dependencies=[Depends(verify_api_key)])
async def create_or_resume_upload(
    body: UploadCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new upload or return existing one for the same meeting_id.

    Idempotent: if an upload for this meeting_id already exists and is not
    completed/error, return the existing upload_id and its uploaded_chunks
    so the client can resume.
    """
    # Check for existing active upload for this meeting
    result = await db.execute(
        select(Upload).where(
            Upload.meeting_id == body.meeting_id,
            Upload.user_id == uuid.UUID(body.user_id),
            Upload.status.notin_(["completed", "error"]),
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        return UploadCreateResponse(
            upload_id=str(existing.id),
            is_existing=True,
            uploaded_chunks=existing.uploaded_chunks or {},
            upload_dir=existing.upload_dir,
        )

    upload = Upload(
        user_id=uuid.UUID(body.user_id),
        meeting_id=body.meeting_id,
        file_name=body.file_name,
        total_size_bytes=body.total_size_bytes,
        status="pending",
        uploaded_chunks={},
        upload_dir=body.upload_dir,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    return UploadCreateResponse(upload_id=str(upload.id))


@router.get("/{upload_id}", response_model=UploadStatusResponse,
            dependencies=[Depends(verify_api_key)])
async def get_upload(
    upload_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Upload).where(Upload.id == upload_id))
    upload = result.scalar_one_or_none()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    return UploadStatusResponse(
        upload_id=str(upload.id),
        user_id=str(upload.user_id),
        meeting_id=upload.meeting_id,
        file_name=upload.file_name,
        total_size_bytes=upload.total_size_bytes,
        status=upload.status,
        uploaded_chunks=upload.uploaded_chunks or {},
        expected_total_chunks=upload.expected_total_chunks,
    )


@router.post("/{upload_id}/chunk", dependencies=[Depends(verify_api_key)])
async def record_chunk(
    upload_id: uuid.UUID,
    body: ChunkRecordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Record that a chunk was successfully saved to disk."""
    result = await db.execute(select(Upload).where(Upload.id == upload_id))
    upload = result.scalar_one_or_none()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    chunks = dict(upload.uploaded_chunks or {})
    chunks[str(body.chunk_number)] = {"size": body.size}
    upload.uploaded_chunks = chunks
    upload.status = "uploading"
    await db.commit()

    return {"status": "ok", "total_recorded": len(chunks)}


@router.post("/{upload_id}/assembling", dependencies=[Depends(verify_api_key)])
async def mark_assembling(
    upload_id: uuid.UUID,
    body: UploadCompleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark upload as assembling (all chunks received)."""
    result = await db.execute(select(Upload).where(Upload.id == upload_id))
    upload = result.scalar_one_or_none()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    upload.status = "assembling"
    upload.expected_total_chunks = body.expected_total_chunks
    await db.commit()
    return {"status": "ok"}


@router.post("/{upload_id}/status", dependencies=[Depends(verify_api_key)])
async def update_upload_status(
    upload_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update upload status (processing, completed, error)."""
    result = await db.execute(select(Upload).where(Upload.id == upload_id))
    upload = result.scalar_one_or_none()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    new_status = body.get("status")
    if new_status:
        upload.status = new_status
        await db.commit()
    return {"status": upload.status}

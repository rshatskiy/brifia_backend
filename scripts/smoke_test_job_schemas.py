"""Smoke tests for ProcessingJob Pydantic schemas."""
import os
import sys
import uuid
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import ValidationError
from app.schemas.processing_job import (
    JobCreate, JobClaimResponse, JobProgress, JobComplete, JobFail,
)


def main():
    failures = []

    # JobCreate accepts minimal payload
    try:
        JobCreate(meeting_id=uuid.uuid4(), audio_local_path="/tmp/x.wav")
    except ValidationError as e:
        failures.append(f"[1] JobCreate minimal failed: {e}")

    # JobProgress rejects unknown stage
    try:
        JobProgress(stage="unknown_stage")
        failures.append("[2] JobProgress accepted unknown stage")
    except ValidationError:
        pass

    # JobProgress accepts valid stages
    try:
        JobProgress(stage="transcribing")
        JobProgress(stage="analyzing")
    except ValidationError as e:
        failures.append(f"[3] JobProgress rejected valid stage: {e}")

    # JobFail requires retriable boolean
    try:
        JobFail(error_message="oops", retriable=True)
        JobFail(error_message="oops", retriable=False)
    except ValidationError as e:
        failures.append(f"[4] JobFail valid payload rejected: {e}")

    # JobComplete with speakers list
    try:
        JobComplete(
            transcript_json='{"segments":[]}',
            transcript="hello",
            duration_seconds=120,
            speakers=[{"label": "SPEAKER_0", "speaking_seconds": 60, "name_suggestions": []}],
        )
    except ValidationError as e:
        failures.append(f"[5] JobComplete with speakers failed: {e}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        sys.exit(1)
    print("OK: ProcessingJob schemas")


if __name__ == "__main__":
    main()

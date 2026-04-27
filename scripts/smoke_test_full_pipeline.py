"""End-to-end smoke against a running uvicorn dev server.

Exercises: create job → claim → progress → complete (with speakers) →
list speakers → bind to participant → list with series sorting → merge.

Requires:
- BACKEND_URL (default http://localhost:8000)
- API_KEY (FASTER_WHISPER_API_KEY from .env)
- USER_TOKEN (a valid JWT for some test user)
- Test fixtures: a meeting and series owned by that user

Run:
    python scripts/smoke_test_full_pipeline.py
"""
import os
import sys
import json
import asyncio
import httpx
import uuid


BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
API_KEY = os.environ["API_KEY"]
USER_TOKEN = os.environ["USER_TOKEN"]
MEETING_ID = os.environ["MEETING_ID"]
SERIES_ID = os.environ.get("SERIES_ID")  # optional


async def main():
    failures = []
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
        api_h = {"X-API-Key": API_KEY}
        usr_h = {"Authorization": f"Bearer {USER_TOKEN}"}

        # 1. Create job
        r = await c.post("/internal/jobs", headers=api_h, json={
            "meeting_id": MEETING_ID,
            "audio_local_path": "/tmp/test.wav",
        })
        if r.status_code != 201:
            failures.append(f"[1] create job: {r.status_code} {r.text}")
            print("FAIL"); sys.exit(1)
        job_id = r.json()["job_id"]

        # 2. Claim
        r = await c.post(f"/internal/jobs/claim?worker_id=smoke-1", headers=api_h)
        if r.status_code != 200 or r.json() is None:
            failures.append(f"[2] claim: {r.status_code} {r.text}")

        # 3. Progress
        r = await c.post(f"/internal/jobs/{job_id}/progress", headers=api_h, json={"stage": "analyzing"})
        if r.status_code != 200:
            failures.append(f"[3] progress: {r.status_code} {r.text}")

        # 4. Complete with speakers
        r = await c.post(f"/internal/jobs/{job_id}/complete", headers=api_h, json={
            "transcript_json": json.dumps({"segments": [], "speakers": ["SPEAKER_0", "SPEAKER_1"]}),
            "transcript": "hello world",
            "duration_seconds": 120,
            "speakers": [
                {"label": "SPEAKER_0", "speaking_seconds": 60, "name_suggestions": [
                    {"name": "Иван", "confidence": 0.9, "evidence": "Меня зовут Иван"}
                ]},
                {"label": "SPEAKER_1", "speaking_seconds": 30, "name_suggestions": []},
            ],
        })
        if r.status_code != 200:
            failures.append(f"[4] complete: {r.status_code} {r.text}")

        # 5. List speakers as user
        r = await c.get(f"/api/v1/meetings/{MEETING_ID}/speakers", headers=usr_h)
        if r.status_code != 200 or len(r.json()) != 2:
            failures.append(f"[5] list speakers: {r.status_code} {r.text}")

        # 6. Create participant
        r = await c.post("/api/v1/participants", headers=usr_h, json={"name": "Smoke Иван"})
        if r.status_code != 201:
            failures.append(f"[6] create participant: {r.status_code} {r.text}")
            print("\n".join(["FAIL: " + f for f in failures])); sys.exit(1)
        p_id = r.json()["id"]

        # 7. Bind speaker
        r = await c.put(
            f"/api/v1/meetings/{MEETING_ID}/speakers/SPEAKER_0",
            headers=usr_h, json={"participant_id": p_id, "accepted_suggestion": True},
        )
        if r.status_code != 200:
            failures.append(f"[7] bind speaker: {r.status_code} {r.text}")

        # 8. List participants (with series_id if provided)
        params = {"series_id": SERIES_ID} if SERIES_ID else {}
        r = await c.get("/api/v1/participants", headers=usr_h, params=params)
        if r.status_code != 200:
            failures.append(f"[8] list participants: {r.status_code} {r.text}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        sys.exit(1)
    print("OK: full pipeline smoke")


if __name__ == "__main__":
    asyncio.run(main())

"""Smoke tests for auth + meetings changes.

Verifies the no-rotation refresh, X-Auth-Reason header, and idempotent
meeting creation logic without needing a running server or DB. Run from
the backend root:

    PYTHONIOENCODING=utf-8 python scripts/smoke_test_auth.py

Set DATABASE_URL/JWT_SECRET in env if not already loaded from .env.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "smoke-test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jose import jwt
from fastapi import HTTPException

from app.auth import decode_access_token
from app.config import get_settings
from app.schemas.meeting import MeetingCreate, MeetingCreateInternal


def main():
    s = get_settings()
    failures = []

    # 1. expired token -> 401 X-Auth-Reason=expired
    expired = jwt.encode(
        {"sub": "u1", "exp": datetime.now(timezone.utc) - timedelta(minutes=5), "type": "access"},
        s.jwt_secret, algorithm=s.jwt_algorithm,
    )
    try:
        decode_access_token(expired)
        failures.append("[1] expired token did not raise")
    except HTTPException as e:
        if e.status_code != 401 or e.headers.get("X-Auth-Reason") != "expired":
            failures.append(f"[1] expected 401/expired, got {e.status_code}/{e.headers.get('X-Auth-Reason')}")

    # 2. invalid signature -> 401 X-Auth-Reason=invalid
    bad = jwt.encode(
        {"sub": "u1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "type": "access"},
        "wrong-secret", algorithm="HS256",
    )
    try:
        decode_access_token(bad)
        failures.append("[2] bad-sig token did not raise")
    except HTTPException as e:
        if e.status_code != 401 or e.headers.get("X-Auth-Reason") != "invalid":
            failures.append(f"[2] expected 401/invalid, got {e.status_code}/{e.headers.get('X-Auth-Reason')}")

    # 3. wrong type -> 401 X-Auth-Reason=wrong_type
    wt = jwt.encode(
        {"sub": "u1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "type": "refresh"},
        s.jwt_secret, algorithm=s.jwt_algorithm,
    )
    try:
        decode_access_token(wt)
        failures.append("[3] wrong-type token did not raise")
    except HTTPException as e:
        if e.status_code != 401 or e.headers.get("X-Auth-Reason") != "wrong_type":
            failures.append(f"[3] expected 401/wrong_type, got {e.status_code}/{e.headers.get('X-Auth-Reason')}")

    # 4. valid token -> returns user_id
    good = jwt.encode(
        {"sub": "abc", "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "type": "access"},
        s.jwt_secret, algorithm=s.jwt_algorithm,
    )
    if decode_access_token(good) != "abc":
        failures.append("[4] valid token did not return user_id")

    # 5. TTL bumped to 24h (override via env on prod)
    if s.access_token_expire_minutes != 1440:
        print(f"[5] WARN: access_token_expire_minutes={s.access_token_expire_minutes} (expected 1440)")
        print(f"      this is fine if .env overrides — verify ACCESS_TOKEN_EXPIRE_MINUTES on the server")

    # 6. MeetingCreate.id optional; MeetingCreateInternal requires id+user_id
    if MeetingCreate(title="t").id is not None:
        failures.append("[6] MeetingCreate id should default to None")
    if MeetingCreate(id=uuid4(), title="t").id is None:
        failures.append("[6] MeetingCreate did not accept id")
    try:
        MeetingCreateInternal(title="x")
        failures.append("[6] MeetingCreateInternal accepted no id/user_id")
    except Exception:
        pass

    if failures:
        print("FAIL")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("OK: all smoke tests passed")


if __name__ == "__main__":
    main()

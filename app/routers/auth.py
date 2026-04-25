import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
import httpx
from app.database import get_db
from app.models.user import User, RefreshToken
from app.models.profile import Profile
from app.auth import (
    hash_password, verify_password, hash_token,
    create_access_token, create_refresh_token,
    get_current_user, decode_access_token, security,
)
from app.schemas.auth import (
    RegisterRequest, LoginRequest, RefreshRequest,
    OAuthRequest, TokenResponse, VerifyTokenResponse,
    WebSessionRequestResponse, WebSessionExchangeRequest, WebSessionExchangeResponse,
)
from app.config import get_settings
from fastapi.security import HTTPAuthorizationCredentials
import secrets
from fastapi import Request
from sqlalchemy import func
from app.models.web_session_token import WebSessionToken

router = APIRouter(prefix="/auth", tags=["auth"])


async def _issue_tokens(user: User, db: AsyncSession) -> TokenResponse:
    settings = get_settings()
    access = create_access_token(str(user.id))
    refresh = create_refresh_token()

    rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(rt)
    await db.commit()

    return TokenResponse(access_token=access, refresh_token=refresh, user_id=str(user.id))


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=req.email,
        encrypted_password=hash_password(req.password),
        auth_provider="email",
    )
    db.add(user)
    await db.flush()

    profile = Profile(user_id=user.id, full_name=req.full_name)
    db.add(profile)
    await db.commit()

    return await _issue_tokens(user, db)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user or not user.encrypted_password or not verify_password(req.password, user.encrypted_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return await _issue_tokens(user, db)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    # No-rotation refresh: issue a new access token but re-use the same refresh token.
    #
    # Why no rotation:
    # The previous implementation deleted the old refresh token immediately on use
    # and issued a new one. When the mobile client made parallel /refresh calls
    # from foreground UI, BackgroundUploadService and the WebSocket reconnect
    # (all reacting to a single 401), only the first won — every other request
    # found the old token already deleted and got 401, which the client
    # interpreted as "session lost", forcing logout and breaking active recordings.
    #
    # Disabling rotation eliminates the race entirely. The refresh token keeps
    # its original 30-day TTL; on suspected compromise the user signs out, which
    # deletes the refresh token from the DB and revokes it for all clients.
    token_h = hash_token(req.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_h,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    rt = result.scalar_one_or_none()
    if not rt:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user_result = await db.execute(select(User).where(User.id == rt.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    new_access = create_access_token(str(user.id))
    return TokenResponse(
        access_token=new_access,
        refresh_token=req.refresh_token,
        user_id=str(user.id),
    )


@router.post("/oauth", response_model=TokenResponse)
async def oauth_login(req: OAuthRequest, db: AsyncSession = Depends(get_db)):
    settings = get_settings()

    if req.provider == "google":
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={req.id_token}")
            if resp.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid Google token")
            google_data = resp.json()

        email = google_data.get("email")
        if not email:
            raise HTTPException(status_code=401, detail="Email not provided by Google")

        aud = google_data.get("aud", "")
        valid_audiences = [settings.google_client_id, settings.google_ios_client_id]
        if aud not in valid_audiences:
            raise HTTPException(status_code=401, detail="Invalid token audience")

        provider_user_id = google_data.get("sub")
        full_name = req.full_name or google_data.get("name")

    elif req.provider == "apple":
        # Apple ID token verification via Apple's public keys
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://appleid.apple.com/auth/keys")
            if resp.status_code != 200:
                raise HTTPException(status_code=401, detail="Cannot verify Apple token")

        from jose import jwt as jose_jwt, jwk
        apple_keys = resp.json()["keys"]
        try:
            header = jose_jwt.get_unverified_header(req.id_token)
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid Apple token")
        key = next((k for k in apple_keys if k.get("kid") == header.get("kid")), None)
        if not key:
            raise HTTPException(status_code=401, detail="Invalid Apple token")

        try:
            payload = jose_jwt.decode(
                req.id_token,
                key,
                algorithms=["RS256"],
                audience=settings.apple_bundle_id,  # com.brifia.app
                issuer="https://appleid.apple.com",
            )
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid Apple token")

        email = payload.get("email")
        provider_user_id = payload.get("sub")
        full_name = req.full_name

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {req.provider}")

    # Find or create user
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            email=email,
            auth_provider=req.provider,
            provider_user_id=provider_user_id,
        )
        db.add(user)
        await db.flush()
        profile = Profile(user_id=user.id, full_name=full_name)
        db.add(profile)
        await db.commit()

    return await _issue_tokens(user, db)


@router.get("/verify", response_model=VerifyTokenResponse)
async def verify_token(user: User = Depends(get_current_user)):
    """Endpoint for faster-whisper server to verify tokens."""
    return VerifyTokenResponse(user_id=str(user.id), email=user.email)


@router.delete("/account")
async def delete_account(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.delete(user)
    await db.commit()
    return {"message": "Account deleted"}


WEB_SESSION_TOKEN_TTL_SECONDS = 300
WEB_SESSION_RATE_LIMIT_COUNT = 10
WEB_SESSION_RATE_LIMIT_WINDOW_MINUTES = 5


@router.post("/web-session/request", response_model=WebSessionRequestResponse)
async def web_session_request(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    window_start = datetime.now(timezone.utc) - timedelta(
        minutes=WEB_SESSION_RATE_LIMIT_WINDOW_MINUTES
    )
    count_result = await db.execute(
        select(func.count(WebSessionToken.id)).where(
            WebSessionToken.user_id == user.id,
            WebSessionToken.created_at > window_start,
        )
    )
    recent_count = count_result.scalar_one() or 0
    if recent_count >= WEB_SESSION_RATE_LIMIT_COUNT:
        raise HTTPException(status_code=429, detail="Too many handoff requests")

    raw_token = secrets.token_urlsafe(32)
    record = WebSessionToken(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=WEB_SESSION_TOKEN_TTL_SECONDS),
        ip_created=request.client.host if request.client else None,
    )
    db.add(record)
    await db.commit()
    return WebSessionRequestResponse(
        token=raw_token, expires_in=WEB_SESSION_TOKEN_TTL_SECONDS
    )


@router.post("/web-session/exchange", response_model=WebSessionExchangeResponse)
async def web_session_exchange(
    body: WebSessionExchangeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not body.token or len(body.token) < 20:
        raise HTTPException(status_code=400, detail="Invalid token format")

    token_h = hash_token(body.token)
    result = await db.execute(
        select(WebSessionToken)
        .where(
            WebSessionToken.token_hash == token_h,
            WebSessionToken.used_at.is_(None),
            WebSessionToken.expires_at > datetime.now(timezone.utc),
        )
        .with_for_update(skip_locked=True)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=410, detail="Token invalid or expired")

    user_result = await db.execute(select(User).where(User.id == record.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=410, detail="Token invalid or expired")

    record.used_at = datetime.now(timezone.utc)
    record.ip_used = request.client.host if request.client else None
    await db.flush()

    access = create_access_token(str(user.id))
    refresh = create_refresh_token()
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=get_settings().refresh_token_expire_days),
    )
    db.add(rt)
    await db.commit()

    return WebSessionExchangeResponse(
        access_token=access, refresh_token=refresh, user_id=str(user.id)
    )

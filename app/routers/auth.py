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
)
from app.config import get_settings
from fastapi.security import HTTPAuthorizationCredentials

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

    await db.delete(rt)
    await db.commit()

    return await _issue_tokens(user, db)


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
        header = jose_jwt.get_unverified_header(req.id_token)
        key = next((k for k in apple_keys if k["kid"] == header["kid"]), None)
        if not key:
            raise HTTPException(status_code=401, detail="Invalid Apple token")

        try:
            payload = jose_jwt.decode(
                req.id_token,
                key,
                algorithms=["RS256"],
                audience=settings.google_client_id,  # Apple bundle ID
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

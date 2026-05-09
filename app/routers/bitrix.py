"""Bitrix24 OAuth + credentials endpoints.

Flow:
  1. Client calls POST /api/v1/bitrix/oauth/init with portal_url. Server signs
     a state payload (user_id + portal_url) with HMAC and returns the Bitrix
     authorization URL pointing back to GET /api/v1/bitrix/oauth/callback.
  2. User authorizes in WebView. Bitrix redirects to /oauth/callback with
     ?code=&state=. The callback verifies the state, exchanges the code for
     tokens directly with the user's Bitrix portal, persists to
     `bitrix_integrations`, and redirects to /oauth/success or /oauth/error.
  3. Client WebView watches for those final URLs to pop the result.
  4. Client then calls GET /api/v1/bitrix/credentials to retrieve the saved
     access/refresh tokens for local API calls into the user's portal.
  5. /oauth/refresh handles 401 retries from the client.

Replaces the legacy supabase Edge Function `bitrix-oauth`. State signing
prevents the unauthenticated callback from being abused to write tokens for
arbitrary user_ids.
"""
import base64
import logging
import hashlib
import hmac
import json
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.bitrix_integration import BitrixIntegration
from app.models.user import User
from app.schemas.bitrix import (
    BitrixCredentialsResponse,
    BitrixOAuthInitRequest,
    BitrixOAuthInitResponse,
    BitrixRefreshRequest,
    BitrixRefreshResponse,
    BitrixStatusResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bitrix", tags=["bitrix"])


# ---- State signing ----

def _sign_state(user_id: str, portal_url: str) -> str:
    """HMAC-signed state token. Format: base64url(payload).base64url(signature).

    Bitrix echoes this verbatim in the callback redirect; we re-derive the
    signature server-side to confirm authenticity before writing tokens.
    """
    settings = get_settings()
    payload = json.dumps({
        "user_id": user_id,
        "portal_url": portal_url,
        "nonce": secrets.token_hex(8),
    }, separators=(",", ":")).encode()
    sig = hmac.new(settings.jwt_secret.encode(), payload, hashlib.sha256).digest()
    return f"{base64.urlsafe_b64encode(payload).decode().rstrip('=')}." \
           f"{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def _verify_state(state: str) -> tuple[str, str]:
    settings = get_settings()
    try:
        payload_b64, sig_b64 = state.split(".", 1)
        # base64 lib needs padding restored
        payload = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed state")

    expected_sig = hmac.new(settings.jwt_secret.encode(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        raise HTTPException(status_code=400, detail="Invalid state signature")

    data = json.loads(payload.decode())
    user_id = data.get("user_id")
    portal_url = data.get("portal_url")
    if not user_id or not portal_url:
        raise HTTPException(status_code=400, detail="State missing fields")
    return user_id, portal_url


# ---- App-not-installed detection (mirrors legacy Edge Function) ----

_APP_NOT_INSTALLED_KEYWORDS = (
    "application not found",
    "application not installed",
    "приложение не найдено",
    "приложение не установлено",
)


def _looks_like_app_not_installed(text: str | None) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in _APP_NOT_INSTALLED_KEYWORDS)


# ---- Helpers ----

def _redirect_uri() -> str:
    settings = get_settings()
    base = settings.bitrix_redirect_base.rstrip("/")
    return f"{base}/api/v1/bitrix/oauth/callback"


def _strip_portal(portal_url: str) -> str:
    """Bitrix expects the portal as `host`, not `https://host/`."""
    p = portal_url.strip()
    if p.startswith("https://"):
        p = p[len("https://"):]
    if p.startswith("http://"):
        p = p[len("http://"):]
    return p.rstrip("/")


# ---- Endpoints ----

@router.post("/oauth/init", response_model=BitrixOAuthInitResponse)
async def oauth_init(
    req: BitrixOAuthInitRequest,
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    if not settings.bitrix_client_id:
        raise HTTPException(status_code=500, detail="Bitrix client_id is not configured")

    portal = _strip_portal(req.portal_url)
    if not portal:
        raise HTTPException(status_code=400, detail="portal_url is required")

    state = _sign_state(str(user.id), portal)
    redirect = _redirect_uri()
    auth_url = (
        f"https://{portal}/oauth/authorize/"
        f"?client_id={urllib.parse.quote(settings.bitrix_client_id)}"
        f"&response_type=code"
        f"&redirect_uri={urllib.parse.quote(redirect, safe='')}"
        f"&state={urllib.parse.quote(state, safe='')}"
    )
    logger.info(
        "bitrix_oauth_init user=%s portal=%s redirect_uri=%s client_id=%s",
        user.id, portal, redirect, settings.bitrix_client_id,
    )
    return BitrixOAuthInitResponse(authorization_url=auth_url)


@router.api_route("/oauth/callback", methods=["GET", "HEAD"])
async def oauth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    # Bitrix marketplace HEAD-probes this URL to validate it's reachable.
    # A bare HEAD has no code/state — short-circuit to 200 so the dashboard
    # accepts the URL. Real OAuth callbacks arrive as GET with params.
    if request.method == "HEAD":
        return HTMLResponse(status_code=200, content="")
    settings = get_settings()

    # Bitrix can redirect back with ?error=... before any code exchange. Detect
    # APPLICATION_NOT_INSTALLED specifically so the WebView can show a tailored
    # install panel instead of a generic failure.
    if error:
        details = error_description or error
        logger.warning(
            "bitrix_oauth_callback bitrix_returned_error error=%r description=%r",
            error, error_description,
        )
        if error == "access_denied" or _looks_like_app_not_installed(details):
            return _redirect_error("APPLICATION_NOT_INSTALLED", details)
        return _redirect_error(error, details)

    if not code or not state:
        logger.warning(
            "bitrix_oauth_callback missing_code_or_state code_len=%d state_len=%d",
            len(code or ""), len(state or ""),
        )
        return _redirect_error("missing_code_or_state", "Bitrix redirect missing code/state")

    try:
        user_id, portal_url = _verify_state(state)
    except HTTPException as exc:
        logger.warning("bitrix_oauth_callback invalid_state detail=%r", exc.detail)
        return _redirect_error("invalid_state", exc.detail)
    logger.info(
        "bitrix_oauth_callback received user=%s portal=%s code_prefix=%s",
        user_id, portal_url, (code or "")[:8],
    )

    if not settings.bitrix_client_id or not settings.bitrix_client_secret:
        return _redirect_error("server_misconfigured", "Bitrix client credentials missing on server")

    token_url = (
        f"https://{portal_url}/oauth/token/"
        f"?grant_type=authorization_code"
        f"&client_id={urllib.parse.quote(settings.bitrix_client_id)}"
        f"&client_secret={urllib.parse.quote(settings.bitrix_client_secret)}"
        f"&code={urllib.parse.quote(code)}"
        f"&redirect_uri={urllib.parse.quote(_redirect_uri(), safe='')}"
    )

    redacted_token_url = (
        token_url
        .replace(settings.bitrix_client_secret, "<SECRET>")
        if settings.bitrix_client_secret else token_url
    )
    logger.info("bitrix_oauth_token_exchange GET %s", redacted_token_url)

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.get(token_url)
        except httpx.HTTPError as e:
            logger.warning("bitrix_oauth_network_error portal=%s err=%s", portal_url, e)
            return _redirect_error("network_error", str(e))

    if resp.status_code != 200:
        body = resp.text
        logger.warning(
            "bitrix_oauth_token_exchange_failed portal=%s status=%d body=%r",
            portal_url, resp.status_code, body[:600],
        )
        # Bitrix sometimes wraps the message in JSON, sometimes in plain text.
        details = body
        try:
            data = resp.json()
            details = data.get("error_description") or data.get("error") or body
        except Exception:
            pass
        if _looks_like_app_not_installed(details) or (
            resp.status_code == 400 and ("invalid_client" in details.lower() or "client is invalid" in details.lower())
        ):
            return _redirect_error("APPLICATION_NOT_INSTALLED", details)
        return _redirect_error("token_exchange_failed", details)

    tokens = resp.json()
    if tokens.get("error"):
        details = tokens.get("error_description") or tokens.get("error")
        logger.warning(
            "bitrix_oauth_token_payload_error portal=%s error=%r description=%r",
            portal_url, tokens.get("error"), tokens.get("error_description"),
        )
        if _looks_like_app_not_installed(details) or "invalid_grant" in str(tokens.get("error", "")).lower():
            return _redirect_error("APPLICATION_NOT_INSTALLED", details)
        return _redirect_error("b24_token_error", details)
    logger.info(
        "bitrix_oauth_token_ok portal=%s bx_user_id=%s expires_in=%s scope=%r",
        portal_url, tokens.get("user_id"), tokens.get("expires_in"), tokens.get("scope"),
    )

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = int(tokens.get("expires_in") or 3600)
    if not access_token or not refresh_token:
        return _redirect_error("incomplete_tokens", "Bitrix did not return access/refresh tokens")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    bitrix_user_id = str(tokens["user_id"]) if tokens.get("user_id") is not None else None
    member_id = tokens.get("member_id")

    # Upsert by (user_id, portal_url)
    import uuid as _uuid
    user_uuid = _uuid.UUID(user_id)
    existing = await db.execute(
        select(BitrixIntegration).where(
            BitrixIntegration.user_id == user_uuid,
            BitrixIntegration.portal_url == portal_url,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.access_token = access_token
        row.refresh_token = refresh_token
        row.bitrix_user_id = bitrix_user_id
        row.member_id = member_id
        row.expires_at = expires_at
    else:
        db.add(BitrixIntegration(
            user_id=user_uuid,
            portal_url=portal_url,
            access_token=access_token,
            refresh_token=refresh_token,
            bitrix_user_id=bitrix_user_id,
            member_id=member_id,
            expires_at=expires_at,
        ))
    await db.commit()

    return _redirect_success()


@router.api_route("/oauth/success", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def oauth_success():
    # Plain HTML page the WebView watches for. Body content is not rendered to
    # the user — they'll have already returned to the app at this point — but
    # we keep it friendly in case the page is opened in a regular browser.
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Brifia · Bitrix24</title></head>"
        "<body><h1>Brifia подключён к Bitrix24</h1>"
        "<p>Можно вернуться в приложение.</p></body></html>"
    )


@router.api_route("/oauth/error", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def oauth_error(
    error: str = Query(default="unknown"),
    details: str = Query(default=""),
):
    safe_error = error.replace("<", "&lt;")
    safe_details = details.replace("<", "&lt;")
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Brifia · Ошибка авторизации Bitrix24</title></head>"
        f"<body><h1>Не удалось подключить Bitrix24</h1>"
        f"<p>Код: {safe_error}</p><p>{safe_details}</p></body></html>"
    )


# --- Install / settings handlers for Bitrix marketplace app ---
# Bitrix calls /install (POST) when an admin installs the app on a portal,
# expecting a 200 response. We don't persist anything here because at install
# time we don't know which Brifia user this portal belongs to — that mapping
# is established later when the user does the in-app OAuth dance, which carries
# a signed state with the Brifia user_id.
#
# /settings is what Bitrix opens (in-iframe) when an admin clicks "Settings"
# on the installed app. Brifia is a mobile-first app — settings live inside
# the mobile UI — so this just shows a friendly redirect message.

@router.api_route("/install", methods=["GET", "POST", "HEAD"], response_class=HTMLResponse)
async def install_handler(request: Request):
    # Log the install ping so we can confirm the portal/admin who actually
    # mounted the app — useful for debugging "приложение не установлено"
    # mismatches between OAuth state and the marketplace registry.
    try:
        if request.method == "POST":
            payload = dict(await request.form())
        else:
            payload = dict(request.query_params)
    except Exception:
        payload = {}
    logger.info(
        "bitrix_install_handler method=%s domain=%s member_id=%s payload_keys=%s",
        request.method,
        payload.get("DOMAIN") or payload.get("domain"),
        payload.get("member_id") or payload.get("MEMBER_ID"),
        list(payload.keys()),
    )
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Brifia установлен</title></head>"
        "<body><h1>Brifia установлен на портал</h1>"
        "<p>Откройте мобильное приложение Brifia, перейдите в настройки "
        "интеграций и подключите Bitrix24, указав адрес портала.</p>"
        "</body></html>"
    )


@router.api_route("/settings", methods=["GET", "POST", "HEAD"], response_class=HTMLResponse)
async def settings_handler():
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Brifia · Настройки</title></head>"
        "<body><h1>Brifia</h1>"
        "<p>Все настройки интеграции находятся в мобильном приложении Brifia.</p>"
        "<p><a href='https://brifia.ru'>brifia.ru</a></p>"
        "</body></html>"
    )


@router.get("/credentials", response_model=BitrixCredentialsResponse)
async def get_credentials(
    portal_url: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the stored Bitrix tokens for this user.

    If `portal_url` is provided, returns that specific integration. Otherwise
    returns the most-recently-updated one (a user typically has only one).
    """
    stmt = select(BitrixIntegration).where(BitrixIntegration.user_id == user.id)
    if portal_url:
        stmt = stmt.where(BitrixIntegration.portal_url == _strip_portal(portal_url))
    else:
        stmt = stmt.order_by(BitrixIntegration.updated_at.desc())
    result = await db.execute(stmt)
    row = result.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Bitrix integration not found")
    return BitrixCredentialsResponse(
        portal_url=row.portal_url,
        bitrix_user_id=row.bitrix_user_id,
        access_token=row.access_token,
        refresh_token=row.refresh_token,
        expires_at=row.expires_at,
    )


@router.get("/status", response_model=BitrixStatusResponse)
async def get_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BitrixIntegration)
        .where(BitrixIntegration.user_id == user.id)
        .order_by(BitrixIntegration.updated_at.desc())
    )
    row = result.scalars().first()
    if not row:
        return BitrixStatusResponse(connected=False)
    return BitrixStatusResponse(connected=True, portal_url=row.portal_url, expires_at=row.expires_at)


@router.post("/oauth/refresh", response_model=BitrixRefreshResponse)
async def oauth_refresh(
    req: BitrixRefreshRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    if not settings.bitrix_client_id or not settings.bitrix_client_secret:
        raise HTTPException(status_code=500, detail="Bitrix credentials not configured")

    portal_url = _strip_portal(req.portal_url)
    result = await db.execute(
        select(BitrixIntegration).where(
            BitrixIntegration.user_id == user.id,
            BitrixIntegration.portal_url == portal_url,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Bitrix integration not found")

    refresh_url = (
        f"https://{portal_url}/oauth/token/"
        f"?grant_type=refresh_token"
        f"&client_id={urllib.parse.quote(settings.bitrix_client_id)}"
        f"&client_secret={urllib.parse.quote(settings.bitrix_client_secret)}"
        f"&refresh_token={urllib.parse.quote(row.refresh_token)}"
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(refresh_url)

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Bitrix refresh failed: {resp.text}")

    data = resp.json()
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data.get("error_description") or data.get("error"))

    new_access = data.get("access_token")
    new_refresh = data.get("refresh_token") or row.refresh_token
    expires_in = int(data.get("expires_in") or 3600)
    if not new_access:
        raise HTTPException(status_code=502, detail="Bitrix returned no access_token")

    row.access_token = new_access
    row.refresh_token = new_refresh
    row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    await db.commit()

    return BitrixRefreshResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_at=row.expires_at,
    )


@router.delete("/credentials", status_code=204)
async def disconnect(
    portal_url: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = delete(BitrixIntegration).where(BitrixIntegration.user_id == user.id)
    if portal_url:
        stmt = stmt.where(BitrixIntegration.portal_url == _strip_portal(portal_url))
    await db.execute(stmt)
    await db.commit()


# ---- Redirect builders ----

def _redirect_success() -> RedirectResponse:
    settings = get_settings()
    base = settings.bitrix_redirect_base.rstrip("/")
    return RedirectResponse(url=f"{base}/api/v1/bitrix/oauth/success", status_code=302)


def _redirect_error(error: str, details: str | None) -> RedirectResponse:
    settings = get_settings()
    base = settings.bitrix_redirect_base.rstrip("/")
    qs = urllib.parse.urlencode({"error": error, "details": details or ""})
    return RedirectResponse(url=f"{base}/api/v1/bitrix/oauth/error?{qs}", status_code=302)

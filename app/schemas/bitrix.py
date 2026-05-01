from pydantic import BaseModel
from datetime import datetime


class BitrixOAuthInitRequest(BaseModel):
    portal_url: str


class BitrixOAuthInitResponse(BaseModel):
    authorization_url: str


class BitrixCredentialsResponse(BaseModel):
    portal_url: str
    bitrix_user_id: str | None
    access_token: str
    refresh_token: str
    expires_at: datetime


class BitrixRefreshRequest(BaseModel):
    portal_url: str


class BitrixRefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: datetime


class BitrixStatusResponse(BaseModel):
    connected: bool
    portal_url: str | None = None
    expires_at: datetime | None = None

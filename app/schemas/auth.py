from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class OAuthRequest(BaseModel):
    id_token: str
    provider: str  # google, apple
    full_name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str


class VerifyTokenResponse(BaseModel):
    user_id: str
    email: str


class WebSessionRequestResponse(BaseModel):
    token: str
    expires_in: int


class WebSessionExchangeRequest(BaseModel):
    token: str


class WebSessionExchangeResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class GenericMessageResponse(BaseModel):
    message: str

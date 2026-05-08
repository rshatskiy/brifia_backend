from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/brifia"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24h — was 30min, raised for B2C recorder use case (long meetings)
    refresh_token_expire_days: int = 30

    google_client_id: str = ""
    google_ios_client_id: str = ""
    google_client_secret: str = ""

    apple_bundle_id: str = "com.brifia.app"
    apple_client_secret: str = ""

    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    payment_success_url: str = ""
    payment_cancel_url: str = ""

    faster_whisper_api_key: str = ""

    # Bitrix24 OAuth — registered marketplace app credentials.
    # bitrix_redirect_base must be the public base URL of this API
    # (e.g. https://api2.brifia.ru); it's used to build /oauth/callback,
    # /oauth/success and /oauth/error URLs that the WebView intercepts.
    bitrix_client_id: str = ""
    bitrix_client_secret: str = ""
    bitrix_redirect_base: str = "https://api2.brifia.ru"

    # SMTP for outbound mail (protocol email-share). All optional — if
    # smtp_host/smtp_from is empty, /share/email returns 503 with an
    # explicit "not configured" error rather than silently dropping.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # default From address — typically noreply@brifia.ru
    smtp_use_tls: bool = False     # implicit TLS (port 465)
    smtp_use_starttls: bool = True # STARTTLS upgrade (port 587)

    # Base URL for the public web app — used to build links inside
    # transactional emails (password reset, dashboard CTA, etc.).
    web_base_url: str = "https://brifia.ru"

    # Voice profile matching — when False, server does NOT compute speaker
    # similarity, does NOT store aggregated voice profiles, and does NOT
    # auto-bind based on voice. Embeddings still flow through to clients
    # via meeting_speakers.embedding for on-device matching (Phase 2).
    # Default False per legal team review (152-FZ biometrics): server-side
    # storage of voice fingerprints requires explicit consent + biometric
    # data policy + RKN notification, which we route around by moving
    # storage to user device.
    voice_profiles_server_matching: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()

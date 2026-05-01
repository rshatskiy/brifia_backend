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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/brifia"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    google_client_id: str = ""
    google_ios_client_id: str = ""
    google_client_secret: str = ""

    apple_bundle_id: str = "com.brifia.app"
    apple_client_secret: str = ""

    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_webhook_secret: str = ""
    payment_success_url: str = ""
    payment_cancel_url: str = ""

    faster_whisper_api_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()

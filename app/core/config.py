"""Application configuration — all settings loaded from environment."""

from functools import lru_cache
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        case_sensitive=False, 
        extra="ignore"
    )

    APP_NAME: str = "Gallery Vault"
    APP_ENV: str = "development"
    APP_DEBUG: bool = False
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_LOG_LEVEL: str = "INFO"
    APP_BASE_URL: str = "http://localhost:8000"

    MONGODB_URI: str
    MONGODB_DB_NAME: str = "gallery_vault"
    MONGODB_MAX_POOL_SIZE: int = 50
    MONGODB_MIN_POOL_SIZE: int = 10
    MONGODB_MAX_IDLE_TIME_MS: int = 30000
    MONGODB_SERVER_SELECTION_TIMEOUT_MS: int = 5000

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_ISSUER: str = "gallery-vault"
    JWT_AUDIENCE: str = "gallery-vault-users"

    TELEGRAM_API_ID: int = 0
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_SESSION_NAME: str = "gallery_vault"
    TELEGRAM_STORAGE_CHANNEL_ID: int = 0
    TELEGRAM_SESSION_STRING: str = ""

    BREVO_API_KEY: str = ""
    BREVO_SENDER_EMAIL: str = "send@mohinbd.com"
    BREVO_SENDER_NAME: str = "Gallery Vault"
    BREVO_API_BASE_URL: str = "https://api.brevo.com/v3"

    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"
    CORS_ALLOW_CREDENTIALS: bool = True

    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_AUTH: str = "5/minute"

    BCRYPT_ROUNDS: int = 12

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def access_token_expire_seconds(self) -> int:
        return self.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60

    @field_validator("APP_DEBUG", "CORS_ALLOW_CREDENTIALS", "RATE_LIMIT_ENABLED", mode="before")
    @classmethod
    def _parse_bool(cls, v: object) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes")
        return bool(v)

    @field_validator(
        "APP_PORT",
        "MONGODB_MAX_POOL_SIZE",
        "MONGODB_MIN_POOL_SIZE",
        "MONGODB_MAX_IDLE_TIME_MS",
        "MONGODB_SERVER_SELECTION_TIMEOUT_MS",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS",
        "BCRYPT_ROUNDS",
        "TELEGRAM_API_ID",
        "TELEGRAM_STORAGE_CHANNEL_ID",
        mode="before",
    )
    @classmethod
    def _parse_int(cls, v: object) -> int:
        if isinstance(v, str):
            return int(v)
        return int(v)


@lru_cache
def get_settings() -> Settings:
    return Settings()

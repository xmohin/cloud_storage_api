"""Application configuration — all settings loaded from environment."""

from functools import lru_cache
from typing import List
from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        case_sensitive=False, 
        extra="ignore"
    )

    APP_NAME: str = "Gallery Vault"
    # Some deployments set `ENVIRONMENT=` / `DEBUG=` instead of the
    # `APP_`-prefixed names — accept either so a .env written with the
    # common Docker/Heroku-style names doesn't silently no-op.
    APP_ENV: str = Field(default="development", validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"))
    APP_DEBUG: bool = Field(default=False, validation_alias=AliasChoices("APP_DEBUG", "DEBUG"))
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
    # Optional dedicated secret for refresh tokens (security.py falls back
    # to JWT_SECRET_KEY when this isn't set). Previously this env var was
    # silently dropped because Settings had no matching field.
    JWT_REFRESH_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_ISSUER: str = "gallery-vault"
    JWT_AUDIENCE: str = "gallery-vault-users"

    TELEGRAM_API_ID: int = 0
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_SESSION_NAME: str = "gallery_vault"
    TELEGRAM_STORAGE_CHANNEL_ID: int = Field(
        default=0, validation_alias=AliasChoices("TELEGRAM_STORAGE_CHANNEL_ID", "TELEGRAM_CHANNEL_ID")
    )
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
    # "memory://" keeps counters local to a single process — fine for one
    # worker, but under gunicorn's multi-worker mode (see Dockerfile
    # WEB_CONCURRENCY) each worker enforces the limit independently, so the
    # effective limit becomes (configured limit × worker count). Point this
    # at a shared backend (e.g. "redis://host:6379") in any multi-worker
    # deployment to get a real, process-wide limit.
    RATE_LIMIT_STORAGE_URI: str = "memory://"

    BCRYPT_ROUNDS: int = 12

    # ── Storage / Uploads ──
    MAX_UPLOAD_SIZE_MB: int = 2000
    TEMP_STORAGE_PATH: str = "/tmp/tmp_uploads"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


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
        "MAX_UPLOAD_SIZE_MB",
        mode="before",
    )
    @classmethod
    def _parse_int(cls, v: object, info: ValidationInfo) -> int:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                # Empty env var (e.g. `TELEGRAM_API_ID=`) — fall back to the
                # field's own default instead of crashing on int("").
                return cls.model_fields[info.field_name].default
        return int(v)


@lru_cache
def get_settings() -> Settings:
    return Settings()

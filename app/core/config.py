import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_NAME: str = "Gallery Vault"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False
    
    # MONGO_URI বা MONGODB_URI দুটোই রিড করবে
    MONGO_URI: str = Field(
        default="mongodb://localhost:27017/gallery_vault",
        validation_alias="MONGODB_URI"
    )
    DATABASE_NAME: str = "gallery_vault"
    
    JWT_SECRET_KEY: str = "secret_key_change_me_in_production_32bytes_min"
    JWT_REFRESH_SECRET_KEY: str = "refresh_secret_key_change_me_in_production"
    ALGORITHM: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    OTP_EXPIRE_MINUTES: int = 10
    
    TELEGRAM_API_ID: int = 0
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHANNEL_ID: int = 0
    
    BREVO_API_KEY: str = ""
    BREVO_SENDER_EMAIL: str = "send@mohinbd.com"
    BREVO_SENDER_NAME: str = "Gallery Vault"
    
    MAX_UPLOAD_SIZE_MB: int = 2000
    TEMP_STORAGE_PATH: str = "/tmp/gallery_vault_temp"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

try:
    os.makedirs(settings.TEMP_STORAGE_PATH, exist_ok=True)
except Exception:
    pass

import os
from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Gallery Vault API"
    ENVIRONMENT: str = Field(default="production")
    API_V1_STR: str = "/api/v1"
    
    # Security
    JWT_SECRET_KEY: str = Field(..., description="Secret key for JWT generation")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    
    # Database
    MONGODB_URI: str = Field(..., description="MongoDB connection string")
    MONGODB_DB_NAME: str = "gallery_vault"
    MONGODB_MIN_POOL_SIZE: int = 10
    MONGODB_MAX_POOL_SIZE: int = 100
    
    # Telegram (User Session)
    TELEGRAM_API_ID: int = Field(..., description="Telegram App API ID")
    TELEGRAM_API_HASH: str = Field(..., description="Telegram App API Hash")
    TELEGRAM_STRING_SESSION: str = Field(..., description="Telethon String Session for user account")
    TELEGRAM_CHANNEL_ID: int = Field(..., description="Telegram Channel ID for cloud storage")
    
    # Brevo Email Configuration
    BREVO_API_KEY: str = Field(..., description="Brevo Email API Key")
    BREVO_SENDER_EMAIL: str = Field(default="send@mohinbd.com", description="Sender email address")
    BREVO_SENDER_NAME: str = Field(default="Gallery Vault", description="Sender name")

    model_config = ConfigDict(
        env_file=".env" if os.path.exists(".env") else None,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

settings = Settings()

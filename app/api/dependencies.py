"""Centralized FastAPI Dependency Injection Types & Callbacks."""

from typing import Annotated, Any
from fastapi import Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import Settings, get_settings
from app.core.database import db
from app.core.security import oauth2_scheme, security
from app.services.email_service import EmailService, email_service
from app.services.telegram_service import TelegramService, telegram_service
from app.services.upload_service import UploadService, upload_service


# ── Configuration Dependency ──
def get_app_settings() -> Settings:
    return get_settings()

SettingsDep = Annotated[Settings, Depends(get_app_settings)]


# ── Database Dependency ──
async def get_database() -> AsyncIOMotorDatabase:
    # db অবজেক্টটি নিজেই AsyncIOMotorDatabase, তাই সরাসরি return db হবে
    return db

DatabaseDep = Annotated[AsyncIOMotorDatabase, Depends(get_database)]


# ── Service Dependencies ──
def get_telegram_service() -> TelegramService:
    return telegram_service

def get_upload_service() -> UploadService:
    return upload_service

def get_email_service() -> EmailService:
    return email_service

TelegramServiceDep = Annotated[TelegramService, Depends(get_telegram_service)]
UploadServiceDep = Annotated[UploadService, Depends(get_upload_service)]
EmailServiceDep = Annotated[EmailService, Depends(get_email_service)]

# Storage Engine Alias (Required by files.py)
StorageEngineDep = UploadServiceDep


# ── Pagination Dependency ──
class PaginationParams:
    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number"),
        limit: int = Query(20, ge=1, le=100, description="Items per page"),
    ):
        self.page = page
        self.limit = limit
        self.skip = (page - 1) * limit

PaginationDep = Annotated[PaginationParams, Depends()]


# ── Authentication Dependencies ──
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    database: DatabaseDep,
) -> dict[str, Any]:
    """Decodes JWT access token and fetches the current authenticated user."""
    payload = security.decode_token(token, expected_type="access")
    user_id = payload.get("sub")
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await database.users.find_one({"_id": user_id, "is_active": True})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or account disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user

CurrentUserDep = Annotated[dict[str, Any], Depends(get_current_user)]


# ── Admin Authorization Dependency ──
async def get_admin_user(
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Validates that the current authenticated user has admin privileges."""
    is_admin = current_user.get("is_admin", False) or current_user.get("role") == "admin"
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required to access this resource",
        )
    return current_user

AdminUserDep = Annotated[dict[str, Any], Depends(get_admin_user)]

"""Pydantic v2 schemas for all API request/response bodies."""

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, ConfigDict, EmailStr, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(
        from_attributes=True, 
        populate_by_name=True, 
        arbitrary_types_allowed=True
    )


class FileType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    ARCHIVE = "archive"
    FOLDER = "folder"
    OTHER = "other"


class FileStatus(str, Enum):
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"


# ── Auth & User Schemas ──

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=128)


class OTPRequest(BaseModel):
    email: EmailStr
    purpose: str = "verification"


class VerifyOTP(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)


class UserProfile(ORMModel):
    id: str = Field(..., alias="_id")
    username: str
    email: EmailStr
    role: UserRole = UserRole.USER
    is_verified: bool = False
    is_active: bool = True
    storage_used_bytes: int = 0
    storage_quota_bytes: int = Field(default=5 * 1024 * 1024 * 1024)
    avatar_message_id: int | None = None
    created_at: datetime
    updated_at: datetime


class UserUpdate(BaseModel):
    username: str | None = Field(None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


# ── Files & Folders Schemas ──

class FileMetadata(ORMModel):
    id: str = Field(..., alias="_id")
    owner_id: str
    parent_id: str | None = None
    original_name: str
    file_type: FileType
    mime_type: str | None = None
    size_bytes: int = 0
    file_hash: str | None = None
    is_folder: bool = False
    is_favorite: bool = False
    status: FileStatus = FileStatus.COMPLETED
    telegram_message_id: int | None = None
    thumbnail_message_id: int | None = None
    deleted_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class FileListResponse(BaseModel):
    files: list[FileMetadata] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    limit: int = 50


class StorageStats(BaseModel):
    used_bytes: int = 0
    quota_bytes: int = 0
    used_percentage: float = 0.0
    file_count: int = 0
    folder_count: int = 0


class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    parent_id: str | None = None


class FileRename(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=255)


class FileMove(BaseModel):
    file_ids: list[str]
    new_parent_id: str | None = None


class FileCopy(BaseModel):
    file_ids: list[str]
    new_parent_id: str | None = None


FolderCreateRequest = FolderCreate
FileRenameRequest = FileRename
FileMoveRequest = FileMove
FileCopyRequest = FileCopy


# ── Chunked Upload Schemas (Fix for uploads.py) ──

class UploadInitRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    file_size: int = Field(..., gt=0)
    mime_type: str = "application/octet-stream"
    total_chunks: int = Field(..., gt=0)


class UploadInitResponse(BaseModel):
    upload_id: str
    chunk_size: int
    total_chunks: int


# ── Share Schemas ──

class ShareCreate(BaseModel):
    file_id: str
    password: str | None = Field(None, min_length=4, max_length=32)
    expires_in_hours: int | None = Field(None, gt=0, le=720)
    max_downloads: int | None = Field(None, gt=0)


class SharePasswordUpdate(BaseModel):
    password: str | None = Field(None, min_length=4, max_length=32)


class ShareExpireUpdate(BaseModel):
    expires_in_hours: int | None = Field(None, gt=0, le=720)


class ShareAccessRequest(BaseModel):
    password: str | None = Field(None, min_length=1, max_length=32)


class ShareResponse(ORMModel):
    id: str = Field(..., alias="_id")
    file_id: str
    owner_id: str
    share_token: str
    has_password: bool = False
    max_downloads: int | None = None
    download_count: int = 0
    expires_at: datetime | None = None
    created_at: datetime


# ── Notification Schemas ──

class Notification(ORMModel):
    id: str = Field(..., alias="_id")
    user_id: str
    message: str
    is_read: bool = False
    created_at: datetime


# ── Admin Schemas ──

class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    role: UserRole | None = None
    storage_quota_bytes: int | None = None


# ── Security Schemas ──

class PinSet(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6, pattern=r"^\d+$")


class PinVerify(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6, pattern=r"^\d+$")


class PinChange(BaseModel):
    current_pin: str
    new_pin: str = Field(..., min_length=4, max_length=6, pattern=r"^\d+$")


# ── Generic Schemas ──

class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str | None = None
    data: T | None = None


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = "1.0.0"
    services: dict[str, str] = Field(default_factory=dict)

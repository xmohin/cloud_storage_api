"""Pydantic v2 schemas for all API request/response bodies."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, ConfigDict, EmailStr, Field

T = TypeVar("T")

class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class FileType(str, Enum):
    IMAGE = "image"; VIDEO = "video"; AUDIO = "audio"; DOCUMENT = "document"; ARCHIVE = "archive"; FOLDER = "folder"; OTHER = "other"

class FileStatus(str, Enum):
    UPLOADING = "uploading"; COMPLETED = "completed"; FAILED = "failed"; DELETED = "deleted"

class UserRole(str, Enum):
    USER = "user"; ADMIN = "admin"

# Auth
class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr; password: str = Field(..., min_length=8, max_length=128)
class UserLogin(BaseModel):
    email: EmailStr; password: str
class TokenResponse(BaseModel):
    access_token: str; refresh_token: str; token_type: str = "bearer"; expires_in: int
class RefreshTokenRequest(BaseModel):
    refresh_token: str
class PasswordResetRequest(BaseModel):
    email: EmailStr
class PasswordResetConfirm(BaseModel):
    email: EmailStr; otp: str = Field(..., min_length=6, max_length=6); new_password: str = Field(..., min_length=8, max_length=128)
class EmailVerificationRequest(BaseModel):
    email: EmailStr
class VerifyEmailOTP(BaseModel):
    email: EmailStr; otp: str = Field(..., min_length=6, max_length=6)

# Uploads
class UploadInitRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255); file_size: int = Field(..., gt=0); mime_type: str = "application/octet-stream"; total_chunks: int = Field(..., gt=0)
class UploadInitResponse(BaseModel):
    upload_id: str; chunk_size: int; is_duplicate: bool = False; file_id: str | None = None; message: str = "Upload initialized"
class UploadStatusResponse(BaseModel):
    upload_id: str; status: str; received_chunks: list[int] = Field(default_factory=list); total_chunks: int; progress: float = 0.0; file_id: str | None = None; telegram_task_id: str | None = None

# Files & Folders
class FileMetadata(ORMModel):
    id: str = Field(..., alias="_id"); owner_id: str; parent_id: str | None = None; original_name: str; file_type: FileType; mime_type: str | None = None; size_bytes: int = 0; file_hash: str | None = None; is_folder: bool = False; is_favorite: bool = False; status: FileStatus = FileStatus.COMPLETED; telegram_message_id: int | None = None; thumbnail_message_id: int | None = None; deleted_at: datetime | None = None; created_at: datetime; updated_at: datetime
class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100); parent_id: str | None = None
class FileRenameRequest(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=255)
class FileMoveRequest(BaseModel):
    new_parent_id: str | None = None
class FileListResponse(BaseModel):
    files: list[FileMetadata]; total: int; page: int; per_page: int; has_next: bool
class StorageStats(BaseModel):
    total_files: int = 0; total_folders: int = 0; total_size_bytes: int = 0; storage_quota_bytes: int = 0; storage_used_percentage: float = 0.0; files_by_type: dict[str, int] = Field(default_factory=dict); trash_count: int = 0

# Sharing
class ShareCreateRequest(BaseModel):
    file_id: str; password: str | None = Field(None, min_length=4, max_length=32); expires_in_hours: int | None = Field(None, gt=0, le=720); max_downloads: int | None = Field(None, gt=0)
class ShareAccessRequest(BaseModel):
    password: str | None = None
class ShareResponse(ORMModel):
    id: str = Field(..., alias="_id"); file_id: str; owner_id: str; share_token: str; is_password_protected: bool = False; expires_at: datetime | None = None; max_downloads: int | None = None; download_count: int = 0; is_revoked: bool = False; created_at: datetime; share_url: str | None = None
class ShareAnalytics(ORMModel):
    id: str = Field(..., alias="_id"); share_id: str; ip_address: str; user_agent: str; accessed_at: datetime

# Generic
class ApiResponse(BaseModel, Generic[T]):
    success: bool = True; message: str | None = None; data: T | None = None
class ErrorResponse(BaseModel):
    success: bool = False; error: dict[str, Any]
class HealthResponse(BaseModel):
    status: str = "healthy"; version: str = "1.0.0"; services: dict[str, str] = Field(default_factory=dict); timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

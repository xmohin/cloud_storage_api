from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List
from datetime import datetime

class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=2, max_length=100)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6)

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str
    new_password: str = Field(min_length=8)

class UserProfileResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    is_verified: bool
    role: str
    storage_used_bytes: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: Optional[str] = None

class FolderResponse(BaseModel):
    id: str
    name: str
    parent_id: Optional[str]
    owner_id: str
    created_at: datetime

class FileResponse(BaseModel):
    id: str
    filename: str
    size_bytes: int
    mime_type: str
    sha256_hash: str
    folder_id: Optional[str]
    is_favorite: bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime

class ChunkUploadInitResponse(BaseModel):
    upload_id: str
    filename: str
    chunk_size: int
    total_chunks: int

class ShareLinkCreateRequest(BaseModel):
    file_id: str
    password: Optional[str] = None
    expires_in_hours: Optional[int] = 24
    max_downloads: Optional[int] = None

class ShareLinkResponse(BaseModel):
    share_code: str
    share_url: str
    expires_at: Optional[datetime]
    has_password: bool

class SystemStatsResponse(BaseModel):
    total_users: int
    total_files: int
    total_storage_bytes: int
    telegram_status: str
    database_status: str

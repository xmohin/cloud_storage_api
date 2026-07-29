from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class UserProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., description="Current account password")
    new_password: str = Field(..., min_length=8, description="New password (minimum 8 characters)")


class AvatarUploadResponse(BaseModel):
    avatar_id: str
    message: str


class StorageCategoryBreakdown(BaseModel):
    images_bytes: int = 0
    videos_bytes: int = 0
    documents_bytes: int = 0
    audio_bytes: int = 0
    others_bytes: int = 0


class DetailedStorageStatsResponse(BaseModel):
    total_used_bytes: int
    total_files_count: int
    categories: StorageCategoryBreakdown
    mime_type_breakdown: Dict[str, int]


class UserActivityItem(BaseModel):
    id: str
    action: str
    ip_address: str
    user_agent: str
    timestamp: datetime


class UserActivityPaginatedResponse(BaseModel):
    activities: List[UserActivityItem]
    total_count: int
    page: int
    limit: int


class DeleteAccountRequest(BaseModel):
    password: str = Field(..., description="Confirm password to authorize account deletion")

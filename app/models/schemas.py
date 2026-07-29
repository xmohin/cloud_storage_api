from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

class HealthCheck(BaseModel):
    status: str
    database: str
    version: str
    environment: str

class StandardResponse(BaseModel, Generic[T]):
    """Standardized API response wrapper."""
    success: bool = True
    message: str = "Operation successful"
    data: Optional[T] = None
    
    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenPayload(BaseModel):
    sub: Optional[str] = None
    exp: Optional[int] = None

class UserBase(BaseModel):
    email: str = Field(..., description="User's email address")
    is_active: bool = True

class UserCreate(UserBase):
    password: str = Field(..., min_length=8, description="Strong password")

class UserInDB(UserBase):
    id: str = Field(..., alias="_id")
    
    model_config = ConfigDict(populate_by_name=True)

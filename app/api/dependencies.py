"""FastAPI dependencies shared across route handlers."""

from typing import Annotated
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.database import db
from app.services.telegram_service import telegram_service
from app.services.email_service import email_service
from app.core.security import oauth2_scheme, security
from app.models.schemas import UserRole

async def get_database() -> AsyncIOMotorDatabase:
    return db.get_database()

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], database: Annotated[AsyncIOMotorDatabase, Depends(get_database)]) -> dict:
    payload = security.decode_token(token, expected_type="access")
    user_id = payload.get("sub"); jti = payload.get("jti")
    if not user_id or not jti: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing required claims", headers={"WWW-Authenticate": "Bearer"})
    blacklisted = await database.blacklist.find_one({"jti": jti})
    if blacklisted: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked", headers={"WWW-Authenticate": "Bearer"})
    user = await database.users.find_one({"_id": user_id})
    if user is None: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found", headers={"WWW-Authenticate": "Bearer"})
    if not user.get("is_active", True): raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
    user["_jti"] = jti
    return user

class PaginationParams:
    def __init__(self, page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100), sort_by: str = Query("created_at"), sort_order: str = Query("desc", pattern="^(asc|desc)$")):
        self.page = page; self.per_page = per_page; self.sort_by = sort_by; self.sort_order = sort_order; self.skip = (page - 1) * per_page
    @property
    def mongo_sort(self) -> dict[str, int]: return {self.sort_by: 1 if self.sort_order == "asc" else -1}

DatabaseDep = Annotated[AsyncIOMotorDatabase, Depends(get_database)]
CurrentUserDep = Annotated[dict, Depends(get_current_user)]
TelegramServiceDep = Annotated[object, Depends(lambda: telegram_service)]
EmailServiceDep = Annotated[object, Depends(lambda: email_service)]
PaginationDep = Annotated[PaginationParams, Depends()]

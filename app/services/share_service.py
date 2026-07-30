"""Service layer for secure file sharing, password protection, and analytics."""

import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.core.logger import get_logger
from app.core.security import security
from app.services.file_service import file_service

logger = get_logger(__name__)


class ShareService:
    @staticmethod
    async def create_share(
        db: AsyncIOMotorDatabase,
        owner_id: str,
        file_id: str,
        password: Optional[str],
        expires_in_hours: Optional[int],
        max_downloads: Optional[int]
    ) -> dict:
        file_doc = await file_service._get_file_doc(db, file_id, owner_id)
        if file_doc.get("is_folder"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot share folders")
            
        share_token = secrets.token_urlsafe(16)
        password_hash = security.hash_password(password) if password else None
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours) if expires_in_hours else None
        
        share_doc = {
            "_id": share_token,
            "file_id": file_id,
            "owner_id": owner_id,
            "share_token": share_token,
            "password_hash": password_hash,
            "expires_at": expires_at,
            "max_downloads": max_downloads,
            "download_count": 0,
            "is_revoked": False,
            "created_at": datetime.now(timezone.utc)
        }
        await db.shares.insert_one(share_doc)
        share_doc.pop("password_hash", None)
        share_doc.pop("_id", None)
        return share_doc

    @staticmethod
    async def verify_and_access_share(
        db: AsyncIOMotorDatabase,
        share_token: str,
        provided_password: Optional[str],
        ip_address: str,
        user_agent: str
    ) -> dict:
        share = await db.shares.find_one({"share_token": share_token})
        if not share:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found")
        if share.get("is_revoked"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Share link revoked")
            
        expires_at = share.get("expires_at")
        if expires_at:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Share link expired")

        if share.get("password_hash"):
            if not provided_password or not security.verify_password(provided_password, share["password_hash"]):
                await db.share_logs.insert_one({
                    "share_id": share_token,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                    "accessed_at": datetime.now(timezone.utc),
                    "status": "unauthorized"
                })
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")

        updated = await db.shares.find_one_and_update(
            {
                "_id": share_token,
                "$or": [
                    {"max_downloads": None},
                    {"$expr": {"$lt": ["$download_count", "$max_downloads"]}}
                ]
            },
            {"$inc": {"download_count": 1}},
            return_document=ReturnDocument.AFTER
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download limit reached")

        file_doc = await db.files.find_one({"_id": share["file_id"], "deleted_at": None})
        if not file_doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared file no longer exists")

        await db.share_logs.insert_one({
            "share_id": share_token,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "accessed_at": datetime.now(timezone.utc),
            "status": "success"
        })
        return file_doc


share_service = ShareService()

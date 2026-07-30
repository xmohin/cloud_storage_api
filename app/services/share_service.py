"""Service layer for secure file sharing, password protection, and analytics."""

import secrets
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.logger import get_logger
from app.core.security import security
from app.services.file_service import file_service

logger = get_logger(__name__)

class ShareService:
    @staticmethod
    async def create_share(db: AsyncIOMotorDatabase, owner_id: str, file_id: str, password: Optional[str], expires_in_hours: Optional[int], max_downloads: Optional[int]) -> dict:
        file_doc = await file_service._get_file_doc(db, file_id, owner_id)
        if file_doc.get("is_folder"): raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot share folders")
        share_token = secrets.token_urlsafe(16)
        password_hash = security.hash_password(password) if password else None
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours) if expires_in_hours else None
        share_doc = {"_id": share_token, "file_id": file_id, "owner_id": owner_id, "share_token": share_token, "password_hash": password_hash, "expires_at": expires_at, "max_downloads": max_downloads, "download_count": 0, "is_revoked": False, "created_at": datetime.now(timezone.utc)}
        await db.shares.insert_one(share_doc)
        share_doc.pop("password_hash", None); share_doc.pop("_id", None); return share_doc

    @staticmethod
    async def verify_and_access_share(db: AsyncIOMotorDatabase, share_token: str, provided_password: Optional[str], ip_address: str, user_agent: str) -> dict:
        share = await db.shares.find_one({"share_token": share_token})
        if not share: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Share link not found")
        if share["is_revoked"]: raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Share link revoked")
        if share["expires_at"] and datetime.now(timezone.utc) > share["expires_at"]: raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Share link expired")
        
        if share["password_hash"]:
            if not provided_password or not security.verify_password(provided_password, share["password_hash"]):
                await db.share_logs.insert_one({"share_id": share_token, "ip_address": ip_address, "user_agent": user_agent, "accessed_at": datetime.now(timezone.utc), "status": "unauthorized"})
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid password")

        # Atomic increment to prevent race conditions on max_downloads
        updated = await db.shares.find_one_and_update(
            {"_id": share_token, "$or": [{"max_downloads": None}, {"$expr": {"$lt": ["$download_count", "$max_downloads"]}}]},
            {"$inc": {"download_count": 1}},
            return_document=True
        )
        if not updated:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Download limit reached")

        file_doc = await db.files.find_one({"_id": share["file_id"], "deleted_at": None})
        if not file_doc: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Shared file no longer exists")
        
        await db.share_logs.insert_one({"share_id": share_token, "ip_address": ip_address, "user_agent": user_agent, "accessed_at": datetime.now(timezone.utc), "status": "success"})
        return file_doc

    @staticmethod
    async def revoke_share(db: AsyncIOMotorDatabase, share_token: str, owner_id: str):
        result = await db.shares.update_one({"share_token": share_token, "owner_id": owner_id}, {"$set": {"is_revoked": True}})
        if result.matched_count == 0: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Share link not found")
        return True

    @staticmethod
    async def get_analytics(db: AsyncIOMotorDatabase, share_token: str, owner_id: str) -> dict:
        share = await db.shares.find_one({"share_token": share_token, "owner_id": owner_id})
        if not share: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Share link not found")
        logs = await db.share_logs.find({"share_id": share_token}).sort("accessed_at", -1).limit(50).to_list(50)
        share.pop("password_hash", None); share.pop("_id", None)
        return {"share": share, "recent_access": logs}

share_service = ShareService()

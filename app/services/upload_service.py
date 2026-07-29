import os
import shutil
from datetime import datetime, timezone
from fastapi import UploadFile, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.config import settings
from app.utils.hashing import compute_stream_sha256, generate_random_token
from app.services.telegram_service import telegram_service
from bson import ObjectId

class UploadService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def handle_direct_upload(self, file: UploadFile, user_id: str, folder_id: str = None) -> dict:
        temp_path = os.path.join(settings.TEMP_STORAGE_PATH, f"{generate_random_token()}_{file.filename}")
        
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        sha256_hash = compute_stream_sha256(temp_path)
        file_size = os.path.getsize(temp_path)
        
        # Deduplication Check
        existing = await self.db.files.find_one({"sha256_hash": sha256_hash, "is_deleted": False})
        if existing:
            os.remove(temp_path)
            new_file = {
                "owner_id": user_id,
                "filename": file.filename,
                "size_bytes": file_size,
                "mime_type": file.content_type,
                "sha256_hash": sha256_hash,
                "telegram_message_id": existing["telegram_message_id"],
                "folder_id": folder_id,
                "is_favorite": False,
                "is_deleted": False,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }
            res = await self.db.files.insert_one(new_file)
            await self.db.users.update_one({"_id": ObjectId(user_id)}, {"$inc": {"storage_used_bytes": file_size}})
            return {"file_id": str(res.inserted_id), "status": "deduplicated"}

        # Telegram Upload
        telegram_msg_id = await telegram_service.upload_file(temp_path, f"User: {user_id} File: {file.filename}")
        os.remove(temp_path)

        file_doc = {
            "owner_id": user_id,
            "filename": file.filename,
            "size_bytes": file_size,
            "mime_type": file.content_type or "application/octet-stream",
            "sha256_hash": sha256_hash,
            "telegram_message_id": telegram_msg_id,
            "folder_id": folder_id,
            "is_favorite": False,
            "is_deleted": False,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
        res = await self.db.files.insert_one(file_doc)
        await self.db.users.update_one({"_id": ObjectId(user_id)}, {"$inc": {"storage_used_bytes": file_size}})
        return {"file_id": str(res.inserted_id), "status": "uploaded"}

from typing import List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from datetime import datetime, timezone
from fastapi import HTTPException
from app.services.telegram_service import telegram_service

class FileService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def list_files(self, user_id: str, folder_id: Optional[str] = None, page: int = 1, limit: int = 50) -> List[dict]:
        query = {"owner_id": user_id, "is_deleted": False}
        if folder_id:
            query["folder_id"] = folder_id
        
        cursor = self.db.files.find(query).skip((page - 1) * limit).limit(limit).sort("created_at", -1)
        files = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            files.append(doc)
        return files

    async def stream_file(self, file_id: str, user_id: str, start: int = 0, end: Optional[int] = None) -> bytes:
        file_doc = await self.db.files.find_one({"_id": ObjectId(file_id), "owner_id": user_id})
        if not file_doc:
            raise HTTPException(status_code=404, detail="File not found.")
        
        return await telegram_service.download_file_bytes(file_doc["telegram_message_id"], start, end)

    async def soft_delete_file(self, file_id: str, user_id: str):
        res = await self.db.files.update_one(
            {"_id": ObjectId(file_id), "owner_id": user_id},
            {"$set": {"is_deleted": True, "deleted_at": datetime.now(timezone.utc)}}
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="File not found.")
        return {"message": "File moved to trash."}

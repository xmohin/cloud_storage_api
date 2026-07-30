"""Service layer for file, folder, trash, and statistics operations."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.config import get_settings
from app.core.logger import get_logger
from app.models.schemas import FileType, FileStatus
from app.services.telegram_service import telegram_service

settings = get_settings()
logger = get_logger(__name__)
TRASH_EXPIRY_DAYS = 30

class FileService:
    @staticmethod
    async def _get_file_doc(db: AsyncIOMotorDatabase, file_id: str, user_id: str, include_deleted: bool = False) -> dict:
        query = {"_id": file_id, "owner_id": user_id}
        if not include_deleted: query["deleted_at"] = None
        file_doc = await db.files.find_one(query)
        if not file_doc: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found")
        return file_doc

    @staticmethod
    async def list_files(db: AsyncIOMotorDatabase, user_id: str, parent_id: Optional[str], skip: int, limit: int) -> dict:
        query = {"owner_id": user_id, "parent_id": parent_id, "deleted_at": None}
        files = await db.files.find(query).sort([("is_folder", -1), ("original_name", 1)]).skip(skip).limit(limit).to_list(limit)
        total = await db.files.count_documents(query)
        return {"files": files, "total": total}

    @staticmethod
    async def list_recent(db: AsyncIOMotorDatabase, user_id: str, skip: int, limit: int) -> dict:
        query = {"owner_id": user_id, "deleted_at": None, "is_folder": False}
        files = await db.files.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        total = await db.files.count_documents(query)
        return {"files": files, "total": total}

    @staticmethod
    async def list_favorites(db: AsyncIOMotorDatabase, user_id: str, skip: int, limit: int) -> dict:
        query = {"owner_id": user_id, "is_favorite": True, "deleted_at": None}
        files = await db.files.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        total = await db.files.count_documents(query)
        return {"files": files, "total": total}

    @staticmethod
    async def search_files(db: AsyncIOMotorDatabase, user_id: str, query_str: str, skip: int, limit: int) -> dict:
        query = {"owner_id": user_id, "deleted_at": None, "$text": {"$search": query_str}}
        files = await db.files.find(query, {"score": {"$meta": "textScore"}}).sort([("score", {"$meta": "textScore"})]).skip(skip).limit(limit).to_list(limit)
        total = await db.files.count_documents(query)
        return {"files": files, "total": total}

    @staticmethod
    async def rename_file(db: AsyncIOMotorDatabase, file_id: str, user_id: str, new_name: str) -> dict:
        file_doc = await FileService._get_file_doc(db, file_id, user_id)
        await db.files.update_one({"_id": file_id}, {"$set": {"original_name": new_name, "updated_at": datetime.now(timezone.utc)}})
        file_doc["original_name"] = new_name; return file_doc

    @staticmethod
    async def move_file(db: AsyncIOMotorDatabase, file_id: str, user_id: str, new_parent_id: Optional[str]) -> dict:
        file_doc = await FileService._get_file_doc(db, file_id, user_id)
        if new_parent_id:
            parent_doc = await FileService._get_file_doc(db, new_parent_id, user_id)
            if not parent_doc.get("is_folder"): raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Target must be a folder")
            if file_doc.get("is_folder"):
                parent_check = new_parent_id
                while parent_check:
                    if parent_check == file_id: raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot move folder into its own subfolder")
                    p_doc = await db.files.find_one({"_id": parent_check, "owner_id": user_id}, {"parent_id": 1})
                    parent_check = p_doc.get("parent_id") if p_doc else None
        await db.files.update_one({"_id": file_id}, {"$set": {"parent_id": new_parent_id, "updated_at": datetime.now(timezone.utc)}})
        file_doc["parent_id"] = new_parent_id; return file_doc

    @staticmethod
    async def toggle_favorite(db: AsyncIOMotorDatabase, file_id: str, user_id: str) -> dict:
        file_doc = await FileService._get_file_doc(db, file_id, user_id)
        new_state = not file_doc.get("is_favorite", False)
        await db.files.update_one({"_id": file_id}, {"$set": {"is_favorite": new_state, "updated_at": datetime.now(timezone.utc)}})
        file_doc["is_favorite"] = new_state; return file_doc

    @staticmethod
    async def create_folder(db: AsyncIOMotorDatabase, user_id: str, name: str, parent_id: Optional[str]) -> dict:
        if parent_id:
            parent_doc = await FileService._get_file_doc(db, parent_id, user_id)
            if not parent_doc.get("is_folder"): raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Parent must be a folder")
        folder_doc = {"_id": str(uuid.uuid4()), "owner_id": user_id, "parent_id": parent_id, "original_name": name, "file_type": FileType.FOLDER, "is_folder": True, "is_favorite": False, "size_bytes": 0, "status": FileStatus.COMPLETED, "deleted_at": None, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}
        await db.files.insert_one(folder_doc); return folder_doc

    @staticmethod
    async def move_to_trash(db: AsyncIOMotorDatabase, file_id: str, user_id: str) -> dict:
        file_doc = await FileService._get_file_doc(db, file_id, user_id)
        if file_doc.get("is_folder"): await FileService._recursive_trash(db, file_id, user_id)
        deleted_at = datetime.now(timezone.utc)
        await db.files.update_one({"_id": file_id}, {"$set": {"deleted_at": deleted_at, "deleted_expires_at": deleted_at + timedelta(days=TRASH_EXPIRY_DAYS), "updated_at": deleted_at}})
        if not file_doc.get("is_folder"): await db.users.update_one({"_id": user_id}, {"$inc": {"storage_used_bytes": -file_doc.get("size_bytes", 0)}})
        file_doc["deleted_at"] = deleted_at; return file_doc

    @staticmethod
    async def _recursive_trash(db: AsyncIOMotorDatabase, folder_id: str, user_id: str):
        children = await db.files.find({"owner_id": user_id, "parent_id": folder_id, "deleted_at": None}).to_list(None)
        for child in children:
            if child.get("is_folder"): await FileService._recursive_trash(db, child["_id"], user_id)
            else: await db.users.update_one({"_id": user_id}, {"$inc": {"storage_used_bytes": -child.get("size_bytes", 0)}})
            deleted_at = datetime.now(timezone.utc)
            await db.files.update_one({"_id": child["_id"]}, {"$set": {"deleted_at": deleted_at, "deleted_expires_at": deleted_at + timedelta(days=TRASH_EXPIRY_DAYS)}})

    @staticmethod
    async def list_trash(db: AsyncIOMotorDatabase, user_id: str, skip: int, limit: int) -> dict:
        query = {"owner_id": user_id, "deleted_at": {"$ne": None}}
        files = await db.files.find(query).sort("deleted_at", -1).skip(skip).limit(limit).to_list(limit)
        total = await db.files.count_documents(query)
        return {"files": files, "total": total}

    @staticmethod
    async def restore_from_trash(db: AsyncIOMotorDatabase, file_id: str, user_id: str) -> dict:
        file_doc = await FileService._get_file_doc(db, file_id, user_id, include_deleted=True)
        if not file_doc.get("deleted_at"): raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File is not in trash")
        parent_id = file_doc.get("parent_id")
        if parent_id:
            parent = await db.files.find_one({"_id": parent_id, "owner_id": user_id})
            if parent and parent.get("deleted_at"): await FileService.restore_from_trash(db, parent_id, user_id)
        await db.files.update_one({"_id": file_id}, {"$set": {"deleted_at": None, "deleted_expires_at": None, "updated_at": datetime.now(timezone.utc)}})
        if not file_doc.get("is_folder"): await db.users.update_one({"_id": user_id}, {"$inc": {"storage_used_bytes": file_doc.get("size_bytes", 0)}})
        file_doc["deleted_at"] = None; return file_doc

    @staticmethod
    async def permanent_delete(db: AsyncIOMotorDatabase, file_id: str, user_id: str):
        file_doc = await FileService._get_file_doc(db, file_id, user_id, include_deleted=True)
        if file_doc.get("is_folder"): await FileService._recursive_permanent_delete(db, file_id, user_id)
        else:
            if file_doc.get("telegram_message_id"):
                duplicates = await db.files.count_documents({"telegram_message_id": file_doc["telegram_message_id"], "_id": {"$ne": file_id}})
                if duplicates == 0:
                    try: await telegram_service.client.delete_messages(entity=settings.TELEGRAM_STORAGE_CHANNEL_ID, message_ids=[file_doc["telegram_message_id"]])
                    except Exception as e: logger.error(f"Failed to delete TG msg: {e}")
        await db.files.delete_one({"_id": file_id})

    @staticmethod
    async def _recursive_permanent_delete(db: AsyncIOMotorDatabase, folder_id: str, user_id: str):
        children = await db.files.find({"owner_id": user_id, "parent_id": folder_id}).to_list(None)
        for child in children:
            if child.get("is_folder"): await FileService._recursive_permanent_delete(db, child["_id"], user_id)
            else:
                if child.get("telegram_message_id"):
                    duplicates = await db.files.count_documents({"telegram_message_id": child["telegram_message_id"], "_id": {"$ne": child["_id"]}})
                    if duplicates == 0:
                        try: await telegram_service.client.delete_messages(entity=settings.TELEGRAM_STORAGE_CHANNEL_ID, message_ids=[child["telegram_message_id"]])
                        except Exception: pass
                await db.files.delete_one({"_id": child["_id"]})
        await db.files.delete_one({"_id": folder_id})

    @staticmethod
    async def get_statistics(db: AsyncIOMotorDatabase, user_id: str) -> dict:
        user = await db.users.find_one({"_id": user_id})
        quota, used = user.get("storage_quota_bytes", 0), user.get("storage_used_bytes", 0)
        pipeline = [{"$match": {"owner_id": user_id, "deleted_at": None, "is_folder": False}}, {"$group": {"_id": "$file_type", "count": {"$sum": 1}}}]
        type_counts = {doc["_id"]: doc["count"] async for doc in db.files.aggregate(pipeline)}
        total_files, total_folders = sum(type_counts.values()), await db.files.count_documents({"owner_id": user_id, "deleted_at": None, "is_folder": True})
        trash_count = await db.files.count_documents({"owner_id": user_id, "deleted_at": {"$ne": None}})
        return {"total_files": total_files, "total_folders": total_folders, "total_size_bytes": used, "storage_quota_bytes": quota, "storage_used_percentage": round((used / quota) * 100, 2) if quota > 0 else 0.0, "files_by_type": type_counts, "trash_count": trash_count}

file_service = FileService()

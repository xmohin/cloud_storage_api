"""Async Upload Engine with chunking, resumption, deduplication, and RAM optimization."""

import os, asyncio, hashlib, shutil, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
import aiofiles
from fastapi import UploadFile, HTTPException, status

from app.core.config import get_settings
from app.core.database import db
from app.core.logger import get_logger  # <--- এটি মিসিং ছিল
from app.services.telegram_service import telegram_service
from app.models.schemas import FileType

settings = get_settings()
logger = get_logger(__name__)
CHUNK_SIZE = 5 * 1024 * 1024  
TEMP_UPLOAD_DIR = "/app/tmp_uploads"

class UploadService:
    def __init__(self): 
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)
        self._cleanup_task = asyncio.create_task(self._stale_upload_cleanup())

    async def stop(self):
        if self._cleanup_task: 
            self._cleanup_task.cancel()

    def _get_file_type(self, mime_type: str) -> FileType:
        if "image" in mime_type: return FileType.IMAGE
        if "video" in mime_type: return FileType.VIDEO
        if "audio" in mime_type: return FileType.AUDIO
        if "pdf" in mime_type or "document" in mime_type: return FileType.DOCUMENT
        if "zip" in mime_type or "rar" in mime_type: return FileType.ARCHIVE
        return FileType.OTHER

    def _get_upload_dir(self, upload_id: str) -> str:
        path = os.path.join(TEMP_UPLOAD_DIR, upload_id)
        os.makedirs(path, exist_ok=True)
        return path

    async def handle_small_upload(self, file: UploadFile, user_id: str) -> dict:
        upload_id = str(uuid.uuid4())
        upload_dir = self._get_upload_dir(upload_id)
        temp_path = os.path.join(upload_dir, file.filename)
        hasher = hashlib.sha256()
        size_bytes = 0
        
        async with aiofiles.open(temp_path, 'wb') as out_file:
            while chunk := await file.read(CHUNK_SIZE):
                await out_file.write(chunk)
                hasher.update(chunk)
                size_bytes += len(chunk)
                
        file_hash = hasher.hexdigest()
        existing_file = await db.files.find_one({"file_hash": file_hash})
        
        if existing_file:
            new_file_doc = await self._create_file_record(user_id, file.filename, file.content_type, size_bytes, file_hash, existing_file["telegram_message_id"], existing_file.get("thumbnail_message_id"))
            shutil.rmtree(upload_dir)
            return {"file_id": new_file_doc["_id"], "is_duplicate": True}
            
        task_id = await telegram_service.enqueue_upload(temp_path)
        file_doc = await self._create_file_record(user_id, file.filename, file.content_type, size_bytes, file_hash, None, None)
        await db.uploads.insert_one({"_id": upload_id, "user_id": user_id, "file_id": file_doc["_id"], "telegram_task_id": task_id, "status": "processing", "created_at": datetime.now(timezone.utc)})
        return {"file_id": file_doc["_id"], "is_duplicate": False, "telegram_task_id": task_id}

    async def init_chunked_upload(self, filename: str, file_size: int, mime_type: str, total_chunks: int, user_id: str) -> dict:
        upload_id = str(uuid.uuid4())
        await db.uploads.insert_one({"_id": upload_id, "user_id": user_id, "filename": filename, "file_size": file_size, "mime_type": mime_type, "total_chunks": total_chunks, "received_chunks": [], "status": "uploading", "created_at": datetime.now(timezone.utc)})
        self._get_upload_dir(upload_id)
        return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE}

    async def upload_chunk(self, upload_id: str, chunk_index: int, file: UploadFile, user_id: str) -> dict:
        upload = await db.uploads.find_one({"_id": upload_id, "user_id": user_id})
        if not upload: 
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Upload session not found")
        if upload["status"] == "paused": 
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Upload is paused.")
            
        chunk_path = os.path.join(TEMP_UPLOAD_DIR, upload_id, f"chunk_{chunk_index}")
        async with aiofiles.open(chunk_path, 'wb') as out_file:
            while chunk := await file.read(CHUNK_SIZE): 
                await out_file.write(chunk)
                
        if chunk_index not in upload["received_chunks"]:
            await db.uploads.update_one({"_id": upload_id}, {"$push": {"received_chunks": chunk_index}})
            
        return {"upload_id": upload_id, "chunk_index": chunk_index}

    async def pause_upload(self, upload_id: str, user_id: str) -> dict:
        await db.uploads.update_one({"_id": upload_id, "user_id": user_id, "status": "uploading"}, {"$set": {"status": "paused"}})
        return {"upload_id": upload_id, "message": "Upload paused"}

    async def resume_upload(self, upload_id: str, user_id: str) -> dict:
        upload = await db.uploads.find_one({"_id": upload_id, "user_id": user_id})
        if not upload or upload["status"] != "paused": 
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Upload not found or not paused")
        await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "uploading"}})
        missing = [i for i in range(upload["total_chunks"]) if i not in upload["received_chunks"]]
        return {"upload_id": upload_id, "missing_chunks": missing}

    async def complete_chunked_upload(self, upload_id: str, user_id: str) -> dict:
        upload = await db.uploads.find_one({"_id": upload_id, "user_id": user_id})
        if not upload or len(upload["received_chunks"]) != upload["total_chunks"]: 
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Not all chunks received")
        await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "processing"}})
        asyncio.create_task(self._process_chunked_upload(upload_id, user_id, upload))
        return {"upload_id": upload_id, "message": "Upload completion started"}

    async def _process_chunked_upload(self, upload_id: str, user_id: str, upload: dict):
        try:
            upload_dir = os.path.join(TEMP_UPLOAD_DIR, upload_id)
            final_path = os.path.join(upload_dir, upload["filename"])
            hasher = hashlib.sha256()
            
            async with aiofiles.open(final_path, 'wb') as out_file:
                for i in range(upload["total_chunks"]):
                    chunk_path = os.path.join(upload_dir, f"chunk_{i}")
                    async with aiofiles.open(chunk_path, 'rb') as in_file:
                        while chunk := await in_file.read(CHUNK_SIZE): 
                            await out_file.write(chunk)
                            hasher.update(chunk)
                    os.remove(chunk_path)
                    
            file_hash = hasher.hexdigest()
            existing_file = await db.files.find_one({"file_hash": file_hash})
            
            if existing_file:
                new_file_doc = await self._create_file_record(user_id, upload["filename"], upload["mime_type"], upload["file_size"], file_hash, existing_file["telegram_message_id"], existing_file.get("thumbnail_message_id"))
                shutil.rmtree(upload_dir)
                await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "completed", "file_id": new_file_doc["_id"], "is_duplicate": True}})
                return
                
            task_id = await telegram_service.enqueue_upload(final_path)
            file_doc = await self._create_file_record(user_id, upload["filename"], upload["mime_type"], upload["file_size"], file_hash, None, None)
            await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "completed", "file_id": file_doc["_id"], "telegram_task_id": task_id}})
        except Exception as e:
            await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "failed", "error": str(e)}})

    async def _create_file_record(self, user_id: str, filename: str, mime_type: str, size_bytes: int, file_hash: str, tg_msg_id: int | None, tg_thumb_id: int | None) -> dict:
        file_doc = {
            "_id": str(uuid.uuid4()), 
            "owner_id": user_id, 
            "original_name": filename, 
            "filename": f"{uuid.uuid4().hex}_{filename}", 
            "file_type": self._get_file_type(mime_type), 
            "mime_type": mime_type, 
            "size_bytes": size_bytes, 
            "file_hash": file_hash, 
            "telegram_message_id": tg_msg_id, 
            "thumbnail_message_id": tg_thumb_id, 
            "status": "completed" if tg_msg_id else "uploading", 
            "created_at": datetime.now(timezone.utc), 
            "updated_at": datetime.now(timezone.utc)
        }
        await db.files.insert_one(file_doc)
        await db.users.update_one({"_id": user_id}, {"$inc": {"storage_used_bytes": size_bytes}})
        return file_doc

    async def get_upload_status(self, upload_id: str, user_id: str) -> dict:
        upload = await db.uploads.find_one({"_id": upload_id, "user_id": user_id})
        if not upload: 
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Upload session not found")
        
        progress = 1.0 if upload["status"] == "completed" else 0.0
        if upload["status"] == "processing" and upload.get("telegram_task_id"):
            task = telegram_service.get_task_status(upload["telegram_task_id"])
            if task: 
                progress = task.progress
                
        return {
            "upload_id": upload_id, 
            "status": upload["status"], 
            "received_chunks": upload.get("received_chunks", []),  # <--- KeyError এড়াতে .get() ব্যবহার করা হয়েছে
            "total_chunks": upload.get("total_chunks", 0), 
            "progress": progress, 
            "file_id": upload.get("file_id"), 
            "telegram_task_id": upload.get("telegram_task_id")
        }

    async def _stale_upload_cleanup(self):
        while True:
            try:
                await asyncio.sleep(3600)
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                stale = await db.uploads.find({"created_at": {"$lt": cutoff}, "status": {"$in": ["uploading", "paused", "failed"]}}).to_list(100)
                for up in stale:
                    upload_dir = os.path.join(TEMP_UPLOAD_DIR, up["_id"])
                    if os.path.exists(upload_dir): 
                        shutil.rmtree(upload_dir)
                    await db.uploads.delete_one({"_id": up["_id"]})
            except asyncio.CancelledError: 
                break
            except Exception as e: 
                logger.error(f"Stale cleanup error: {e}")

upload_service = UploadService()

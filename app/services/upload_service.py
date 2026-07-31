"""Async Upload Engine with chunking, resumption, deduplication, and RAM optimization."""

import os
import asyncio
import hashlib
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, Optional
import aiofiles
from fastapi import UploadFile, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import get_settings
from app.core.logger import get_logger
from app.services.telegram_service import telegram_service
from app.models.schemas import FileType
from app.utils.validators import validate_filename

settings = get_settings()
logger = get_logger(__name__)
CHUNK_SIZE = 5 * 1024 * 1024  
TEMP_UPLOAD_DIR = settings.TEMP_STORAGE_PATH


class UploadService:
    def __init__(self):
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)

    def _get_file_type(self, mime_type: str) -> FileType:
        mime = mime_type.lower()
        if "image" in mime:
            return FileType.IMAGE
        if "video" in mime:
            return FileType.VIDEO
        if "audio" in mime:
            return FileType.AUDIO
        if "pdf" in mime or "document" in mime:
            return FileType.DOCUMENT
        if "zip" in mime or "rar" in mime or "tar" in mime:
            return FileType.ARCHIVE
        return FileType.OTHER

    def _get_upload_dir(self, upload_id: str) -> str:
        path = os.path.join(TEMP_UPLOAD_DIR, upload_id)
        os.makedirs(path, exist_ok=True)
        return path

    async def _resolve_parent_id(self, db: AsyncIOMotorDatabase, parent_id: Optional[str], user_id: str) -> Optional[str]:
        """Validates an optional destination folder before an upload is attached to it."""
        if not parent_id:
            return None
        folder = await db.files.find_one({"_id": parent_id, "owner_id": user_id, "deleted_at": None})
        if not folder or not folder.get("is_folder"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target folder not found")
        return parent_id

    async def handle_small_upload(
        self, db: AsyncIOMotorDatabase, file: UploadFile, user_id: str, parent_id: Optional[str] = None
    ) -> dict:
        parent_id = await self._resolve_parent_id(db, parent_id, user_id)
        upload_id = str(uuid.uuid4())
        upload_dir = self._get_upload_dir(upload_id)
        safe_filename = validate_filename(file.filename)
        temp_path = os.path.join(upload_dir, safe_filename)
        hasher = hashlib.sha256()
        size_bytes = 0

        async with aiofiles.open(temp_path, "wb") as out_file:
            while chunk := await file.read(CHUNK_SIZE):
                size_bytes += len(chunk)
                if size_bytes > settings.max_upload_size_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB upload limit",
                    )
                await out_file.write(chunk)
                hasher.update(chunk)

        file_hash = hasher.hexdigest()
        # Scoped to owner_id: a global lookup let one user's upload silently
        # reuse another user's telegram_message_id (cross-user file linking).
        existing_file = await db.files.find_one({"owner_id": user_id, "file_hash": file_hash})

        if existing_file:
            new_file_doc = await self._create_file_record(
                db, user_id, safe_filename, file.content_type or "application/octet-stream",
                size_bytes, file_hash, existing_file.get("telegram_message_id"), existing_file.get("thumbnail_message_id"),
                parent_id
            )
            shutil.rmtree(upload_dir, ignore_errors=True)
            return {"file_id": new_file_doc["_id"], "is_duplicate": True}

        file_doc = await self._create_file_record(
            db, user_id, safe_filename, file.content_type or "application/octet-stream",
            size_bytes, file_hash, None, None, parent_id
        )
        task_id = await telegram_service.enqueue_upload(temp_path, file_id=file_doc["_id"])
        await db.uploads.insert_one({
            "_id": upload_id,
            "user_id": user_id,
            "file_id": file_doc["_id"],
            "telegram_task_id": task_id,
            "status": "processing",
            "created_at": datetime.now(timezone.utc)
        })
        return {"file_id": file_doc["_id"], "is_duplicate": False, "telegram_task_id": task_id}

    async def init_chunked_upload(
        self, db: AsyncIOMotorDatabase, filename: str, file_size: int, mime_type: str, total_chunks: int, user_id: str,
        parent_id: Optional[str] = None
    ) -> dict:
        if file_size > settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB upload limit",
            )
        parent_id = await self._resolve_parent_id(db, parent_id, user_id)
        upload_id = str(uuid.uuid4())
        safe_filename = validate_filename(filename)
        await db.uploads.insert_one({
            "_id": upload_id,
            "user_id": user_id,
            "filename": safe_filename,
            "file_size": file_size,
            "mime_type": mime_type,
            "total_chunks": total_chunks,
            "received_chunks": [],
            "parent_id": parent_id,
            "status": "uploading",
            "created_at": datetime.now(timezone.utc)
        })
        self._get_upload_dir(upload_id)
        return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE, "total_chunks": total_chunks}

    async def upload_chunk(
        self, db: AsyncIOMotorDatabase, upload_id: str, chunk_index: int, file: UploadFile, user_id: str
    ) -> dict:
        upload = await db.uploads.find_one({"_id": upload_id, "user_id": user_id})
        if not upload:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")
        if upload.get("status") == "paused":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload is paused.")

        chunk_path = os.path.join(TEMP_UPLOAD_DIR, upload_id, f"chunk_{chunk_index}")
        async with aiofiles.open(chunk_path, "wb") as out_file:
            while chunk := await file.read(CHUNK_SIZE):
                await out_file.write(chunk)

        if chunk_index not in upload.get("received_chunks", []):
            await db.uploads.update_one({"_id": upload_id}, {"$push": {"received_chunks": chunk_index}})

        return {"upload_id": upload_id, "chunk_index": chunk_index}

    async def pause_upload(self, db: AsyncIOMotorDatabase, upload_id: str, user_id: str) -> dict:
        await db.uploads.update_one({"_id": upload_id, "user_id": user_id, "status": "uploading"}, {"$set": {"status": "paused"}})
        return {"upload_id": upload_id, "message": "Upload paused"}

    async def resume_upload(self, db: AsyncIOMotorDatabase, upload_id: str, user_id: str) -> dict:
        upload = await db.uploads.find_one({"_id": upload_id, "user_id": user_id})
        if not upload or upload.get("status") != "paused":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload not found or not paused")
        await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "uploading"}})
        missing = [i for i in range(upload["total_chunks"]) if i not in upload.get("received_chunks", [])]
        return {"upload_id": upload_id, "missing_chunks": missing}

    async def complete_chunked_upload(self, db: AsyncIOMotorDatabase, upload_id: str, user_id: str) -> dict:
        upload = await db.uploads.find_one({"_id": upload_id, "user_id": user_id})
        if not upload or len(upload.get("received_chunks", [])) != upload.get("total_chunks"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not all chunks received")
        await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "processing"}})
        asyncio.create_task(self._process_chunked_upload(db, upload_id, user_id, upload))
        return {"upload_id": upload_id, "message": "Upload completion started"}

    async def _process_chunked_upload(self, db: AsyncIOMotorDatabase, upload_id: str, user_id: str, upload: dict):
        try:
            upload_dir = os.path.join(TEMP_UPLOAD_DIR, upload_id)
            final_path = os.path.join(upload_dir, upload["filename"])
            hasher = hashlib.sha256()

            async with aiofiles.open(final_path, "wb") as out_file:
                for i in range(upload["total_chunks"]):
                    chunk_path = os.path.join(upload_dir, f"chunk_{i}")
                    async with aiofiles.open(chunk_path, "rb") as in_file:
                        while chunk := await in_file.read(CHUNK_SIZE):
                            await out_file.write(chunk)
                            hasher.update(chunk)
                    if os.path.exists(chunk_path):
                        os.remove(chunk_path)

            file_hash = hasher.hexdigest()
            parent_id = upload.get("parent_id")
            existing_file = await db.files.find_one({"owner_id": user_id, "file_hash": file_hash})

            if existing_file:
                new_file_doc = await self._create_file_record(
                    db, user_id, upload["filename"], upload["mime_type"], upload["file_size"],
                    file_hash, existing_file.get("telegram_message_id"), existing_file.get("thumbnail_message_id"),
                    parent_id
                )
                shutil.rmtree(upload_dir, ignore_errors=True)
                await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "completed", "file_id": new_file_doc["_id"], "is_duplicate": True}})
                return

            file_doc = await self._create_file_record(
                db, user_id, upload["filename"], upload["mime_type"], upload["file_size"], file_hash, None, None, parent_id
            )
            task_id = await telegram_service.enqueue_upload(final_path, file_id=file_doc["_id"])
            # status stays "processing" until the telegram worker actually finishes
            # the upload and writes telegram_message_id back (see telegram_service.py)
            await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "processing", "file_id": file_doc["_id"], "telegram_task_id": task_id}})
        except Exception as e:
            await db.uploads.update_one({"_id": upload_id}, {"$set": {"status": "failed", "error": str(e)}})

    async def _create_file_record(
        self, db: AsyncIOMotorDatabase, user_id: str, filename: str, mime_type: str, size_bytes: int, file_hash: str, tg_msg_id: int | None, tg_thumb_id: int | None,
        parent_id: str | None = None
    ) -> dict:
        file_doc = {
            "_id": str(uuid.uuid4()),
            "owner_id": user_id,
            "parent_id": parent_id,
            "original_name": filename,
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

    async def download_file_stream(
        self, message_id: int, start: int = 0, end: int | None = None
    ) -> AsyncGenerator[bytes, None]:
        """Streams file bytes for a given inclusive byte range.

        files.py's /stream endpoint calls this on the StorageEngineDep
        (== UploadService). It delegates to TelegramService.stream_download,
        translating the (start, end) inclusive byte range into the
        (offset, limit-as-byte-count) signature that method expects.
        """
        byte_limit = (end - start + 1) if end is not None else 0
        async for chunk in telegram_service.stream_download(
            message_id, offset=start, limit=byte_limit
        ):
            yield chunk

    async def get_upload_status(self, db: AsyncIOMotorDatabase, upload_id: str, user_id: str) -> dict:
        upload = await db.uploads.find_one({"_id": upload_id, "user_id": user_id})
        if not upload:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")

        progress = 1.0 if upload.get("status") == "completed" else 0.0
        if upload.get("status") == "processing" and upload.get("telegram_task_id"):
            task = telegram_service.get_task_status(upload["telegram_task_id"])
            if task:
                progress = task.progress

        return {
            "upload_id": upload_id,
            "status": upload.get("status"),
            "received_chunks": upload.get("received_chunks", []),
            "total_chunks": upload.get("total_chunks", 0),
            "progress": progress,
            "file_id": upload.get("file_id"),
            "telegram_task_id": upload.get("telegram_task_id")
        }


upload_service = UploadService()

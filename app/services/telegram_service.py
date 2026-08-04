"""Advanced Telegram Storage Service with async queues, retry, and streaming."""

import asyncio
import mimetypes
import os
import shutil
import time
import uuid
from io import BytesIO
from typing import Optional, Callable, AsyncGenerator
import aiofiles
from cachetools import LRUCache, TTLCache
from PIL import Image
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.custom import Message
from telethon.tl.types import DocumentAttributeFilename
from telethon.sessions import StringSession

from app.core.config import get_settings
from app.core.database import db
from app.core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class TaskStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskInfo:
    def __init__(self, task_id: str, task_type: str, file_path: str, message_id: int = None, file_id: str = None):
        self.task_id = task_id
        self.task_type = task_type
        self.file_path = file_path
        self.message_id = message_id
        self.file_id = file_id
        self.status = TaskStatus.QUEUED
        self.progress = 0.0
        self.error = None
        self.result = None
        self.created_at = time.time()
        self._last_persisted_progress = -1.0


class TelegramService:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.upload_queue: asyncio.Queue = asyncio.Queue()
        self.download_queue: asyncio.Queue = asyncio.Queue()
        # Local cache still used for the worker that owns the task; multi-worker
        # status lookups go through MongoDB (see _persist_task / get_task_status_async).
        self.tasks: TTLCache = TTLCache(maxsize=5000, ttl=3600)
        self.message_cache: LRUCache = LRUCache(maxsize=1000)
        self._workers: list = []
        self._health_monitor_task: Optional[asyncio.Task] = None
        self._is_running = False

    async def start(self):
        if self._is_running:
            return
        if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
            logger.warning("Telegram API ID/Hash not configured.")
            return

        # Telethon StringSession is not safe across multiple OS processes.
        # Gunicorn WEB_CONCURRENCY>1 means every worker opens the same session
        # → disconnects / FloodWait. Prefer WEB_CONCURRENCY=1 when Telegram
        # storage is enabled (task status is already cross-worker via Mongo).
        import os
        workers = int(os.environ.get("WEB_CONCURRENCY", "1") or "1")
        if workers > 1:
            logger.warning(
                "telegram_multi_worker_session_risk",
                web_concurrency=workers,
                detail=(
                    "WEB_CONCURRENCY>1 with a shared TELEGRAM_SESSION_STRING "
                    "can cause Telethon session conflicts. Set WEB_CONCURRENCY=1 "
                    "or run Telegram uploads in a single dedicated process."
                ),
            )

        self.client = TelegramClient(
            StringSession(settings.TELEGRAM_SESSION_STRING),
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH,
            connection_retries=5,
            retry_delay=2,
            request_retries=3,
            auto_reconnect=True,
        )
        await self.client.connect()

        if not await self.client.is_user_authorized():
            logger.error("Telegram session not authorized.")
            return

        self._is_running = True
        for _ in range(2):
            self._workers.append(asyncio.create_task(self._upload_worker()))
            self._workers.append(asyncio.create_task(self._download_worker()))

        self._health_monitor_task = asyncio.create_task(self._health_monitor())
        logger.info("Telegram Service started successfully.")

    async def stop(self):
        self._is_running = False
        if self._health_monitor_task:
            self._health_monitor_task.cancel()
        for worker in self._workers:
            worker.cancel()
        if self.client:
            await self.client.disconnect()

    async def _health_monitor(self):
        while self._is_running:
            try:
                if self.client and not self.client.is_connected():
                    await self.client.connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("telegram_health_monitor_error", error=str(e))
            await asyncio.sleep(30)

    def is_connected(self) -> bool:
        return self.client is not None and self.client.is_connected()

    async def _execute_with_retry(self, func, *args, max_retries=3, **kwargs):
        last_error = None
        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except FloodWaitError as e:
                logger.warning("telegram_flood_wait", seconds=e.seconds)
                await asyncio.sleep(e.seconds + 1)
                last_error = e
            except (RPCError, Exception) as e:
                last_error = e
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
        if last_error:
            raise last_error
        raise RuntimeError(f"Operation failed after {max_retries} attempts")

    async def _persist_task(self, task: TaskInfo, force: bool = False) -> None:
        """Write task status to Mongo so other gunicorn workers can read it.

        Progress is throttled to ~5% steps unless force=True (status transitions).
        """
        if not force and abs(task.progress - task._last_persisted_progress) < 0.05:
            return
        task._last_persisted_progress = task.progress
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            await db.tasks.update_one(
                {"_id": task.task_id},
                {
                    "$set": {
                        "task_type": task.task_type,
                        "file_id": task.file_id,
                        "status": task.status,
                        "progress": task.progress,
                        "error": task.error,
                        "result": task.result,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            if task.file_id:
                await db.uploads.update_many(
                    {"file_id": task.file_id},
                    {
                        "$set": {
                            "telegram_progress": task.progress,
                            "status": (
                                "completed"
                                if task.status == TaskStatus.COMPLETED
                                else "failed"
                                if task.status == TaskStatus.FAILED
                                else "processing"
                            ),
                        }
                    },
                )
        except Exception as e:
            logger.error("task_persist_error", task_id=task.task_id, error=str(e))

    def _get_progress_callback(self, task: TaskInfo) -> Callable:
        loop = asyncio.get_event_loop()

        def callback(current: int, total: int):
            task.progress = round(current / total, 4) if total > 0 else 0.0
            # Schedule a throttled persist without blocking the telethon thread.
            if abs(task.progress - task._last_persisted_progress) >= 0.05:
                try:
                    loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self._persist_task(task))
                    )
                except Exception:
                    pass

        return callback

    @staticmethod
    def _cleanup_upload_path(file_path: str) -> None:
        """Removes the per-upload temp directory that contains file_path."""
        if not file_path:
            return
        upload_dir = os.path.dirname(file_path)
        temp_root = os.path.abspath(settings.TEMP_STORAGE_PATH)
        if upload_dir and os.path.abspath(upload_dir).startswith(temp_root + os.sep):
            shutil.rmtree(upload_dir, ignore_errors=True)

    @staticmethod
    def _is_image_path(file_path: str) -> bool:
        mime_type, _ = mimetypes.guess_type(file_path)
        return bool(mime_type and mime_type.startswith("image"))

    def _extract_thumbnail(self, file_path: str) -> Optional[bytes]:
        try:
            with Image.open(file_path) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.thumbnail((320, 320))
                buffer = BytesIO()
                img.save(buffer, format="JPEG", quality=85)
                buffer.seek(0)
                return buffer.read()
        except Exception:
            return None

    async def _get_message(self, message_id: int) -> Optional[Message]:
        if message_id in self.message_cache:
            return self.message_cache[message_id]
        if not self.client:
            return None
        try:
            message = await self._execute_with_retry(
                self.client.get_messages,
                entity=settings.TELEGRAM_STORAGE_CHANNEL_ID,
                ids=message_id,
            )
            if message:
                self.message_cache[message_id] = message
            return message
        except Exception:
            return None

    async def enqueue_upload(self, file_path: str, file_id: str = None) -> str:
        task_id = str(uuid.uuid4())
        task = TaskInfo(task_id, "upload", file_path, file_id=file_id)
        self.tasks[task_id] = task
        await self._persist_task(task, force=True)
        await self.upload_queue.put(task)
        return task_id

    async def enqueue_download(self, message_id: int, save_path: str) -> str:
        task_id = str(uuid.uuid4())
        task = TaskInfo(task_id, "download", save_path, message_id)
        self.tasks[task_id] = task
        await self._persist_task(task, force=True)
        await self.download_queue.put(task)
        return task_id

    def get_task_status(self, task_id: str) -> Optional[TaskInfo]:
        """In-process lookup only — prefer get_task_status_async under multi-worker."""
        return self.tasks.get(task_id)

    async def get_task_status_async(self, task_id: str) -> Optional[TaskInfo]:
        """Cross-worker safe: memory first, then MongoDB `tasks` collection."""
        local = self.tasks.get(task_id)
        if local:
            return local
        try:
            doc = await db.tasks.find_one({"_id": task_id})
        except Exception:
            return None
        if not doc:
            return None
        task = TaskInfo(
            task_id,
            doc.get("task_type", "upload"),
            "",
            file_id=doc.get("file_id"),
        )
        task.status = doc.get("status", TaskStatus.QUEUED)
        task.progress = float(doc.get("progress") or 0.0)
        task.error = doc.get("error")
        task.result = doc.get("result")
        task.created_at = doc.get("created_at") or time.time()
        return task

    async def _upload_worker(self):
        while self._is_running:
            try:
                task: TaskInfo = await self.upload_queue.get()
            except asyncio.CancelledError:
                break
            if not task:
                continue
            task.status = TaskStatus.PROCESSING
            await self._persist_task(task, force=True)
            try:
                if not self.client:
                    raise RuntimeError("Telegram client is not connected")
                attributes = [DocumentAttributeFilename(os.path.basename(task.file_path))]
                thumbnail = None
                if self._is_image_path(task.file_path):
                    thumbnail = await asyncio.to_thread(self._extract_thumbnail, task.file_path)
                message = await self._execute_with_retry(
                    self.client.send_file,
                    entity=settings.TELEGRAM_STORAGE_CHANNEL_ID,
                    file=task.file_path,
                    attributes=attributes,
                    thumb=thumbnail,
                    part_size_kb=512,
                    progress_callback=self._get_progress_callback(task),
                )
                task.result = message.id
                task.status = TaskStatus.COMPLETED
                task.progress = 1.0
                self.message_cache[message.id] = message
                if task.file_id:
                    await db.files.update_one(
                        {"_id": task.file_id},
                        {"$set": {"telegram_message_id": message.id, "status": "completed"}},
                    )
                    await db.uploads.update_many(
                        {"file_id": task.file_id},
                        {"$set": {"status": "completed", "telegram_progress": 1.0}},
                    )
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                if task.file_id:
                    # Mark failed and refund quota charged at record-create time.
                    # quota_refunded=True prevents move_to_trash / permanent_delete
                    # from subtracting size_bytes a second time (double-refund bug).
                    file_doc = await db.files.find_one(
                        {"_id": task.file_id}, {"size_bytes": 1, "owner_id": 1, "quota_refunded": 1}
                    )
                    await db.files.update_one(
                        {"_id": task.file_id},
                        {"$set": {"status": "failed", "quota_refunded": True}},
                    )
                    await db.uploads.update_many(
                        {"file_id": task.file_id}, {"$set": {"status": "failed"}}
                    )
                    if (
                        file_doc
                        and file_doc.get("size_bytes")
                        and not file_doc.get("quota_refunded")
                    ):
                        await db.users.update_one(
                            {"_id": file_doc["owner_id"]},
                            {"$inc": {"storage_used_bytes": -int(file_doc["size_bytes"])}},
                        )
            finally:
                await self._persist_task(task, force=True)
                self._cleanup_upload_path(task.file_path)
                self.upload_queue.task_done()

    async def _download_worker(self):
        while self._is_running:
            try:
                task: TaskInfo = await self.download_queue.get()
            except asyncio.CancelledError:
                break
            if not task:
                continue
            task.status = TaskStatus.PROCESSING
            await self._persist_task(task, force=True)
            try:
                if not self.client:
                    raise RuntimeError("Telegram client is not connected")
                message = await self._get_message(task.message_id)
                if not message or not message.media:
                    raise ValueError("Media not found")

                total_bytes = getattr(message, "file", None)
                total_size = total_bytes.size if total_bytes else 0
                downloaded = 0

                async with aiofiles.open(task.file_path, "wb") as f:
                    async for chunk in self.client.iter_download(message.media, request_size=1024 * 1024):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            task.progress = round(downloaded / total_size, 4)

                task.status = TaskStatus.COMPLETED
                task.progress = 1.0
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                if os.path.exists(task.file_path):
                    os.remove(task.file_path)
            finally:
                await self._persist_task(task, force=True)
                self.download_queue.task_done()

    async def stream_download(self, message_id: int, offset: int = 0, limit: int = 0) -> AsyncGenerator[bytes, None]:
        if not self.client:
            raise RuntimeError("Telegram client is not connected")
        message = await self._get_message(message_id)
        if not message or not message.media:
            raise ValueError("Message or media not found")
        yielded = 0
        try:
            async for chunk in self.client.iter_download(message.media, offset=offset, request_size=512 * 1024):
                if limit > 0 and yielded + len(chunk) > limit:
                    yield chunk[: limit - yielded]
                    return
                yield chunk
                yielded += len(chunk)
                if limit > 0 and yielded >= limit:
                    return
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
            raise
        except Exception as e:
            logger.error("stream_download_error", error=str(e))
            raise


telegram_service = TelegramService()

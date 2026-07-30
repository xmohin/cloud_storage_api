"""Advanced Telegram Storage Service with async queues, retry, and streaming."""

import asyncio
import os
import time
import uuid
from io import BytesIO
from typing import Optional, Callable, AsyncGenerator, Dict, Any
import aiofiles
from cachetools import LRUCache, TTLCache
from PIL import Image
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.custom import Message
from telethon.tl.types import DocumentAttributeFilename
from telethon.sessions import StringSession

from app.core.config import get_settings
from app.core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class TaskStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskInfo:
    def __init__(self, task_id: str, task_type: str, file_path: str, message_id: int = None):
        self.task_id = task_id
        self.task_type = task_type
        self.file_path = file_path
        self.message_id = message_id
        self.status = TaskStatus.QUEUED
        self.progress = 0.0
        self.error = None
        self.result = None
        self.created_at = time.time()


class TelegramService:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.upload_queue: asyncio.Queue = asyncio.Queue()
        self.download_queue: asyncio.Queue = asyncio.Queue()
        # memory leak বন্ধ করার জন্য TTLCache (max size: 5000, TTL: 1 hour)
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

    def _get_progress_callback(self, task: TaskInfo) -> Callable:
        def callback(current: int, total: int):
            task.progress = round(current / total, 4) if total > 0 else 0.0
        return callback

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

    async def enqueue_upload(self, file_path: str) -> str:
        task_id = str(uuid.uuid4())
        task = TaskInfo(task_id, 'upload', file_path)
        self.tasks[task_id] = task
        await self.upload_queue.put(task)
        return task_id

    async def enqueue_download(self, message_id: int, save_path: str) -> str:
        task_id = str(uuid.uuid4())
        task = TaskInfo(task_id, 'download', save_path, message_id)
        self.tasks[task_id] = task
        await self.download_queue.put(task)
        return task_id

    def get_task_status(self, task_id: str) -> Optional[TaskInfo]:
        return self.tasks.get(task_id)

    async def _upload_worker(self):
        while self._is_running:
            try:
                task: TaskInfo = await self.upload_queue.get()
            except asyncio.CancelledError:
                break
            if not task:
                continue
            task.status = TaskStatus.PROCESSING
            try:
                attributes = [DocumentAttributeFilename(os.path.basename(task.file_path))]
                message = await self._execute_with_retry(
                    self.client.send_file,
                    entity=settings.TELEGRAM_STORAGE_CHANNEL_ID,
                    file=task.file_path,
                    attributes=attributes,
                    thumb=self._extract_thumbnail(task.file_path),
                    part_size_kb=512,
                    progress_callback=self._get_progress_callback(task),
                )
                task.result = message.id
                task.status = TaskStatus.COMPLETED
                task.progress = 1.0
                self.message_cache[message.id] = message
                if os.path.exists(task.file_path):
                    os.remove(task.file_path)
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
            finally:
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
            try:
                message = await self._get_message(task.message_id)
                if not message or not message.media:
                    raise ValueError("Media not found")

                total_bytes = getattr(message, "file", None)
                total_size = total_bytes.size if total_bytes else 0
                downloaded = 0

                async with aiofiles.open(task.file_path, 'wb') as f:
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
                self.download_queue.task_done()

    async def stream_download(self, message_id: int, offset: int = 0, limit: int = 0) -> AsyncGenerator[bytes, None]:
        message = await self._get_message(message_id)
        if not message or not message.media:
            raise ValueError("Message or media not found")
        yielded = 0
        try:
            async for chunk in self.client.iter_download(message.media, offset=offset, request_size=512 * 1024):
                if limit > 0 and yielded + len(chunk) > limit:
                    yield chunk[:limit - yielded]
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

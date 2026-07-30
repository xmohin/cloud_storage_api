"""Advanced Async Email Service with retry queue, rate limiting, and HTML templates."""

import asyncio, time
from datetime import datetime, timezone
from typing import Optional
from cachetools import TTLCache
from app.core.config import get_settings
from app.core.database import email_client
from app.core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)

EMAIL_RATE_LIMIT_SECONDS = 60
MAX_RETRIES = 3

class EmailService:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self._workers: list = []
        self._is_running = False
        self._rate_limits: TTLCache = TTLCache(maxsize=10000, ttl=EMAIL_RATE_LIMIT_SECONDS)

    async def start(self):
        if self._is_running: return
        self._is_running = True
        for _ in range(2): self._workers.append(asyncio.create_task(self._worker()))

    async def stop(self):
        self._is_running = False
        for _ in self._workers: await self.queue.put(None)
        for w in self._workers: await w

    async def enqueue_email(self, to_email: str, subject: str, html_content: str, text_content: str = None):
        await self.queue.put({"to_email": to_email, "subject": subject, "html_content": html_content, "text_content": text_content, "retries": 0})

    async def _worker(self):
        while self._is_running:
            task = await self.queue.get()
            if not task: break
            email = task["to_email"]
            if email in self._rate_limits:
                asyncio.create_task(self._delay_requeue(task, EMAIL_RATE_LIMIT_SECONDS))
                self.queue.task_done(); continue
            success = await email_client.send_email(email, task["subject"], task["html_content"], text_content=task.get("text_content"))
            if success: self._rate_limits[email] = time.time()
            else:
                if task["retries"] < MAX_RETRIES:
                    task["retries"] += 1; delay = 2 ** task["retries"]
                    asyncio.create_task(self._delay_requeue(task, delay))
            self.queue.task_done()

    async def _delay_requeue(self, task: dict, delay: int):
        await asyncio.sleep(delay); await self.queue.put(task)

    async def send_otp_email(self, email: str, otp: str, purpose: str = "verification"):
        html = f"<html><body><h2>Gallery Vault - {purpose.title()}</h2><p>Your OTP is:</p><h1>{otp}</h1></body></html>"
        await self.enqueue_email(email, f"{purpose.title()} OTP - Gallery Vault", html, f"Your {purpose} OTP is: {otp}")

    async def send_share_email(self, to_email: str, owner_name: str, filename: str, share_url: str, password: Optional[str] = None):
        pwd_html = f"<p><strong>Password:</strong> {password}</p>" if password else ""
        html = f"<html><body><h2>File Shared with You</h2><p><strong>{owner_name}</strong> shared <strong>{filename}</strong> with you.</p>{pwd_html}<a href='{share_url}'>Download File</a></body></html>"
        await self.enqueue_email(to_email, f"{owner_name} shared a file with you", html, f"Download: {share_url}")

email_service = EmailService()

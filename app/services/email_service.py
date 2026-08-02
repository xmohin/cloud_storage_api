"""Advanced Async Email Service with retry queue, rate limiting, and HTML templates."""

import asyncio, html, time
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
            if success:
                self._rate_limits[email] = time.time()
            else:
                if task["retries"] < MAX_RETRIES:
                    task["retries"] += 1; delay = 2 ** task["retries"]
                    asyncio.create_task(self._delay_requeue(task, delay))
                else:
                    # Previously dropped with zero logging here — the only
                    # trace was database.py's per-attempt Brevo error, easy
                    # to miss. Log once, clearly, when we give up entirely.
                    logger.error(
                        "Email delivery permanently failed after max retries — dropping",
                        to_email=email, subject=task["subject"], retries=task["retries"],
                    )
            self.queue.task_done()

    async def _delay_requeue(self, task: dict, delay: int):
        await asyncio.sleep(delay); await self.queue.put(task)

    @property
    def is_configured(self) -> bool:
        return email_client.is_configured

    async def send_otp_email(self, email: str, otp: str, purpose: str = "verification"):
        # purpose can be free text supplied by the caller (OTPRequest.purpose
        # on /send-otp is user input) — escape before interpolating into HTML
        # so it can't inject markup/script content into the outgoing email
        # (MEDIUM #17). otp is always server-generated, but html.escape is
        # cheap enough to apply uniformly rather than special-case it.
        safe_purpose = html.escape(purpose.title())
        html_body = f"<html><body><h2>Gallery Vault - {safe_purpose}</h2><p>Your OTP is:</p><h1>{html.escape(otp)}</h1></body></html>"
        await self.enqueue_email(email, f"{safe_purpose} OTP - Gallery Vault", html_body, f"Your {purpose} OTP is: {otp}")

    async def send_share_email(self, to_email: str, owner_name: str, filename: str, share_url: str, password: Optional[str] = None):
        # Same treatment as send_otp_email above — owner_name (username) and
        # filename are user-supplied and were previously interpolated into
        # HTML unescaped; share_url was even placed inside a single-quoted
        # attribute, so a stray "'" in it would have broken out of the tag.
        safe_owner = html.escape(owner_name)
        safe_filename = html.escape(filename)
        safe_url = html.escape(share_url, quote=True)
        pwd_html = f"<p><strong>Password:</strong> {html.escape(password)}</p>" if password else ""
        html_body = (
            f"<html><body><h2>File Shared with You</h2>"
            f"<p><strong>{safe_owner}</strong> shared <strong>{safe_filename}</strong> with you.</p>"
            f"{pwd_html}<a href=\"{safe_url}\">Download File</a></body></html>"
        )
        await self.enqueue_email(to_email, f"{owner_name} shared a file with you", html_body, f"Download: {share_url}")

email_service = EmailService()

"""External service connection management — MongoDB and Brevo."""

from typing import Any
import httpx
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.core.config import get_settings
from app.core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class Database:
    _client: AsyncIOMotorClient | None = None
    _database: AsyncIOMotorDatabase | None = None

    @classmethod
    async def connect(cls) -> None:
        if cls._client is not None:
            return
        safe_uri = settings.MONGODB_URI.split("@")[-1] if "@" in settings.MONGODB_URI else "localhost"
        logger.info("Connecting to MongoDB", host=safe_uri, db=settings.MONGODB_DB_NAME)
        cls._client = AsyncIOMotorClient(
            settings.MONGODB_URI,
            maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
            minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
            maxIdleTimeMS=settings.MONGODB_MAX_IDLE_TIME_MS,
            serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
            retryWrites=True,
            retryReads=True,
            appName=settings.APP_NAME,
        )
        try:
            await cls._client.admin.command("ping")
        except Exception as exc:
            cls._client = None
            logger.error("MongoDB connection failed", error=str(exc))
            raise
        cls._database = cls._client[settings.MONGODB_DB_NAME]
        await cls._ensure_indexes()

    @classmethod
    async def _ensure_indexes(cls) -> None:
        db = cls._database
        if db is None:
            return
        await db.users.create_index("email", unique=True)
        await db.users.create_index("username", unique=True)
        await db.files.create_index(
            [("owner_id", 1), ("parent_id", 1), ("deleted_at", 1), ("is_folder", -1), ("original_name", 1)],
            name="idx_directory_list",
        )
        await db.files.create_index([("owner_id", 1), ("is_favorite", 1), ("deleted_at", 1)], name="idx_favorites")
        await db.files.create_index([("owner_id", 1), ("deleted_at", 1), ("created_at", -1)], name="idx_recent")
        await db.files.create_index([("owner_id", 1), ("deleted_at", 1)], name="idx_trash")
        await db.files.create_index([("original_name", "text")], name="idx_search")
        await db.files.create_index("file_hash", name="idx_dedup")
        await db.files.create_index("deleted_expires_at", expireAfterSeconds=0, name="idx_trash_ttl")
        await db.tokens.create_index("jti", unique=True)
        await db.tokens.create_index("expires_at", expireAfterSeconds=0)
        await db.sessions.create_index("user_id")
        await db.sessions.create_index("refresh_jti", unique=True)
        await db.sessions.create_index("expires_at", expireAfterSeconds=0)
        await db.blacklist.create_index("jti", unique=True)
        await db.blacklist.create_index("expires_at", expireAfterSeconds=0)
        await db.uploads.create_index("user_id")
        await db.uploads.create_index("status")
        await db.uploads.create_index("created_at", expireAfterSeconds=86400)
        await db.shares.create_index("share_token", unique=True)
        await db.shares.create_index([("owner_id", 1), ("file_id", 1)])
        await db.shares.create_index("expires_at", expireAfterSeconds=0)
        await db.share_logs.create_index("share_id")
        await db.share_logs.create_index("accessed_at", expireAfterSeconds=2592000)
        logger.info("MongoDB indexes ensured")

    @classmethod
    async def disconnect(cls) -> None:
        if cls._client is not None:
            cls._client.close()
            cls._client = None
            cls._database = None
            logger.info("MongoDB disconnected")

    @classmethod
    def get_database(cls) -> AsyncIOMotorDatabase:
        if cls._database is None:
            raise RuntimeError("Database not initialised")
        return cls._database

    @classmethod
    async def health_check(cls) -> bool:
        try:
            if cls._client is None:
                return False
            result = await cls._client.admin.command("ping")
            return result.get("ok") == 1.0
        except Exception:
            return False


db = Database()


class BrevoEmailClient:
    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._base_url = settings.BREVO_API_BASE_URL.rstrip("/")
        self._api_key = settings.BREVO_API_KEY
        self._sender_email = settings.BREVO_SENDER_EMAIL
        self._sender_name = settings.BREVO_SENDER_NAME

    async def connect(self) -> None:
        if self._http is not None:
            return
        if not self._api_key:
            logger.warning("Brevo API key not configured")
            return
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "api-key": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        )
        logger.info("Brevo HTTP client initialised")

    async def disconnect(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
            logger.info("Brevo HTTP client closed")

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        to_name: str | None = None,
        text_content: str | None = None,
    ) -> bool:
        if self._http is None:
            return False
        payload: dict[str, Any] = {
            "sender": {"email": self._sender_email, "name": self._sender_name},
            "to": [{"email": to_email, **({"name": to_name} if to_name else {})}],
            "subject": subject,
            "htmlContent": html_content,
        }
        if text_content:
            payload["textContent"] = text_content
        try:
            resp = await self._http.post("/smtp/email", json=payload)
            if resp.status_code in (200, 201):
                return True
            logger.error("Brevo API error", status_code=resp.status_code, body=resp.text)
            return False
        except httpx.HTTPError as exc:
            logger.error("Brevo request failed", error=str(exc))
            return False


email_client = BrevoEmailClient()

"""External service connection management — MongoDB and Brevo."""

from typing import Any
import httpx
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.collation import Collation
from pymongo.errors import OperationFailure
from app.core.config import get_settings
from app.core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class Database:
    _client: AsyncIOMotorClient | None = None
    _database: AsyncIOMotorDatabase | None = None

    def __getattr__(self, name: str) -> Any:
        """
        Proxy attribute access to the underlying Motor database instance.
        This allows accessing collections directly via db.collection_name (e.g., db.users).
        """
        if self._database is None:
            raise RuntimeError("Database not initialised")
        return self._database[name]

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
    async def _ensure_case_insensitive_username_index(cls, db: AsyncIOMotorDatabase) -> None:
        """Unique index on `username`, case-insensitive so "User" and "user"
        can't end up as two separate accounts. strength=2 compares
        case-insensitively while still treating accented characters as
        distinct from their unaccented form.

        If a deployment already has legacy accounts differing only by case,
        building this index fails outright (Mongo won't build a unique index
        over data that already violates it) — in that case we log loudly for
        an operator to clean up the duplicates, and fall back to the old
        case-sensitive index so at least basic uniqueness holds and startup
        isn't blocked on a data-hygiene issue.
        """
        try:
            await db.users.create_index(
                "username",
                unique=True,
                collation=Collation(locale="en", strength=2),
            )
        except OperationFailure as exc:
            logger.error(
                "username_case_insensitive_index_failed",
                error=str(exc),
                hint=(
                    "Two or more existing usernames likely differ only by "
                    "case — rename one of each colliding pair, then restart "
                    "so the case-insensitive index can build. Falling back "
                    "to the case-sensitive index for now."
                ),
            )
            await db.users.create_index("username", unique=True)

    @classmethod
    async def _ensure_indexes(cls) -> None:
        db = cls._database
        if db is None:
            return
        await db.users.create_index("email", unique=True)
        await cls._ensure_case_insensitive_username_index(db)
        await db.files.create_index(
            [("owner_id", 1), ("parent_id", 1), ("deleted_at", 1), ("is_folder", -1), ("original_name", 1)],
            name="idx_directory_list",
        )
        await db.files.create_index([("owner_id", 1), ("is_favorite", 1), ("deleted_at", 1)], name="idx_favorites")
        await db.files.create_index([("owner_id", 1), ("deleted_at", 1), ("created_at", -1)], name="idx_recent")
        await db.files.create_index([("owner_id", 1), ("deleted_at", 1)], name="idx_trash")
        await db.files.create_index([("original_name", "text")], name="idx_search")
        # Old index was on file_hash alone, matching the pre-fix global dedup
        # lookup. Dedup is now owner-scoped (see upload_service.py, HIGH #10),
        # so drop the stale definition before recreating it under the same
        # name with the compound key — Mongo errors on a name reused with a
        # different key spec, same as the sessions index below.
        try:
            await db.files.drop_index("idx_dedup")
        except Exception:
            pass
        await db.files.create_index([("owner_id", 1), ("file_hash", 1)], name="idx_dedup")
        # TTL removed — purge_expired_trash() now deletes these after Telegram cleanup
        try:
            await db.files.drop_index("idx_trash_ttl")
        except Exception:
            pass
        await db.files.create_index("deleted_expires_at", name="idx_trash_ttl")
        await db.tokens.create_index("jti", unique=True)
        await db.tokens.create_index("expires_at", expireAfterSeconds=0)
        
        # Drop the old conflicting index if it exists
        try:
            await db.sessions.drop_index("user_id_1")
        except Exception:
            pass
            
        # Non-unique: a user can have multiple active sessions (multi-device login,
        # revoked via update_many in auth.py). A unique index here caused
        # DuplicateKeyError on a user's 2nd login/device.
        await db.sessions.create_index("user_id")
        await db.sessions.create_index("refresh_jti", unique=True)
        await db.sessions.create_index("expires_at", expireAfterSeconds=0)
        
        await db.blacklist.create_index("jti", unique=True)
        await db.blacklist.create_index("expires_at", expireAfterSeconds=0)
        await db.uploads.create_index("user_id")
        await db.uploads.create_index("status")
        # TTL was on the static created_at, so a chunked upload still in
        # progress past 24h (paused/resumed, or just slow) had its session
        # doc deleted out from under it mid-upload. last_activity_at is
        # refreshed on every chunk/pause/resume in upload_service.py, so
        # only genuinely abandoned sessions expire now — see MEDIUM #19.
        try:
            await db.uploads.drop_index("created_at_1")
        except Exception:
            pass
        await db.uploads.create_index(
            "last_activity_at", expireAfterSeconds=86400, name="idx_uploads_ttl"
        )
        await db.shares.create_index("share_token", unique=True)
        await db.shares.create_index([("owner_id", 1), ("file_id", 1)])
        await db.shares.create_index("expires_at", expireAfterSeconds=0)
        await db.share_logs.create_index("share_id")
        await db.share_logs.create_index("accessed_at", expireAfterSeconds=2592000)
        # Cross-worker Telegram task status — auto-expire after 1h
        await db.tasks.create_index("updated_at", expireAfterSeconds=3600, name="idx_tasks_ttl")
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
    def is_connected(cls) -> bool:
        """Whether connect() has completed and the database handle is live.

        Public wrapper so callers (e.g. the /health endpoint) don't need to
        reach into the private `_database` attribute directly. This is a
        cheap, in-process check (no I/O) — use health_check() instead when
        you need to confirm the connection is actually reachable right now.
        """
        return cls._database is not None

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

    @property
    def is_configured(self) -> bool:
        """True once a Brevo API key was present and the HTTP client initialised.

        False means every send_email() call will short-circuit to `return False`
        without even attempting a network call — the most common reason OTP
        emails silently never arrive.
        """
        return self._http is not None

    async def connect(self) -> None:
        if self._http is not None:
            return
        if not self._api_key:
            logger.warning(
                "Brevo API key not configured — OTP/share emails will silently "
                "fail to send until BREVO_API_KEY is set."
            )
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
        logger.info("Brevo HTTP client initialised", sender=self._sender_email)

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
            logger.error(
                "Email send skipped — Brevo client not configured",
                to_email=to_email, subject=subject,
            )
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
            logger.error(
                "Brevo API error", to_email=to_email, subject=subject,
                status_code=resp.status_code, body=resp.text,
            )
            return False
        except httpx.HTTPError as exc:
            logger.error(
                "Brevo request failed", to_email=to_email, subject=subject, error=str(exc)
            )
            return False


email_client = BrevoEmailClient()

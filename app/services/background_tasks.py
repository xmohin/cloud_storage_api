"""Background tasks for garbage collection and cleanup worker."""

import asyncio
from datetime import datetime, timedelta, timezone
from app.core.database import db
from app.core.logger import get_logger

logger = get_logger(__name__)


async def start_background_tasks():
    while True:
        try:
            database = db.get_database()
            if database is not None:
                threshold = datetime.now(timezone.utc) - timedelta(days=30)
                deleted_files = database.files.find({"deleted_at": {"$ne": None, "$lt": threshold}})
                async for file in deleted_files:
                    await database.files.delete_one({"_id": file["_id"]})
                    logger.info("expired_trash_purged", file_id=str(file["_id"]))
        except Exception as e:
            logger.error("background_task_error", error=str(e))
        await asyncio.sleep(86400)

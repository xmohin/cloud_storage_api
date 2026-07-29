import asyncio
from datetime import datetime, timedelta, timezone
from app.core.database import db_manager
from app.core.logger import logger

async def start_background_tasks():
    while True:
        try:
            db = db_manager.db
            if db is not None:
                # Cleanup soft deleted files older than 30 days
                threshold = datetime.now(timezone.utc) - timedelta(days=30)
                deleted_files = db.files.find({"is_deleted": True, "deleted_at": {"$lt": threshold}})
                async for file in deleted_files:
                    await db.files.delete_one({"_id": file["_id"]})
                    logger.info(f"Permanently purged expired trash file: {file['_id']}")
        except Exception as e:
            logger.error(f"Error in background task worker: {str(e)}")
        await asyncio.sleep(86400) # Run daily

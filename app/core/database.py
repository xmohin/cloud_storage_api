from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.logger import logger

class Database:
    client: AsyncIOMotorClient | None = None

    @classmethod
    def connect(cls) -> None:
        try:
            logger.info("Connecting to MongoDB Atlas...")
            cls.client = AsyncIOMotorClient(
                settings.MONGODB_URI,
                minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
                maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
                serverSelectionTimeoutMS=5000,
                tz_aware=True
            )
            logger.info("Successfully connected to MongoDB Atlas.")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            raise e

    @classmethod
    def disconnect(cls) -> None:
        if cls.client:
            logger.info("Closing MongoDB connection...")
            cls.client.close()
            logger.info("MongoDB connection closed.")

    @classmethod
    def get_db(cls):
        if cls.client is None:
            raise ConnectionError("Database client is not initialized.")
        return cls.client[settings.MONGODB_DB_NAME]

db_manager = Database()

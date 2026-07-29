from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.core.config import settings
from app.core.logger import logger

class DatabaseManager:
    client: AsyncIOMotorClient = None
    db: AsyncIOMotorDatabase = None

db_manager = DatabaseManager()

async def connect_to_mongo():
    logger.info("Connecting to MongoDB Atlas...")
    db_manager.client = AsyncIOMotorClient(
        settings.MONGO_URI,
        maxPoolSize=100,
        minPoolSize=10,
        serverSelectionTimeoutMS=5000
    )
    db_manager.db = db_manager.client[settings.DATABASE_NAME]
    await init_indexes()
    logger.info("Connected to MongoDB successfully and verified indexes.")

async def close_mongo_connection():
    if db_manager.client:
        logger.info("Closing MongoDB connection...")
        db_manager.client.close()

async def init_indexes():
    db = db_manager.db
    
    # Users Index
    await db.users.create_index("email", unique=True)
    
    # Session & Blacklist Indexes
    await db.sessions.create_index([("user_id", 1), ("session_id", 1)], unique=True)
    await db.token_blacklist.create_index("token", unique=True)
    await db.token_blacklist.create_index("expires_at", expireAfterSeconds=0)
    
    # Files Indexes
    await db.files.create_index([("owner_id", 1), ("is_deleted", 1)])
    await db.files.create_index([("sha256_hash", 1)])
    await db.files.create_index([("folder_id", 1), ("owner_id", 1)])
    await db.files.create_index([("owner_id", 1), ("is_favorite", 1)])
    
    # Folders Indexes
    await db.folders.create_index([("owner_id", 1), ("parent_id", 1)])
    
    # Shares Indexes
    await db.shares.create_index("share_code", unique=True)

def get_database() -> AsyncIOMotorDatabase:
    return db_manager.db

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import connect_to_mongo, close_mongo_connection
from app.core.middleware import EnterpriseSecurityMiddleware
from app.core.logger import logger
from app.services.telegram_service import telegram_service
from app.services.background import start_background_tasks
from app.api import auth, upload, files, folders, shares, trash, stats, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup Phase ---
    logger.info("Starting Gallery Vault Backend Services...")
    
    # 1. Connect MongoDB
    await connect_to_mongo()
    
    # 2. Connect Telegram Service safely (Non-blocking crash)
    try:
        if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_API_ID != 0:
            await telegram_service.start()
        else:
            logger.warning("Telegram credentials not fully configured. Telegram service skipped.")
    except Exception as e:
        logger.error(f"Failed to connect Telegram Client on startup: {str(e)}")
    
    # 3. Start Background Worker Task
    bg_task = asyncio.create_task(start_background_tasks())
    
    yield
    
    # --- Shutdown Phase ---
    logger.info("Shutting down Gallery Vault Backend Services...")
    
    # Cancel background worker
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        logger.info("Background tasks safely cancelled.")
        
    # Stop Telegram service
    try:
        await telegram_service.stop()
    except Exception as e:
        logger.error(f"Error disconnecting Telegram service: {str(e)}")
        
    # Close Mongo Connection
    await close_mongo_connection()


app = FastAPI(
    title=settings.APP_NAME,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Custom Middlewares
app.add_middleware(EnterpriseSecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(auth.router)
app.include_router(upload.router)
app.include_router(files.router)
app.include_router(folders.router)
app.include_router(shares.router)
app.include_router(trash.router)
app.include_router(stats.router)
app.include_router(admin.router)


@app.get("/health", tags=["Health Check"])
async def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "app_name": settings.APP_NAME
    }

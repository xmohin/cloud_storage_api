from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from telethon import TelegramClient

from app.core.config import settings
from app.core.database import db_manager
from app.core.logger import logger
from app.core.middleware import RequestIDMiddleware, SecurityHeadersMiddleware, RateLimitMiddleware
from app.models.schemas import HealthCheck, StandardResponse

# Dictionary to hold app state like external clients
app_state = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup sequence
    logger.info("Initializing Gallery Vault application...")
    
    # 1. Initialize Database
    db_manager.connect()
    
    # 2. Initialize Telethon Client (Foundation)
    telegram_client = TelegramClient(
        'gallery_vault_session', 
        settings.TELEGRAM_API_ID, 
        settings.TELEGRAM_API_HASH
    )
    await telegram_client.start(bot_token=settings.TELEGRAM_BOT_TOKEN)
    app_state["telegram_client"] = telegram_client
    logger.info("Telegram client initialized successfully.")
    
    yield
    
    # Shutdown sequence
    logger.info("Shutting down Gallery Vault application...")
    
    # 1. Disconnect Database
    db_manager.disconnect()
    
    # 2. Disconnect Telethon
    client = app_state.get("telegram_client")
    if client:
        await client.disconnect()
        logger.info("Telegram client disconnected.")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="Production backend for Gallery Vault cloud storage.",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENVIRONMENT == "development" else ["https://yourproductiondomain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom Middlewares
app.add_middleware(RateLimitMiddleware, max_requests=150, window_seconds=60)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)

# Global Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled exception: {str(exc)}", 
        exc_info=True,
        extra={"url": str(request.url), "method": request.method}
    )
    return JSONResponse(
        status_code=500,
        content={"success": False, "message": "An internal server error occurred.", "data": None}
    )

@app.get("/health", response_model=HealthCheck, tags=["System"])
async def health_check():
    """System health check endpoint for Load Balancers and Render checks."""
    try:
        # Perform a lightweight ping to the DB to ensure connection is alive
        db = db_manager.get_db()
        await db.command("ping")
        db_status = "connected"
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        db_status = "disconnected"
        
    return HealthCheck(
        status="healthy" if db_status == "connected" else "unhealthy",
        database=db_status,
        version="1.0.0",
        environment=settings.ENVIRONMENT
    )

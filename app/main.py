"""Gallery Vault API — application entry point."""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from app import __version__
from app.core.config import get_settings
from app.core.database import db, email_client
from app.services.telegram_service import telegram_service
from app.services.upload_service import upload_service
from app.services.email_service import email_service
from app.core.logger import configure_logging, get_logger
from app.core.middleware import setup_exception_handlers, setup_middleware, limiter

# Import all routers
from app.api.v1.auth import router as auth_router
from app.api.v1.user import router as user_router
from app.api.v1.files import router as files_router
from app.api.v1.folders import router as folders_router
from app.api.v1.search import router as search_router
from app.api.v1.share import router as share_router
from app.api.v1.backup import router as backup_router
from app.api.v1.storage import router as storage_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.admin import router as admin_router
from app.api.v1.security import router as security_router
from app.api.v1.system import router as system_router

settings = get_settings()
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Gallery Vault API", version=__version__, env=settings.APP_ENV)
    
    # ── Startup Phase ──
    try:
        await db.connect()
        await telegram_service.start()
        await upload_service.start()
        await email_client.connect()
        await email_service.start()
        logger.info("All background services initialized successfully.")
    except Exception as e:
        logger.critical(f"Failed to start services during startup: {e}")
        raise e

    yield

    # ── Shutdown Phase ──
    logger.info("Shutting down Gallery Vault API...")
    
    # Each cleanup step is executed safely
    for service_name, stop_coro in [
        ("Email Service", email_service.stop()),
        ("Email Client", email_client.disconnect()),
        ("Upload Service", upload_service.stop()),
        ("Telegram Service", telegram_service.stop()),
        ("Database Connection", db.disconnect()),
    ]:
        try:
            await stop_coro
            logger.info(f"{service_name} stopped successfully.")
        except Exception as e:
            logger.error(f"Error stopping {service_name}: {e}")

    logger.info("Application shutdown complete.")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # Attach rate limiter state & setup global middlewares/handlers
    app.state.limiter = limiter
    setup_middleware(app)
    setup_exception_handlers(app)

    # ── API Routers ──
    app.include_router(system_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(user_router, prefix="/api/v1")
    app.include_router(files_router, prefix="/api/v1")
    app.include_router(folders_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(share_router, prefix="/api/v1")
    app.include_router(backup_router, prefix="/api/v1")
    app.include_router(storage_router, prefix="/api/v1")
    app.include_router(notifications_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(security_router, prefix="/api/v1")

    return app


app = create_app()

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
from app.models.schemas import HealthResponse
from app.api.v1.auth import router as auth_router
from app.api.v1.uploads import router as uploads_router
from app.api.v1.files import router as files_router
from app.api.v1.shares import router as shares_router

settings = get_settings()
configure_logging()
logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Gallery Vault API", version=__version__, env=settings.APP_ENV)
    await db.connect()
    await telegram_service.start()
    await upload_service.start()
    await email_client.connect()
    await email_service.start()
    yield
    logger.info("Shutting down Gallery Vault API")
    await email_service.stop()
    await email_client.disconnect()
    await upload_service.stop()
    await telegram_service.stop()
    await db.disconnect()

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    setup_middleware(app)
    setup_exception_handlers(app)

    @app.get("/health", response_model=HealthResponse, tags=["System"])
    async def health_check() -> HealthResponse:
        mongo_ok = await db.health_check()
        tg_ok = telegram_service.is_connected()
        services = {
            "mongodb": "healthy" if mongo_ok else "unhealthy",
            "telegram": "healthy" if tg_ok else "unhealthy",
            "brevo": "healthy" if email_client._http is not None else "degraded",
        }
        return HealthResponse(status="healthy" if mongo_ok else "degraded", version=__version__, services=services)

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(uploads_router, prefix="/api/v1")
    app.include_router(files_router, prefix="/api/v1")
    app.include_router(shares_router, prefix="/api/v1")
    return app

app = create_app()

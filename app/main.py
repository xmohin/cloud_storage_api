"""Gallery Vault API — application entry point."""

import importlib
from contextlib import asynccontextmanager
from typing import Any
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from app import __version__
from app.core.config import get_settings
from app.core.database import db, email_client
from app.core.logger import configure_logging, get_logger
from app.core.middleware import limiter, setup_exception_handlers, setup_middleware
from app.services.email_service import email_service
from app.services.telegram_service import telegram_service
from app.services.upload_service import upload_service


def safe_import_router(module_paths: list[str], router_attr_names: list[str] = None) -> APIRouter:
    """Helper to defensively load FastAPI APIRouter instances across naming conventions.
    
    Only skips a candidate module if the module file itself does NOT exist.
    If the module exists but fails due to internal errors, the actual exception is raised.
    """
    if router_attr_names is None:
        router_attr_names = ["router", "api_router"]

    attempted_paths = []
    for path in module_paths:
        try:
            mod = importlib.import_module(path)
            
            # 1. Check standard names ("router", "api_router")
            for attr in router_attr_names:
                if hasattr(mod, attr):
                    return getattr(mod, attr)
                    
            # 2. Check path-derived name (e.g. "shares_router", "security_router")
            module_name = path.split(".")[-1]
            specific_attr = f"{module_name}_router"
            if hasattr(mod, specific_attr):
                return getattr(mod, specific_attr)

        except ModuleNotFoundError as err:
            # যদি সরাসরি ওই মডিউলটি না পাওয়া যায়, তবে পরবর্তী প্যাথ ট্রাই করবে
            if err.name == path:
                attempted_paths.append(path)
                continue
            # কিন্তু মডিউলটি আছে অথচ তার ভিতরের কোনো ইমপোর্ট মিসিং, তখন প্রকৃত এরর রেইজ করবে
            raise err
        except Exception as err:
            raise err

    raise ImportError(f"Could not load router from {module_paths}. Attempted modules: {attempted_paths}")


# ── Defensive Router Imports ──
admin_router = safe_import_router(["app.api.v1.admin"])
auth_router = safe_import_router(["app.api.v1.auth"])
backup_router = safe_import_router(["app.api.v1.backup"])
files_router = safe_import_router(["app.api.v1.files", "app.api.v1.file"])
folders_router = safe_import_router(["app.api.v1.folders", "app.api.v1.folder"])
notifications_router = safe_import_router(["app.api.v1.notifications", "app.api.v1.notification"])
search_router = safe_import_router(["app.api.v1.search"])
security_router = safe_import_router(["app.api.v1.security"])
share_router = safe_import_router(["app.api.v1.shares", "app.api.v1.share"])
storage_router = safe_import_router(["app.api.v1.storage"])
system_router = safe_import_router(["app.api.v1.system"])
uploads_router = safe_import_router(["app.api.v1.uploads", "app.api.v1.upload"])
user_router = safe_import_router(["app.api.v1.users", "app.api.v1.user"])


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
        if hasattr(email_client, "connect"):
            await email_client.connect()
        if hasattr(email_service, "start"):
            await email_service.start()
        logger.info("All background services initialized successfully.")
    except Exception as e:
        logger.critical(f"Failed to start services during startup: {e}")
        raise e

    yield

    # ── Shutdown Phase ──
    logger.info("Shutting down Gallery Vault API...")

    services_to_stop = []
    if hasattr(email_service, "stop"):
        services_to_stop.append(("Email Service", email_service.stop()))
    if hasattr(email_client, "disconnect"):
        services_to_stop.append(("Email Client", email_client.disconnect()))
    if hasattr(upload_service, "stop"):
        services_to_stop.append(("Upload Service", upload_service.stop()))
    if hasattr(telegram_service, "stop"):
        services_to_stop.append(("Telegram Service", telegram_service.stop()))
    if hasattr(db, "disconnect"):
        services_to_stop.append(("Database Connection", db.disconnect()))

    for service_name, stop_coro in services_to_stop:
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
    app.include_router(uploads_router, prefix="/api/v1")
    app.include_router(notifications_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(security_router, prefix="/api/v1")

    return app


app = create_app()

# ── Health Check & Root Endpoints (Added to fix 404 errors) ──

@app.get("/", tags=["Health"])
async def root():
    return {"message": f"Welcome to {settings.APP_NAME} API", "status": "active"}

@app.get("/health", tags=["Health"])
@app.get("/api/health", tags=["Health"])
async def health_check():
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "service": f"{settings.APP_NAME} API is running smoothly"}
    )

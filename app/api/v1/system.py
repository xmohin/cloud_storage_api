"""System monitoring and health check endpoints."""

from fastapi import APIRouter
from app.core.database import db, email_client
from app.services.telegram_service import telegram_service
from app.models.schemas import ApiResponse, HealthResponse

try:
    from app import __version__
except ImportError:
    __version__ = "1.0.0"

router = APIRouter(tags=["System"])


@router.get("/health", response_model=HealthResponse)
async def health():
    # Surfaces whether each external dependency is actually usable — in
    # particular `email: "not_configured"` is the #1 reason OTPs silently
    # never arrive (missing/invalid BREVO_API_KEY), and previously nothing
    # exposed this short of grepping server logs.
    services = {
        "database": "connected" if db._database is not None else "disconnected",
        "telegram": "connected" if telegram_service.is_connected() else "disconnected",
        "email": "configured" if email_client.is_configured else "not_configured",
    }
    overall = "healthy" if all(v in ("connected", "configured") for v in services.values()) else "degraded"
    return HealthResponse(status=overall, version=__version__, services=services)


@router.get("/ready")
async def readiness():
    return ApiResponse(data={"status": "ready"})


@router.get("/metrics")
async def metrics():
    return ApiResponse(data={"uptime": "100%", "requests": 0})


@router.get("/version")
async def version():
    return ApiResponse(data={"version": __version__})

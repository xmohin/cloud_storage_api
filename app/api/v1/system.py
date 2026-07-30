"""System monitoring and health check endpoints."""

from fastapi import APIRouter
from app.models.schemas import ApiResponse, HealthResponse

try:
    from app import __version__
except ImportError:
    __version__ = "1.0.0"

router = APIRouter(tags=["System"])


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="healthy", version=__version__)


@router.get("/ready")
async def readiness():
    return ApiResponse(data={"status": "ready"})


@router.get("/metrics")
async def metrics():
    return ApiResponse(data={"uptime": "100%", "requests": 0})


@router.get("/version")
async def version():
    return ApiResponse(data={"version": __version__})

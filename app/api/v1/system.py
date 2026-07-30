# app/api/v1/system.py
from fastapi import APIRouter
from app.models.schemas import ApiResponse, HealthResponse
from app import __version__

router = APIRouter(tags=["System"])

@router.get("/health")
async def health():
    return HealthResponse(status="healthy", version=__version__)

@router.get("/ready")
async def readiness():
    return {"status": "ready"}

@router.get("/metrics")
async def metrics():
    return {"uptime": "100%", "requests": 0}

@router.get("/version")
async def version():
    return {"version": __version__}

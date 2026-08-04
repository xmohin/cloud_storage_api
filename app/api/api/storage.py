"""Storage information and management endpoints."""

from fastapi import APIRouter
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.models.schemas import ApiResponse
from app.services.file_service import file_service

router = APIRouter(prefix="/storage", tags=["Storage"])


@router.get("/info")
async def storage_info(user: CurrentUserDep):
    return ApiResponse(
        data={
            "used": user.get("storage_used_bytes", 0),
            "quota": user.get("storage_quota_bytes", 0)
        }
    )


@router.get("/usage")
async def storage_usage(user: CurrentUserDep):
    return ApiResponse(data={"used": user.get("storage_used_bytes", 0)})


@router.get("/statistics")
async def storage_stats(user: CurrentUserDep, db: DatabaseDep):
    stats = await file_service.get_statistics(db, user["_id"])
    return ApiResponse(
        data={
            **stats,
            "used_bytes": stats["total_size_bytes"],  # kept for backward compatibility
        }
    )


@router.post("/cleanup")
async def cleanup_storage(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(message="Cleanup task initiated")


@router.get("/health")
async def storage_health():
    return ApiResponse(data={"status": "healthy"})

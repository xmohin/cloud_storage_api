"""Storage information and management endpoints."""

from fastapi import APIRouter
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.models.schemas import ApiResponse

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
    total_files = await db.files.count_documents({"owner_id": user["_id"], "is_folder": False, "deleted_at": None})
    total_folders = await db.files.count_documents({"owner_id": user["_id"], "is_folder": True, "deleted_at": None})
    return ApiResponse(
        data={
            "total_files": total_files,
            "total_folders": total_folders,
            "used_bytes": user.get("storage_used_bytes", 0)
        }
    )


@router.post("/cleanup")
async def cleanup_storage(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(message="Cleanup task initiated")


@router.get("/health")
async def storage_health():
    return ApiResponse(data={"status": "healthy"})

# app/api/v1/storage.py
from fastapi import APIRouter, Depends
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.services.file_service import file_service
from app.models.schemas import ApiResponse

router = APIRouter(prefix="/storage", tags=["Storage"])

@router.get("/info")
async def storage_info(user: CurrentUserDep):
    return ApiResponse(data={"used": user.get("storage_used_bytes", 0), "quota": user.get("storage_quota_bytes", 0)})

@router.get("/usage")
async def storage_usage(user: CurrentUserDep):
    return ApiResponse(data={"used": user.get("storage_used_bytes", 0)})

@router.get("/statistics")
async def storage_stats(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=await file_service.get_statistics(db, user["_id"]))

@router.post("/cleanup")
async def cleanup_storage(user: CurrentUserDep, db: DatabaseDep):
    # Trigger trash cleanup
    return ApiResponse(message="Cleanup started")

@router.get("/health")
async def storage_health(user: CurrentUserDep):
    return ApiResponse(data={"status": "healthy"})

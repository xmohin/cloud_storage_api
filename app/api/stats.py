from fastapi import APIRouter, Depends
from app.models.schemas import SystemStatsResponse
from app.api.dependencies import get_current_user
from app.core.database import get_database
from app.services.telegram_service import telegram_service
from motor.motor_asyncio import AsyncIOMotorDatabase

router = APIRouter(prefix="/stats", tags=["Statistics & Monitoring"])

@router.get("", response_model=SystemStatsResponse)
async def get_system_stats(
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    total_users = await db.users.count_documents({})
    total_files = await db.files.count_documents({})
    
    pipeline = [{"$group": {"_id": None, "total_bytes": {"$sum": "$size_bytes"}}}]
    res = await db.files.aggregate(pipeline).to_list(length=1)
    total_storage = res[0]["total_bytes"] if res else 0
    
    tg_status = "connected" if telegram_service.client and telegram_service.client.is_connected() else "disconnected"
    
    return SystemStatsResponse(
        total_users=total_users,
        total_files=total_files,
        total_storage_bytes=total_storage,
        telegram_status=tg_status,
        database_status="healthy"
    )

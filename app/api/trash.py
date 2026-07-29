from fastapi import APIRouter, Depends
from app.services.file_service import FileService
from app.api.dependencies import get_current_user
from app.core.database import get_database
from motor.motor_asyncio import AsyncIOMotorDatabase

router = APIRouter(prefix="/trash", tags=["Trash & Retention"])

@router.delete("/files/{file_id}")
async def soft_delete(
    file_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    service = FileService(db)
    return await service.soft_delete_file(file_id, current_user["id"])

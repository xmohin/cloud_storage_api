from fastapi import APIRouter, Depends, UploadFile, File, Form
from typing import Optional
from app.services.upload_service import UploadService
from app.api.dependencies import get_current_user
from app.core.database import get_database
from motor.motor_asyncio import AsyncIOMotorDatabase

router = APIRouter(prefix="/upload", tags=["Upload"])

@router.post("/direct")
async def upload_direct(
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    service = UploadService(db)
    return await service.handle_direct_upload(file, current_user["id"], folder_id)

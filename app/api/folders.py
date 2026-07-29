from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import FolderCreateRequest, FolderResponse
from app.api.dependencies import get_current_user
from app.core.database import get_database
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timezone
from bson import ObjectId

router = APIRouter(prefix="/folders", tags=["Folders"])

@router.post("", response_model=FolderResponse)
async def create_folder(
    req: FolderCreateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    folder_doc = {
        "name": req.name,
        "parent_id": req.parent_id,
        "owner_id": current_user["id"],
        "created_at": datetime.now(timezone.utc)
    }
    res = await db.folders.insert_one(folder_doc)
    return FolderResponse(
        id=str(res.inserted_id),
        name=req.name,
        parent_id=req.parent_id,
        owner_id=current_user["id"],
        created_at=folder_doc["created_at"]
    )

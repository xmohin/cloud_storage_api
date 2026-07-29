from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import ShareLinkCreateRequest, ShareLinkResponse
from app.api.dependencies import get_current_user
from app.core.database import get_database
from app.utils.hashing import generate_random_token
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timedelta, timezone

router = APIRouter(prefix="/shares", tags=["Share Links"])

@router.post("", response_model=ShareLinkResponse)
async def create_share_link(
    req: ShareLinkCreateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    code = generate_random_token(12)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=req.expires_in_hours) if req.expires_in_hours else None
    
    share_doc = {
        "share_code": code,
        "file_id": req.file_id,
        "owner_id": current_user["id"],
        "password": req.password,
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc)
    }
    await db.shares.insert_one(share_doc)
    return ShareLinkResponse(
        share_code=code,
        share_url=f"/shares/public/{code}",
        expires_at=expires_at,
        has_password=bool(req.password)
    )

from fastapi import APIRouter, Depends, HTTPException, status
from app.api.dependencies import get_current_user
from app.core.database import get_database
from motor.motor_asyncio import AsyncIOMotorDatabase

router = APIRouter(prefix="/admin", tags=["Admin Portal"])

@router.get("/users")
async def admin_list_users(
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required.")
    
    cursor = db.users.find({}, {"password_hash": 0, "otp_code": 0})
    users = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        users.append(doc)
    return users

# app/api/v1/notifications.py
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.models.schemas import ApiResponse, Notification

router = APIRouter(prefix="/notifications", tags=["Notifications"])

@router.get("")
async def list_notifications(user: CurrentUserDep, db: DatabaseDep):
    notifs = await db.notifications.find({"user_id": user["_id"]}).sort("created_at", -1).to_list(50)
    return ApiResponse(data=notifs)

@router.put("/read")
async def read_notification(notif_id: str, user: CurrentUserDep, db: DatabaseDep):
    await db.notifications.update_one({"_id": notif_id, "user_id": user["_id"]}, {"$set": {"is_read": True}})
    return ApiResponse(message="Marked as read")

@router.put("/read-all")
async def read_all_notifications(user: CurrentUserDep, db: DatabaseDep):
    await db.notifications.update_many({"user_id": user["_id"], "is_read": False}, {"$set": {"is_read": True}})
    return ApiResponse(message="All marked as read")

@router.delete("/delete")
async def delete_notification(notif_id: str, user: CurrentUserDep, db: DatabaseDep):
    await db.notifications.delete_one({"_id": notif_id, "user_id": user["_id"]})
    return ApiResponse(message="Deleted")

@router.delete("/clear")
async def clear_notifications(user: CurrentUserDep, db: DatabaseDep):
    await db.notifications.delete_many({"user_id": user["_id"]})
    return ApiResponse(message="Cleared")

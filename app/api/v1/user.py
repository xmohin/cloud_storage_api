"""User profile and settings endpoints."""

import uuid
from fastapi import APIRouter, Depends, UploadFile, File, status, HTTPException
from fastapi.responses import StreamingResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep, TelegramServiceDep
from app.services.telegram_service import telegram_service
from app.core.security import security
from app.models.schemas import ApiResponse, UserProfile, UserUpdate, PasswordChange

router = APIRouter(prefix="/user", tags=["User Management"])

@router.get("/profile")
async def get_profile(user: CurrentUserDep):
    return ApiResponse(data=UserProfile(**user))

@router.put("/profile")
async def update_profile(payload: UserUpdate, user: CurrentUserDep, db: DatabaseDep):
    update_data = payload.dict(exclude_unset=True)
    if update_data:
        update_data["updated_at"] = datetime.now(timezone.utc)
        await db.users.update_one({"_id": user["_id"]}, {"$set": update_data})
        user.update(update_data)
    return ApiResponse(data=UserProfile(**user), message="Profile updated")

@router.put("/password")
async def change_password(payload: PasswordChange, user: CurrentUserDep, db: DatabaseDep):
    if not security.verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Incorrect current password")
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": security.hash_password(payload.new_password)}})
    return ApiResponse(message="Password changed successfully")

@router.put("/avatar")
async def upload_avatar(user: CurrentUserDep, db: DatabaseDep, tg: TelegramServiceDep, file: UploadFile = File(...)):
    # Save to temp, upload to telegram, update user doc
    import os, aiofiles
    temp_path = f"/tmp/{uuid.uuid4()}_{file.filename}"
    async with aiofiles.open(temp_path, 'wb') as out_file:
        while chunk := await file.read(1024 * 1024): await out_file.write(chunk)
    
    task_id = await tg.enqueue_upload(temp_path)
    # Wait for task to complete (simplified for brevity, in prod use background task and websockets)
    import asyncio, time
    task = tg.get_task_status(task_id)
    while task and task.status != "completed":
        await asyncio.sleep(1)
        task = tg.get_task_status(task_id)
    
    if task and task.result:
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"avatar_message_id": task.result}})
        return ApiResponse(message="Avatar updated", data={"avatar_message_id": task.result})
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Avatar upload failed")

@router.delete("/avatar")
async def delete_avatar(user: CurrentUserDep, db: DatabaseDep):
    await db.users.update_one({"_id": user["_id"]}, {"$unset": {"avatar_message_id": ""}})
    return ApiResponse(message="Avatar deleted")

@router.get("/storage")
async def get_storage_info(user: CurrentUserDep):
    return ApiResponse(data={
        "used": user.get("storage_used_bytes", 0),
        "quota": user.get("storage_quota_bytes", 5 * 1024 * 1024 * 1024)
    })

@router.get("/activity")
async def get_user_activity(user: CurrentUserDep, db: DatabaseDep):
    # Fetch recent files or logs
    recent_files = await db.files.find({"owner_id": user["_id"], "deleted_at": None}).sort("created_at", -1).limit(10).to_list(10)
    return ApiResponse(data={"recent_uploads": recent_files})

@router.delete("/account")
async def delete_account(user: CurrentUserDep, db: DatabaseDep):
    # Soft delete account, queue hard delete
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"is_active": False, "deleted_at": datetime.now(timezone.utc)}})
    await db.sessions.update_many({"user_id": user["_id"]}, {"$set": {"is_active": False}})
    # In production, trigger a background task to delete all user files from Telegram
    return ApiResponse(message="Account scheduled for deletion")

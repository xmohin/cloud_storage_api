"""User profile and settings endpoints."""

import os
import uuid
import asyncio
import aiofiles
from datetime import datetime, timezone
from fastapi import APIRouter, UploadFile, File, status, HTTPException
from app.api.dependencies import CurrentUserDep, DatabaseDep, TelegramServiceDep
from app.core.security import security
from app.models.schemas import ApiResponse, UserProfile, UserUpdate, PasswordChange

router = APIRouter(prefix="/user", tags=["User Management"])


@router.get("/profile")
async def get_profile(user: CurrentUserDep):
    return ApiResponse(data=UserProfile(**user))


@router.put("/profile")
async def update_profile(payload: UserUpdate, user: CurrentUserDep, db: DatabaseDep):
    update_data = payload.model_dump(exclude_unset=True)
    if update_data:
        update_data["updated_at"] = datetime.now(timezone.utc)
        await db.users.update_one({"_id": user["_id"]}, {"$set": update_data})
        user.update(update_data)
    return ApiResponse(data=UserProfile(**user), message="Profile updated")


@router.put("/password")
async def change_password(payload: PasswordChange, user: CurrentUserDep, db: DatabaseDep):
    if not security.verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect current password")
    
    hashed_pwd = security.hash_password(payload.new_password)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": hashed_pwd}})
    return ApiResponse(message="Password changed successfully")


@router.put("/avatar")
async def upload_avatar(
    user: CurrentUserDep, 
    db: DatabaseDep, 
    tg: TelegramServiceDep, 
    file: UploadFile = File(...)
):
    temp_path = f"/tmp/{uuid.uuid4()}_{file.filename}"
    try:
        async with aiofiles.open(temp_path, "wb") as out_file:
            while chunk := await file.read(1024 * 1024):
                await out_file.write(chunk)

        task_id = await tg.enqueue_upload(temp_path)
        
        task = tg.get_task_status(task_id)
        while task and task.status not in ("completed", "failed"):
            await asyncio.sleep(1)
            task = tg.get_task_status(task_id)

        if task and task.status == "completed" and task.result:
            await db.users.update_one({"_id": user["_id"]}, {"$set": {"avatar_message_id": task.result}})
            return ApiResponse(message="Avatar updated", data={"avatar_message_id": task.result})
        
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Avatar upload failed")
    finally:
        # Prevent disk leak by cleaning up temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)


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
    recent_files = await db.files.find(
        {"owner_id": user["_id"], "deleted_at": None}
    ).sort("created_at", -1).limit(10).to_list(length=10)
    return ApiResponse(data={"recent_uploads": recent_files})


@router.delete("/account")
async def delete_account(user: CurrentUserDep, db: DatabaseDep):
    now = datetime.now(timezone.utc)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"is_active": False, "deleted_at": now}})
    await db.sessions.update_many({"user_id": user["_id"]}, {"$set": {"is_active": False}})
    return ApiResponse(message="Account scheduled for deletion")

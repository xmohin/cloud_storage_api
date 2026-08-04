"""User profile and settings endpoints."""

import os
import uuid
import asyncio
import aiofiles
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, UploadFile, File, status, HTTPException
from pymongo.errors import DuplicateKeyError
from app.api.dependencies import CurrentUserDep, DatabaseDep, EmailServiceDep, TelegramServiceDep
from app.core.security import security
from app.models.schemas import ApiResponse, UserProfile, UserUpdate, PasswordChange
from app.utils.validators import validate_filename, validate_password_strength

router = APIRouter(prefix="/user", tags=["User Management"])


@router.get("/profile")
async def get_profile(user: CurrentUserDep):
    return ApiResponse(data=UserProfile(**user))


@router.put("/profile")
async def update_profile(payload: UserUpdate, user: CurrentUserDep, db: DatabaseDep, email_service: EmailServiceDep):
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return ApiResponse(data=UserProfile(**user), message="Profile updated")

    email_changed = "email" in update_data and update_data["email"] != user.get("email")
    new_otp: str | None = None
    if email_changed:
        # The new address hasn't been proven to belong to this user — force
        # re-verification instead of silently trusting it, and stop treating
        # the account as verified in the meantime (issue #12).
        new_otp = security.generate_otp()
        update_data["is_verified"] = False
        update_data["otp_hash"] = security.hash_otp(new_otp)
        update_data["otp_purpose"] = "verification"
        update_data["otp_expires_at"] = datetime.now(timezone.utc) + timedelta(minutes=10)

    update_data["updated_at"] = datetime.now(timezone.utc)

    try:
        await db.users.update_one({"_id": user["_id"]}, {"$set": update_data})
    except DuplicateKeyError:
        # Unique index on email/username — this used to bubble up as an
        # unhandled 500 instead of a normal client-facing conflict (#12).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username or email is already taken",
        )

    user.update(update_data)

    if email_changed and new_otp:
        await email_service.send_otp_email(user["email"], new_otp, "Email Verification")

    message = "Profile updated. Please verify your new email address." if email_changed else "Profile updated"
    return ApiResponse(data=UserProfile(**user), message=message)


@router.put("/password")
async def change_password(payload: PasswordChange, user: CurrentUserDep, db: DatabaseDep):
    if not security.verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect current password")
    
    validate_password_strength(payload.new_password)
    hashed_pwd = security.hash_password(payload.new_password)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": hashed_pwd}})

    # reset-password already revokes every active session; plain
    # change-password didn't, so a stolen refresh token kept working right
    # through a password change (#13). The device that just changed the
    # password keeps its current access token (short-lived, ≤ JWT_ACCESS_
    # TOKEN_EXPIRE_MINUTES) but will need to log in again once it expires,
    # same as every other device.
    await db.sessions.update_many(
        {"user_id": user["_id"], "is_active": True}, {"$set": {"is_active": False}}
    )
    return ApiResponse(message="Password changed successfully. You've been logged out of all other devices.")


AVATAR_UPLOAD_TIMEOUT_SECONDS = 60


@router.put("/avatar")
async def upload_avatar(
    user: CurrentUserDep, 
    db: DatabaseDep, 
    tg: TelegramServiceDep, 
    file: UploadFile = File(...)
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Avatar must be an image file")
    if not tg.is_connected():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Telegram storage is currently unavailable")

    safe_filename = validate_filename(file.filename)
    temp_path = os.path.join("/tmp", f"{uuid.uuid4()}_{safe_filename}")
    try:
        async with aiofiles.open(temp_path, "wb") as out_file:
            while chunk := await file.read(1024 * 1024):
                await out_file.write(chunk)

        task_id = await tg.enqueue_upload(temp_path)
        
        task = tg.get_task_status(task_id)
        waited_seconds = 0
        while task and task.status not in ("completed", "failed"):
            if waited_seconds >= AVATAR_UPLOAD_TIMEOUT_SECONDS:
                raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Avatar upload timed out")
            await asyncio.sleep(1)
            waited_seconds += 1
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

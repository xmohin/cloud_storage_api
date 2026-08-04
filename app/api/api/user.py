"""User profile and settings endpoints."""

import os
import uuid
import asyncio
import aiofiles
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, UploadFile, File, status, HTTPException
from pymongo.errors import DuplicateKeyError
from app.api.dependencies import CurrentUserDep, DatabaseDep, EmailServiceDep, TelegramServiceDep
from app.core.config import get_settings
from app.core.security import security
from app.models.schemas import ApiResponse, UserProfile, UserUpdate, PasswordChange, FileMetadata
from app.utils.validators import validate_filename, validate_password_strength

router = APIRouter(prefix="/user", tags=["User Management"])
settings = get_settings()


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
    file: UploadFile = File(...),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Avatar must be an image file")
    if not tg.is_connected():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram storage is currently unavailable",
        )

    safe_filename = validate_filename(file.filename)
    # Store under TEMP_STORAGE_PATH so telegram_service._cleanup_upload_path
    # can safely remove the per-upload directory after send (and so we never
    # race-delete a file the worker is still reading from /tmp).
    upload_id = str(uuid.uuid4())
    upload_dir = os.path.join(settings.TEMP_STORAGE_PATH, f"avatar_{upload_id}")
    os.makedirs(upload_dir, exist_ok=True)
    temp_path = os.path.join(upload_dir, safe_filename)

    try:
        async with aiofiles.open(temp_path, "wb") as out_file:
            while chunk := await file.read(1024 * 1024):
                await out_file.write(chunk)

        task_id = await tg.enqueue_upload(temp_path)

        task = await tg.get_task_status_async(task_id)
        waited_seconds = 0
        while task and task.status not in ("completed", "failed"):
            if waited_seconds >= AVATAR_UPLOAD_TIMEOUT_SECONDS:
                # Leave the file for the worker / stale-dir cleanup loop —
                # deleting it here would race the still-running Telegram send.
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Avatar upload timed out",
                )
            await asyncio.sleep(1)
            waited_seconds += 1
            task = await tg.get_task_status_async(task_id)

        if task and task.status == "completed" and task.result:
            # Best-effort: drop the previous avatar message from the channel
            old_msg = user.get("avatar_message_id")
            if old_msg and tg.client:
                try:
                    await tg.client.delete_messages(
                        entity=settings.TELEGRAM_STORAGE_CHANNEL_ID,
                        message_ids=[old_msg],
                    )
                except Exception:
                    pass
            await db.users.update_one(
                {"_id": user["_id"]}, {"$set": {"avatar_message_id": task.result}}
            )
            return ApiResponse(message="Avatar updated", data={"avatar_message_id": task.result})

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Avatar upload failed",
        )
    except HTTPException:
        raise
    except Exception:
        # On unexpected failure before the worker owns the path, clean up.
        import shutil
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise


@router.delete("/avatar")
async def delete_avatar(user: CurrentUserDep, db: DatabaseDep, tg: TelegramServiceDep):
    old_msg = user.get("avatar_message_id")
    await db.users.update_one({"_id": user["_id"]}, {"$unset": {"avatar_message_id": ""}})
    if old_msg and tg.is_connected() and tg.client:
        try:
            await tg.client.delete_messages(
                entity=settings.TELEGRAM_STORAGE_CHANNEL_ID,
                message_ids=[old_msg],
            )
        except Exception:
            pass
    return ApiResponse(message="Avatar deleted")


@router.get("/storage")
async def get_storage_info(user: CurrentUserDep):
    return ApiResponse(data={
        "used": user.get("storage_used_bytes", 0),
        "quota": user.get("storage_quota_bytes", 5 * 1024 * 1024 * 1024),
    })


@router.get("/activity")
async def get_user_activity(user: CurrentUserDep, db: DatabaseDep):
    recent_files = await db.files.find(
        {"owner_id": user["_id"], "deleted_at": None}
    ).sort("created_at", -1).limit(10).to_list(length=10)
    # Go through FileMetadata so internal fields (_id vs id, telegram_message_id,
    # file_hash, …) are not leaked as raw Mongo documents.
    return ApiResponse(data={
        "recent_uploads": [FileMetadata(**f) for f in recent_files],
    })


@router.delete("/account")
async def delete_account(user: CurrentUserDep, db: DatabaseDep):
    """Deactivate the account, revoke sessions, soft-delete all owned files,
    and deactivate every share link. Telegram media is left for the trash
    purge loop / admin cleanup so a soft-deleted account can still be
    recovered by an operator within the retention window.
    """
    from app.services.file_service import TRASH_EXPIRY_DAYS

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=TRASH_EXPIRY_DAYS)
    user_id = user["_id"]

    await db.users.update_one(
        {"_id": user_id},
        {"$set": {"is_active": False, "deleted_at": now}},
    )
    await db.sessions.update_many(
        {"user_id": user_id}, {"$set": {"is_active": False}}
    )
    await db.shares.update_many(
        {"owner_id": user_id, "is_active": True},
        {"$set": {"is_active": False}},
    )
    # Soft-delete every non-already-trashed file/folder so storage_used can
    # be zeroed and the purge loop eventually frees Telegram objects.
    await db.files.update_many(
        {"owner_id": user_id, "deleted_at": None},
        {"$set": {"deleted_at": now, "deleted_expires_at": expires, "updated_at": now}},
    )
    await db.users.update_one(
        {"_id": user_id}, {"$set": {"storage_used_bytes": 0}}
    )
    return ApiResponse(message="Account scheduled for deletion")

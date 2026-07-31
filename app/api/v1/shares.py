"""Share link management endpoints."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.core.security import security
from app.utils.validators import validate_bcrypt_length
from app.models.schemas import (
    ApiResponse,
    ShareCreateRequest,
    ShareAccessRequest,
    ShareResponse,
    FileMetadata,
)

router = APIRouter(prefix="/shares", tags=["Share Links"])


def _ensure_tz_aware(dt: datetime | None) -> datetime | None:
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_share(payload: ShareCreateRequest, user: CurrentUserDep, db: DatabaseDep):
    file_doc = await db.files.find_one({"_id": payload.file_id, "owner_id": user["_id"], "deleted_at": None})
    if not file_doc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": "File or folder not found"}
        )
    if file_doc.get("is_folder"):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "message": "Cannot share folders"}
        )

    share_token = security.generate_share_code()
    if payload.password:
        validate_bcrypt_length(payload.password)
    password_hash = security.hash_password(payload.password) if payload.password else None
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=payload.expires_in_hours)
        if payload.expires_in_hours else None
    )

    share_doc = {
        "_id": str(uuid4()),
        "share_token": share_token,
        "file_id": payload.file_id,
        "owner_id": user["_id"],
        "password_hash": password_hash,
        "has_password": password_hash is not None,
        "expires_at": expires_at,
        "max_downloads": payload.max_downloads,
        "download_count": 0,
        "created_at": datetime.now(timezone.utc),
        "is_active": True
    }

    await db.shares.insert_one(share_doc)
    return ApiResponse(data=ShareResponse(**share_doc))


@router.post("/{code}/access")
async def access_share(code: str, payload: ShareAccessRequest, db: DatabaseDep):
    share = await db.shares.find_one({"share_token": code, "is_active": True})
    if not share:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": "Share link not found or expired"}
        )

    # Expiry Check
    expires_at = _ensure_tz_aware(share.get("expires_at"))
    if expires_at and datetime.now(timezone.utc) > expires_at:
        await db.shares.update_one({"_id": share["_id"]}, {"$set": {"is_active": False}})
        return JSONResponse(
            status_code=status.HTTP_410_GONE,
            content={"success": False, "message": "Share link has expired"}
        )

    # Max Downloads Limit Check
    if share.get("max_downloads") and share["download_count"] >= share["max_downloads"]:
        return JSONResponse(
            status_code=status.HTTP_410_GONE,
            content={"success": False, "message": "Download limit reached"}
        )

    # Password Check
    if share.get("password_hash"):
        if not payload.password or not security.verify_password(payload.password, share["password_hash"]):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"success": False, "message": "Password required or incorrect"}
            )

    file_doc = await db.files.find_one({"_id": share["file_id"], "deleted_at": None})
    if not file_doc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": "Shared file no longer exists"}
        )

    # Increment download/access count
    await db.shares.update_one({"_id": share["_id"]}, {"$inc": {"download_count": 1}})

    return ApiResponse(data=FileMetadata(**file_doc))


@router.delete("/{share_id}")
async def revoke_share(share_id: str, user: CurrentUserDep, db: DatabaseDep):
    res = await db.shares.update_one(
        {"_id": share_id, "owner_id": user["_id"]},
        {"$set": {"is_active": False}}
    )
    if res.modified_count == 0:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": "Share link not found"}
        )
    return ApiResponse(message="Share link revoked successfully")

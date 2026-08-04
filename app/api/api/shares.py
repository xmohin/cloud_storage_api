"""Share link management endpoints."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from app.api.dependencies import CurrentUserDep, DatabaseDep, EmailServiceDep, StorageEngineDep
from app.core.config import get_settings
from app.core.security import security
from app.utils.validators import validate_bcrypt_length
from app.utils.helpers import content_disposition_attachment, parse_range_header
from app.models.schemas import (
    ApiResponse,
    ShareCreateRequest,
    ShareAccessRequest,
    ShareEmailRequest,
    ShareResponse,
    SharedFileInfo,
)

router = APIRouter(prefix="/shares", tags=["Share Links"])
settings = get_settings()


def _ensure_tz_aware(dt: datetime | None) -> datetime | None:
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _claim_download_slot(db: AsyncIOMotorDatabase, share_id: str, max_downloads: int | None) -> dict | None:
    """Atomically re-checks max_downloads and increments download_count in a
    single find_one_and_update. Returns the updated share doc, or None if
    the limit has already been reached (or the share was deactivated
    concurrently, e.g. by the expiry check racing on another request).

    Previously the check (`download_count >= max_downloads`) and the
    increment (`$inc`) were two separate steps, so two concurrent requests
    could both pass the check before either one's increment landed,
    exceeding max_downloads (issue #16).
    """
    query: dict = {"_id": share_id, "is_active": True}
    if max_downloads is not None:
        query["download_count"] = {"$lt": max_downloads}
    return await db.shares.find_one_and_update(
        query,
        {"$inc": {"download_count": 1}},
        return_document=ReturnDocument.AFTER,
    )


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
    if file_doc.get("status") != "completed" or not file_doc.get("telegram_message_id"):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"success": False, "message": "Only completed files can be shared"},
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

    # Max Downloads Limit Check — informational only. /access previews the
    # link and never itself consumes a download slot (see CRITICAL #3 note
    # below); this early check just avoids doing a password check against a
    # link that's already exhausted.
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

    # CRITICAL #3 fix: this used to also call _claim_download_slot() here,
    # so every preview-then-download flow spent 2 of the link's slots for
    # 1 real download. download_count is now only ever incremented in
    # download_shared_file below — the endpoint that actually streams
    # bytes — so max_downloads counts real downloads, not previews.

    # Only the fields a recipient actually needs to see — the full
    # FileMetadata (owner_id, telegram_message_id, file_hash, etc.) was
    # previously returned to anyone holding the link, verified or not (#16).
    return ApiResponse(data=SharedFileInfo(**file_doc))


@router.get("/{code}/download")
async def download_shared_file(
    code: str,
    db: DatabaseDep,
    storage: StorageEngineDep,
    password: str | None = None,
    range: str | None = Header(None),
    x_share_password: str | None = Header(None, alias="X-Share-Password"),
):
    share = await db.shares.find_one({"share_token": code, "is_active": True})
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found or expired")

    expires_at = _ensure_tz_aware(share.get("expires_at"))
    if expires_at and datetime.now(timezone.utc) > expires_at:
        await db.shares.update_one({"_id": share["_id"]}, {"$set": {"is_active": False}})
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Share link has expired")

    if share.get("max_downloads") and share["download_count"] >= share["max_downloads"]:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Download limit reached")

    # Prefer X-Share-Password header so the secret is not written into access logs
    # (query-string ?password= is still accepted for simple clients).
    effective_password = x_share_password or password
    if share.get("password_hash"):
        if not effective_password or not security.verify_password(effective_password, share["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Password required or incorrect")

    file_doc = await db.files.find_one({"_id": share["file_id"], "deleted_at": None})
    if not file_doc or file_doc.get("is_folder"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared file no longer exists")
    if file_doc.get("status") != "completed" or not file_doc.get("telegram_message_id"):
        raise HTTPException(status_code=409, detail="File is not ready to download")

    file_size = file_doc["size_bytes"]
    parsed_range = parse_range_header(range, file_size)
    if parsed_range is None:
        raise HTTPException(status_code=416, detail="Requested range not satisfiable", headers={"Content-Range": f"bytes */{file_size}"})
    start_offset, end_offset = parsed_range
    content_length = (end_offset - start_offset) + 1

    # The ONLY place download_count is incremented (CRITICAL #3) — this is
    # the endpoint that actually serves the bytes. Atomic check-and-increment
    # together, closing the race a plain read-then-write would leave (#16).
    if not await _claim_download_slot(db, share["_id"], share.get("max_downloads")):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Download limit reached")

    async def chunk_generator():
        async for chunk in storage.download_file_stream(
            file_doc["telegram_message_id"],
            start=start_offset,
            end=end_offset
        ):
            yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": file_doc.get("mime_type", "application/octet-stream"),
        "Content-Disposition": content_disposition_attachment(file_doc.get("original_name", "download")),
    }
    # Content-Range only on actual partial responses (RFC 7233) — same fix as
    # /files/{id}/stream (#25). Sending it on a plain 200 confuses clients.
    if range:
        headers["Content-Range"] = f"bytes {start_offset}-{end_offset}/{file_size}"
    status_code = status.HTTP_206_PARTIAL_CONTENT if range else status.HTTP_200_OK
    return StreamingResponse(chunk_generator(), status_code=status_code, headers=headers)


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


@router.post("/{share_id}/email")
async def email_share(
    share_id: str,
    payload: ShareEmailRequest,
    user: CurrentUserDep,
    db: DatabaseDep,
    email_service: EmailServiceDep,
):
    """Emails an existing share link to a recipient.

    CRITICAL #4: email_service.send_share_email() was fully implemented
    (including the MEDIUM #17 XSS-escaping fix) but had no caller anywhere
    in the API — there was no way to actually email a share link. This is
    the first endpoint that calls it.
    """
    share = await db.shares.find_one({"_id": share_id, "owner_id": user["_id"], "is_active": True})
    if not share:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": "Share link not found"}
        )

    file_doc = await db.files.find_one({"_id": share["file_id"]})
    filename = file_doc["original_name"] if file_doc else "a file"
    share_url = f"{settings.APP_BASE_URL.rstrip('/')}/shared/{share['share_token']}"

    # The plaintext password (if any) was never stored — only its bcrypt
    # hash — so it genuinely can't be included here even if we wanted to;
    # the owner needs to share it with the recipient separately.
    await email_service.send_share_email(
        payload.recipient_email,
        user.get("username") or user.get("email", "Someone"),
        filename,
        share_url,
        password=None,
    )
    return ApiResponse(message="Share emailed successfully")

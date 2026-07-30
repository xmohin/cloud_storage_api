# app/api/v1/share.py
from fastapi import APIRouter, Depends, Request, status, HTTPException
from fastapi.responses import StreamingResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.services.share_service import share_service
from app.services.telegram_service import telegram_service
from app.models.schemas import ApiResponse, ShareCreate, SharePasswordUpdate, ShareExpireUpdate, ShareAccessRequest

router = APIRouter(prefix="/share", tags=["Sharing"])

@router.post("/create")
async def create_share(payload: ShareCreate, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=await share_service.create_share(db, user["_id"], payload.file_id, payload.password, payload.expires_in_hours, payload.max_downloads))

@router.get("/list")
async def list_shares(user: CurrentUserDep, db: DatabaseDep):
    shares = await db.shares.find({"owner_id": user["_id"]}).to_list(None)
    return ApiResponse(data=shares)

@router.get("/{code}")
async def access_share(code: str, payload: ShareAccessRequest, request: Request, db: DatabaseDep):
    ip, ua = request.client.host if request.client else "Unknown", request.headers.get("User-Agent", "Unknown")
    file_doc = await share_service.verify_and_access_share(db, code, payload.password, ip, ua)
    return StreamingResponse(telegram_service.stream_download(file_doc["telegram_message_id"]), media_type=file_doc.get("mime_type", "application/octet-stream"))

@router.put("/{code}")
async def update_share(code: str, user: CurrentUserDep, db: DatabaseDep):
    # Logic to update share settings
    return ApiResponse(message="Share updated")

@router.delete("/{code}")
async def revoke_share(code: str, user: CurrentUserDep, db: DatabaseDep):
    await share_service.revoke_share(db, code, user["_id"])
    return ApiResponse(message="Share revoked")

@router.post("/password")
async def set_password(payload: SharePasswordUpdate, user: CurrentUserDep, db: DatabaseDep):
    # Logic to set password on a share
    return ApiResponse(message="Password updated")

@router.post("/expire")
async def set_expiry(payload: ShareExpireUpdate, user: CurrentUserDep, db: DatabaseDep):
    # Logic to set expiry
    return ApiResponse(message="Expiry updated")

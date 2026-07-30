"""Sharing API endpoints."""

from fastapi import APIRouter, Request, status, HTTPException
from fastapi.responses import StreamingResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.services.share_service import share_service
from app.services.telegram_service import telegram_service
from app.core.config import get_settings
from app.models.schemas import ApiResponse, ShareCreateRequest, ShareAccessRequest, ShareResponse

settings = get_settings()
router = APIRouter(prefix="/shares", tags=["File Sharing"])

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_share_link(payload: ShareCreateRequest, user: CurrentUserDep, db: DatabaseDep):
    share_doc = await share_service.create_share(db, user["_id"], payload.file_id, payload.password, payload.expires_in_hours, payload.max_downloads)
    share_doc["share_url"] = f"{settings.APP_BASE_URL}/api/v1/shares/access/{share_doc['share_token']}"
    return ApiResponse(data=ShareResponse(**share_doc))

@router.post("/access/{share_token}")
async def access_shared_file(share_token: str, payload: ShareAccessRequest, request: Request, db: DatabaseDep):
    ip, ua = request.client.host if request.client else "Unknown", request.headers.get("User-Agent", "Unknown")
    file_doc = await share_service.verify_and_access_share(db, share_token, payload.password, ip, ua)
    if not file_doc.get("telegram_message_id"): raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File content not available")
    return StreamingResponse(telegram_service.stream_download(file_doc["telegram_message_id"]), media_type=file_doc.get("mime_type", "application/octet-stream"))

@router.delete("/{share_token}")
async def revoke_share_link(share_token: str, user: CurrentUserDep, db: DatabaseDep):
    await share_service.revoke_share(db, share_token, user["_id"])
    return ApiResponse(message="Share link revoked")

@router.get("/{share_token}/analytics")
async def get_share_analytics(share_token: str, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=await share_service.get_analytics(db, share_token, user["_id"]))

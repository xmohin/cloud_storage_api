"""Share link management endpoints."""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.services.share_service import share_service
from app.models.schemas import (
    ApiResponse,
    ShareCreateRequest,
    ShareAccessRequest,
    ShareResponse,
    FileMetadata,
)

router = APIRouter(prefix="/shares", tags=["Share Links"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_share(payload: ShareCreateRequest, user: CurrentUserDep, db: DatabaseDep):
    share_doc = await share_service.create_share(
        db,
        owner_id=user["_id"],
        file_id=payload.file_id,
        password=payload.password,
        expires_in_hours=payload.expires_in_hours,
        max_downloads=payload.max_downloads,
    )
    return ApiResponse(data=ShareResponse(**{"_id": share_doc["share_token"], **share_doc}))


@router.post("/{code}/access")
async def access_share(code: str, payload: ShareAccessRequest, request: Request, db: DatabaseDep):
    file_doc = await share_service.verify_and_access_share(
        db,
        share_token=code,
        provided_password=payload.password,
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("User-Agent", "unknown"),
    )
    return ApiResponse(data=FileMetadata(**file_doc))


@router.delete("/{share_id}")
async def revoke_share(share_id: str, user: CurrentUserDep, db: DatabaseDep):
    res = await db.shares.update_one(
        {"_id": share_id, "owner_id": user["_id"]},
        {"$set": {"is_revoked": True}}
    )
    if res.modified_count == 0:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": "Share link not found"}
        )
    return ApiResponse(message="Share link revoked successfully")

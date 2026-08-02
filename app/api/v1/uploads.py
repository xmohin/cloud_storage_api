"""File upload endpoints."""

from typing import Optional
from fastapi import APIRouter, Form, UploadFile, File
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.services.upload_service import upload_service
from app.models.schemas import ApiResponse, UploadInitRequest, UploadInitResponse

router = APIRouter(prefix="/uploads", tags=["File Uploads"])


@router.post("/small")
async def upload_small_file(
    user: CurrentUserDep,
    db: DatabaseDep,
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
):
    result = await upload_service.handle_small_upload(db, file, user["_id"], folder_id)
    return ApiResponse(data=result)


@router.post("/init")
async def init_chunked_upload(payload: UploadInitRequest, user: CurrentUserDep, db: DatabaseDep):
    result = await upload_service.init_chunked_upload(
        db, payload.filename, payload.file_size, payload.mime_type, payload.total_chunks, user["_id"], payload.folder_id
    )
    return ApiResponse(data=UploadInitResponse(**result))


@router.put("/{upload_id}/chunks/{chunk_index}")
async def upload_chunk(upload_id: str, chunk_index: int, user: CurrentUserDep, db: DatabaseDep, file: UploadFile = File(...)):
    result = await upload_service.upload_chunk(db, upload_id, chunk_index, file, user["_id"])
    return ApiResponse(data=result)


@router.post("/{upload_id}/pause")
async def pause_upload(upload_id: str, user: CurrentUserDep, db: DatabaseDep):
    result = await upload_service.pause_upload(db, upload_id, user["_id"])
    return ApiResponse(data=result)


@router.post("/{upload_id}/resume")
async def resume_upload(upload_id: str, user: CurrentUserDep, db: DatabaseDep):
    result = await upload_service.resume_upload(db, upload_id, user["_id"])
    return ApiResponse(data=result)


@router.post("/{upload_id}/complete")
async def complete_upload(upload_id: str, user: CurrentUserDep, db: DatabaseDep):
    result = await upload_service.complete_chunked_upload(db, upload_id, user["_id"])
    return ApiResponse(data=result)


@router.get("/{upload_id}/status")
async def get_upload_status(upload_id: str, user: CurrentUserDep, db: DatabaseDep):
    result = await upload_service.get_upload_status(db, upload_id, user["_id"])
    return ApiResponse(data=result)

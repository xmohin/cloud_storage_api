"""File management and streaming endpoints."""

from typing import Optional
from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep, PaginationDep
from app.services.file_service import file_service
from app.services.telegram_service import telegram_service
from app.models.schemas import (
    ApiResponse,
    FileMetadata,
    FileRenameRequest,
)

router = APIRouter(prefix="/files", tags=["Files"])


@router.get("")
async def list_files(
    user: CurrentUserDep,
    db: DatabaseDep,
    pagination: PaginationDep,
    folder_id: Optional[str] = None
):
    res = await file_service.list_files(
        db, user["_id"], folder_id, pagination.skip, pagination.limit
    )
    files = [FileMetadata(**f) for f in res["files"]]
    return ApiResponse(data={"files": files, "total": res["total"]})


@router.get("/{file_id}")
async def get_file_info(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    file_doc = await file_service._get_file_doc(db, file_id, user["_id"])
    return ApiResponse(data=FileMetadata(**file_doc))


@router.put("/{file_id}/rename")
async def rename_file(
    file_id: str,
    payload: FileRenameRequest,
    user: CurrentUserDep,
    db: DatabaseDep
):
    updated_doc = await file_service.rename_file(db, file_id, user["_id"], payload.new_name)
    return ApiResponse(data=FileMetadata(**updated_doc))


@router.delete("/{file_id}")
async def delete_file(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    trashed_doc = await file_service.move_to_trash(db, file_id, user["_id"])
    return ApiResponse(data=FileMetadata(**trashed_doc))


@router.get("/{file_id}/stream")
async def stream_file(
    file_id: str,
    user: CurrentUserDep,
    db: DatabaseDep,
    range: Optional[str] = Header(None)
):
    file_doc = await file_service._get_file_doc(db, file_id, user["_id"])
    if file_doc.get("is_folder"):
        raise HTTPException(status_code=400, detail="Cannot stream a folder")
    if not file_doc.get("telegram_message_id"):
        raise HTTPException(status_code=409, detail="File is not ready for streaming")

    file_size = file_doc["size_bytes"]
    start_offset = 0
    end_offset = file_size - 1

    # Range header parsing (e.g., "bytes=0-1024")
    if range and range.startswith("bytes="):
        parts = range.replace("bytes=", "").split("-")
        if parts[0]:
            start_offset = int(parts[0])
        if len(parts) > 1 and parts[1]:
            end_offset = int(parts[1])

    # Ensure range values stay within bounds
    start_offset = max(0, start_offset)
    end_offset = min(file_size - 1, end_offset)

    content_length = (end_offset - start_offset) + 1

    # Chunk generator from Storage Engine
    async def chunk_generator():
        async for chunk in telegram_service.stream_download(
            file_doc["telegram_message_id"],
            offset=start_offset,
            limit=content_length,
        ):
            yield chunk

    headers = {
        "Content-Range": f"bytes {start_offset}-{end_offset}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": file_doc.get("mime_type", "application/octet-stream"),
    }

    status_code = status.HTTP_206_PARTIAL_CONTENT if range else status.HTTP_200_OK
    return StreamingResponse(chunk_generator(), status_code=status_code, headers=headers)

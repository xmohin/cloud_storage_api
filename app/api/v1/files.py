"""File management and streaming endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep, PaginationDep, StorageEngineDep
from app.services.file_service import file_service
from app.utils.helpers import parse_range_header
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
        db, user["_id"], folder_id, pagination.skip, pagination.per_page
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
    storage: StorageEngineDep,
    range: Optional[str] = Header(None)
):
    file_doc = await file_service._get_file_doc(db, file_id, user["_id"])
    if file_doc.get("is_folder"):
        raise HTTPException(status_code=400, detail="Cannot stream a folder")
    if file_doc.get("status") != "completed" or not file_doc.get("telegram_message_id"):
        raise HTTPException(status_code=409, detail="File is not ready to stream")

    file_size = file_doc["size_bytes"]
    parsed_range = parse_range_header(range, file_size)
    if parsed_range is None:
        raise HTTPException(status_code=416, detail="Requested range not satisfiable", headers={"Content-Range": f"bytes */{file_size}"})
    start_offset, end_offset = parsed_range
    content_length = (end_offset - start_offset) + 1

    # Chunk generator from Storage Engine
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
    }
    # Content-Range is only meaningful on a 206 (or the 416 above) — sending it
    # on a plain 200 full-content response is a spec violation (RFC 7233 §4.2)
    # and can confuse clients into thinking they received a partial response.
    if range:
        headers["Content-Range"] = f"bytes {start_offset}-{end_offset}/{file_size}"

    status_code = status.HTTP_206_PARTIAL_CONTENT if range else status.HTTP_200_OK
    return StreamingResponse(chunk_generator(), status_code=status_code, headers=headers)

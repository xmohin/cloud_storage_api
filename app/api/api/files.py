"""File management and streaming endpoints."""

from typing import Optional
from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep, PaginationDep, StorageEngineDep
from app.services.file_service import file_service
from app.utils.helpers import content_disposition_attachment, parse_range_header
from app.models.schemas import (
    ApiResponse,
    FileMetadata,
    FileRenameRequest,
    FileType,
)

router = APIRouter(prefix="/files", tags=["Files"])


@router.get("")
async def list_files(
    user: CurrentUserDep,
    db: DatabaseDep,
    pagination: PaginationDep,
    folder_id: Optional[str] = None,
):
    res = await file_service.list_files(
        db, user["_id"], folder_id, pagination.skip, pagination.per_page
    )
    files = [FileMetadata(**f) for f in res["files"]]
    return ApiResponse(data={"files": files, "total": res["total"]})


@router.get("/favorites")
async def list_favorites(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_favorites(db, user["_id"], pagination.skip, pagination.per_page)
    files = [FileMetadata(**f) for f in res["files"]]
    return ApiResponse(data={"files": files, "total": res["total"]})


@router.get("/recent")
async def list_recent(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_recent(db, user["_id"], pagination.skip, pagination.per_page)
    files = [FileMetadata(**f) for f in res["files"]]
    return ApiResponse(data={"files": files, "total": res["total"]})


@router.get("/trash")
async def list_trash(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_trash(db, user["_id"], pagination.skip, pagination.per_page)
    files = [FileMetadata(**f) for f in res["files"]]
    return ApiResponse(data={"files": files, "total": res["total"]})


@router.delete("/trash/empty")
async def empty_trash(user: CurrentUserDep, db: DatabaseDep):
    """Permanently delete every item currently in the user's trash."""
    trashed = await db.files.find(
        {"owner_id": user["_id"], "deleted_at": {"$ne": None}},
        {"_id": 1},
    ).to_list(None)
    deleted = 0
    for item in trashed:
        try:
            await file_service.permanent_delete(db, item["_id"], user["_id"])
            deleted += 1
        except HTTPException:
            continue
    return ApiResponse(message="Trash emptied", data={"deleted": deleted})


@router.get("/type/{file_type}")
async def list_by_type(
    file_type: FileType, user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep
):
    res = await file_service.list_by_type(
        db, user["_id"], file_type, pagination.skip, pagination.per_page
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
    db: DatabaseDep,
):
    updated_doc = await file_service.rename_file(db, file_id, user["_id"], payload.new_name)
    return ApiResponse(data=FileMetadata(**updated_doc))


@router.put("/{file_id}/favorite")
async def toggle_favorite(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    updated_doc = await file_service.toggle_favorite(db, file_id, user["_id"])
    return ApiResponse(data=FileMetadata(**updated_doc))


@router.delete("/{file_id}")
async def delete_file(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    trashed_doc = await file_service.move_to_trash(db, file_id, user["_id"])
    return ApiResponse(data=FileMetadata(**trashed_doc))


@router.delete("/{file_id}/permanent")
async def permanent_delete_file(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    """Permanently delete a file/folder (must already be in trash, or force via this route)."""
    await file_service.permanent_delete(db, file_id, user["_id"])
    return ApiResponse(message="File permanently deleted")


@router.post("/{file_id}/restore")
async def restore_file(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    restored_doc = await file_service.restore_from_trash(db, file_id, user["_id"])
    return ApiResponse(data=FileMetadata(**restored_doc))


@router.get("/{file_id}/stream")
async def stream_file(
    file_id: str,
    user: CurrentUserDep,
    db: DatabaseDep,
    storage: StorageEngineDep,
    range: Optional[str] = Header(None),
):
    file_doc = await file_service._get_file_doc(db, file_id, user["_id"])
    if file_doc.get("is_folder"):
        raise HTTPException(status_code=400, detail="Cannot stream a folder")
    if file_doc.get("status") != "completed" or not file_doc.get("telegram_message_id"):
        raise HTTPException(status_code=409, detail="File is not ready to stream")

    file_size = file_doc["size_bytes"]
    parsed_range = parse_range_header(range, file_size)
    if parsed_range is None:
        raise HTTPException(
            status_code=416,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    start_offset, end_offset = parsed_range
    content_length = (end_offset - start_offset) + 1

    async def chunk_generator():
        async for chunk in storage.download_file_stream(
            file_doc["telegram_message_id"],
            start=start_offset,
            end=end_offset,
        ):
            yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": file_doc.get("mime_type", "application/octet-stream"),
        "Content-Disposition": content_disposition_attachment(
            file_doc.get("original_name", "download")
        ),
    }
    # Content-Range is only meaningful on a 206 (or the 416 above) — sending it
    # on a plain 200 full-content response is a spec violation (RFC 7233 §4.2)
    # and can confuse clients into thinking they received a partial response.
    if range:
        headers["Content-Range"] = f"bytes {start_offset}-{end_offset}/{file_size}"

    status_code = status.HTTP_206_PARTIAL_CONTENT if range else status.HTTP_200_OK
    return StreamingResponse(chunk_generator(), status_code=status_code, headers=headers)

"""File, Folder, Trash, and Statistics API endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, Query, Request, Response, status, HTTPException
from fastapi.responses import StreamingResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep, PaginationDep
from app.services.file_service import file_service
from app.services.telegram_service import telegram_service
from app.models.schemas import ApiResponse, FileListResponse, FileMetadata, FileRenameRequest, FileMoveRequest, FolderCreateRequest, StorageStats

router = APIRouter(prefix="/files", tags=["Files & Folders"])

@router.get("/list")
async def list_files(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep, parent_id: Optional[str] = Query(None)):
    res = await file_service.list_files(db, user["_id"], parent_id, pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/recent")
async def list_recent_files(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_recent(db, user["_id"], pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/favorites")
async def list_favorites(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_favorites(db, user["_id"], pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/search")
async def search_files(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep, q: str = Query(..., min_length=1)):
    res = await file_service.search_files(db, user["_id"], q, pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/stats")
async def get_storage_statistics(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=StorageStats(**await file_service.get_statistics(db, user["_id"])))

@router.get("/{file_id}/metadata")
async def get_file_metadata(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service._get_file_doc(db, file_id, user["_id"])))

@router.patch("/{file_id}/rename")
async def rename_file(file_id: str, payload: FileRenameRequest, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.rename_file(db, file_id, user["_id"], payload.new_name)))

@router.patch("/{file_id}/move")
async def move_file(file_id: str, payload: FileMoveRequest, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.move_file(db, file_id, user["_id"], payload.new_parent_id)))

@router.patch("/{file_id}/favorite")
async def toggle_favorite(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.toggle_favorite(db, file_id, user["_id"])))

@router.get("/{file_id}/download")
async def download_file(file_id: str, user: CurrentUserDep, db: DatabaseDep, request: Request, offset: int = Query(0, ge=0), limit: int = Query(0, ge=0)):
    file_doc = await file_service._get_file_doc(db, file_id, user["_id"])
    if file_doc.get("is_folder"): raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot download folder")
    msg_id = file_doc.get("telegram_message_id")
    if not msg_id: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File content not available")
    if limit > 0: return StreamingResponse(telegram_service.stream_download(msg_id, offset, limit), media_type=file_doc.get("mime_type", "application/octet-stream"))
    range_header = request.headers.get("Range")
    if range_header:
        start_str, end_str = range_header.strip().split("=")[-1].split("-")
        start_offset = int(start_str) if start_str else 0; end_offset = int(end_str) if end_str else None
        async def stream_range():
            async for chunk in telegram_service.stream_download(msg_id, start_offset, (end_offset - start_offset + 1) if end_offset else 0): yield chunk
        return StreamingResponse(stream_range(), media_type=file_doc.get("mime_type", "application/octet-stream"), status_code=status.HTTP_206_PARTIAL_CONTENT, headers={"Content-Range": f"bytes {start_offset}-{end_offset or file_doc['size_bytes']-1}/{file_doc['size_bytes']}", "Accept-Ranges": "bytes"})
    return StreamingResponse(telegram_service.stream_download(msg_id), media_type=file_doc.get("mime_type", "application/octet-stream"))

@router.get("/{file_id}/thumbnail")
async def get_thumbnail(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    file_doc = await file_service._get_file_doc(db, file_id, user["_id"])
    if not file_doc.get("thumbnail_message_id"): raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Thumbnail not available")
    return StreamingResponse(telegram_service.stream_download(file_doc["thumbnail_message_id"]), media_type="image/jpeg")

@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(payload: FolderCreateRequest, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.create_folder(db, user["_id"], payload.name, payload.parent_id)))

@router.post("/{file_id}/trash")
async def move_to_trash(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.move_to_trash(db, file_id, user["_id"])))

@router.get("/trash/list")
async def list_trash(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_trash(db, user["_id"], pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.post("/trash/{file_id}/restore")
async def restore_from_trash(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.restore_from_trash(db, file_id, user["_id"])))

@router.delete("/trash/{file_id}")
async def permanent_delete(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    await file_service.permanent_delete(db, file_id, user["_id"])
    return ApiResponse(message="Deleted permanently")

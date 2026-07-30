# app/api/v1/files.py
"""File management endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, Query, UploadFile, File, status, HTTPException
from app.api.dependencies import CurrentUserDep, DatabaseDep, PaginationDep
from app.services.file_service import file_service
from app.services.upload_service import upload_service
from app.services.telegram_service import telegram_service
from app.models.schemas import ApiResponse, FileListResponse, FileMetadata, FileRename, FileMove, FileCopy, FileType

router = APIRouter(prefix="/files", tags=["Files"])

@router.post("/upload")
async def upload_file(user: CurrentUserDep, file: UploadFile = File(...)):
    return ApiResponse(data=await upload_service.handle_small_upload(file, user["_id"]))

@router.post("/upload-multiple")
async def upload_multiple_files(user: CurrentUserDep, files: list[UploadFile] = File(...)):
    results = []
    for f in files:
        results.append(await upload_service.handle_small_upload(f, user["_id"]))
    return ApiResponse(data=results)

@router.get("")
async def list_files(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep, parent_id: Optional[str] = Query(None)):
    res = await file_service.list_files(db, user["_id"], parent_id, pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/recent")
async def list_recent(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_recent(db, user["_id"], pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/trash")
async def list_trash(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_trash(db, user["_id"], pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/images")
async def list_images(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_by_type(db, user["_id"], FileType.IMAGE, pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/videos")
async def list_videos(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_by_type(db, user["_id"], FileType.VIDEO, pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/documents")
async def list_documents(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_by_type(db, user["_id"], FileType.DOCUMENT, pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/audio")
async def list_audio(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_by_type(db, user["_id"], FileType.AUDIO, pagination.skip, pagination.per_page)
    return ApiResponse(data=FileListResponse(files=[FileMetadata(**f) for f in res["files"]], total=res["total"], page=pagination.page, per_page=pagination.per_page, has_next=(pagination.skip + pagination.per_page) < res["total"]))

@router.get("/{file_id}")
async def get_file(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service._get_file_doc(db, file_id, user["_id"])))

@router.put("/{file_id}")
async def rename_file(file_id: str, payload: FileRename, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.rename_file(db, file_id, user["_id"], payload.new_name)))

@router.delete("/{file_id}")
async def soft_delete_file(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.move_to_trash(db, file_id, user["_id"])))

@router.delete("/permanent/{file_id}")
async def hard_delete_file(file_id: str, user: CurrentUserDep, db: DatabaseDep):
    await file_service.permanent_delete(db, file_id, user["_id"])
    return ApiResponse(message="Deleted permanently")

@router.post("/favorite")
async def favorite_files(payload: FileMove, user: CurrentUserDep, db: DatabaseDep):
    await db.files.update_many({"_id": {"$in": payload.file_ids}, "owner_id": user["_id"]}, {"$set": {"is_favorite": True}})
    return ApiResponse(message="Favorited")

@router.post("/unfavorite")
async def unfavorite_files(payload: FileMove, user: CurrentUserDep, db: DatabaseDep):
    await db.files.update_many({"_id": {"$in": payload.file_ids}, "owner_id": user["_id"]}, {"$set": {"is_favorite": False}})
    return ApiResponse(message="Unfavorited")

@router.post("/move")
async def move_files(payload: FileMove, user: CurrentUserDep, db: DatabaseDep):
    for fid in payload.file_ids:
        await file_service.move_file(db, fid, user["_id"], payload.new_parent_id)
    return ApiResponse(message="Moved successfully")

@router.post("/copy")
async def copy_files(payload: FileCopy, user: CurrentUserDep, db: DatabaseDep):
    for fid in payload.file_ids:
        await file_service.copy_file(db, fid, user["_id"], payload.new_parent_id)
    return ApiResponse(message="Copied successfully")

@router.post("/restore")
async def restore_files(payload: FileMove, user: CurrentUserDep, db: DatabaseDep):
    for fid in payload.file_ids:
        await file_service.restore_from_trash(db, fid, user["_id"])
    return ApiResponse(message="Restored successfully")

"""Folder management endpoints."""

import asyncio
from fastapi import APIRouter, Depends, status
from app.api.dependencies import CurrentUserDep, DatabaseDep, PaginationDep
from app.services.file_service import file_service
from app.models.schemas import (
    ApiResponse,
    FileMetadata,
    FolderCreateRequest,
    FileRenameRequest,
    FileMoveRequest,
)

router = APIRouter(prefix="/folders", tags=["Folders"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_folder(payload: FolderCreateRequest, user: CurrentUserDep, db: DatabaseDep):
    folder_doc = await file_service.create_folder(db, user["_id"], payload.name, payload.parent_id)
    return ApiResponse(data=FileMetadata(**folder_doc))


@router.get("")
async def list_root_folders(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_files(db, user["_id"], None, pagination.skip, pagination.per_page)
    folders = [FileMetadata(**f) for f in res["files"] if f.get("is_folder")]
    return ApiResponse(data={"folders": folders, "total": len(folders)})


@router.get("/tree")
async def get_folder_tree(user: CurrentUserDep, db: DatabaseDep):
    folders_cursor = db.files.find({"owner_id": user["_id"], "is_folder": True, "deleted_at": None})
    folders = await folders_cursor.to_list(1000)
    return ApiResponse(data={"tree": [FileMetadata(**f) for f in folders]})


@router.get("/{folder_id}")
async def get_folder(folder_id: str, user: CurrentUserDep, db: DatabaseDep):
    folder_doc = await file_service._get_file_doc(db, folder_id, user["_id"])
    return ApiResponse(data=FileMetadata(**folder_doc))


@router.put("/{folder_id}")
async def rename_folder(folder_id: str, payload: FileRenameRequest, user: CurrentUserDep, db: DatabaseDep):
    updated_doc = await file_service.rename_file(db, folder_id, user["_id"], payload.new_name)
    return ApiResponse(data=FileMetadata(**updated_doc))


@router.delete("/{folder_id}")
async def delete_folder(folder_id: str, user: CurrentUserDep, db: DatabaseDep):
    trashed_doc = await file_service.move_to_trash(db, folder_id, user["_id"])
    return ApiResponse(data=FileMetadata(**trashed_doc))


@router.post("/move")
async def move_folders(payload: FileMoveRequest, user: CurrentUserDep, db: DatabaseDep):
    # Move each file/folder concurrently instead of one sequential await per
    # item — file_ids is a required field, so it's always present here.
    await asyncio.gather(*(
        file_service.move_file(db, fid, user["_id"], payload.new_parent_id)
        for fid in payload.file_ids
    ))
    return ApiResponse(message="Folders moved successfully")

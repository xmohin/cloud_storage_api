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
    FileCopyRequest,
)

router = APIRouter(prefix="/folders", tags=["Folders"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_folder(payload: FolderCreateRequest, user: CurrentUserDep, db: DatabaseDep):
    folder_doc = await file_service.create_folder(db, user["_id"], payload.name, payload.parent_id)
    return ApiResponse(data=FileMetadata(**folder_doc))


@router.get("")
async def list_root_folders(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    # is_folder=True pushes the filter into the DB query so skip/limit and
    # the total count both apply to folders only — previously this paged
    # over all root items and filtered in Python, so a page could show 0
    # folders while later pages had some, and "total" was just the current
    # page's count (#14).
    res = await file_service.list_files(
        db, user["_id"], None, pagination.skip, pagination.per_page, is_folder=True
    )
    folders = [FileMetadata(**f) for f in res["files"]]
    return ApiResponse(data={"folders": folders, "total": res["total"]})


@router.get("/tree")
async def get_folder_tree(user: CurrentUserDep, db: DatabaseDep):
    # No hard 1000 cap — large libraries need the full tree. Folder docs are small.
    folders_cursor = db.files.find({"owner_id": user["_id"], "is_folder": True, "deleted_at": None})
    folders = await folders_cursor.to_list(None)
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


@router.post("/copy")
async def copy_folders(payload: FileCopyRequest, user: CurrentUserDep, db: DatabaseDep):
    """CRITICAL #4: file_service.copy_file() — including its #15 destination
    and quota validation — was fully implemented, and FileCopyRequest exists
    specifically to mirror FileMoveRequest above, but no route ever called it.

    Sequential, not gather() like move_folders: copy_file() re-checks quota
    against storage_used_bytes on every call, and move doesn't touch that
    counter at all. Running a multi-item copy batch concurrently would let
    every item see the same pre-copy total and let the whole batch blow
    past quota — the same class of race flagged in the upload path.
    """
    copied = []
    for fid in payload.file_ids:
        copied.append(await file_service.copy_file(db, fid, user["_id"], payload.new_parent_id))
    return ApiResponse(data=[FileMetadata(**doc) for doc in copied], message="Copied successfully")

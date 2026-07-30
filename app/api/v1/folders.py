# app/api/v1/folders.py
"""Folder management endpoints."""

from fastapi import APIRouter, Depends, status
from app.api.dependencies import CurrentUserDep, DatabaseDep, PaginationDep
from app.services.file_service import file_service
from app.models.schemas import ApiResponse, FileMetadata, FolderCreate, FileRename, FileMove

router = APIRouter(prefix="/folders", tags=["Folders"])

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_folder(payload: FolderCreate, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.create_folder(db, user["_id"], payload.name, payload.parent_id)))

@router.get("")
async def list_root_folders(user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.list_files(db, user["_id"], None, pagination.skip, pagination.per_page)
    # Filter folders only
    folders = [f for f in res["files"] if f.get("is_folder")]
    return ApiResponse(data={"folders": folders, "total": len(folders)})

@router.get("/tree")
async def get_folder_tree(user: CurrentUserDep, db: DatabaseDep):
    # Simplified tree fetch
    folders = await db.files.find({"owner_id": user["_id"], "is_folder": True, "deleted_at": None}).to_list(None)
    return ApiResponse(data={"tree": folders})

@router.get("/{folder_id}")
async def get_folder(folder_id: str, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service._get_file_doc(db, folder_id, user["_id"])))

@router.put("/{folder_id}")
async def rename_folder(folder_id: str, payload: FileRename, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.rename_file(db, folder_id, user["_id"], payload.new_name)))

@router.delete("/{folder_id}")
async def delete_folder(folder_id: str, user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=FileMetadata(**await file_service.move_to_trash(db, folder_id, user["_id"])))

@router.post("/move")
async def move_folders(payload: FileMove, user: CurrentUserDep, db: DatabaseDep):
    for fid in payload.file_ids:
        await file_service.move_file(db, fid, user["_id"], payload.new_parent_id)
    return ApiResponse(message="Folders moved")

@router.post("/rename")
async def rename_folders(payload: FileRename, user: CurrentUserDep, db: DatabaseDep):
    # Assuming payload has file_id and new_name for consistency
    return ApiResponse(message="Use PUT /folders/{id} instead")

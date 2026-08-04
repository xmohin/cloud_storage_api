"""Search endpoints."""

from fastapi import APIRouter
from app.api.dependencies import CurrentUserDep, DatabaseDep, PaginationDep
from app.services.file_service import file_service
from app.models.schemas import ApiResponse, FileListResponse, FileMetadata, FileType

router = APIRouter(prefix="/search", tags=["Search"])


def _list_response(files: list, total: int, pagination) -> FileListResponse:
    return FileListResponse(
        files=[FileMetadata(**f) for f in files],
        total=total,
        page=pagination.page,
        limit=pagination.per_page,
        per_page=pagination.per_page,
        has_next=(pagination.skip + pagination.per_page) < total,
    )


@router.get("")
async def search(q: str, user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.search_files(db, user["_id"], q, pagination.skip, pagination.per_page)
    return ApiResponse(data=_list_response(res["files"], res["total"], pagination))


@router.get("/images")
async def search_images(q: str, user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    query = {
        "owner_id": user["_id"],
        "deleted_at": None,
        "file_type": FileType.IMAGE.value,
        "$text": {"$search": q},
    }
    files = await db.files.find(query).skip(pagination.skip).limit(pagination.per_page).to_list(pagination.per_page)
    total = await db.files.count_documents(query)
    return ApiResponse(data=_list_response(files, total, pagination))


@router.get("/videos")
async def search_videos(q: str, user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    query = {
        "owner_id": user["_id"],
        "deleted_at": None,
        "file_type": FileType.VIDEO.value,
        "$text": {"$search": q},
    }
    files = await db.files.find(query).skip(pagination.skip).limit(pagination.per_page).to_list(pagination.per_page)
    total = await db.files.count_documents(query)
    return ApiResponse(data=_list_response(files, total, pagination))


@router.get("/folders")
async def search_folders(q: str, user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    query = {
        "owner_id": user["_id"],
        "deleted_at": None,
        "is_folder": True,
        "$text": {"$search": q},
    }
    files = await db.files.find(query).skip(pagination.skip).limit(pagination.per_page).to_list(pagination.per_page)
    total = await db.files.count_documents(query)
    return ApiResponse(data=_list_response(files, total, pagination))


@router.get("/documents")
async def search_documents(q: str, user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    query = {
        "owner_id": user["_id"],
        "deleted_at": None,
        "file_type": FileType.DOCUMENT.value,
        "$text": {"$search": q},
    }
    files = await db.files.find(query).skip(pagination.skip).limit(pagination.per_page).to_list(pagination.per_page)
    total = await db.files.count_documents(query)
    return ApiResponse(data=_list_response(files, total, pagination))

"""Search endpoints."""

from fastapi import APIRouter
from app.api.dependencies import CurrentUserDep, DatabaseDep, PaginationDep
from app.services.file_service import file_service
from app.models.schemas import ApiResponse, FileListResponse, FileMetadata, FileType

router = APIRouter(prefix="/search", tags=["Search"])

@router.get("")
async def search(q: str, user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    res = await file_service.search_files(db, user["_id"], q, pagination.skip, pagination.limit)
    return ApiResponse(
        data=FileListResponse(
            files=[FileMetadata(**f) for f in res["files"]],
            total=res["total"],
            page=pagination.page,
            limit=pagination.limit,
        )
    )

@router.get("/images")
async def search_images(q: str, user: CurrentUserDep, db: DatabaseDep, pagination: PaginationDep):
    # Combine text search with type filter
    query = {"owner_id": user["_id"], "deleted_at": None, "file_type": FileType.IMAGE.value, "$text": {"$search": q}}
    files = await db.files.find(query).skip(pagination.skip).limit(pagination.limit).to_list(pagination.limit)
    total = await db.files.count_documents(query)
    return ApiResponse(
        data=FileListResponse(
            files=[FileMetadata(**f) for f in files],
            total=total,
            page=pagination.page,
            limit=pagination.limit,
        )
    )

# Similar endpoints for /videos, /folders, /tags...

from fastapi import APIRouter, Depends, Header, Response, status
from typing import Optional, List
from app.services.file_service import FileService
from app.api.dependencies import get_current_user
from app.core.database import get_database
from app.utils.helpers import parse_range_header
from motor.motor_asyncio import AsyncIOMotorDatabase

router = APIRouter(prefix="/files", tags=["Files"])

@router.get("", response_model=List[dict])
async def list_files(
    folder_id: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    service = FileService(db)
    return await service.list_files(current_user["id"], folder_id, page, limit)

@router.get("/{file_id}/stream")
async def stream_file(
    file_id: str,
    range: Optional[str] = Header(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    service = FileService(db)
    file_bytes = await service.stream_file(file_id, current_user["id"])
    file_size = len(file_bytes)
    
    if range:
        start, end = parse_range_header(range, file_size)
        chunk = file_bytes[start:end + 1]
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(chunk))
        }
        return Response(content=chunk, status_code=status.HTTP_206_PARTIAL_CONTENT, headers=headers)
    
    return Response(content=file_bytes, media_type="application/octet-stream")

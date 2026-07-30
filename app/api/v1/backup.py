# app/api/v1/backup.py
from fastapi import APIRouter, Depends, UploadFile, File
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.models.schemas import ApiResponse

router = APIRouter(prefix="/backup", tags=["Backup"])

@router.post("/upload")
async def backup_upload(user: CurrentUserDep, db: DatabaseDep, file: UploadFile = File(...)):
    # Upload backup file to Telegram
    return ApiResponse(message="Backup uploaded")

@router.post("/upload-folder")
async def backup_folder(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(message="Folder backup started")

@router.post("/restore")
async def restore_backup(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(message="Restore started")

@router.get("/list")
async def list_backups(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data=[])

@router.get("/status")
async def backup_status(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(data={"status": "idle"})

@router.post("/sync")
async def sync_backup(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(message="Sync started")

@router.post("/resync")
async def resync_backup(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(message="Resync started")

@router.delete("/delete")
async def delete_backup(user: CurrentUserDep, db: DatabaseDep):
    return ApiResponse(message="Backup deleted")

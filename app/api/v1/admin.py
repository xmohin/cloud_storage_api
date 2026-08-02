from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from app.api.dependencies import DatabaseDep, AdminUserDep, PaginationDep
from app.models.schemas import ApiResponse, AdminUserUpdate, UserRole, UserProfile, FileMetadata
from app.services.file_service import file_service

router = APIRouter(prefix="/admin", tags=["Admin"])

@router.get("/dashboard")
async def dashboard(admin: AdminUserDep, db: DatabaseDep):
    users_count = await db.users.count_documents({})
    files_count = await db.files.count_documents({})
    storage_used = await db.users.aggregate([{"$group": {"_id": None, "total": {"$sum": "$storage_used_bytes"}}}]).to_list(1)
    return ApiResponse(data={"users": users_count, "files": files_count, "storage_used": storage_used[0]["total"] if storage_used else 0})

@router.get("/users")
async def list_users(admin: AdminUserDep, db: DatabaseDep, pagination: PaginationDep):
    cursor = db.users.find({}, {"password_hash": 0}).sort("created_at", -1).skip(pagination.skip).limit(pagination.limit)
    users = await cursor.to_list(pagination.limit)
    total = await db.users.count_documents({})
    return ApiResponse(data={"users": [UserProfile(**u) for u in users], "total": total})

@router.get("/user/{user_id}")
async def get_user(user_id: str, admin: AdminUserDep, db: DatabaseDep):
    user = await db.users.find_one({"_id": user_id}, {"password_hash": 0})
    if not user: raise HTTPException(404, detail="User not found")
    return ApiResponse(data=UserProfile(**user))

@router.put("/user/{user_id}")
async def update_user(user_id: str, payload: AdminUserUpdate, admin: AdminUserDep, db: DatabaseDep):
    update_data = payload.model_dump(exclude_unset=True)
    if "role" in update_data: update_data["role"] = update_data["role"].value
    await db.users.update_one({"_id": user_id}, {"$set": update_data})
    return ApiResponse(message="User updated")

@router.delete("/user/{user_id}")
async def delete_user(user_id: str, admin: AdminUserDep, db: DatabaseDep):
    await db.users.update_one({"_id": user_id}, {"$set": {"is_active": False, "deleted_at": datetime.now(timezone.utc)}})
    return ApiResponse(message="User deactivated")

@router.get("/files")
async def list_all_files(admin: AdminUserDep, db: DatabaseDep, pagination: PaginationDep):
    cursor = db.files.find({}).sort("created_at", -1).skip(pagination.skip).limit(pagination.limit)
    files = await cursor.to_list(pagination.limit)
    total = await db.files.count_documents({})
    return ApiResponse(data={"files": [FileMetadata(**f) for f in files], "total": total})

@router.delete("/file/{file_id}")
async def delete_file(file_id: str, admin: AdminUserDep, db: DatabaseDep):
    file_doc = await db.files.find_one({"_id": file_id})
    if not file_doc: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found")
    await file_service.permanent_delete(db, file_id, file_doc["owner_id"])
    return ApiResponse(message="File deleted")

@router.get("/logs")
async def get_logs(admin: AdminUserDep, db: DatabaseDep):
    return ApiResponse(data=[])

@router.get("/statistics")
async def get_stats(admin: AdminUserDep, db: DatabaseDep):
    return ApiResponse(data={"status": "ok"})

@router.get("/server")
async def server_info(admin: AdminUserDep):
    return ApiResponse(data={"status": "running"})

@router.post("/maintenance")
async def maintenance_mode(admin: AdminUserDep):
    return ApiResponse(message="Maintenance mode toggled")

@router.get("/backups")
async def list_backups(admin: AdminUserDep, db: DatabaseDep):
    return ApiResponse(data=[])

"""Backup and restore endpoints for database collections."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from bson import json_util
from fastapi import APIRouter, HTTPException, UploadFile, File, status
from fastapi.responses import Response
from app.api.dependencies import AdminUserDep, DatabaseDep
from app.models.schemas import ApiResponse

router = APIRouter(prefix="/backup", tags=["Backup & Restore"])


@router.get("/export", response_class=Response)
async def export_database(admin: AdminUserDep, db: DatabaseDep):
    """Export all database collections into a downloadable JSON backup file."""
    collections = await db.list_collection_names()
    backup_data: Dict[str, List[Dict[str, Any]]] = {}

    for collection_name in collections:
        if collection_name.startswith("system."):
            continue
        cursor = db[collection_name].find({})
        docs = await cursor.to_list(length=100000)
        # Convert BSON objects (ObjectId, datetime) safely using json_util
        backup_data[collection_name] = json.loads(json_util.dumps(docs))

    export_json = json.dumps(backup_data, indent=2)
    filename = f"gallery_vault_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"

    return Response(
        content=export_json,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/import")
async def import_database(
    admin: AdminUserDep, 
    db: DatabaseDep, 
    file: UploadFile = File(...)
):
    """Restore database collections from an uploaded JSON backup file."""
    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Only JSON backup files are accepted"
        )

    try:
        content = await file.read()
        backup_data = json.loads(content.decode("utf-8"))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"Invalid JSON file format: {str(e)}"
        )

    if not isinstance(backup_data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid backup structure"
        )

    restored_summary = {}

    for collection_name, docs_data in backup_data.items():
        if not isinstance(docs_data, list) or not docs_data:
            continue

        # Convert back from json_util dump format to native BSON documents
        bson_docs = json_util.loads(json.dumps(docs_data))
        
        # Clear existing collection and insert restored documents
        await db[collection_name].delete_many({})
        if bson_docs:
            await db[collection_name].insert_many(bson_docs)
            restored_summary[collection_name] = len(bson_docs)

    return ApiResponse(
        message="Database restored successfully", 
        data={"restored_collections": restored_summary}
    )

"""Backup and restore endpoints for database collections."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from bson import json_util
from fastapi import APIRouter, HTTPException, UploadFile, File, status
from fastapi.responses import Response
from app.api.dependencies import AdminUserDep, DatabaseDep
from app.core.logger import get_logger
from app.models.schemas import ApiResponse

router = APIRouter(prefix="/backup", tags=["Backup & Restore"])
logger = get_logger(__name__)

# The only collections the app itself ever writes to (mirrors
# app/core/database.py's _ensure_indexes). A backup file is untrusted
# input — it may be stale, hand-edited, or from another deployment — so an
# unrecognised top-level key is skipped instead of being handed straight to
# delete_many()/insert_many() on whatever collection name it happens to
# contain (previously there was no whitelist here at all).
ALLOWED_COLLECTIONS = {
    "users", "files", "tokens", "sessions", "blacklist",
    "uploads", "shares", "share_logs", "notifications",
}


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
    """Restore database collections from an uploaded JSON backup file.

    Rewritten: the old version ran delete_many({}) on every collection
    named in the file with no validation, no whitelist, and nothing to
    fall back on if insert_many() then failed partway through — a bad
    upload could wipe real collections with no way back. This version:
      1. Only considers names in ALLOWED_COLLECTIONS.
      2. Fully parses and validates *every* collection's documents before
         deleting anything, so a malformed entry later in the file can't
         leave earlier collections already wiped.
      3. Snapshots each collection's current contents first. If any
         delete+insert step fails, every collection touched so far
         (including ones that already succeeded) is put back exactly as it
         was, so a failed import is a no-op instead of data loss.
    Standalone MongoDB (this app's default docker-compose deployment) can't
    use multi-document transactions, so this snapshot/restore approach is
    deliberately transaction-free and works on any deployment.
    """
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

    skipped_collections = sorted(set(backup_data.keys()) - ALLOWED_COLLECTIONS)

    # ── Pass 1: parse & validate every collection fully before touching the
    # database. (An empty-list entry means "nothing to restore for this
    # collection" and is left alone, same as the pre-fix behaviour.)
    to_restore: Dict[str, List[Dict[str, Any]]] = {}
    for collection_name, docs_data in backup_data.items():
        if collection_name not in ALLOWED_COLLECTIONS:
            continue
        if not isinstance(docs_data, list) or not docs_data:
            continue
        if not all(isinstance(d, dict) for d in docs_data):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Collection '{collection_name}' contains a non-document entry",
            )
        try:
            # Convert back from json_util dump format to native BSON documents
            bson_docs = json_util.loads(json.dumps(docs_data))
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Collection '{collection_name}' could not be parsed: {e}",
            )
        to_restore[collection_name] = bson_docs

    if not to_restore:
        return ApiResponse(
            message="Nothing to restore",
            data={"restored_collections": {}, "skipped_collections": skipped_collections},
        )

    # ── Pass 2: snapshot current contents of every collection we're about
    # to touch, so a failure below can restore all of them, not just the
    # one that failed.
    snapshots: Dict[str, List[Dict[str, Any]]] = {}
    for collection_name in to_restore:
        snapshots[collection_name] = await db[collection_name].find({}).to_list(None)

    async def _revert(done: List[str]) -> List[str]:
        """Best-effort: restores every collection in `done` to its
        pre-import snapshot. Returns the names it could NOT restore (e.g. if
        the same underlying failure that broke the import also breaks the
        revert's own insert_many), so the caller reports the real outcome
        instead of unconditionally claiming a full revert succeeded."""
        failed: List[str] = []
        for name in done:
            try:
                await db[name].delete_many({})
                if snapshots[name]:
                    await db[name].insert_many(snapshots[name])
            except Exception:
                logger.error("backup_import_revert_failed", collection=name)
                failed.append(name)
        return failed

    # ── Pass 3: replace each collection. Any failure reverts every
    # collection touched so far (not just the failing one), so the import
    # is all-or-nothing instead of leaving a mix of old/new/empty data.
    restored_summary: Dict[str, int] = {}
    completed: List[str] = []
    for collection_name, bson_docs in to_restore.items():
        try:
            await db[collection_name].delete_many({})
            await db[collection_name].insert_many(bson_docs)
        except Exception as e:
            # `completed` only holds collections that fully finished before
            # this one — but delete_many() above may already have emptied
            # *this* collection before insert_many() failed, so it must be
            # reverted too, not just the ones that came before it.
            revert_failed = await _revert(completed + [collection_name])
            if revert_failed:
                detail = (
                    f"Restore failed on collection '{collection_name}' ({e}). "
                    f"Automatic revert ALSO failed for: {', '.join(revert_failed)} — "
                    f"these collections may be left empty or inconsistent; check "
                    f"server logs before retrying. All other touched collections "
                    f"were reverted to their pre-import state."
                )
            else:
                detail = (
                    f"Restore failed on collection '{collection_name}' ({e}); "
                    f"all collections have been reverted to their pre-import state."
                )
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
        restored_summary[collection_name] = len(bson_docs)
        completed.append(collection_name)

    return ApiResponse(
        message="Database restored successfully",
        data={"restored_collections": restored_summary, "skipped_collections": skipped_collections},
    )

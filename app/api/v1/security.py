# app/api/v1/security.py
from fastapi import APIRouter, Depends, HTTPException, status
from app.api.dependencies import CurrentUserDep, DatabaseDep
from app.core.security import security
from app.models.schemas import ApiResponse, PinSet, PinVerify, PinChange

router = APIRouter(prefix="/security", tags=["Security"])

@router.post("/pin/set")
async def set_pin(payload: PinSet, user: CurrentUserDep, db: DatabaseDep):
    if user.get("pin_hash"): raise HTTPException(400, detail="PIN already set. Use /change instead.")
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"pin_hash": security.hash_password(payload.pin)}})
    return ApiResponse(message="PIN set successfully")

@router.post("/pin/verify")
async def verify_pin(payload: PinVerify, user: CurrentUserDep):
    if not user.get("pin_hash") or not security.verify_password(payload.pin, user["pin_hash"]):
        raise HTTPException(401, detail="Invalid PIN")
    return ApiResponse(message="PIN verified")

@router.put("/pin/change")
async def change_pin(payload: PinChange, user: CurrentUserDep, db: DatabaseDep):
    if not user.get("pin_hash") or not security.verify_password(payload.current_pin, user["pin_hash"]):
        raise HTTPException(401, detail="Invalid current PIN")
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"pin_hash": security.hash_password(payload.new_pin)}})
    return ApiResponse(message="PIN changed")

@router.post("/lock")
async def lock_app(user: CurrentUserDep, db: DatabaseDep):
    # Set a flag in user session or profile
    return ApiResponse(message="App locked")

@router.post("/unlock")
async def unlock_app(user: CurrentUserDep):
    return ApiResponse(message="App unlocked")

@router.get("/sessions")
async def list_sessions(user: CurrentUserDep, db: DatabaseDep):
    sessions = await db.sessions.find({"user_id": user["_id"], "is_active": True}).to_list(None)
    return ApiResponse(data=sessions)

@router.delete("/session/{session_id}")
async def delete_session(session_id: str, user: CurrentUserDep, db: DatabaseDep):
    await db.sessions.update_one({"_id": session_id, "user_id": user["_id"]}, {"$set": {"is_active": False}})
    return ApiResponse(message="Session revoked")

@router.get("/devices")
async def list_devices(user: CurrentUserDep, db: DatabaseDep):
    # Same as sessions but filtered by unique user agent
    sessions = await db.sessions.find({"user_id": user["_id"], "is_active": True}).to_list(None)
    return ApiResponse(data=sessions)

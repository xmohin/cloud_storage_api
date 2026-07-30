"""Authentication endpoints."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from app.api.dependencies import CurrentUserDep, DatabaseDep, EmailServiceDep
from app.core.config import get_settings
from app.core.security import security
from app.models.schemas import ApiResponse, EmailVerificationRequest, PasswordResetConfirm, PasswordResetRequest, RefreshTokenRequest, SessionListResponse, SessionInfo, TokenResponse, UserLogin, UserRegister, VerifyEmailOTP

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])

async def _blacklist_token(db: DatabaseDep, jti: str, exp: int):
    await db.blacklist.update_one({"jti": jti}, {"$set": {"jti": jti, "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc)}}, upsert=True)

async def _create_session(db: DatabaseDep, request: Request, user_id: str, refresh_jti: str):
    await db.sessions.insert_one({"_id": str(uuid4()), "user_id": user_id, "user_agent": request.headers.get("User-Agent", "Unknown"), "ip_address": request.client.host if request.client else "Unknown", "refresh_jti": refresh_jti, "is_active": True, "created_at": datetime.now(timezone.utc), "last_used_at": datetime.now(timezone.utc), "expires_at": datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)})

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegister, db: DatabaseDep, email_service: EmailServiceDep):
    if await db.users.find_one({"$or": [{"email": payload.email}, {"username": payload.username}]}): return JSONResponse(status.HTTP_409_CONFLICT, content={"success": False, "message": "User already exists"})
    otp = security.generate_otp()
    await db.users.insert_one({"_id": str(uuid4()), "username": payload.username, "email": payload.email, "password_hash": security.hash_password(payload.password), "role": "user", "is_active": True, "is_verified": False, "storage_used_bytes": 0, "storage_quota_bytes": 5 * 1024 * 1024 * 1024, "otp_hash": security.hash_otp(otp), "otp_expires_at": datetime.now(timezone.utc) + timedelta(minutes=10), "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)})
    await email_service.send_otp_email(payload.email, otp, "Email Verification")
    return ApiResponse(message="Registration successful. Please verify your email.")

@router.post("/verify-email")
async def verify_email(payload: VerifyEmailOTP, db: DatabaseDep):
    user = await db.users.find_one({"email": payload.email})
    if not user: return JSONResponse(status.HTTP_404_NOT_FOUND, content={"success": False, "message": "User not found"})
    if user.get("is_verified"): return ApiResponse(message="Email already verified")
    if not user.get("otp_hash") or datetime.now(timezone.utc) > user["otp_expires_at"]: return JSONResponse(status.HTTP_400_BAD_REQUEST, content={"success": False, "message": "OTP invalid or expired"})
    if not security.verify_otp(payload.otp, user["otp_hash"]): return JSONResponse(status.HTTP_400_BAD_REQUEST, content={"success": False, "message": "Invalid OTP"})
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"is_verified": True, "otp_hash": None, "otp_expires_at": None}})
    return ApiResponse(message="Email verified successfully.")

@router.post("/resend-otp")
async def resend_otp(payload: EmailVerificationRequest, db: DatabaseDep, email_service: EmailServiceDep):
    user = await db.users.find_one({"email": payload.email})
    if not user or user.get("is_verified"): return ApiResponse(message="If the email exists, a new OTP has been sent.")
    otp = security.generate_otp()
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"otp_hash": security.hash_otp(otp), "otp_expires_at": datetime.now(timezone.utc) + timedelta(minutes=10)}})
    await email_service.send_otp_email(payload.email, otp, "Email Verification")
    return ApiResponse(message="If the email exists, a new OTP has been sent.")

@router.post("/login")
async def login(payload: UserLogin, request: Request, db: DatabaseDep):
    user = await db.users.find_one({"email": payload.email})
    if not user or not security.verify_password(payload.password, user["password_hash"]): return JSONResponse(status.HTTP_401_UNAUTHORIZED, content={"success": False, "message": "Incorrect credentials"})
    if not user.get("is_verified"): return JSONResponse(status.HTTP_403_FORBIDDEN, content={"success": False, "message": "Email not verified"})
    access_token, _ = security.create_access_token(user["_id"])
    refresh_token, refresh_jti = security.create_refresh_token(user["_id"])
    await _create_session(db, request, user["_id"], refresh_jti)
    return ApiResponse(data=TokenResponse(access_token=access_token, refresh_token=refresh_token, expires_in=settings.access_token_expire_seconds))

@router.post("/refresh")
async def refresh_token(payload: RefreshTokenRequest, db: DatabaseDep):
    try: token_data = security.decode_token(payload.refresh_token, "refresh")
    except Exception: return JSONResponse(status.HTTP_401_UNAUTHORIZED, content={"success": False, "message": "Invalid token"})
    if await db.blacklist.find_one({"jti": token_data["jti"]}): return JSONResponse(status.HTTP_401_UNAUTHORIZED, content={"success": False, "message": "Token revoked"})
    session = await db.sessions.find_one({"refresh_jti": token_data["jti"], "is_active": True})
    if not session: return JSONResponse(status.HTTP_401_UNAUTHORIZED, content={"success": False, "message": "Session invalid"})
    await _blacklist_token(db, token_data["jti"], token_data["exp"])
    new_access, _ = security.create_access_token(token_data["sub"])
    new_refresh, new_jti = security.create_refresh_token(token_data["sub"])
    await db.sessions.update_one({"_id": session["_id"]}, {"$set": {"refresh_jti": new_jti, "last_used_at": datetime.now(timezone.utc)}})
    return ApiResponse(data=TokenResponse(access_token=new_access, refresh_token=new_refresh, expires_in=settings.access_token_expire_seconds))

@router.post("/forgot-password")
async def forgot_password(payload: PasswordResetRequest, db: DatabaseDep, email_service: EmailServiceDep):
    user = await db.users.find_one({"email": payload.email})
    if user:
        otp = security.generate_otp()
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"otp_hash": security.hash_otp(otp), "otp_expires_at": datetime.now(timezone.utc) + timedelta(minutes=10)}})
        await email_service.send_otp_email(payload.email, otp, "Password Reset")
    return ApiResponse(message="If the email exists, a reset OTP has been sent.")

@router.post("/reset-password")
async def reset_password(payload: PasswordResetConfirm, db: DatabaseDep):
    user = await db.users.find_one({"email": payload.email})
    if not user or not user.get("otp_hash") or datetime.now(timezone.utc) > user["otp_expires_at"] or not security.verify_otp(payload.otp, user["otp_hash"]): return JSONResponse(status.HTTP_400_BAD_REQUEST, content={"success": False, "message": "Invalid OTP"})
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": security.hash_password(payload.new_password), "otp_hash": None, "otp_expires_at": None}})
    await db.sessions.update_many({"user_id": user["_id"], "is_active": True}, {"$set": {"is_active": False}})
    return ApiResponse(message="Password reset successful.")

@router.get("/sessions")
async def list_sessions(user: CurrentUserDep, db: DatabaseDep):
    sessions = await db.sessions.find({"user_id": user["_id"], "is_active": True}).sort("last_used_at", -1).to_list(100)
    return ApiResponse(data=SessionListResponse(sessions=[SessionInfo(**s) for s in sessions], total=len(sessions)))

@router.post("/logout")
async def logout(payload: RefreshTokenRequest, user: CurrentUserDep, db: DatabaseDep):
    if user.get("_jti"): await _blacklist_token(db, user["_jti"], int((datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()))
    try:
        refresh_data = security.decode_token(payload.refresh_token, "refresh")
        await _blacklist_token(db, refresh_data["jti"], refresh_data["exp"])
        await db.sessions.update_one({"refresh_jti": refresh_data["jti"]}, {"$set": {"is_active": False}})
    except Exception: pass
    return ApiResponse(message="Logged out successfully")

@router.post("/logout-all")
async def logout_all(user: CurrentUserDep, db: DatabaseDep):
    if user.get("_jti"): await _blacklist_token(db, user["_jti"], int((datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()))
    sessions = await db.sessions.find({"user_id": user["_id"], "is_active": True}).to_list(None)
    for s in sessions:
        if s.get("refresh_jti"): await _blacklist_token(db, s["refresh_jti"], int((datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)).timestamp()))
    await db.sessions.update_many({"user_id": user["_id"], "is_active": True}, {"$set": {"is_active": False}})
    return ApiResponse(message="Logged out from all devices")

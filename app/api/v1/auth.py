"""Authentication endpoints."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi import APIRouter, Request, status, Depends
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.api.dependencies import CurrentUserDep, DatabaseDep, EmailServiceDep
from app.core.config import get_settings
from app.core.middleware import limiter
from app.core.security import security
from app.utils.validators import validate_password_strength
from app.models.schemas import (
    ApiResponse,
    OTPRequest,
    VerifyOTP,
    PasswordResetRequest,
    PasswordResetConfirm,
    RefreshTokenRequest,
    UserProfile,
    TokenResponse,
    UserLogin,
    UserRegister,
)

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])

# Fields cleared together whenever a pending OTP is consumed (or invalidated
# by issuing a new one), so a used/superseded code can never be replayed.
_OTP_CLEAR_FIELDS = {"otp_hash": None, "otp_purpose": None, "otp_expires_at": None}


def _ensure_tz_aware(dt: datetime | None) -> datetime | None:
    """Ensure MongoDB datetime objects are timezone aware for comparison."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _issue_otp(db: AsyncIOMotorDatabase, user_id: str, purpose: str) -> str:
    """Generates an OTP and stores it with a fixed, code-controlled purpose
    tag that is never taken from user input. /verify-otp, /reset-password,
    and /login/2fa each check this tag before consuming a code, so an OTP
    minted for one flow can no longer be redeemed by another endpoint —
    previously all three shared the same untagged otp_hash field (#11).
    """
    otp = security.generate_otp()
    await db.users.update_one(
        {"_id": user_id},
        {"$set": {
            "otp_hash": security.hash_otp(otp),
            "otp_purpose": purpose,
            "otp_expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        }},
    )
    return otp


async def _issue_login_tokens(db: AsyncIOMotorDatabase, user: dict, request: Request) -> ApiResponse:
    """Creates the access/refresh token pair and session record for a
    completed login. Shared by the plain-password path and the 2FA-confirm
    path (/login/2fa) so both end up with identical session bookkeeping.
    """
    access_token, _ = security.create_access_token(user["_id"])
    refresh_token, refresh_jti = security.create_refresh_token(user["_id"])
    await db.sessions.insert_one({
        "_id": str(uuid4()),
        "user_id": user["_id"],
        "user_agent": request.headers.get("User-Agent", "Unknown"),
        "ip_address": request.client.host if request.client else "Unknown",
        "refresh_jti": refresh_jti,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "last_used_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    })
    return ApiResponse(data=TokenResponse(access_token=access_token, refresh_token=refresh_token, expires_in=settings.access_token_expire_seconds))


@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def register(request: Request, payload: UserRegister, db: DatabaseDep, email_service: EmailServiceDep):
    if await db.users.find_one({"$or": [{"email": payload.email}, {"username": payload.username}]}):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"success": False, "message": "User already exists"}
        )
    validate_password_strength(payload.password)
    otp = security.generate_otp()
    try:
        await db.users.insert_one({
            "_id": str(uuid4()),
            "username": payload.username,
            "email": payload.email,
            "password_hash": security.hash_password(payload.password),
            "role": "user",
            "is_active": True,
            "is_verified": False,
            "storage_used_bytes": 0,
            "storage_quota_bytes": 5 * 1024 * 1024 * 1024,
            "otp_hash": security.hash_otp(otp),
            "otp_purpose": "verification",
            "otp_expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        })
    except DuplicateKeyError:
        # Two concurrent registrations for the same email/username both pass
        # the find_one check above, then race to insert_one — the loser used
        # to bubble up as an unhandled 500 instead of a normal conflict (#18).
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"success": False, "message": "User already exists"}
        )
    await email_service.send_otp_email(payload.email, otp, "Email Verification")
    return ApiResponse(message="Registration successful. Please verify your email.")


@router.post("/login")
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def login(payload: UserLogin, request: Request, db: DatabaseDep, email_service: EmailServiceDep):
    user = await db.users.find_one({"email": payload.email})
    if not user or not security.verify_password(payload.password, user["password_hash"]):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"success": False, "message": "Incorrect credentials"}
        )
    if not user.get("is_verified"):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"success": False, "message": "Email not verified"}
        )

    if user.get("two_factor_enabled"):
        # /security/2fa/toggle set this flag but nothing downstream ever
        # consulted it, so "2FA" protected nothing (#20). Hold the tokens
        # back until the emailed code is confirmed via POST /auth/login/2fa.
        otp = await _issue_otp(db, user["_id"], "login")
        await email_service.send_otp_email(payload.email, otp, "Login Verification")
        return ApiResponse(
            data={"two_factor_required": True, "email": user["email"]},
            message="Enter the verification code sent to your email to finish logging in.",
        )

    return await _issue_login_tokens(db, user, request)


@router.post("/login/2fa")
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def verify_login_two_factor(request: Request, payload: VerifyOTP, db: DatabaseDep):
    """Completes a login that /auth/login deferred because two_factor_enabled
    was set (#20). Issues the same access/refresh token pair /auth/login
    would have returned directly, once the emailed login OTP checks out.
    """
    user = await db.users.find_one({"email": payload.email})
    if not user or not user.get("otp_hash"):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"success": False, "message": "Invalid OTP"})

    otp_expires_at = _ensure_tz_aware(user.get("otp_expires_at"))
    if (
        not otp_expires_at
        or datetime.now(timezone.utc) > otp_expires_at
        or user.get("otp_purpose") != "login"
        or not security.verify_otp(payload.otp, user["otp_hash"])
    ):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"success": False, "message": "Invalid OTP"})

    await db.users.update_one({"_id": user["_id"]}, {"$set": _OTP_CLEAR_FIELDS})
    return await _issue_login_tokens(db, user, request)


@router.post("/refresh")
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def refresh_token(request: Request, payload: RefreshTokenRequest, db: DatabaseDep):
    try:
        token_data = security.decode_token(payload.refresh_token, "refresh")
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"success": False, "message": "Invalid token"}
        )
    if await db.blacklist.find_one({"jti": token_data["jti"]}):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"success": False, "message": "Token revoked"}
        )
    session = await db.sessions.find_one({"refresh_jti": token_data["jti"], "is_active": True})
    if not session:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"success": False, "message": "Session invalid"}
        )
    await db.blacklist.update_one(
        {"jti": token_data["jti"]},
        {"$set": {"jti": token_data["jti"], "expires_at": datetime.fromtimestamp(token_data["exp"], tz=timezone.utc)}},
        upsert=True
    )
    new_access, _ = security.create_access_token(token_data["sub"])
    new_refresh, new_jti = security.create_refresh_token(token_data["sub"])
    await db.sessions.update_one(
        {"_id": session["_id"]},
        {"$set": {"refresh_jti": new_jti, "last_used_at": datetime.now(timezone.utc)}}
    )
    return ApiResponse(data=TokenResponse(access_token=new_access, refresh_token=new_refresh, expires_in=settings.access_token_expire_seconds))


@router.post("/logout")
async def logout(payload: RefreshTokenRequest, user: CurrentUserDep, db: DatabaseDep):
    if user.get("_jti"):
        await db.blacklist.update_one(
            {"jti": user["_jti"]},
            {"$set": {"jti": user["_jti"], "expires_at": datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)}},
            upsert=True
        )
    try:
        refresh_data = security.decode_token(payload.refresh_token, "refresh")
        await db.blacklist.update_one(
            {"jti": refresh_data["jti"]},
            {"$set": {"jti": refresh_data["jti"], "expires_at": datetime.fromtimestamp(refresh_data["exp"], tz=timezone.utc)}},
            upsert=True
        )
        await db.sessions.update_one({"refresh_jti": refresh_data["jti"]}, {"$set": {"is_active": False}})
    except Exception:
        pass
    return ApiResponse(message="Logged out successfully")


@router.post("/send-otp")
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def send_otp(request: Request, payload: OTPRequest, db: DatabaseDep, email_service: EmailServiceDep):
    user = await db.users.find_one({"email": payload.email})
    if user and not user.get("is_verified"):
        otp = await _issue_otp(db, user["_id"], "verification")
        # payload.purpose is free text from the client — used only as
        # display copy for the email heading/subject (HTML-escaped inside
        # send_otp_email), never as the security-critical purpose tag above.
        await email_service.send_otp_email(payload.email, otp, payload.purpose)
    return ApiResponse(message="If the email exists, an OTP has been sent.")


@router.post("/verify-otp")
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def verify_otp(request: Request, payload: VerifyOTP, db: DatabaseDep):
    user = await db.users.find_one({"email": payload.email})
    if not user or not user.get("otp_hash"):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"success": False, "message": "Invalid OTP"})

    otp_expires_at = _ensure_tz_aware(user.get("otp_expires_at"))
    if (
        not otp_expires_at
        or datetime.now(timezone.utc) > otp_expires_at
        or user.get("otp_purpose") != "verification"
        or not security.verify_otp(payload.otp, user["otp_hash"])
    ):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"success": False, "message": "Invalid OTP"})

    await db.users.update_one({"_id": user["_id"]}, {"$set": {"is_verified": True, **_OTP_CLEAR_FIELDS}})
    return ApiResponse(message="Email verified successfully.")


@router.post("/forgot-password")
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def forgot_password(request: Request, payload: PasswordResetRequest, db: DatabaseDep, email_service: EmailServiceDep):
    user = await db.users.find_one({"email": payload.email})
    if user:
        otp = await _issue_otp(db, user["_id"], "password_reset")
        await email_service.send_otp_email(payload.email, otp, "Password Reset")
    return ApiResponse(message="If the email exists, a reset OTP has been sent.")


@router.post("/reset-password")
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def reset_password(request: Request, payload: PasswordResetConfirm, db: DatabaseDep):
    user = await db.users.find_one({"email": payload.email})
    if not user or not user.get("otp_hash"):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"success": False, "message": "Invalid OTP"})

    otp_expires_at = _ensure_tz_aware(user.get("otp_expires_at"))
    if (
        not otp_expires_at
        or datetime.now(timezone.utc) > otp_expires_at
        or user.get("otp_purpose") != "password_reset"
        or not security.verify_otp(payload.otp, user["otp_hash"])
    ):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"success": False, "message": "Invalid OTP"})

    validate_password_strength(payload.new_password)
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": security.hash_password(payload.new_password), **_OTP_CLEAR_FIELDS}},
    )
    await db.sessions.update_many({"user_id": user["_id"], "is_active": True}, {"$set": {"is_active": False}})
    return ApiResponse(message="Password reset successful.")


@router.get("/me")
async def get_me(user: CurrentUserDep):
    return ApiResponse(data=UserProfile(**user))


@router.delete("/sessions")
async def delete_all_sessions(user: CurrentUserDep, db: DatabaseDep):
    await db.sessions.update_many({"user_id": user["_id"], "is_active": True}, {"$set": {"is_active": False}})
    if user.get("_jti"):
        await db.blacklist.update_one(
            {"jti": user["_jti"]},
            {"$set": {"jti": user["_jti"], "expires_at": datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)}},
            upsert=True
        )
    return ApiResponse(message="All sessions revoked")

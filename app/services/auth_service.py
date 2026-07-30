"""Authentication and user session management service."""

from datetime import datetime, timezone, timedelta
from typing import Optional
from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import get_settings
from app.core.security import security
from app.services.email_service import email_service
from app.models.schemas import (
    UserRegister,
    UserLogin,
    VerifyOTP,
    PasswordResetRequest,
    PasswordResetConfirm,
    OTPRequest,
    UserProfile,
)

settings = get_settings()
OTP_EXPIRE_MINUTES = 10


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def register_user(self, req: UserRegister) -> dict:
        existing = await self.db.users.find_one({"email": req.email})
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists."
            )

        otp = security.generate_otp()
        otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRE_MINUTES)

        user_doc = {
            "username": req.username,
            "email": req.email,
            "password_hash": security.hash_password(req.password),
            "is_verified": False,
            "is_active": True,
            "role": "user",
            "storage_used_bytes": 0,
            "storage_quota_bytes": 5 * 1024 * 1024 * 1024,
            "otp_code": otp,
            "otp_expires_at": otp_expires_at,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

        res = await self.db.users.insert_one(user_doc)
        await email_service.send_otp_email(
            email=req.email,
            otp=otp,
            purpose="verification"
        )

        return {
            "message": "Registration successful. Please verify the OTP sent to your email.",
            "user_id": str(res.inserted_id)
        }

    async def login_user(self, req: UserLogin, user_agent: str, ip_address: Optional[str] = None) -> dict:
        user = await self.db.users.find_one({"email": req.email})
        if not user or not security.verify_password(req.password, user["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password."
            )

        if not user.get("is_verified", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account not verified. Please verify your email via OTP."
            )

        if not user.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive."
            )

        user_id = str(user["_id"])
        access_token, access_jti = security.create_access_token(
            subject=user_id,
            extra_claims={"role": user.get("role", "user")}
        )
        refresh_token, refresh_jti = security.create_refresh_token(subject=user_id)

        session_doc = {
            "user_id": user_id,
            "refresh_jti": refresh_jti,
            "access_jti": access_jti,
            "refresh_token": refresh_token,
            "user_agent": user_agent,
            "ip_address": ip_address or "unknown",
            "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        }
        await self.db.sessions.insert_one(session_doc)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.access_token_expire_seconds
        }

    async def refresh_tokens(self, refresh_token: str) -> dict:
        payload = security.decode_token(refresh_token, expected_type="refresh")
        user_id = payload.get("sub")
        refresh_jti = payload.get("jti")

        session = await self.db.sessions.find_one({"user_id": user_id, "refresh_jti": refresh_jti})
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired or token revoked."
            )

        user = await self.db.users.find_one({"_id": user_id})
        if not user:
            try:
                user = await self.db.users.find_one({"_id": ObjectId(user_id)})
            except Exception:
                pass

        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

        new_access_token, new_access_jti = security.create_access_token(
            subject=user_id, 
            extra_claims={"role": user.get("role", "user")}
        )
        new_refresh_token, new_refresh_jti = security.create_refresh_token(subject=user_id)

        await self.db.sessions.update_one(
            {"_id": session["_id"]},
            {
                "$set": {
                    "refresh_token": new_refresh_token,
                    "refresh_jti": new_refresh_jti,
                    "access_jti": new_access_jti,
                    "created_at": datetime.now(timezone.utc)
                }
            }
        )

        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
            "expires_in": settings.access_token_expire_seconds
        }

    async def send_otp(self, req: OTPRequest) -> dict:
        user = await self.db.users.find_one({"email": req.email})
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User email not registered.")

        otp = security.generate_otp()
        otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRE_MINUTES)

        await self.db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"otp_code": otp, "otp_expires_at": otp_expires_at}}
        )

        await email_service.send_otp_email(
            email=req.email,
            otp=otp,
            purpose=req.purpose
        )

        return {"message": "OTP code has been sent to your email."}

    async def verify_otp(self, req: VerifyOTP) -> dict:
        user = await self.db.users.find_one({"email": req.email})
        if not user or user.get("otp_code") != req.otp:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP code.")

        expires_at = user.get("otp_expires_at")
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at and datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP code has expired.")

        await self.db.users.update_one(
            {"_id": user["_id"]},
            {
                "$set": {"is_verified": True},
                "$unset": {"otp_code": "", "otp_expires_at": ""}
            }
        )

        return {"message": "Email verified successfully."}

    async def forgot_password(self, req: PasswordResetRequest) -> dict:
        user = await self.db.users.find_one({"email": req.email})
        if not user:
            return {"message": "If the email is registered, a reset OTP has been sent."}

        otp = security.generate_otp()
        otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRE_MINUTES)

        await self.db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"reset_otp_code": otp, "reset_otp_expires_at": otp_expires_at}}
        )

        await email_service.send_otp_email(
            email=req.email,
            otp=otp,
            purpose="password_reset"
        )

        return {"message": "If the email is registered, a reset OTP has been sent."}

    async def reset_password(self, req: PasswordResetConfirm) -> dict:
        user = await self.db.users.find_one({"email": req.email})
        if not user or user.get("reset_otp_code") != req.otp:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset OTP code.")

        expires_at = user.get("reset_otp_expires_at")
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at and datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset OTP code has expired.")

        new_password_hash = security.hash_password(req.new_password)

        await self.db.users.update_one(
            {"_id": user["_id"]},
            {
                "$set": {"password_hash": new_password_hash},
                "$unset": {"reset_otp_code": "", "reset_otp_expires_at": ""}
            }
        )
        await self.db.sessions.delete_many({"user_id": str(user["_id"])})

        return {"message": "Password reset successfully. All existing sessions logged out."}

    async def logout_user(self, user_id: str, refresh_token: str) -> dict:
        res = await self.db.sessions.delete_one({"user_id": user_id, "refresh_token": refresh_token})
        if res.deleted_count == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session not found or already logged out.")
        return {"message": "Logged out successfully."}

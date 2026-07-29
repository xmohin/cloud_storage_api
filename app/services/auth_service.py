from datetime import datetime, timezone, timedelta
from typing import List, Optional
from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.utils.hashing import generate_otp
from app.services.email_service import email_service
from app.models.schemas import (
    UserRegisterRequest,
    LoginRequest,
    OTPVerifyRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SendOTPRequest,
    UserProfileResponse,
    SessionResponse,
)


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def register_user(self, req: UserRegisterRequest) -> dict:
        existing = await self.db.users.find_one({"email": req.email})
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists."
            )

        otp = generate_otp()
        otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

        user_doc = {
            "email": req.email,
            "password_hash": hash_password(req.password),
            "full_name": req.full_name,
            "is_verified": False,
            "role": "user",
            "storage_used_bytes": 0,
            "otp_code": otp,
            "otp_expires_at": otp_expires_at,
            "created_at": datetime.now(timezone.utc),
        }

        res = await self.db.users.insert_one(user_doc)
        await email_service.send_otp_email(
            recipient_email=req.email,
            otp=otp,
            subject="Gallery Vault - Account Verification Code"
        )

        return {
            "message": "Registration successful. Please verify the OTP sent to your email.",
            "user_id": str(res.inserted_id)
        }

    async def login_user(self, req: LoginRequest, user_agent: str, ip_address: Optional[str] = None) -> dict:
        user = await self.db.users.find_one({"email": req.email})
        if not user or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password."
            )

        if not user.get("is_verified", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account not verified. Please verify your email via OTP."
            )

        user_id = str(user["_id"])
        access_token = create_access_token({"sub": user_id, "role": user.get("role", "user")})
        refresh_token = create_refresh_token({"sub": user_id})

        session_doc = {
            "user_id": user_id,
            "refresh_token": refresh_token,
            "user_agent": user_agent,
            "ip_address": ip_address or "unknown",
            "created_at": datetime.now(timezone.utc),
        }
        await self.db.sessions.insert_one(session_doc)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    async def refresh_tokens(self, refresh_token: str) -> dict:
        try:
            payload = decode_token(refresh_token, is_refresh=True)
            user_id = payload.get("sub")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token."
            )

        session = await self.db.sessions.find_one({"user_id": user_id, "refresh_token": refresh_token})
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired or token revoked."
            )

        user = await self.db.users.find_one({"_id": ObjectId(user_id)})
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

        new_access_token = create_access_token({"sub": user_id, "role": user.get("role", "user")})
        new_refresh_token = create_refresh_token({"sub": user_id})

        # Refresh token rotation
        await self.db.sessions.update_one(
            {"_id": session["_id"]},
            {"$set": {"refresh_token": new_refresh_token, "created_at": datetime.now(timezone.utc)}}
        )

        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        }

    async def send_otp(self, req: SendOTPRequest) -> dict:
        user = await self.db.users.find_one({"email": req.email})
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User email not registered.")

        otp = generate_otp()
        otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

        await self.db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"otp_code": otp, "otp_expires_at": otp_expires_at}}
        )

        await email_service.send_otp_email(
            recipient_email=req.email,
            otp=otp,
            subject="Gallery Vault - Verification Code"
        )

        return {"message": "OTP code has been sent to your email."}

    async def verify_otp(self, req: OTPVerifyRequest) -> dict:
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

    async def forgot_password(self, req: ForgotPasswordRequest) -> dict:
        user = await self.db.users.find_one({"email": req.email})
        if not user:
            # Prevent user enumeration attack
            return {"message": "If the email is registered, a reset OTP has been sent."}

        otp = generate_otp()
        otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

        await self.db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"reset_otp_code": otp, "reset_otp_expires_at": otp_expires_at}}
        )

        await email_service.send_otp_email(
            recipient_email=req.email,
            otp=otp,
            subject="Gallery Vault - Password Reset Code"
        )

        return {"message": "If the email is registered, a reset OTP has been sent."}

    async def reset_password(self, req: ResetPasswordRequest) -> dict:
        user = await self.db.users.find_one({"email": req.email})
        if not user or user.get("reset_otp_code") != req.otp:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset OTP code.")

        expires_at = user.get("reset_otp_expires_at")
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at and datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset OTP code has expired.")

        new_password_hash = hash_password(req.new_password)

        # Update password & invalidate all existing user sessions for security
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

    async def delete_all_sessions(self, user_id: str) -> dict:
        res = await self.db.sessions.delete_many({"user_id": user_id})
        return {"message": f"Successfully terminated {res.deleted_count} active session(s)."}

    async def get_current_user_profile(self, user_data: dict) -> UserProfileResponse:
        return UserProfileResponse(
            id=user_data["id"],
            email=user_data["email"],
            full_name=user_data["full_name"],
            is_verified=user_data.get("is_verified", False),
            role=user_data.get("role", "user"),
            storage_used_bytes=user_data.get("storage_used_bytes", 0),
            created_at=user_data["created_at"],
        )

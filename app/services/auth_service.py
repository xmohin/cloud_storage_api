from datetime import datetime, timezone
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token, decode_token
from app.utils.hashing import generate_otp
from app.services.email_service import email_service
from app.models.schemas import UserRegisterRequest, LoginRequest, OTPVerifyRequest, ForgotPasswordRequest, ResetPasswordRequest
from bson import ObjectId

class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def register_user(self, req: UserRegisterRequest):
        existing = await self.db.users.find_one({"email": req.email})
        if existing:
            raise HTTPException(status_code=400, detail="User already exists.")
        
        otp = generate_otp()
        user_doc = {
            "email": req.email,
            "password_hash": hash_password(req.password),
            "full_name": req.full_name,
            "is_verified": False,
            "role": "user",
            "storage_used_bytes": 0,
            "otp_code": otp,
            "otp_expires_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc)
        }
        res = await self.db.users.insert_one(user_doc)
        await email_service.send_otp_email(req.email, otp, "Verify your Gallery Vault Account")
        return {"message": "Registration successful. Please check your email for the activation OTP.", "user_id": str(res.inserted_id)}

    async def verify_otp(self, req: OTPVerifyRequest):
        user = await self.db.users.find_one({"email": req.email, "otp_code": req.otp})
        if not user:
            raise HTTPException(status_code=400, detail="Invalid OTP code.")
        
        await self.db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"is_verified": True}, "$unset": {"otp_code": "", "otp_expires_at": ""}}
        )
        return {"message": "Email verified successfully."}

    async def login_user(self, req: LoginRequest, user_agent: str):
        user = await self.db.users.find_one({"email": req.email})
        if not user or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials.")
        if not user.get("is_verified", False):
            raise HTTPException(status_code=403, detail="Account not verified.")
        
        user_id = str(user["_id"])
        access_token = create_access_token({"sub": user_id, "role": user["role"]})
        refresh_token = create_refresh_token({"sub": user_id})
        
        session_doc = {
            "user_id": user_id,
            "refresh_token": refresh_token,
            "user_agent": user_agent,
            "created_at": datetime.now(timezone.utc)
        }
        await self.db.sessions.insert_one(session_doc)
        return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}

    async def refresh_tokens(self, refresh_token: str):
        payload = decode_token(refresh_token, is_refresh=True)
        user_id = payload.get("sub")
        
        session = await self.db.sessions.find_one({"user_id": user_id, "refresh_token": refresh_token})
        if not session:
            raise HTTPException(status_code=401, detail="Invalid refresh token or session expired.")
        
        new_access_token = create_access_token({"sub": user_id, "role": "user"})
        new_refresh_token = create_refresh_token({"sub": user_id})
        
        await self.db.sessions.update_one(
            {"_id": session["_id"]},
            {"$set": {"refresh_token": new_refresh_token, "created_at": datetime.now(timezone.utc)}}
        )
        return {"access_token": new_access_token, "refresh_token": new_refresh_token, "token_type": "bearer"}

    async def logout_user(self, user_id: str, refresh_token: str):
        await self.db.sessions.delete_one({"user_id": user_id, "refresh_token": refresh_token})
        return {"message": "Logged out successfully."}

    async def logout_all_devices(self, user_id: str):
        await self.db.sessions.delete_many({"user_id": user_id})
        return {"message": "Logged out from all devices successfully."}

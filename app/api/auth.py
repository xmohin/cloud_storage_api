from fastapi import APIRouter, Depends, Header
from app.models.schemas import UserRegisterRequest, LoginRequest, OTPVerifyRequest, TokenResponse, ForgotPasswordRequest, ResetPasswordRequest
from app.services.auth_service import AuthService
from app.core.database import get_database
from app.api.dependencies import get_current_user
from motor.motor_asyncio import AsyncIOMotorDatabase

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/register")
async def register(req: UserRegisterRequest, db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.register_user(req)

@router.post("/verify-otp")
async def verify_otp(req: OTPVerifyRequest, db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.verify_otp(req)

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, user_agent: str = Header(default="unknown"), db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.login_user(req, user_agent)

@router.post("/refresh", response_model=TokenResponse)
async def refresh(refresh_token: str, db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.refresh_tokens(refresh_token)

@router.post("/logout")
async def logout(refresh_token: str, current_user: dict = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.logout_user(current_user["id"], refresh_token)

@router.post("/logout-all")
async def logout_all(current_user: dict = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.logout_all_devices(current_user["id"])

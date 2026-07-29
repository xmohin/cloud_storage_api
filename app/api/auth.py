from fastapi import APIRouter, Depends, Header, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.database import get_database
from app.api.dependencies import get_current_user
from app.services.auth_service import AuthService
from app.models.schemas import (
    UserRegisterRequest,
    LoginRequest,
    SendOTPRequest,
    OTPVerifyRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserProfileResponse,
    MessageResponse,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@router.post("/register", response_model=dict, status_code=status.HTTP_201_CREATED)
async def register(req: UserRegisterRequest, db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.register_user(req)


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    user_agent: str = Header(default="unknown"),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    ip_address = request.client.host if request.client else "unknown"
    service = AuthService(db)
    return await service.login_user(req, user_agent, ip_address)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    req: RefreshTokenRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    service = AuthService(db)
    return await service.logout_user(current_user["id"], req.refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(req: RefreshTokenRequest, db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.refresh_tokens(req.refresh_token)


@router.post("/send-otp", response_model=MessageResponse)
async def send_otp(req: SendOTPRequest, db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.send_otp(req)


@router.post("/verify-otp", response_model=MessageResponse)
async def verify_otp(req: OTPVerifyRequest, db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.verify_otp(req)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(req: ForgotPasswordRequest, db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.forgot_password(req)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(req: ResetPasswordRequest, db: AsyncIOMotorDatabase = Depends(get_database)):
    service = AuthService(db)
    return await service.reset_password(req)


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user_info(
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    service = AuthService(db)
    return await service.get_current_user_profile(current_user)


@router.delete("/sessions", response_model=MessageResponse)
async def delete_all_sessions(
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    service = AuthService(db)
    return await service.delete_all_sessions(current_user["id"])

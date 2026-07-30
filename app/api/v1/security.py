"""Security utility functions for authentication, hashing, and tokens."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple
import jwt
from passlib.context import CryptContext
from app.core.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class SecurityService:
    @staticmethod
    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def generate_otp(length: int = 6) -> str:
        return "".join([str(secrets.randbelow(10)) for _ in range(length)])

    @staticmethod
    def hash_otp(otp: str) -> str:
        return hmac.new(
            settings.SECRET_KEY.encode(),
            otp.encode(),
            hashlib.sha256
        ).hexdigest()

    @staticmethod
    def verify_otp(plain_otp: str, hashed_otp: str) -> bool:
        return hmac.compare_digest(SecurityService.hash_otp(plain_otp), hashed_otp)

    @staticmethod
    def create_access_token(subject: str) -> Tuple[str, str]:
        jti = secrets.token_hex(16)
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode = {
            "sub": str(subject),
            "exp": expire,
            "jti": jti,
            "type": "access"
        }
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        return encoded_jwt, jti

    @staticmethod
    def create_refresh_token(subject: str) -> Tuple[str, str]:
        jti = secrets.token_hex(16)
        expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        to_encode = {
            "sub": str(subject),
            "exp": expire,
            "jti": jti,
            "type": "refresh"
        }
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        return encoded_jwt, jti

    @staticmethod
    def decode_token(token: str, expected_type: str = "access") -> Dict[str, Any]:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != expected_type:
            raise ValueError("Invalid token type")
        return payload

    @staticmethod
    def generate_share_code(length: int = 8) -> str:
        return secrets.token_urlsafe(length)[:length]


security = SecurityService()

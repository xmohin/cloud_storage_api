"""Security utilities — JWT tokens, password hashing, and OTPs."""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
import hashlib
import secrets
import bcrypt
import jwt
from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.core.config import get_settings

settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


class SecurityManager:
    @staticmethod
    def _prehash(password: str) -> bytes:
        """SHA-256 pre-hash before bcrypt.

        bcrypt only looks at the first 72 bytes of its input and silently
        ignores the rest — a 100-character password and its first-72-bytes
        prefix would hash identically. Pre-hashing with SHA-256 (a fixed
        32-byte digest, well under the 72-byte cap) folds the *entire*
        password into the bcrypt input so no entropy from long passwords is
        ever dropped. This is the same technique used by e.g. Django's
        BCryptSHA256PasswordHasher.
        """
        return hashlib.sha256(password.encode("utf-8")).digest()

    @staticmethod
    def hash_password(password: str) -> str:
        """Hashes a password using native bcrypt (with SHA-256 pre-hash)."""
        pwd_bytes = SecurityManager._prehash(password)
        rounds = getattr(settings, "BCRYPT_ROUNDS", 12)
        salt = bcrypt.gensalt(rounds=rounds)
        return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verifies a plain password against a bcrypt hash safely."""
        try:
            return bcrypt.checkpw(
                SecurityManager._prehash(plain_password),
                hashed_password.encode("utf-8")
            )
        except (ValueError, TypeError, AttributeError):
            return False

    @staticmethod
    def create_access_token(
        subject: str, extra_claims: dict[str, Any] | None = None
    ) -> tuple[str, str]:
        """Creates a JWT access token."""
        now = datetime.now(timezone.utc)
        expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        jti = str(uuid4())
        payload: dict[str, Any] = {
            "sub": subject,
            "iat": now,
            "exp": expire,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "type": "access",
            "jti": jti,
        }
        if extra_claims:
            payload.update(extra_claims)
        
        token = jwt.encode(
            payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )
        return token, jti

    @staticmethod
    def create_refresh_token(subject: str) -> tuple[str, str]:
        """Creates a JWT refresh token using the dedicated refresh secret key."""
        now = datetime.now(timezone.utc)
        expire = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        jti = str(uuid4())
        secret_key = getattr(
            settings, "JWT_REFRESH_SECRET_KEY", settings.JWT_SECRET_KEY
        )
        payload: dict[str, Any] = {
            "sub": subject,
            "iat": now,
            "exp": expire,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "type": "refresh",
            "jti": jti,
        }
        token = jwt.encode(
            payload, secret_key, algorithm=settings.JWT_ALGORITHM
        )
        return token, jti

    @staticmethod
    def decode_token(
        token: str, expected_type: str | None = None
    ) -> dict[str, Any]:
        """Decodes and validates a JWT token."""
        # Select correct secret key based on token type
        secret_key = (
            getattr(settings, "JWT_REFRESH_SECRET_KEY", settings.JWT_SECRET_KEY)
            if expected_type == "refresh"
            else settings.JWT_SECRET_KEY
        )

        try:
            payload = jwt.decode(
                token,
                secret_key,
                algorithms=[settings.JWT_ALGORITHM],
                issuer=settings.JWT_ISSUER,
                audience=settings.JWT_AUDIENCE,
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if expected_type and payload.get("type") != expected_type:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Expected {expected_type} token",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        return payload

    @staticmethod
    def generate_otp() -> str:
        """Generates a cryptographically secure 6-digit OTP."""
        return f"{secrets.randbelow(1000000):06d}"

    @staticmethod
    def hash_otp(otp: str) -> str:
        """Hashes an OTP using native bcrypt."""
        return SecurityManager.hash_password(otp)

    @staticmethod
    def verify_otp(plain_otp: str, hashed_otp: str) -> bool:
        """Verifies an OTP against its hashed counterpart."""
        return SecurityManager.verify_password(plain_otp, hashed_otp)

    @staticmethod
    def generate_share_code() -> str:
        """Generates a cryptographically secure, URL-safe share token."""
        return secrets.token_urlsafe(16)


security = SecurityManager()

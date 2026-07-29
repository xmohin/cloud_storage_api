from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError

from app.core.config import settings
from app.core.database import db_manager
from app.core.security import decode_access_token
from app.models.schemas import TokenPayload

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")

async def get_db() -> AsyncIOMotorDatabase:
    """Dependency to inject the database instance."""
    return db_manager.get_db()

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncIOMotorDatabase = Depends(get_db)
) -> dict:
    """
    Validates the JWT token and retrieves the current user.
    Returns a dictionary representing the user document.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = decode_access_token(token)
        token_data = TokenPayload(**payload)
        
        if token_data.sub is None:
            raise credentials_exception
            
    except (InvalidTokenError, ValidationError):
        raise credentials_exception
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    user_id = token_data.sub
    # Fetch user from DB (assuming a 'users' collection exists)
    user = await db["users"].find_one({"_id": user_id})
    
    if user is None:
        raise credentials_exception
        
    if not user.get("is_active", True):
        raise HTTPException(status_code=400, detail="Inactive user")
        
    return user

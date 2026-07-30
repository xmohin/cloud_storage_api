"""Security, 2FA, and Audit Log Endpoints."""

from typing import Any
from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import CurrentUserDep, DatabaseDep

# 🔑 'router' অবজেক্টটি সঠিকভাবে এক্সপোর্ট করা নিশ্চিত করুন
router = APIRouter(prefix="/security", tags=["Security"])


@router.get("/status", status_code=status.HTTP_200_OK)
async def get_security_status(
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Get current user's security overview (2FA status, last login, active sessions)."""
    return {
        "status": "success",
        "data": {
            "user_id": str(current_user.get("_id")),
            "email": current_user.get("email"),
            "is_active": current_user.get("is_active", True),
            "two_factor_enabled": current_user.get("two_factor_enabled", False),
            "last_login": current_user.get("last_login"),
        },
    }


@router.post("/2fa/toggle", status_code=status.HTTP_200_OK)
async def toggle_two_factor_auth(
    current_user: CurrentUserDep,
    database: DatabaseDep,
) -> dict[str, Any]:
    """Toggle 2FA state for the current authenticated user."""
    current_state = current_user.get("two_factor_enabled", False)
    new_state = not current_state

    await database.users.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"two_factor_enabled": new_state}},
    )

    return {
        "status": "success",
        "message": f"Two-factor authentication {'enabled' if new_state else 'disabled'}.",
        "two_factor_enabled": new_state,
    }

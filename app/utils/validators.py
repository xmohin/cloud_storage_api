"""Input validator and sanitizer functions."""

import os
import re
from fastapi import HTTPException, status


def validate_filename(filename: str) -> str:
    """
    Sanitizes and validates a filename to prevent Path Traversal,
    Null Byte Injections, and unsafe OS characters.
    """
    if not filename or not isinstance(filename, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename must be a non-empty string."
        )

    # 1. Extract base filename to neutralize path traversal (e.g., ../../secret.txt)
    clean_name = os.path.basename(filename.strip())

    # 2. Remove null bytes, control characters, and unsafe chars
    sanitized = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", clean_name).strip(". ")

    # 3. Handle OS reserved names (Windows & Unix system conflicts)
    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
    }
    file_stem = sanitized.split('.')[0].upper()
    if file_stem in reserved_names:
        sanitized = f"file_{sanitized}"

    # 4. Final boundary length check
    if not sanitized or len(sanitized) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unsafe filename format."
        )

    return sanitized


def validate_bcrypt_length(password: str) -> str:
    """Rejects input whose UTF-8 byte length exceeds bcrypt's 72-byte limit.

    bcrypt (native, pre-4.0) only hashes the first 72 bytes and silently
    ignores the rest. Character count isn't a safe proxy for this since
    multi-byte UTF-8 characters (accents, emoji, etc.) inflate byte length
    faster than character length — reject upfront instead of letting two
    different long inputs hash identically. Shared by account password
    validation and anything else that feeds a value into security.hash_password
    (e.g. share-link passwords), since the underlying bcrypt limit is the same
    either way.
    """
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must not exceed 72 bytes (some characters, like accents or emoji, count as more than one byte)."
        )
    return password


def validate_password_strength(password: str) -> str:
    """Validates password strength rules."""
    if len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters long."
        )
    if not re.search(r"[A-Z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one uppercase letter."
        )
    if not re.search(r"[a-z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one lowercase letter."
        )
    if not re.search(r"[0-9]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one number."
        )
    validate_bcrypt_length(password)
    return password

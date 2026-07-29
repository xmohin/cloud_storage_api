import re
from fastapi import HTTPException, status

def validate_filename(filename: str) -> str:
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
    if not sanitized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename format.")
    return sanitized

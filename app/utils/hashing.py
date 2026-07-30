import hashlib
import secrets
import aiofiles


async def compute_stream_sha256(file_path: str) -> str:
    """Async SHA-256 hash calculator to prevent blocking the event loop."""
    sha256_hash = hashlib.sha256()
    async with aiofiles.open(file_path, "rb") as f:
        while chunk := await f.read(65536):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def generate_random_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"

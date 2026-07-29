import hashlib
import secrets

def compute_stream_sha256(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def generate_random_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)

def generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000}"

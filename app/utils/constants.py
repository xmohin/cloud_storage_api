from enum import Enum

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"

class UploadStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class RetentionPolicy:
    TRASH_RETENTION_DAYS = 30
    CHUNK_EXPIRATION_HOURS = 24

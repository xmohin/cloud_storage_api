"""
Smoke tests for the LOW-severity fixes (review items #21-#28).

Same conventions as test_smoke.py / test_medium_fixes.py: in-memory Mongo via
mongomock-motor, Telegram/email network calls stubbed via the
`client`/`mock_db`/`app` fixtures in conftest.py. Run locally with:

    pip install -r requirements.txt -r requirements-dev.txt
    pytest tests/ -v
"""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


# ── helpers (mirrors test_medium_fixes.py) ──
async def _register_and_verify(client, mock_db, email="user1@example.com", username="user1", password="Sup3rSecret!"):
    r = await client.post("/api/v1/auth/register", json={
        "username": username, "email": email, "password": password,
    })
    assert r.status_code == 201, r.text
    await mock_db.users.update_one({"email": email}, {"$set": {"is_verified": True}})


async def _login(client, email="user1@example.com", password="Sup3rSecret!"):
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _file_doc(owner_id, **overrides):
    doc = {
        "_id": str(uuid4()), "owner_id": owner_id, "parent_id": None,
        "original_name": "photo.jpg", "file_type": "image", "mime_type": "image/jpeg",
        "size_bytes": 1234, "file_hash": str(uuid4()), "is_folder": False,
        "is_favorite": False, "status": "completed", "telegram_message_id": 42,
        "thumbnail_message_id": None, "deleted_at": None, "tags": [],
        "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
    }
    doc.update(overrides)
    return doc


def _user_doc(**overrides):
    now = datetime.now(timezone.utc)
    doc = {
        "_id": str(uuid4()), "username": f"user_{uuid4().hex[:8]}",
        "email": f"{uuid4().hex[:8]}@example.com", "password_hash": "x",
        "role": "user", "is_active": True, "is_verified": True,
        "storage_used_bytes": 0, "storage_quota_bytes": 5 * 1024 * 1024 * 1024,
        "created_at": now, "updated_at": now,
    }
    doc.update(overrides)
    return doc


# ── #21 rate limiter storage is configurable, and warns when the unsafe
#    memory:// default is paired with rate limiting enabled ──
async def test_rate_limit_storage_uri_configurable_and_warns_when_unsafe(monkeypatch):
    from app.core import middleware as middleware_module
    from app.core.config import get_settings

    assert get_settings().RATE_LIMIT_STORAGE_URI == "memory://"

    class _FakeLogger:
        def __init__(self):
            self.warnings = []

        def warning(self, *args, **kwargs):
            self.warnings.append((args, kwargs))

    fake_logger = _FakeLogger()
    monkeypatch.setattr(middleware_module, "logger", fake_logger)
    monkeypatch.setattr(middleware_module.settings, "RATE_LIMIT_ENABLED", True)

    monkeypatch.setattr(middleware_module.settings, "RATE_LIMIT_STORAGE_URI", "memory://")
    middleware_module.warn_if_rate_limit_storage_unsafe()
    assert fake_logger.warnings, (
        "expected a warning when RATE_LIMIT_ENABLED and storage is still memory:// (#21)"
    )

    fake_logger.warnings.clear()
    monkeypatch.setattr(middleware_module.settings, "RATE_LIMIT_STORAGE_URI", "redis://localhost:6379")
    middleware_module.warn_if_rate_limit_storage_unsafe()
    assert not fake_logger.warnings, "a shared backend must not trigger the memory:// warning (#21)"


# ── #22 notifications go through the Notification schema, and notif_id is a
#    path param, not a query param ──
async def test_notifications_returned_via_schema_and_notif_id_is_path_param(client, mock_db):
    await _register_and_verify(client, mock_db, email="notify@example.com", username="notifyuser")
    tokens = await _login(client, email="notify@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    notif_id = str(uuid4())
    await mock_db.notifications.insert_one({
        "_id": notif_id, "user_id": me["id"], "message": "Welcome!",
        "is_read": False, "created_at": datetime.now(timezone.utc),
    })

    r = await client.get("/api/v1/notifications", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert len(body) == 1
    assert body[0]["id"] == notif_id, "list must go through the Notification schema, not a raw Mongo doc (#22)"
    assert body[0]["message"] == "Welcome!"
    assert body[0]["is_read"] is False

    # notif_id must now be a path param, not ?notif_id=... (#22)
    r = await client.put(f"/api/v1/notifications/{notif_id}/read", headers=headers)
    assert r.status_code == 200, r.text
    updated = await mock_db.notifications.find_one({"_id": notif_id})
    assert updated["is_read"] is True

    r = await client.delete(f"/api/v1/notifications/{notif_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert await mock_db.notifications.find_one({"_id": notif_id}) is None


async def test_notifications_clear_route_is_not_shadowed_by_notif_id_route(client, mock_db):
    """Regression check for the reordering this fix required: /{notif_id}
    was registered before /clear, so DELETE /notifications/clear used to
    match delete_notification(notif_id="clear") instead of clear_notifications
    — a silent no-op that left every notification in place (#22)."""
    await _register_and_verify(client, mock_db, email="clearnotify@example.com", username="clearnotifyuser")
    tokens = await _login(client, email="clearnotify@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    for _ in range(3):
        await mock_db.notifications.insert_one({
            "_id": str(uuid4()), "user_id": me["id"], "message": "hi",
            "is_read": False, "created_at": datetime.now(timezone.utc),
        })

    r = await client.delete("/api/v1/notifications/clear", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Cleared", "must hit clear_notifications, not delete_notification(notif_id='clear') (#22)"
    remaining = await mock_db.notifications.count_documents({"user_id": me["id"]})
    assert remaining == 0, "all of the user's notifications should be gone after /clear (#22)"


# ── #23 admin user/file listings are paginated instead of hard-capped at 100 ──
async def test_admin_list_users_is_paginated(client, mock_db):
    await _register_and_verify(client, mock_db, email="admin1@example.com", username="admin1")
    await mock_db.users.update_one({"email": "admin1@example.com"}, {"$set": {"role": "admin"}})
    tokens = await _login(client, email="admin1@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # 21 more users, plus the admin itself = 22 total (default page size is 20)
    for _ in range(21):
        await mock_db.users.insert_one(_user_doc())

    r = await client.get("/api/v1/admin/users?page=1&limit=20", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["total"] == 22, "total must reflect the real count, not be capped (#23)"
    assert len(body["users"]) == 20

    r = await client.get("/api/v1/admin/users?page=2&limit=20", headers=headers)
    assert r.status_code == 200, r.text
    body2 = r.json()["data"]
    assert len(body2["users"]) == 2, "page 2 must return the remaining users past the old hardcoded cap (#23)"


async def test_admin_list_files_is_paginated(client, mock_db):
    await _register_and_verify(client, mock_db, email="admin2@example.com", username="admin2")
    await mock_db.users.update_one({"email": "admin2@example.com"}, {"$set": {"role": "admin"}})
    tokens = await _login(client, email="admin2@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    for _ in range(22):
        await mock_db.files.insert_one(_file_doc(me["id"]))

    r = await client.get("/api/v1/admin/files?page=1&limit=20", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["total"] == 22, "total must reflect the real count, not be capped (#23)"
    assert len(body["files"]) == 20

    r = await client.get("/api/v1/admin/files?page=2&limit=20", headers=headers)
    body2 = r.json()["data"]
    assert len(body2["files"]) == 2, "page 2 must return the remaining files past the old hardcoded cap (#23)"


# ── #24 get_statistics on a vanished user 404s instead of crashing ──
async def test_get_statistics_missing_user_returns_404_not_attributeerror(mock_db):
    from fastapi import HTTPException
    from app.services.file_service import file_service

    with pytest.raises(HTTPException) as exc_info:
        await file_service.get_statistics(mock_db, "does-not-exist")
    assert exc_info.value.status_code == 404, (
        "a token for a since-deleted user must 404, not raise AttributeError "
        "from user.get(...) on None (#24)"
    )


# ── #25 Content-Range is only sent on an actual partial (206) response ──
async def test_stream_full_file_omits_content_range_header(client, mock_db, monkeypatch):
    from app.services import telegram_service as telegram_module

    async def fake_stream_download(message_id, offset=0, limit=0):
        yield b"0123456789"

    monkeypatch.setattr(telegram_module.telegram_service, "stream_download", fake_stream_download)

    await _register_and_verify(client, mock_db, email="stream@example.com", username="streamuser")
    tokens = await _login(client, email="stream@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    file_id = str(uuid4())
    await mock_db.files.insert_one(_file_doc(me["id"], _id=file_id, size_bytes=10))

    # No Range header -> plain 200, and Content-Range must be absent (#25)
    r = await client.get(f"/api/v1/files/{file_id}/stream", headers=headers)
    assert r.status_code == 200, r.text
    assert "content-range" not in r.headers, "a 200 full-content response must not carry Content-Range (#25)"

    # An actual Range request still gets a real 206 + Content-Range
    r2 = await client.get(f"/api/v1/files/{file_id}/stream", headers={**headers, "Range": "bytes=0-4"})
    assert r2.status_code == 206, r2.text
    assert r2.headers.get("content-range") == "bytes 0-4/10"


# ── #26 the finalize task keeps a strong reference so it can't be GC'd mid-run ──
async def test_background_task_reference_is_retained_until_done():
    from app.services.upload_service import UploadService

    svc = UploadService()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def _work():
        started.set()
        await finish.wait()

    task = svc._spawn_background_task(_work())
    await started.wait()
    assert task in svc._background_tasks, (
        "a spawned background task must stay referenced while running — the "
        "event loop only holds a weak ref, so an unreferenced task can be "
        "garbage-collected mid-execution (#26)"
    )

    finish.set()
    await task
    await asyncio.sleep(0)  # let the done-callback run
    assert task not in svc._background_tasks, "a finished task should be dropped from the retention set"


# ── #27 /health goes through a public accessor instead of db._database ──
async def test_health_reports_database_connected_via_public_accessor(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["services"]["database"] == "connected"


def test_is_connected_reflects_database_state(monkeypatch):
    from app.core.database import Database

    monkeypatch.setattr(Database, "_database", None)
    assert Database.is_connected() is False

    monkeypatch.setattr(Database, "_database", object())
    assert Database.is_connected() is True


# ── #28 username uniqueness is enforced case-insensitively ──
async def test_username_index_is_created_with_case_insensitive_collation(monkeypatch):
    from app.core import database as database_module

    class _RecordingCollection:
        def __init__(self):
            self.create_index_calls = []

        async def create_index(self, *args, **kwargs):
            self.create_index_calls.append((args, kwargs))
            return "idx"

        async def drop_index(self, *args, **kwargs):
            return None

    class _RecordingDB:
        def __init__(self):
            self.users = _RecordingCollection()

        def __getattr__(self, name):
            return _RecordingCollection()

    fake_db = _RecordingDB()
    monkeypatch.setattr(database_module.Database, "_database", fake_db)

    captured_collation_kwargs = {}
    real_collation_cls = database_module.Collation

    def _spy_collation(*args, **kwargs):
        captured_collation_kwargs.update(kwargs)
        return real_collation_cls(*args, **kwargs)

    monkeypatch.setattr(database_module, "Collation", _spy_collation)

    await database_module.Database._ensure_indexes()

    username_calls = [c for c in fake_db.users.create_index_calls if c[0] and c[0][0] == "username"]
    assert username_calls, "expected a create_index('username', ...) call"
    args, kwargs = username_calls[0]
    assert kwargs.get("unique") is True
    assert kwargs.get("collation") is not None, "username uniqueness must be case-insensitive (#28)"
    assert captured_collation_kwargs.get("locale") == "en"
    assert captured_collation_kwargs.get("strength") == 2, "strength=2 => case-insensitive comparison (#28)"


async def test_username_index_falls_back_if_case_duplicates_already_exist(monkeypatch):
    """If a deployment already has e.g. both 'User' and 'user', the
    collation index build fails outright — startup must not crash (#28)."""
    from app.core import database as database_module
    from pymongo.errors import OperationFailure

    class _RecordingCollection:
        def __init__(self):
            self.create_index_calls = []

        async def create_index(self, *args, **kwargs):
            self.create_index_calls.append((args, kwargs))
            if kwargs.get("collation") is not None:
                raise OperationFailure("Index build failed: E11000 duplicate key")
            return "idx"

        async def drop_index(self, *args, **kwargs):
            return None

    class _RecordingDB:
        def __init__(self):
            self.users = _RecordingCollection()

        def __getattr__(self, name):
            return _RecordingCollection()

    fake_db = _RecordingDB()
    monkeypatch.setattr(database_module.Database, "_database", fake_db)

    await database_module.Database._ensure_indexes()  # must not raise

    username_calls = [c for c in fake_db.users.create_index_calls if c[0] and c[0][0] == "username"]
    assert len(username_calls) == 2, "expected the collation attempt plus a plain fallback (#28)"
    assert username_calls[0][1].get("collation") is not None
    assert username_calls[1][1].get("collation") is None, "fallback must be the plain case-sensitive index"

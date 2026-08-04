"""
Smoke tests for the fixes applied to xmohin/cloud_storage_api.

Each test is tied to one item from the review report. These use an
in-memory Mongo and stubbed Telegram/email calls, so they check that the
*wiring* is correct, not that your real MongoDB/Telegram credentials work.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


# ── OTP emails silently failing: /health now reports real config status,
#    and a permanently-failed send is now logged instead of vanishing ──
async def test_health_reports_email_not_configured(client):
    # conftest sets a dummy BREVO_API_KEY-less env, so Brevo stays unconfigured
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    services = r.json()["services"]
    assert services["email"] == "not_configured"
    assert r.json()["status"] == "degraded"


async def test_worker_logs_when_email_permanently_fails(monkeypatch):
    from app.services import email_service as email_module

    async def always_fail(*a, **kw):
        return False

    monkeypatch.setattr(email_module.email_client, "send_email", always_fail)
    monkeypatch.setattr(email_module, "MAX_RETRIES", 0)  # any failure exhausts retries immediately

    logged = {}
    def fake_error(msg, **kw):
        logged["msg"] = msg
        logged.update(kw)
    monkeypatch.setattr(email_module.logger, "error", fake_error)

    svc = email_module.EmailService()
    svc._is_running = True
    await svc.queue.put({
        "to_email": "nobody@example.com", "subject": "Test OTP",
        "html_content": "<p>123456</p>", "text_content": None, "retries": 0,
    })
    await svc.queue.put(None)  # stop signal, consumed right after the real task

    await svc._worker()

    assert "permanently failed" in logged.get("msg", "")
    assert logged.get("to_email") == "nobody@example.com"


# ── helpers ──
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


# ── #1 pagination.per_page no longer AttributeErrors, and #13 the
#    FileListResponse shape actually carries per_page/has_next ──
async def test_files_list_pagination_works(client, mock_db):
    await _register_and_verify(client, mock_db, email="pager@example.com", username="pager")
    tokens = await _login(client, email="pager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    r = await client.get("/api/v1/files?page=1&limit=10", headers=headers)
    assert r.status_code == 200, r.text  # pre-fix: 500 AttributeError on .per_page
    assert r.json()["data"]["total"] == 0


# ── .env naming mismatches: TELEGRAM_CHANNEL_ID / ENVIRONMENT / DEBUG /
#    JWT_REFRESH_SECRET_KEY now resolve instead of being silently ignored ──
def test_settings_accepts_alternate_env_var_names(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("JWT_SECRET_KEY", "x")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "-100999")  # not the "real" name
    monkeypatch.setenv("ENVIRONMENT", "production")       # not the "real" name
    monkeypatch.setenv("DEBUG", "true")                    # not the "real" name
    monkeypatch.setenv("JWT_REFRESH_SECRET_KEY", "refresh-secret")

    s = Settings()
    assert s.TELEGRAM_STORAGE_CHANNEL_ID == -100999
    assert s.APP_ENV == "production"
    assert s.APP_DEBUG is True
    assert s.JWT_REFRESH_SECRET_KEY == "refresh-secret"


# ── MAX_UPLOAD_SIZE_MB is now actually enforced ──
async def test_chunked_upload_rejects_oversized_file(client, mock_db):
    await _register_and_verify(client, mock_db, email="uploader@example.com", username="uploader")
    tokens = await _login(client, email="uploader@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    from app.core.config import get_settings
    too_big = get_settings().max_upload_size_bytes + 1

    r = await client.post("/api/v1/files/upload/init", json={
        "filename": "huge.bin", "file_size": too_big,
        "mime_type": "application/octet-stream", "total_chunks": 1,
    }, headers=headers)
    assert r.status_code == 413, r.text


# ── #6 multi-device / repeat login no longer DuplicateKeyError ──
async def test_multi_device_login_does_not_crash(client, mock_db):
    await _register_and_verify(client, mock_db)
    first = await _login(client)
    second = await _login(client)  # second "device" logging in with same user_id
    assert first["access_token"] != second["access_token"]
    sessions = await mock_db.sessions.count_documents({})
    assert sessions == 2, "both sessions should coexist now that user_id isn't uniquely indexed"


# ── #8 logout blacklists the access token ──
async def test_logout_blacklists_access_token(client, mock_db):
    await _register_and_verify(client, mock_db, email="user2@example.com", username="user2")
    tokens = await _login(client, email="user2@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200

    logout = await client.post("/api/v1/auth/logout",
                                json={"refresh_token": tokens["refresh_token"]}, headers=headers)
    assert logout.status_code == 200

    me_after = await client.get("/api/v1/auth/me", headers=headers)
    assert me_after.status_code == 401, "access token should now be blacklisted (issue #8)"


# ── #1 (again) + folders create/list/move ──
async def test_folder_create_list_and_move(client, mock_db):
    await _register_and_verify(client, mock_db, email="user3@example.com", username="user3")
    tokens = await _login(client, email="user3@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    f1 = await client.post("/api/v1/folders", json={"name": "Folder A"}, headers=headers)
    f2 = await client.post("/api/v1/folders", json={"name": "Folder B"}, headers=headers)
    assert f1.status_code == 201 and f2.status_code == 201, (f1.text, f2.text)

    listing = await client.get("/api/v1/folders", headers=headers)
    assert listing.status_code == 200, listing.text  # would 500 pre-fix (#1 per_page)
    assert listing.json()["data"]["total"] == 2

    dest = await client.post("/api/v1/folders", json={"name": "Dest"}, headers=headers)
    dest_id = dest.json()["data"]["id"]
    move = await client.post("/api/v1/folders/move", json={
        "file_ids": [f1.json()["data"]["id"], f2.json()["data"]["id"]],
        "new_parent_id": dest_id,
    }, headers=headers)
    assert move.status_code == 200, move.text


# ── #3 + #4 share creation: generate_share_code() + schema match ──
async def test_share_create_matches_schema(client, mock_db):
    await _register_and_verify(client, mock_db, email="user4@example.com", username="user4")
    tokens = await _login(client, email="user4@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    file_id = str(uuid4())
    await mock_db.files.insert_one({
        "_id": file_id, "owner_id": me["id"], "parent_id": None,
        "original_name": "photo.jpg", "file_type": "image", "mime_type": "image/jpeg",
        "size_bytes": 1234, "file_hash": "deadbeef", "is_folder": False,
        "is_favorite": False, "status": "completed", "telegram_message_id": 42,
        "thumbnail_message_id": None, "deleted_at": None, "tags": [],
        "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
    })

    r = await client.post("/api/v1/shares", json={"file_id": file_id}, headers=headers)
    assert r.status_code == 201, r.text  # pre-fix: AttributeError then ValidationError
    body = r.json()["data"]
    assert "share_token" in body and "owner_id" in body and "has_password" in body


# ── #2 streaming delegates correctly (unit-level, no real Telegram needed) ──
async def test_download_stream_delegates_to_telegram(monkeypatch):
    from app.services.upload_service import upload_service
    from app.services import telegram_service as telegram_module

    captured = {}

    async def fake_stream_download(message_id, offset=0, limit=0):
        captured["message_id"] = message_id
        captured["offset"] = offset
        captured["limit"] = limit
        yield b"hello "
        yield b"world"

    monkeypatch.setattr(telegram_module.telegram_service, "stream_download", fake_stream_download)

    chunks = [c async for c in upload_service.download_file_stream(42, start=10, end=19)]
    assert b"".join(chunks) == b"hello world"
    assert captured == {"message_id": 42, "offset": 10, "limit": 10}


# ── bcrypt 72-byte truncation fix ──
def test_long_password_is_not_silently_truncated():
    from app.core.security import security

    p1 = "x" * 80 + "AAA"
    p2 = "x" * 80 + "ZZZ"  # differs only after byte 72 -> old bcrypt would treat these as equal

    h1 = security.hash_password(p1)
    assert security.verify_password(p1, h1) is True
    assert security.verify_password(p2, h1) is False, (
        "password differing only after byte 72 should NOT verify against p1's hash"
    )

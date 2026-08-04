"""
Smoke tests for the MEDIUM-severity fixes (review items #11-#20).

Same conventions as test_smoke.py: in-memory Mongo via mongomock-motor,
Telegram/email network calls stubbed via the `client`/`mock_db`/`app`
fixtures in conftest.py. Run locally with:

    pip install -r requirements.txt -r requirements-dev.txt
    pytest tests/ -v
"""
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


# ── helpers (mirrors test_smoke.py) ──
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


# ── #11 an OTP minted for one purpose can't be redeemed by another endpoint ──
async def test_otp_purpose_is_not_interchangeable(client, mock_db):
    await _register_and_verify(client, mock_db, email="purpose@example.com", username="purposeuser")

    await client.post("/api/v1/auth/forgot-password", json={"email": "purpose@example.com"})
    user = await mock_db.users.find_one({"email": "purpose@example.com"})
    assert user["otp_purpose"] == "password_reset"

    # send_otp_email is stubbed (conftest), so we can't observe the real code —
    # inject a known one with the same purpose the endpoint would have set.
    from app.core.security import security
    known_otp = "123456"
    await mock_db.users.update_one(
        {"email": "purpose@example.com"},
        {"$set": {
            "otp_hash": security.hash_otp(known_otp),
            "otp_purpose": "password_reset",
            "otp_expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        }},
    )

    # A password-reset OTP must NOT verify an email...
    r = await client.post("/api/v1/auth/verify-otp", json={"email": "purpose@example.com", "otp": known_otp})
    assert r.status_code == 400, r.text

    # ...but must still work for the purpose it was actually issued for.
    r = await client.post("/api/v1/auth/reset-password", json={
        "email": "purpose@example.com", "otp": known_otp, "new_password": "NewSup3rSecret1!",
    })
    assert r.status_code == 200, r.text

    # Symmetric check: a verification-purpose OTP must not reset a password.
    await mock_db.users.update_one(
        {"email": "purpose@example.com"},
        {"$set": {
            "otp_hash": security.hash_otp(known_otp),
            "otp_purpose": "verification",
            "otp_expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        }},
    )
    r = await client.post("/api/v1/auth/reset-password", json={
        "email": "purpose@example.com", "otp": known_otp, "new_password": "AnotherSup3rSecret1!",
    })
    assert r.status_code == 400, r.text


# ── #12 changing email forces re-verification; duplicate key -> 409 not 500 ──
async def test_profile_email_change_forces_reverification(client, mock_db):
    await _register_and_verify(client, mock_db, email="changeme@example.com", username="changer")
    tokens = await _login(client, email="changeme@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    r = await client.put("/api/v1/user/profile", json={"email": "changed@example.com"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["is_verified"] is False, "new email must be unverified until confirmed (#12)"

    user = await mock_db.users.find_one({"email": "changed@example.com"})
    assert user is not None, "the update should have gone through"
    assert user["is_verified"] is False
    assert user["otp_purpose"] == "verification"

    # The now-unverified account should be blocked from logging in, same as
    # any other unverified account.
    r = await client.post("/api/v1/auth/login", json={"email": "changed@example.com", "password": "Sup3rSecret!"})
    assert r.status_code == 403, r.text


async def test_profile_update_duplicate_key_returns_409():
    # Calls the route function directly with hand-built fakes so this test
    # doesn't depend on whether mongomock enforces unique indexes the same
    # way a real MongoDB deployment would (#12).
    from fastapi import HTTPException
    from pymongo.errors import DuplicateKeyError
    from app.api.v1 import user as user_module
    from app.models.schemas import UserUpdate

    class FakeUsersCollection:
        async def update_one(self, *a, **kw):
            raise DuplicateKeyError("email already exists")

    class FakeDB:
        def __init__(self):
            self.users = FakeUsersCollection()

    class FakeEmailService:
        async def send_otp_email(self, *a, **kw):
            return None

    fake_user = {
        "_id": "u1", "email": "old@example.com", "username": "olduser",
        "password_hash": "x", "role": "user", "is_verified": True, "is_active": True,
        "storage_used_bytes": 0, "storage_quota_bytes": 1,
        "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
    }

    with pytest.raises(HTTPException) as exc_info:
        await user_module.update_profile(
            UserUpdate(username="someone_elses_name"), fake_user, FakeDB(), FakeEmailService()
        )
    assert exc_info.value.status_code == 409, "duplicate username/email must be a clean 409, not a raw 500 (#12)"


# ── #13 change-password revokes every other active session ──
async def test_change_password_revokes_other_sessions(client, mock_db):
    await _register_and_verify(client, mock_db, email="pwchange@example.com", username="pwchanger")
    device_a = await _login(client, email="pwchange@example.com")
    device_b = await _login(client, email="pwchange@example.com")
    assert await mock_db.sessions.count_documents({"is_active": True}) == 2

    headers_a = {"Authorization": f"Bearer {device_a['access_token']}"}
    r = await client.put("/api/v1/user/password", json={
        "current_password": "Sup3rSecret!", "new_password": "EvenSup3rer1!",
    }, headers=headers_a)
    assert r.status_code == 200, r.text

    assert await mock_db.sessions.count_documents({"is_active": True}) == 0, (
        "reset-password already revoked every session; plain change-password didn't (#13)"
    )

    # device_b's refresh token must now be rejected.
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": device_b["refresh_token"]})
    assert r.status_code == 401, r.text


# ── #14 root folder listing filters/counts folders at the DB level ──
async def test_root_folder_listing_total_is_not_just_current_page(client, mock_db):
    await _register_and_verify(client, mock_db, email="folderpage@example.com", username="folderpager")
    tokens = await _login(client, email="folderpage@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    # Two plain files at the root, interleaved alphabetically with three
    # folders — a page-then-filter-in-Python approach can both undercount
    # `total` and, on some pages, miss folders entirely.
    await mock_db.files.insert_one(_file_doc(me["id"], original_name="a_file.txt", is_folder=False))
    await mock_db.files.insert_one(_file_doc(me["id"], original_name="z_file.txt", is_folder=False))
    for name in ["f1", "f2", "f3"]:
        r = await client.post("/api/v1/folders", json={"name": name}, headers=headers)
        assert r.status_code == 201, r.text

    r = await client.get("/api/v1/folders?page=1&limit=2", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data["folders"]) == 2
    assert data["total"] == 3, "total must be the true folder count, not len() of the current page (#14)"

    r2 = await client.get("/api/v1/folders?page=2&limit=2", headers=headers)
    data2 = r2.json()["data"]
    assert len(data2["folders"]) == 1, "the 3rd folder must still show up on page 2 (#14)"
    assert data2["total"] == 3


# ── #15 copy_file validates the destination, deep-copies folders, checks quota ──
async def test_copy_file_validates_destination_and_deep_copies_folder(mock_db):
    from fastapi import HTTPException
    from app.services.file_service import file_service

    owner = str(uuid4())
    await mock_db.users.insert_one({"_id": owner, "storage_used_bytes": 0, "storage_quota_bytes": 1_000_000})

    folder_id = str(uuid4())
    await mock_db.files.insert_one({
        "_id": folder_id, "owner_id": owner, "parent_id": None, "original_name": "MyFolder",
        "file_type": "folder", "is_folder": True, "is_favorite": False, "size_bytes": 0,
        "status": "completed", "deleted_at": None,
        "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
    })
    original_child_id = str(uuid4())
    await mock_db.files.insert_one({
        "_id": original_child_id, "owner_id": owner, "parent_id": folder_id, "original_name": "inside.txt",
        "file_type": "document", "mime_type": "text/plain", "is_folder": False, "is_favorite": False,
        "size_bytes": 500, "file_hash": "h1", "status": "completed", "telegram_message_id": 1,
        "deleted_at": None, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
    })

    # Copying into a folder that doesn't exist (or isn't this user's) must be
    # rejected, the same way move_file already rejects it (#15).
    with pytest.raises(HTTPException) as exc_info:
        await file_service.copy_file(mock_db, folder_id, owner, "does-not-exist")
    assert exc_info.value.status_code == 404

    copied = await file_service.copy_file(mock_db, folder_id, owner, None)
    assert copied["original_name"] == "Copy of MyFolder"

    copied_children = await mock_db.files.find({"parent_id": copied["_id"]}).to_list(None)
    assert len(copied_children) == 1, "copying a folder must copy its children too, not leave it empty (#15)"
    assert copied_children[0]["original_name"] == "inside.txt"
    assert copied_children[0]["_id"] != original_child_id, "the child must get its own new id, not reuse the original's"

    user_after = await mock_db.users.find_one({"_id": owner})
    assert user_after["storage_used_bytes"] == 500, "copying should account for the duplicated bytes (#15)"


async def test_copy_file_rejects_when_over_quota(mock_db):
    from fastapi import HTTPException
    from app.services.file_service import file_service

    owner = str(uuid4())
    await mock_db.users.insert_one({"_id": owner, "storage_used_bytes": 900_000, "storage_quota_bytes": 1_000_000})
    file_id = str(uuid4())
    await mock_db.files.insert_one({
        "_id": file_id, "owner_id": owner, "parent_id": None, "original_name": "big.bin",
        "file_type": "other", "is_folder": False, "is_favorite": False, "size_bytes": 200_000,
        "file_hash": "h2", "status": "completed", "telegram_message_id": 2, "deleted_at": None,
        "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
    })

    with pytest.raises(HTTPException) as exc_info:
        await file_service.copy_file(mock_db, file_id, owner, None)
    assert exc_info.value.status_code == 413, "copy_file previously had no quota check at all (#15)"


# ── #16 share access hides internal fields, and the download limit is atomic ──
async def test_share_access_hides_internal_fields(client, mock_db):
    await _register_and_verify(client, mock_db, email="sharer@example.com", username="sharer")
    tokens = await _login(client, email="sharer@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    file_id = str(uuid4())
    await mock_db.files.insert_one(_file_doc(
        me["id"], _id=file_id, original_name="secret_plans.pdf", mime_type="application/pdf",
        file_hash="supersecrethash", telegram_message_id=999,
    ))
    share = await client.post("/api/v1/shares", json={"file_id": file_id}, headers=headers)
    token = share.json()["data"]["share_token"]

    r = await client.post(f"/api/v1/shares/{token}/access", json={})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["original_name"] == "secret_plans.pdf"
    for leaked_field in ("owner_id", "telegram_message_id", "file_hash", "thumbnail_message_id", "status"):
        assert leaked_field not in body, f"{leaked_field} must not be exposed to share-link visitors (#16)"


async def test_share_download_limit_is_never_exceeded_under_concurrency(client, mock_db, monkeypatch):
    # CRITICAL #3 (later review round — see test_critical_fixes.py) made
    # /access preview-only: it no longer calls _claim_download_slot(), only
    # /download does. This test was written to prove _claim_download_slot()'s
    # atomic find_one_and_update can't be raced past max_downloads (#16) —
    # that's a property of the helper, not of which endpoint calls it — so
    # it now exercises /download (the endpoint that actually owns the claim)
    # to keep proving the same thing.
    from app.services import telegram_service as telegram_module

    async def fake_stream_download(message_id, offset=0, limit=0):
        yield b"x"

    monkeypatch.setattr(telegram_module.telegram_service, "stream_download", fake_stream_download)

    await _register_and_verify(client, mock_db, email="limiter@example.com", username="limiter")
    tokens = await _login(client, email="limiter@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    file_id = str(uuid4())
    await mock_db.files.insert_one(_file_doc(me["id"], _id=file_id, original_name="limited.txt"))
    share = await client.post(
        "/api/v1/shares", json={"file_id": file_id, "max_downloads": 1}, headers=headers
    )
    token = share.json()["data"]["share_token"]

    # Whether or not mongomock genuinely interleaves these the way a real
    # MongoDB round-trip would, the atomic find_one_and_update means the
    # final count can never exceed max_downloads (#16).
    results = await asyncio.gather(*(
        client.get(f"/api/v1/shares/{token}/download") for _ in range(5)
    ))
    successes = [r for r in results if r.status_code == 200]
    assert len(successes) == 1, "max_downloads=1 must allow exactly one success (#16)"

    share_doc = await mock_db.shares.find_one({"share_token": token})
    assert share_doc["download_count"] == 1


# ── #17 purpose is HTML-escaped before being embedded in the OTP email ──
async def test_send_otp_email_escapes_html_in_purpose(monkeypatch):
    from app.services import email_service as email_module

    captured = {}

    async def fake_send_email(to_email, subject, html_content, *a, **kw):
        captured["subject"] = subject
        captured["html_content"] = html_content
        return True

    monkeypatch.setattr(email_module.email_client, "send_email", fake_send_email)

    svc = email_module.EmailService()
    svc._is_running = True
    malicious_purpose = "<script>alert(1)</script>"
    await svc.send_otp_email("victim@example.com", "123456", malicious_purpose)
    await svc.queue.put(None)
    await svc._worker()

    assert "<script>" not in captured["html_content"].lower(), "raw markup must not reach the email HTML (#17)"
    assert "&lt;" in captured["html_content"] and "&gt;" in captured["html_content"], (
        "the angle brackets should show up escaped as entities instead"
    )


# ── #18 a losing racer in registration never surfaces as a raw 500 ──
async def test_concurrent_registration_same_email_never_500s(client):
    payload = {"username": "concurrent_user", "email": "raceme@example.com", "password": "Sup3rSecret!"}
    results = await asyncio.gather(
        client.post("/api/v1/auth/register", json=payload),
        client.post("/api/v1/auth/register", json={**payload, "username": "concurrent_user_2"}),
    )
    statuses = sorted(r.status_code for r in results)
    assert 500 not in statuses, "a losing racer must never surface as an unhandled 500 (#18)"
    assert statuses == [201, 409], (
        f"expected exactly one 201 and one 409, got {statuses} — whether mongomock actually "
        "races the two inserts or just serializes them, the observable outcome must be the same"
    )


# ── #19 stale temp dirs get swept, and the per-upload dir is cleaned as a whole ──
def test_cleanup_upload_path_removes_whole_directory(tmp_path, monkeypatch):
    from app.services import telegram_service as telegram_module

    monkeypatch.setattr(telegram_module.settings, "TEMP_STORAGE_PATH", str(tmp_path))
    upload_dir = tmp_path / "upload-xyz"
    upload_dir.mkdir()
    temp_file = upload_dir / "video.mp4"
    temp_file.write_bytes(b"data")

    telegram_module.TelegramService._cleanup_upload_path(str(temp_file))

    assert not upload_dir.exists(), (
        "the whole per-upload directory should be removed, not just the file — previously "
        "only the file was removed via os.remove, and only on the success path (#19)"
    )


def test_cleanup_upload_path_leaves_paths_outside_temp_root_alone(tmp_path, monkeypatch):
    from app.services import telegram_service as telegram_module

    monkeypatch.setattr(telegram_module.settings, "TEMP_STORAGE_PATH", str(tmp_path / "uploads_root"))
    outside_dir = tmp_path / "not_the_upload_root"
    outside_dir.mkdir()
    outside_file = outside_dir / "avatar.png"
    outside_file.write_bytes(b"data")

    telegram_module.TelegramService._cleanup_upload_path(str(outside_file))

    assert outside_dir.exists(), "paths outside TEMP_STORAGE_PATH (e.g. avatar temp files) must be left alone"


async def test_cleanup_stale_dirs_removes_old_dirs_but_not_fresh_ones(tmp_path, monkeypatch):
    from app.services import upload_service as upload_module

    monkeypatch.setattr(upload_module, "TEMP_UPLOAD_DIR", str(tmp_path))

    stale_dir = tmp_path / "stale-upload-id"
    stale_dir.mkdir()
    (stale_dir / "leftover.bin").write_bytes(b"x")
    old_time = time.time() - (upload_module.STALE_UPLOAD_DIR_MAX_AGE_SECONDS + 3600)
    os.utime(stale_dir, (old_time, old_time))

    fresh_dir = tmp_path / "fresh-upload-id"
    fresh_dir.mkdir()

    svc = upload_module.UploadService()
    removed = await svc._cleanup_stale_dirs()

    assert removed == 1
    assert not stale_dir.exists(), "directories untouched past the TTL window must be swept (#19)"
    assert fresh_dir.exists(), "a directory still receiving activity must not be touched"


# ── #20 two_factor_enabled actually gates login until the emailed code is confirmed ──
async def test_two_factor_login_requires_otp_confirmation(client, mock_db):
    await _register_and_verify(client, mock_db, email="2fa@example.com", username="twofa")
    await mock_db.users.update_one({"email": "2fa@example.com"}, {"$set": {"two_factor_enabled": True}})

    r = await client.post("/api/v1/auth/login", json={"email": "2fa@example.com", "password": "Sup3rSecret!"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["two_factor_required"] is True
    assert "access_token" not in body, "tokens must not be issued before 2FA is confirmed (#20)"

    user = await mock_db.users.find_one({"email": "2fa@example.com"})
    assert user["otp_purpose"] == "login"

    from app.core.security import security
    known_otp = "654321"
    await mock_db.users.update_one(
        {"email": "2fa@example.com"},
        {"$set": {
            "otp_hash": security.hash_otp(known_otp),
            "otp_purpose": "login",
            "otp_expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        }},
    )
    r = await client.post("/api/v1/auth/login/2fa", json={"email": "2fa@example.com", "otp": known_otp})
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()["data"], "the second factor should now issue real tokens (#20)"


async def test_login_without_two_factor_is_unaffected(client, mock_db):
    # Regression guard: accounts that never enabled 2FA must keep logging in
    # directly, with no behavior change from issue #20's fix.
    await _register_and_verify(client, mock_db, email="no2fa@example.com", username="notwofa")
    tokens = await _login(client, email="no2fa@example.com")
    assert "access_token" in tokens

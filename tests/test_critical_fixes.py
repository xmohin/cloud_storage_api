"""
Smoke tests for the CRITICAL-severity fixes: the 2 originally reported
(#1 password hashing, #2 refresh tokens) plus 3 more found while continuing
the review (#3 share download-limit double count, #4 several fully-built
FileService/EmailService methods with no route ever calling them, #5 a
storage_used_bytes double-count hiding inside #4's restore_from_trash).

Same conventions as test_smoke.py / test_medium_fixes.py: in-memory Mongo
via mongomock-motor, Telegram/email network calls stubbed via the
`client`/`mock_db`/`app` fixtures in conftest.py. Run locally with:

    pip install -r requirements.txt -r requirements-dev.txt
    pytest tests/ -v
"""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


# ── helpers (mirrors test_smoke.py / test_medium_fixes.py) ──
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


# ── CRITICAL #1: hash_password/verify_password no longer crash when the
#    SHA-256 pre-hash digest happens to contain a NUL byte — about
#    1-(255/256)^32 ≈ 11.76% of random passwords/OTPs, i.e. roughly 1 in
#    8-9 registrations or logins was a 500 (app/core/security.py:32) ──
def test_password_hashing_handles_a_known_nul_byte_digest():
    import hashlib
    from app.core.security import security

    # Deterministic, not probabilistic: this exact string's raw SHA-256
    # digest is known to contain a 0x00 byte, so pre-fix this raised
    # "ValueError: password may not contain NUL bytes" every single time.
    nul_byte_password = "RegressionProbe-28!"
    assert 0 in hashlib.sha256(nul_byte_password.encode()).digest(), "sanity check on the fixture itself"

    hashed = security.hash_password(nul_byte_password)  # pre-fix: raises ValueError
    assert security.verify_password(nul_byte_password, hashed) is True
    assert security.verify_password("wrong-password", hashed) is False


def test_password_hashing_survives_many_random_passwords():
    import random
    from app.core.security import security

    rng = random.Random(1337)  # fixed seed -> same passwords every CI run
    for _ in range(300):
        pw = "".join(chr(rng.randint(33, 126)) for _ in range(rng.randint(8, 40)))
        hashed = security.hash_password(pw)  # pre-fix: ~12% of these raise
        assert security.verify_password(pw, hashed) is True


async def test_register_never_500s_regardless_of_password_digest(client):
    # End-to-end version of the same fix, hitting the real endpoint.
    import random
    rng = random.Random(2024)
    for i in range(15):
        pw = "".join(chr(rng.randint(33, 126)) for _ in range(rng.randint(8, 40)))
        r = await client.post("/api/v1/auth/register", json={
            "username": f"nulcheck{i}", "email": f"nulcheck{i}@example.com", "password": pw,
        })
        assert r.status_code == 201, r.text


# ── CRITICAL #2: create_refresh_token/decode_token no longer TypeError when
#    JWT_REFRESH_SECRET_KEY is unset — the documented default (config.py
#    calls it optional, falling back to JWT_SECRET_KEY) and exactly what
#    conftest.py's own environment does, which is why every existing test
#    that logs in was already exercising this path (security.py:84-86,
#    and the identical bug in decode_token at :107-108, not in the
#    original report) ──
def test_refresh_token_roundtrip_when_dedicated_secret_is_unset(monkeypatch):
    from app.core.security import security
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "JWT_REFRESH_SECRET_KEY", None)  # the documented default

    token, jti = security.create_refresh_token("some-user-id")  # pre-fix: TypeError here
    payload = security.decode_token(token, expected_type="refresh")  # pre-fix: TypeError here too
    assert payload["sub"] == "some-user-id"
    assert payload["type"] == "refresh"
    assert payload["jti"] == jti


async def test_login_and_refresh_endpoint_work_end_to_end(client, mock_db):
    # conftest.py never sets JWT_REFRESH_SECRET_KEY, so every test in this
    # suite that logs in was already exercising CRITICAL #2 — pre-fix, ALL
    # of them would have 500'd, not just this one.
    await _register_and_verify(client, mock_db, email="critical2@example.com", username="critical2")
    tokens = await _login(client, email="critical2@example.com")
    assert "refresh_token" in tokens

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()["data"]


# ── CRITICAL #3: a share's max_downloads now counts actual downloads, not
#    previews — /access and /download both used to call
#    _claim_download_slot(), so a normal preview-then-download flow spent 2
#    of the link's slots per real download; with max_downloads=1 (the most
#    common setting) the preview alone always exhausted the link and the
#    real download always 410'd (app/api/v1/shares.py) ──
async def test_share_preview_does_not_consume_the_only_download_slot(client, mock_db, monkeypatch):
    from app.services import telegram_service as telegram_module

    async def fake_stream_download(message_id, offset=0, limit=0):
        yield b"file-bytes"

    monkeypatch.setattr(telegram_module.telegram_service, "stream_download", fake_stream_download)

    await _register_and_verify(client, mock_db, email="sharer2@example.com", username="sharer2")
    tokens = await _login(client, email="sharer2@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    file_id = str(uuid4())
    await mock_db.files.insert_one(_file_doc(me["id"], _id=file_id, original_name="onceonly.txt"))
    share = await client.post(
        "/api/v1/shares", json={"file_id": file_id, "max_downloads": 1}, headers=headers
    )
    token = share.json()["data"]["share_token"]

    # Preview first, exactly as a real link-landing-page UI would.
    preview = await client.post(f"/api/v1/shares/{token}/access", json={})
    assert preview.status_code == 200, preview.text

    # The actual download must still succeed — pre-fix this always 410'd.
    download = await client.get(f"/api/v1/shares/{token}/download")
    assert download.status_code == 200, (
        f"max_downloads=1 must still allow the real download after a preview; "
        f"got {download.status_code}: {download.text} (CRITICAL #3)"
    )

    share_doc = await mock_db.shares.find_one({"share_token": token})
    assert share_doc["download_count"] == 1, "download_count should reflect the one real download, not the preview too"

    # A second real download attempt is correctly blocked now that the
    # (single) slot has actually been used.
    second_download = await client.get(f"/api/v1/shares/{token}/download")
    assert second_download.status_code == 410


# ── CRITICAL #4: several FileService/EmailService methods were fully
#    implemented (some with their own historical #14/#15/#17/#24 fixes) but
#    had no API route calling them, making them 100% unreachable: favorites,
#    recent, browse-by-type, trash listing + restore, full statistics, and
#    emailing a share link ──
async def test_favorites_recent_and_type_listing_are_now_reachable(client, mock_db):
    await _register_and_verify(client, mock_db, email="lister@example.com", username="lister")
    tokens = await _login(client, email="lister@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    fav_id = str(uuid4())
    await mock_db.files.insert_one(_file_doc(me["id"], _id=fav_id, original_name="fav.jpg", is_favorite=True))
    await mock_db.files.insert_one(_file_doc(me["id"], original_name="not_fav.jpg"))

    favorites = await client.get("/api/v1/files/favorites", headers=headers)
    assert favorites.status_code == 200, favorites.text
    assert favorites.json()["data"]["total"] == 1
    assert favorites.json()["data"]["files"][0]["id"] == fav_id

    recent = await client.get("/api/v1/files/recent", headers=headers)
    assert recent.status_code == 200, recent.text
    assert recent.json()["data"]["total"] == 2

    by_type = await client.get("/api/v1/files/type/image", headers=headers)
    assert by_type.status_code == 200, by_type.text
    assert by_type.json()["data"]["total"] == 2

    # /favorites, /recent, /type/{x} must resolve to their own handlers, not
    # be shadowed by GET /{file_id} treating "favorites" etc. as a file_id
    # (the same class of bug already fixed once for notifications, #22).
    not_a_file_id = await client.get("/api/v1/files/favorites", headers=headers)
    assert not_a_file_id.json()["data"].get("files") is not None, (
        "GET /files/favorites must hit list_favorites, not get_file_info(file_id='favorites')"
    )


async def test_toggle_favorite_is_reachable(client, mock_db):
    await _register_and_verify(client, mock_db, email="favtoggle@example.com", username="favtoggle")
    tokens = await _login(client, email="favtoggle@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    file_id = str(uuid4())
    await mock_db.files.insert_one(_file_doc(me["id"], _id=file_id))

    r1 = await client.put(f"/api/v1/files/{file_id}/favorite", headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["is_favorite"] is True

    r2 = await client.put(f"/api/v1/files/{file_id}/favorite", headers=headers)
    assert r2.json()["data"]["is_favorite"] is False, "a second toggle should flip it back off"


async def test_copy_endpoint_duplicates_file_and_charges_quota_once(client, mock_db):
    await _register_and_verify(client, mock_db, email="copier@example.com", username="copier")
    tokens = await _login(client, email="copier@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    file_id = str(uuid4())
    await mock_db.files.insert_one(_file_doc(me["id"], _id=file_id, size_bytes=2_000))

    r = await client.post("/api/v1/folders/copy", json={"file_ids": [file_id], "new_parent_id": None}, headers=headers)
    assert r.status_code == 200, r.text
    copied = r.json()["data"][0]
    assert copied["id"] != file_id, "copy must produce a new file with a new id"

    total_files = await mock_db.files.count_documents({"owner_id": me["id"]})
    assert total_files == 2, "original + copy should both exist"

    user = await mock_db.users.find_one({"_id": me["id"]})
    assert user["storage_used_bytes"] == 2_000, "quota should be charged exactly once for the copy"


async def test_storage_statistics_uses_the_full_get_statistics(client, mock_db):
    await _register_and_verify(client, mock_db, email="statser@example.com", username="statser")
    tokens = await _login(client, email="statser@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    await mock_db.files.insert_one(_file_doc(me["id"], file_type="image"))
    await mock_db.files.insert_one(_file_doc(me["id"], file_type="video", original_name="clip.mp4"))

    r = await client.get("/api/v1/storage/statistics", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["files_by_type"] == {"image": 1, "video": 1}, "per-type breakdown (previously unreachable via this route)"
    assert data["trash_count"] == 0
    assert "used_bytes" in data, "kept for backward compatibility with the old inline response shape"


async def test_email_share_endpoint_is_reachable(client, mock_db, monkeypatch):
    from app.services import email_service as email_module

    captured = {}

    async def fake_send_share_email(to_email, owner_name, filename, share_url, password=None):
        captured.update(locals())

    monkeypatch.setattr(email_module.email_service, "send_share_email", fake_send_share_email)

    await _register_and_verify(client, mock_db, email="emailer@example.com", username="emailer")
    tokens = await _login(client, email="emailer@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    file_id = str(uuid4())
    await mock_db.files.insert_one(_file_doc(me["id"], _id=file_id, original_name="report.pdf"))
    share = await client.post("/api/v1/shares", json={"file_id": file_id}, headers=headers)
    share_id = share.json()["data"]["id"]

    r = await client.post(
        f"/api/v1/shares/{share_id}/email",
        json={"recipient_email": "friend@example.com"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert captured["to_email"] == "friend@example.com"
    assert captured["filename"] == "report.pdf"
    assert captured["password"] is None, "the plaintext password is never available to resend, by design"


# ── CRITICAL #5: restoring a file whose ancestor folder is ALSO trashed no
#    longer double-$incs storage_used_bytes — restoring the trashed ancestor
#    (recursively, above) already sweeps the file back up via
#    _recursive_restore, and the old code then unconditionally credited it
#    again on the way back out (app/services/file_service.py,
#    restore_from_trash). Also exercises /files/trash (CRITICAL #4), the
#    other half of "trash was a one-way door" ──
async def test_restore_nested_file_does_not_double_count_storage(client, mock_db):
    await _register_and_verify(client, mock_db, email="restorer@example.com", username="restorer")
    tokens = await _login(client, email="restorer@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    now = datetime.now(timezone.utc)
    folder_id, file_id = str(uuid4()), str(uuid4())
    await mock_db.files.insert_one({
        "_id": folder_id, "owner_id": me["id"], "parent_id": None,
        "original_name": "Trip Photos", "file_type": "folder", "mime_type": None,
        "size_bytes": 0, "file_hash": None, "is_folder": True,
        "is_favorite": False, "status": "completed", "telegram_message_id": None,
        "thumbnail_message_id": None, "deleted_at": now, "deleted_expires_at": now, "tags": [],
        "created_at": now, "updated_at": now,
    })
    await mock_db.files.insert_one(_file_doc(
        me["id"], _id=file_id, parent_id=folder_id, original_name="beach.jpg",
        size_bytes=5_000, deleted_at=now, deleted_expires_at=now,
    ))

    # Previously unreachable (no route) — confirms CRITICAL #4 for trash too.
    trash = await client.get("/api/v1/files/trash", headers=headers)
    assert trash.status_code == 200, trash.text
    assert trash.json()["data"]["total"] == 2

    restore = await client.post(f"/api/v1/files/{file_id}/restore", headers=headers)
    assert restore.status_code == 200, restore.text

    user = await mock_db.users.find_one({"_id": me["id"]})
    assert user["storage_used_bytes"] == 5_000, (
        f"restoring a file nested under an also-trashed folder must credit its size "
        f"exactly once, got {user['storage_used_bytes']} (CRITICAL #5)"
    )
    folder = await mock_db.files.find_one({"_id": folder_id})
    assert folder["deleted_at"] is None, "the trashed ancestor should be restored too, not just the file"

    trash_after = await client.get("/api/v1/files/trash", headers=headers)
    assert trash_after.json()["data"]["total"] == 0


# ── Later review-pass CRITICAL fixes (items #3/#4/#5, distinct from the
#    #1-#5 above): `_id` leaking into JSON instead of `id`, uploaded files
#    missing is_folder/is_favorite/deleted_at, and /backup/import being able
#    to permanently wipe data with no validation, whitelist, or rollback ──

def test_id_field_serializes_as_id_not_mongo_underscore_id():
    """The API must always expose `id`, never Mongo's raw `_id` key. Routes
    return bare ApiResponse objects with no response_model declared, so
    every response actually goes through fastapi.encoders.jsonable_encoder
    — which defaults to by_alias=True. A plain alias="_id" on the schema
    field controls *both* directions, so it was leaking `_id` into every
    JSON response; validation_alias="_id" reads Mongo's key on the way in
    without also renaming the key on the way out."""
    from datetime import datetime, timezone
    from fastapi.encoders import jsonable_encoder
    from app.models.schemas import ApiResponse, UserProfile

    mongo_doc = {
        "_id": "user-123", "username": "alice", "email": "alice@example.com",
        "role": "user", "is_verified": True, "is_active": True,
        "storage_used_bytes": 0, "storage_quota_bytes": 5_000_000_000,
        "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
    }
    profile = UserProfile(**mongo_doc)
    assert profile.id == "user-123", "validation_alias must still read Mongo's _id on the way in"

    encoded = jsonable_encoder(ApiResponse(data=profile))
    assert encoded["data"]["id"] == "user-123", "the client-facing JSON must expose `id`, not `_id`"
    assert "_id" not in encoded["data"], "the raw Mongo key must not leak into the API response"


async def test_uploaded_file_is_visible_in_recent_type_and_statistics(client, mock_db):
    await _register_and_verify(client, mock_db, email="uploader2@example.com", username="uploader2")
    tokens = await _login(client, email="uploader2@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    r = await client.post(
        "/api/v1/uploads/small",
        files={"file": ("photo.jpg", b"fake-image-bytes", "image/jpeg")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    file_id = r.json()["data"]["file_id"]

    stored = await mock_db.files.find_one({"_id": file_id})
    assert stored["is_folder"] is False, (
        "an uploaded file's document must have is_folder written explicitly — "
        "a missing field does not match {'is_folder': False} in a Mongo query"
    )
    assert stored["is_favorite"] is False
    assert stored["deleted_at"] is None

    recent = await client.get("/api/v1/files/recent", headers=headers)
    assert recent.status_code == 200, recent.text
    assert recent.json()["data"]["total"] == 1, "the upload must show up in /files/recent"

    by_type = await client.get("/api/v1/files/type/image", headers=headers)
    assert by_type.status_code == 200, by_type.text
    assert by_type.json()["data"]["total"] == 1, "the upload must show up in /files/type/image"

    stats = await client.get("/api/v1/storage/statistics", headers=headers)
    assert stats.status_code == 200, stats.text
    assert stats.json()["data"]["total_files"] == 1, "the upload must be counted in /storage/statistics"
    assert stats.json()["data"]["files_by_type"] == {"image": 1}


async def test_backup_import_skips_unknown_collections(client, mock_db):
    await _register_and_verify(client, mock_db, email="backupadmin1@example.com", username="backupadmin1")
    await mock_db.users.update_one({"email": "backupadmin1@example.com"}, {"$set": {"role": "admin"}})
    tokens = await _login(client, email="backupadmin1@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    import json
    payload = json.dumps({"some_collection_the_app_never_uses": [{"_id": "x", "evil": True}]})
    r = await client.post(
        "/api/v1/backup/import",
        files={"file": ("backup.json", payload, "application/json")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["restored_collections"] == {}
    assert body["skipped_collections"] == ["some_collection_the_app_never_uses"]
    assert "some_collection_the_app_never_uses" not in await mock_db.list_collection_names(), (
        "an unrecognised collection name in a backup file must never be created or written to"
    )


async def test_backup_import_reverts_every_touched_collection_on_failure(client, mock_db):
    await _register_and_verify(client, mock_db, email="backupadmin2@example.com", username="backupadmin2")
    await mock_db.users.update_one({"email": "backupadmin2@example.com"}, {"$set": {"role": "admin"}})
    tokens = await _login(client, email="backupadmin2@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]

    original_user_doc = await mock_db.users.find_one({"_id": me["id"]})
    existing_notif_id = str(uuid4())
    await mock_db.notifications.insert_one({
        "_id": existing_notif_id, "user_id": me["id"], "message": "original",
        "is_read": False, "created_at": "2026-01-01T00:00:00Z",
    })

    import json
    dup_id = str(uuid4())
    # "users" is well-formed and would succeed if it were the only entry.
    # "notifications" has two documents sharing the same _id, which Mongo's
    # unique index on _id rejects on the 2nd — insert_many() fails partway
    # through, *after* delete_many() already ran on both collections.
    payload = json.dumps({
        "users": [{
            "_id": me["id"], "username": "backupadmin2", "email": "backupadmin2@example.com",
            "password_hash": "x", "role": "admin", "is_verified": True, "is_active": True,
            "storage_used_bytes": 0, "storage_quota_bytes": 999,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }],
        "notifications": [
            {"_id": dup_id, "user_id": me["id"], "message": "dup1", "is_read": False, "created_at": "2026-01-01T00:00:00Z"},
            {"_id": dup_id, "user_id": me["id"], "message": "dup2", "is_read": False, "created_at": "2026-01-01T00:00:00Z"},
        ],
    })

    r = await client.post(
        "/api/v1/backup/import",
        files={"file": ("backup.json", payload, "application/json")},
        headers=headers,
    )
    assert r.status_code == 500, r.text
    assert "reverted" in r.json()["detail"].lower(), r.text

    user_after = await mock_db.users.find_one({"_id": me["id"]})
    assert user_after == original_user_doc, (
        "users must be reverted to byte-identical pre-import content, not left "
        "holding the failed import's data (it had already succeeded when "
        "'notifications' failed right after it)"
    )

    notifs = await mock_db.notifications.find({}).to_list(None)
    assert len(notifs) == 1 and notifs[0]["_id"] == existing_notif_id, (
        "a failed import must fully revert every collection it touched — including "
        "'users', which had already succeeded before 'notifications' failed — instead "
        "of leaving a mix of old/new/empty data"
    )

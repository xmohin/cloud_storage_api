"""
Shared fixtures for the smoke-test suite.

These tests do NOT need a real MongoDB or a real Telegram account:
  - MongoDB is replaced with an in-memory mongomock-motor database.
  - TelegramService / EmailService network calls are monkeypatched to no-ops.

This means the tests only prove the *application logic* (routing, schema
shapes, DB write-back, index behaviour, auth flow) is wired correctly —
they do NOT prove your real MONGODB_URI or Telegram credentials work.
Run these locally with:

    pip install -r requirements.txt -r requirements-dev.txt
    pytest tests/ -v
"""
import os
import sys
import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

# ── Make sure `app` package is importable regardless of cwd ──
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── Required env vars must exist BEFORE app.core.config is imported ──
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB_NAME", "gallery_vault_test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-smoke-tests-only")
os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "test_hash")
os.environ.setdefault("TELEGRAM_STORAGE_CHANNEL_ID", "-100123456789")
os.environ.setdefault("APP_ENV", "development")


@pytest_asyncio.fixture
async def mock_db():
    """In-memory Mongo database (mongomock-motor) wired into app.core.database.Database."""
    from mongomock_motor import AsyncMongoMockClient
    from app.core.database import Database

    client = AsyncMongoMockClient()
    database = client["gallery_vault_test"]

    # Database is a class-level singleton (`db = Database()`), and
    # __getattr__ proxies to the classmethod-set `_database` attribute —
    # so setting the class attribute directly bypasses the real connect().
    Database._client = client
    Database._database = database

    yield database

    Database._client = None
    Database._database = None


@pytest_asyncio.fixture
async def app(mock_db, monkeypatch):
    """FastAPI app with Telegram/email network calls stubbed out."""
    from app.services import telegram_service as telegram_module
    from app.services import upload_service as upload_module
    from app.services import email_service as email_module
    from app.core import middleware as middleware_module

    # The Limiter's in-memory counters are a module-level singleton that
    # persists for the life of the process, not just one test — and
    # RATE_LIMIT_AUTH defaults to 5/minute, which the smoke-test suite blows
    # through almost immediately once several tests each call /auth/register
    # or /auth/login. Disable enforcement here so tests exercise the actual
    # endpoint logic rather than incidentally racing the rate limiter.
    monkeypatch.setattr(middleware_module.limiter, "enabled", False)

    # Telegram: never touch the real network.
    async def _noop_start(*a, **kw):
        return None

    async def _noop_stop(*a, **kw):
        return None

    monkeypatch.setattr(telegram_module.telegram_service, "start", _noop_start)
    monkeypatch.setattr(telegram_module.telegram_service, "stop", _noop_stop)
    monkeypatch.setattr(upload_module.upload_service, "start", _noop_start)
    monkeypatch.setattr(upload_module.upload_service, "stop", _noop_stop)

    async def _noop_send_otp_email(*a, **kw):
        return None

    monkeypatch.setattr(email_module.email_service, "send_otp_email", _noop_send_otp_email)
    if hasattr(email_module.email_service, "start"):
        monkeypatch.setattr(email_module.email_service, "start", _noop_start)

    # Import after monkeypatching/env is set so module-level code sees them.
    from app.main import create_app
    application = create_app()

    yield application


@pytest_asyncio.fixture
async def client(app):
    """httpx AsyncClient bound to the app, running the real lifespan (startup/shutdown)."""
    import httpx
    from asgi_lifespan import LifespanManager

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

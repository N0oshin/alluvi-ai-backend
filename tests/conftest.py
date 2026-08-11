"""Test fixtures: a fresh SQLite database and an authenticated client per test."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("VISION_PROVIDER", "stub")
# Pinned, not defaulted from .env: a real provider here would mail live
# addresses on every test run. os.environ wins over .env in pydantic-settings.
os.environ["EMAIL_PROVIDER"] = "console"
os.environ.setdefault("JWT_SECRET", "test-secret")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402

TEST_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def engine():
    # A single shared in-memory connection, so every session in a test sees
    # the same database.
    eng = create_async_engine(TEST_URL, poolclass=None)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncGenerator[AsyncClient, None]:
    async def _override_db():
        """Mirrors the production middleware's boundary: commit on success,
        roll back on failure. Overriding the dependency bypasses the
        middleware, so the commit has to happen here instead."""
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient, session_factory) -> AsyncClient:
    """A client carrying a Bearer token for a verified user."""
    from sqlalchemy import select

    from app.db.models import User

    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/Auth/SignUp",
        json={"name": "Test User", "email": email, "password": "Passw0rd!"},
    )
    assert resp.status_code == 201, resp.text

    # Skip the emailed OTP: mark verified directly, then sign in.
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == email))
        user.email_verified = True
        await db.commit()

    resp = await client.post(
        "/api/Auth/login", json={"email": email, "password": "Passw0rd!"}
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client

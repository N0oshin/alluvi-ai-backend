"""Async engine, session factory, and the request transaction boundary.

The session is created by middleware (`app.main`) and committed **before the
response is sent**, not by a `yield` dependency.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Hand the route the request-scoped session opened by the middleware."""
    yield request.state.db

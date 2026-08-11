"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.i18n import Lang, lang_from_request
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db

# auto_error=False so a missing header raises our own `detail` envelope
# rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> AppError:
    """401, deliberately.

    The client's AuthorizationInterceptor wipes the session on 401 *and* 403,
    so 403 must never be used for ordinary authorization failures — see
    `resource_forbidden()` below.
    """
    return AppError(
        "auth.invalid_token",
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="INVALID_TOKEN",
    )


def resource_forbidden() -> AppError:
    """Ownership failure — 404, not 403.

    A 403 here would silently log the user out. Treating "not yours" as
    "not found" is also the safer disclosure.
    """
    return AppError(
        "resource.not_found",
        status_code=status.HTTP_404_NOT_FOUND,
        code="NOT_FOUND",
    )


def not_found() -> AppError:
    return AppError(
        "resource.not_found",
        status_code=status.HTTP_404_NOT_FOUND,
        code="NOT_FOUND",
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None or not credentials.credentials:
        raise _unauthorized()

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise _unauthorized()

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise _unauthorized() from None

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or user.deleted_at is not None:
        raise _unauthorized()
    return user


def get_lang(request: Request) -> Lang:
    return lang_from_request(request)


CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db)]
LangDep = Annotated[Lang, Depends(get_lang)]

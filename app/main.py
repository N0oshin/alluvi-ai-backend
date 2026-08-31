"""
The client's Retrofit paths are relative with no leading slash and the base URL
carries a trailing slash, so everything mounts under `/api/`.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.db.session import SessionLocal
from app.services.push.scheduler import reminder_loop

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
    logger.info(
        "Starting %s (vision=%s, storage=%s, push=%s)",
        settings.APP_NAME,
        settings.VISION_PROVIDER,
        settings.STORAGE_BACKEND,
        settings.PUSH_PROVIDER,
    )
    reminder_task = (
        asyncio.create_task(reminder_loop()) if settings.REMINDERS_ENABLED else None
    )
    try:
        yield
    finally:
        if reminder_task is not None:
            reminder_task.cancel()
            with suppress(asyncio.CancelledError):
                await reminder_task
        # Close the shared Gemini HTTP client (created lazily on first scan).
        from app.services.vision import gemini

        if gemini._http_client is not None and not gemini._http_client.is_closed:
            await gemini._http_client.aclose()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # a mobile client has no browser origin
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def db_session_middleware(request: Request, call_next):
    """Open one session per request and settle it *before* the response goes out.

    This is deliberately middleware and not a `yield` dependency: FastAPI runs
    dependency teardown after the response is sent, so a commit there races the
    client's next request. Committing here guarantees read-your-writes.

    Exception handlers run inside `call_next`, so by this point a failure has
    already become a 4xx/5xx response — the status code is what decides commit
    vs rollback.
    """
    async with SessionLocal() as session:
        request.state.db = session
        response = await call_next(request)
        try:
            if response.status_code < 400:
                await session.commit()
            else:
                await session.rollback()
        except Exception:
            await session.rollback()
            raise
        return response


register_error_handlers(app)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# Local media. In production these bytes live in object storage behind
# time-limited pre-signed URLs, not on the app server.
Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
app.mount(
    settings.MEDIA_URL_PREFIX,
    StaticFiles(directory=settings.MEDIA_ROOT),
    name="media",
)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}

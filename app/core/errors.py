"""Error envelope.

The Flutter client reads `e.response?.data['detail']` **unguarded** into a
non-nullable String (`lib/core/networking/api_error_handler.dart`). So every
non-2xx response this API produces must be a JSON object with a `detail`
key whose value is a plain string. A list, a bare string, or HTML makes the
client throw inside its own error handler and the user sees a crash instead
of a message.

FastAPI's defaults violate this in two places, both fixed below:
  * `RequestValidationError` returns `{"detail": [ {...}, ... ]}` — a list.
  * An unhandled exception returns a bare 500 with no body at all.

`detail` is also displayed verbatim by the client (it does not localise
`badResponse` errors), so messages are localised here off the `langCode`
header. An optional machine-readable `code` rides alongside, because the
client hardcodes its own status code and the real HTTP status never reaches
the UI.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.i18n import Lang, lang_from_request, t


class AppError(Exception):
    """Raised by services/routers; always renders as a `detail` envelope."""

    def __init__(
        self,
        message_key: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        code: str | None = None,
        **fmt: object,
    ) -> None:
        self.message_key = message_key
        self.status_code = status_code
        self.code = code
        self.fmt = fmt
        super().__init__(message_key)


def _envelope(detail: str, code: str | None, status_code: int) -> JSONResponse:
    body: dict[str, object] = {"detail": detail}
    if code:
        body["code"] = code
    return JSONResponse(status_code=status_code, content=body)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        lang = lang_from_request(request)
        return _envelope(
            t(exc.message_key, lang, **exc.fmt), exc.code, exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        lang = lang_from_request(request)
        # Flatten to a single sentence — the client cannot render a list.
        first = exc.errors()[0] if exc.errors() else None
        if first:
            loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
            detail = f"{loc}: {first.get('msg', '')}".strip(": ")
        else:
            detail = t("error.validation", lang)
        return _envelope(detail, "VALIDATION_ERROR", status.HTTP_422_UNPROCESSABLE_ENTITY)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        lang = lang_from_request(request)
        detail = exc.detail if isinstance(exc.detail, str) else t("error.generic", lang)
        return _envelope(detail, None, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        lang = lang_from_request(request)
        return _envelope(
            t("error.server", lang),
            "INTERNAL_ERROR",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


__all__ = ["AppError", "register_error_handlers", "Lang"]

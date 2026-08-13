"""In-process rate limiting for the abuse-prone auth endpoints.

Sliding-window counters held in process memory — the right size for a
single-instance deployment: no Redis to run, no network hop. If the app
later scales to N workers the limits become N× looser, which degrades
safely; at that point move the counters to Redis behind the same helper.

Each endpoint is keyed twice on purpose:
  * per-email — stops a targeted attack on one account no matter how many
    source addresses it comes from;
  * per-IP    — stops one machine from spraying many accounts (or flooding
    the outbound email budget with fresh addresses).
"""

from __future__ import annotations

import time
from collections import deque

from fastapi import Request, status

from app.core.config import settings
from app.core.errors import AppError


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        self._sweep(cutoff, now)

        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def _sweep(self, cutoff: float, now: float) -> None:
        """Drop keys whose newest hit has aged out, at most once per window,
        so months of one-off keys can't grow the dict without bound."""
        if now - self._last_sweep < self.window:
            return
        self._last_sweep = now
        stale = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
        for key in stale:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


def client_ip(request: Request) -> str:
    """First hop of X-Forwarded-For when present, else the socket peer.

    Only meaningful when the production proxy *overwrites* (not appends to)
    the header; a client talking to uvicorn directly could spoof it, which
    is one more reason the app must not be exposed without a proxy.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce(limiter: SlidingWindowLimiter, key: str) -> None:
    if not settings.RATE_LIMIT_ENABLED:
        return
    if not limiter.allow(key):
        raise AppError(
            "error.rate_limited",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMITED",
        )


# Generous for a human retyping a password; hostile to a script. Windows are
# short enough that a locked-out real user is unblocked by making coffee.
login_by_email = SlidingWindowLimiter(10, 15 * 60)
login_by_ip = SlidingWindowLimiter(30, 15 * 60)
# Sign-up and forgot-password both send an email, so these guard the sending
# domain's reputation as much as the database.
signup_by_ip = SlidingWindowLimiter(10, 60 * 60)
forgot_by_email = SlidingWindowLimiter(3, 60 * 60)
forgot_by_ip = SlidingWindowLimiter(10, 60 * 60)

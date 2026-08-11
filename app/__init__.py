"""Alluvi backend.

Windows note: psycopg's async mode cannot run on the ProactorEventLoop, which
is Python's default on Windows. The policy is switched here — at package
import, before any loop is created — so Alembic, the seed script, pytest, and
uvicorn all get a compatible loop without each having to remember.
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

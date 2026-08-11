"""Development server entrypoint. On Windows, use this rather than the
`uvicorn` CLI.

    python run.py                 # http://127.0.0.1:8000, reload on
    python run.py --no-reload
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Alluvi API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    reload = not args.no_reload

    if reload or sys.platform != "win32":
        # Reload runs the app in a subprocess, where uvicorn already selects
        # SelectorEventLoop; every non-Windows platform is fine as-is.
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=True if reload else False,
            log_level=args.log_level,
        )
        return

    # Windows, single process: run the server under SelectorEventLoop.
    config = uvicorn.Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()

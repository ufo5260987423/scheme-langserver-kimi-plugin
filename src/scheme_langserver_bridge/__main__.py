"""Entry point: python3 -m scheme_langserver_bridge"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys

from . import server as server_module
from .server import mcp

logger = logging.getLogger(__name__)

_shutdown_event = asyncio.Event()


def _setup_logging() -> None:
    level_name = os.environ.get("SCHEME_BRIDGE_LOGLEVEL", "INFO").upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR"}
    if level_name not in valid_levels:
        level = logging.INFO
        fallback = True
    else:
        level = getattr(logging, level_name)
        fallback = False

    # FastMCP configures logging on import; force reconfigure.
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(handler)

    if fallback:
        logger.warning(
            "Invalid SCHEME_BRIDGE_LOGLEVEL %r, falling back to INFO",
            level_name,
        )


def _signal_handler() -> None:
    logger.info("Shutdown signal received")
    _shutdown_event.set()


async def _run_server() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    server_task = asyncio.create_task(mcp.run_stdio_async())
    shutdown_task = asyncio.create_task(_shutdown_event.wait())

    done, pending = await asyncio.wait(
        [server_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    if _shutdown_event.is_set():
        if server_module._client is not None:
            await server_module._client.stop()
        # os._exit avoids asyncio.run() hanging on anyio's blocked stdin thread.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    else:
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def main() -> None:
    _setup_logging()
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()

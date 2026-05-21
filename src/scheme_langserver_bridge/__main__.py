"""Entry point: python3 -m scheme_langserver_bridge"""

from __future__ import annotations

import asyncio
import logging

from .server import mcp


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()

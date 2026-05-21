#!/usr/bin/env python3
"""Manual test: verify LSP connection to scheme-langserver."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scheme_langserver_bridge.config import Config
from scheme_langserver_bridge.lsp_client import LspClient

logging.basicConfig(level=logging.DEBUG)

TEST_ROOT = str(Path(__file__).parent.parent / "tests" / "fixtures" / "scheme-project")
TEST_FILE = str(Path(TEST_ROOT) / "test.scm")


async def main() -> None:
    config = Config.from_env()
    print(f"Using langserver: {config.langserver_path}")
    print(f"Log path: {config.log_path}")

    client = LspClient(config)
    try:
        print("\n[1/5] Initializing...")
        init_result = await client.start(TEST_ROOT)
        print(f"Initialized! Server info: {init_result.get('serverInfo', {})}")
        caps = init_result.get("capabilities", {})
        print(f"Capabilities: {json.dumps(caps, indent=2)[:500]}...")

        print("\n[2/5] Opening file...")
        text = Path(TEST_FILE).read_text()
        uri = "file://" + str(Path(TEST_FILE).resolve())
        client.did_open(uri, "scheme", 1, text)
        await asyncio.sleep(1)  # Give server time to analyze

        print("\n[3/5] Hover on 'factorial'...")
        # factorial is defined at line 6 (0-indexed: 5), column 9
        hover = await client.hover(uri, 5, 9)
        print(f"Hover result: {json.dumps(hover, indent=2)}")

        print("\n[4/5] Completion at line 7, col 8...")
        comp = await client.completion(uri, 6, 8)
        print(f"Completion result: {json.dumps(comp, indent=2)[:1000]}...")

        print("\n[5/5] Diagnostics...")
        diags = client.get_diagnostics(uri)
        print(f"Diagnostics: {json.dumps(diags, indent=2)}")

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
    finally:
        print("\nShutting down...")
        await client.stop()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())

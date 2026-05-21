"""Tests for LSP client concurrency behavior."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheme_langserver_bridge.config import Config
from scheme_langserver_bridge.lsp_client import LspClient


class FakeWriter:
    """Records every byte chunk written to it."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def _make_response_frame(req_id: int, result: object) -> bytes:
    payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
    return f"Content-Length: {len(payload)}\r\n\r\n{payload}".encode()


def _create_mock_process(
    writer: FakeWriter,
    stdout: asyncio.StreamReader,
    stderr: asyncio.StreamReader,
) -> MagicMock:
    mock = MagicMock()
    mock.stdin = writer
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = None
    mock.terminate = MagicMock()
    mock.kill = MagicMock()
    mock.wait = AsyncMock(return_value=0)
    return mock


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_interleave() -> None:
    """Verify two concurrent _request() calls produce complete, non-interleaved frames."""
    config = Config(langserver_path="fake", log_path=None, timeout=5.0)
    client = LspClient(config)

    writer = FakeWriter()
    stdout = asyncio.StreamReader()
    stderr = asyncio.StreamReader()
    stderr.feed_eof()
    mock_proc = _create_mock_process(writer, stdout, stderr)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        # Feed initialize response (id=1) after a tiny delay so start() can complete
        async def _feed_init() -> None:
            await asyncio.sleep(0.01)
            stdout.feed_data(_make_response_frame(1, {"capabilities": {}}))

        feeder = asyncio.create_task(_feed_init())
        await client.start("/tmp/fake")
        feeder.cancel()

    # Launch two concurrent requests
    async def _feed_responses() -> None:
        await asyncio.sleep(0.01)
        stdout.feed_data(_make_response_frame(2, "result1"))
        stdout.feed_data(_make_response_frame(3, "result2"))

    feeder = asyncio.create_task(_feed_responses())
    results = await asyncio.gather(
        client._request("method1", {}),
        client._request("method2", {}),
    )
    # feeder may already be done by the time we cancel
    if not feeder.done():
        feeder.cancel()
        try:
            await feeder
        except asyncio.CancelledError:
            pass

    assert results == ["result1", "result2"]

    # Verify every chunk written to stdin is a complete Content-Length frame
    assert len(writer.chunks) >= 3  # initialize + 2 requests
    for chunk in writer.chunks:
        data = chunk.decode("utf-8")
        assert data.startswith("Content-Length: ")
        header, body = data.split("\r\n\r\n", 1)
        content_length = int(header.split(":", 1)[1].strip())
        assert len(body.encode("utf-8")) == content_length

    # Avoid a long shutdown timeout by marking the process as crashed
    client._crashed = True
    await client.stop()

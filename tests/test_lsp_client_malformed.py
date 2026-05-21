"""Tests for LSP client malformed message handling."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from scheme_langserver_bridge.config import Config
from scheme_langserver_bridge.lsp_client import LspClient


class FakeWriter:
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
    return mock


@pytest.mark.asyncio
async def test_read_loop_handles_malformed_messages() -> None:
    """Verify _read_loop survives malformed messages and processes subsequent valid ones."""
    config = Config(langserver_path="fake", log_path=None)
    client = LspClient(config)

    valid_payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    valid_frame = f"Content-Length: {len(valid_payload)}\r\n\r\n{valid_payload}".encode()

    # 1. No Content-Length header (empty body)
    no_cl = b"X-Header: something\r\n\r\n"

    # 2. Invalid Content-Length value
    bad_cl = b"Content-Length: abc\r\n\r\n"

    # 3. Bad JSON body
    bad_json = b'{"not json}'
    bad_json_frame = f"Content-Length: {len(bad_json)}\r\n\r\n".encode() + bad_json

    # 4. Invalid UTF-8 bytes in body
    bad_utf8 = b"\xff\xfe"
    bad_utf8_frame = f"Content-Length: {len(bad_utf8)}\r\n\r\n".encode() + bad_utf8

    stdout = asyncio.StreamReader()
    for chunk in (no_cl, bad_cl, bad_json_frame, bad_utf8_frame, valid_frame):
        stdout.feed_data(chunk)
    stdout.feed_eof()

    stderr = asyncio.StreamReader()
    stderr.feed_eof()

    writer = FakeWriter()
    mock_proc = _create_mock_process(writer, stdout, stderr)
    client.process = mock_proc
    client._reader_task = asyncio.create_task(client._read_loop())

    # Populate a pending future so the valid message can be dispatched
    future = asyncio.get_running_loop().create_future()
    client._pending[1] = future

    await client._reader_task

    assert future.done()
    assert future.result() == {}

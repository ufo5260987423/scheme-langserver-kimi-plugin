"""Tests for LSP client cleanup on failures and timeouts."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheme_langserver_bridge.config import Config
from scheme_langserver_bridge.lsp_client import LspClient, LspError


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
    mock.wait = AsyncMock(return_value=0)
    return mock


@pytest.mark.asyncio
async def test_start_cancels_tasks_and_kills_process_on_initialize_failure() -> None:
    """Verify start() cleans up reader_task, stderr_task and the process when initialize fails."""
    config = Config(langserver_path="fake", log_path=None, timeout=0.01)
    client = LspClient(config)

    # stdout/stderr never return data (simulate a hung process)
    stdout = asyncio.StreamReader()
    stderr = asyncio.StreamReader()
    writer = FakeWriter()
    mock_proc = _create_mock_process(writer, stdout, stderr)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with pytest.raises(LspError):
            await client.start("/tmp/fake")

    assert client._reader_task is not None
    assert client._reader_task.cancelled()
    assert client._stderr_task is not None
    assert client._stderr_task.cancelled()
    assert mock_proc.kill.called


@pytest.mark.asyncio
async def test_timeout_cleans_other_pending_futures() -> None:
    """When one request times out and kills the process, other pending futures get an exception."""
    config = Config(langserver_path="fake", log_path=None, timeout=5.0)
    client = LspClient(config)

    stdout = asyncio.StreamReader()
    stderr = asyncio.StreamReader()
    writer = FakeWriter()
    mock_proc = _create_mock_process(writer, stdout, stderr)

    client.process = mock_proc
    client._initialized = True
    client._reader_task = asyncio.create_task(client._read_loop())
    client._stderr_task = asyncio.create_task(client._drain_stderr())

    try:
        task1 = asyncio.create_task(client._request("methodA", {}, timeout=0.01))
        await asyncio.sleep(0.005)  # Ensure task1 starts before task2
        task2 = asyncio.create_task(client._request("methodB", {}, timeout=10.0))

        start = time.monotonic()
        with pytest.raises(LspError):
            await task1

        with pytest.raises(LspError):
            await task2

        elapsed = time.monotonic() - start
        # task2 should not have waited its full 10s timeout
        assert elapsed < 1.0
        assert client._crashed is True
    finally:
        client._reader_task.cancel()
        client._stderr_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await client._reader_task
        with pytest.raises(asyncio.CancelledError):
            await client._stderr_task

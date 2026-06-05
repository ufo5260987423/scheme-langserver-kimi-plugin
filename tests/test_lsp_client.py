"""Unit tests for LspClient internal fixes."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheme_langserver_bridge.crash_reporter import CrashReporter
from scheme_langserver_bridge.lsp_client import LspClient, LspError, _set_resource_limits


class TestCrashReporterWiring:
    async def test_request_records_outgoing_when_reporter_attached(self) -> None:
        config = MagicMock()
        config.timeout = 5.0
        client = LspClient(config)
        reporter = MagicMock(spec=CrashReporter)
        client._crash_reporter = reporter
        client._shutdown = False
        client._crashed = False
        client._initialized = True

        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        async def fake_wait() -> int:
            return 0

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = None
        mock_process.stderr = None
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = fake_wait
        client.process = mock_process

        # Create a pending future that will never resolve, then timeout
        with pytest.raises(LspError, match="timed out"):
            await client._request("hover", {}, timeout=0.001)

        assert reporter.record_outgoing.called
        payload = reporter.record_outgoing.call_args[0][0]
        assert "hover" in payload

    async def test_notify_records_outgoing_when_reporter_attached(self) -> None:
        config = MagicMock()
        client = LspClient(config)
        reporter = MagicMock(spec=CrashReporter)
        client._crash_reporter = reporter
        client._shutdown = False
        client._crashed = False
        client._initialized = True

        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = None
        mock_process.stderr = None
        mock_process.returncode = None
        client.process = mock_process

        await client._notify("textDocument/didOpen", {"textDocument": {"uri": "file:///a.scm"}})

        assert reporter.record_outgoing.called
        payload = reporter.record_outgoing.call_args[0][0]
        assert "textDocument/didOpen" in payload

    async def test_stderr_records_via_reporter(self) -> None:
        config = MagicMock()
        client = LspClient(config)
        reporter = MagicMock(spec=CrashReporter)
        client._crash_reporter = reporter

        mock_stderr = MagicMock()
        mock_stderr.readline = AsyncMock(side_effect=[b"error line\n", b""])

        mock_process = MagicMock()
        mock_process.stderr = mock_stderr
        client.process = mock_process

        await client._drain_stderr()

        assert reporter.record_stderr.called
        assert "error line" in reporter.record_stderr.call_args[0][0]

    async def test_timeout_triggers_auto_report(self) -> None:
        config = MagicMock()
        config.timeout = 5.0
        client = LspClient(config)
        reporter = MagicMock(spec=CrashReporter)
        client._crash_reporter = reporter
        client._shutdown = False
        client._crashed = False
        client._initialized = True

        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        async def fake_wait() -> int:
            return 0

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = None
        mock_process.stderr = None
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = fake_wait
        client.process = mock_process

        with pytest.raises(LspError, match="timed out"):
            await client._request("hover", {}, timeout=0.001)

        assert reporter.auto_generate_on_crash.called
        assert "timeout" in reporter.auto_generate_on_crash.call_args[1]["reason"]


class TestWriteLock:
    def test_lock_exists(self) -> None:
        client = LspClient(MagicMock())
        assert isinstance(client._write_lock, asyncio.Lock)

    async def test_concurrent_writes_are_serialized(self) -> None:
        client = LspClient(MagicMock())
        client._shutdown = False
        client._crashed = False
        client._initialized = True

        order: list[str] = []

        mock_stdin = MagicMock()

        def write(data: bytes) -> None:
            order.append("write")

        async def drain() -> None:
            order.append("drain_start")
            await asyncio.sleep(0)
            order.append("drain_end")

        mock_stdin.write = write
        mock_stdin.drain = drain

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = None
        mock_process.stderr = None
        mock_process.returncode = None
        client.process = mock_process

        async def notify() -> None:
            await client._notify("textDocument/didOpen", {"textDocument": {"uri": "file:///a.scm"}})

        await asyncio.gather(notify(), notify())

        # With the lock held across write+drain, the second write must wait
        # until the first drain finishes.
        assert order == [
            "write",
            "drain_start",
            "drain_end",
            "write",
            "drain_start",
            "drain_end",
        ]


class TestTimeoutCleanup:
    async def test_timeout_clears_all_pending_futures(self) -> None:
        client = LspClient(MagicMock())
        client._shutdown = False
        client._crashed = False
        client._initialized = True
        client.process = MagicMock()
        client.process.stdin = MagicMock()
        client.process.stdin.write = MagicMock()
        client.process.stdin.drain = AsyncMock()
        client.process.returncode = None
        client.process.terminate = MagicMock()
        client.process.kill = MagicMock()
        client.process.wait = AsyncMock(return_value=0)

        # Ensure manual pending keys do not collide with _next_id()
        client._req_id = 1000
        fut1 = asyncio.get_running_loop().create_future()
        fut2 = asyncio.get_running_loop().create_future()
        client._pending[1] = fut1
        client._pending[2] = fut2

        with pytest.raises(LspError, match="timed out"):
            await client._request("hover", {}, timeout=0.001)

        assert fut1.done()
        exc1 = fut1.exception()
        assert isinstance(exc1, LspError)
        assert exc1.message == "LSP server terminated due to timeout"

        assert fut2.done()
        exc2 = fut2.exception()
        assert isinstance(exc2, LspError)
        assert exc2.message == "LSP server terminated due to timeout"

        assert not client._pending
        client.process.terminate.assert_called_once()


class TestInitializeFailureCleanup:
    async def test_start_failure_cleans_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LspClient(MagicMock())
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.stdin = MagicMock()
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(return_value=b"")
        mock_process.stderr = AsyncMock()
        mock_process.stderr.readline = AsyncMock(return_value=b"")
        mock_process.wait = AsyncMock(return_value=0)

        async def fake_create_subprocess(*args: Any, **kwargs: Any) -> Any:
            return mock_process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)

        async def fake_request(self: LspClient, method: str, params: Any) -> Any:
            raise RuntimeError("init failed")

        monkeypatch.setattr(LspClient, "_request", fake_request)

        with pytest.raises(RuntimeError, match="init failed"):
            await client.start("/tmp")

        assert client.process is None
        assert client._shutdown is True
        if client._reader_task is not None:
            assert client._reader_task.cancelled() or client._reader_task.done()
        if client._stderr_task is not None:
            assert client._stderr_task.cancelled() or client._stderr_task.done()


class TestReadLoopResilience:
    async def test_malformed_content_length_header(self, caplog: pytest.LogCaptureFixture) -> None:
        client = LspClient(MagicMock())
        client._shutdown = False

        mock_reader = MagicMock()
        mock_reader.readline = AsyncMock(side_effect=[
            b"Content-Length: abc\r\n",
            b"\r\n",
            b"",
        ])
        client.process = MagicMock()
        client.process.stdout = mock_reader

        await client._read_loop()
        assert client._crashed is True
        assert "Malformed Content-Length header" in caplog.text

    async def test_invalid_utf8_body_uses_replace(self) -> None:
        client = LspClient(MagicMock())
        client._shutdown = False

        # \x80 is invalid UTF-8 and becomes U+FFFD after replacement.
        body = b'{"jsonrpc":"2.0","id":1,"result":"\x80"}'
        mock_reader = MagicMock()
        mock_reader.readline = AsyncMock(side_effect=[
            f"Content-Length: {len(body)}\r\n".encode(),
            b"\r\n",
            b"",
        ])
        mock_reader.readexactly = AsyncMock(return_value=body)
        client.process = MagicMock()
        client.process.stdout = mock_reader

        fut = asyncio.get_running_loop().create_future()
        client._pending[1] = fut

        await client._read_loop()
        assert fut.done()
        assert fut.result() == "\ufffd"

    async def test_eof_sets_crashed(self) -> None:
        client = LspClient(MagicMock())
        mock_reader = MagicMock()
        mock_reader.readline = AsyncMock(return_value=b"")
        client.process = MagicMock()
        client.process.stdout = mock_reader

        await client._read_loop()
        assert client._crashed is True


class TestShutdownRejection:
    async def test_request_and_notify_reject_after_shutdown(self) -> None:
        client = LspClient(MagicMock())
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.returncode = None
        client.process = mock_process
        client._shutdown = True
        client._initialized = True

        with pytest.raises(RuntimeError, match="shutting down"):
            await client._request("hover", {})
        with pytest.raises(RuntimeError, match="shutting down"):
            await client._notify("textDocument/didOpen", {})


class TestResourceLimits:
    def test_setrlimit_valueerror_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scheme_langserver_bridge.lsp_client as mod

        mock_setrlimit = MagicMock(side_effect=ValueError("too high"))
        monkeypatch.setattr(mod.resource, "setrlimit", mock_setrlimit)

        # Should not raise despite ValueError from every setrlimit call.
        _set_resource_limits(1024 ** 3, 60)
        assert mock_setrlimit.call_count == 3


class TestCleanupMethod:
    async def test_cleanup_cancels_tasks_and_kills_process(self) -> None:
        client = LspClient(MagicMock())
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)
        client.process = mock_process

        async def dummy() -> None:
            while True:
                await asyncio.sleep(0.1)

        client._reader_task = asyncio.create_task(dummy())
        client._stderr_task = asyncio.create_task(dummy())

        await client._cleanup()

        assert client._reader_task.cancelled()
        assert client._stderr_task.cancelled()
        mock_process.kill.assert_called_once()
        assert client.process is None


class TestAkkuEnv:
    async def test_start_sets_chezschemelibdirs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        akku = tmp_path / ".akku"
        akku.mkdir()
        (akku / "lib").mkdir()

        client = LspClient(MagicMock())
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)

        captured: dict[str, Any] = {}

        async def fake_create_subprocess(*args: Any, **kwargs: Any) -> Any:
            captured["env"] = kwargs.get("env")
            return mock_process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)

        async def fake_request(self: LspClient, method: str, params: Any) -> Any:
            return {}

        async def fake_notify(self: LspClient, method: str, params: Any) -> None:
            pass

        monkeypatch.setattr(LspClient, "_request", fake_request)
        monkeypatch.setattr(LspClient, "_notify", fake_notify)

        await client.start(str(tmp_path))
        assert captured["env"] is not None
        assert "CHEZSCHEMELIBDIRS" in captured["env"]
        libdirs = captured["env"]["CHEZSCHEMELIBDIRS"].split(":")
        assert str(akku) in libdirs
        assert str(akku / "lib") in libdirs

    async def test_start_preserves_existing_chezschemelibdirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        akku = tmp_path / ".akku"
        akku.mkdir()
        (akku / "lib").mkdir()

        monkeypatch.setenv("CHEZSCHEMELIBDIRS", "/existing/path")

        client = LspClient(MagicMock())
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)

        captured: dict[str, Any] = {}

        async def fake_create_subprocess(*args: Any, **kwargs: Any) -> Any:
            captured["env"] = kwargs.get("env")
            return mock_process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)

        async def fake_request(self: LspClient, method: str, params: Any) -> Any:
            return {}

        async def fake_notify(self: LspClient, method: str, params: Any) -> None:
            pass

        monkeypatch.setattr(LspClient, "_request", fake_request)
        monkeypatch.setattr(LspClient, "_notify", fake_notify)

        await client.start(str(tmp_path))
        assert captured["env"] is not None
        chez = captured["env"]["CHEZSCHEMELIBDIRS"]
        assert chez.startswith(str(akku))
        assert "/existing/path" in chez

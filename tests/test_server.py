"""Tests for server module with mocked LSP client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scheme_langserver_bridge.server as server_module
from scheme_langserver_bridge.lsp_client import LspError


@pytest.fixture(autouse=True)
def reset_global_client():
    """Ensure _client is reset before each test."""
    original = server_module._client
    server_module._client = None
    yield
    server_module._client = original


class TestEnsureClient:
    def test_raises_when_not_initialized(self) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            server_module._ensure_client()


class TestLspInitialize:
    async def test_starts_client_and_returns_caps(self) -> None:
        mock_client = MagicMock()
        mock_client.start = AsyncMock(return_value={
            "serverInfo": {"name": "test"},
            "capabilities": {
                "hoverProvider": True,
                "definitionProvider": True,
                "referencesProvider": True,
            },
        })

        with patch("scheme_langserver_bridge.server.LspClient", return_value=mock_client):
            result = await server_module.lsp_initialize("/project/root")

        assert result["content"]["initialized"] is True
        assert result["content"]["capabilities"]["hover"] is True
        assert result["content"]["capabilities"]["definition"] is True
        mock_client.start.assert_awaited_once_with("/project/root")

    async def test_returns_warning_if_already_initialized(self) -> None:
        server_module._client = MagicMock()
        result = await server_module.lsp_initialize("/project/root")
        assert "warning" in result["content"]

    async def test_previous_root_is_string_not_cmd(self) -> None:
        mock_client = MagicMock()
        mock_client.config.build_cmd = MagicMock(return_value=["/bin/langserver", "/log"])
        server_module._client = mock_client
        result = await server_module.lsp_initialize("/project/root")
        assert result["content"]["previous_root"] == "/project/root"
        mock_client.config.build_cmd.assert_not_called()


class TestLspShutdown:
    async def test_shuts_down_client(self) -> None:
        mock_client = MagicMock()
        mock_client.stop = AsyncMock()
        server_module._client = mock_client

        result = await server_module.lsp_shutdown()

        assert result["content"]["shutdown"] is True
        mock_client.stop.assert_awaited_once()
        assert server_module._client is None

    async def test_warns_if_not_running(self) -> None:
        result = await server_module.lsp_shutdown()
        assert "warning" in result["content"]


class TestLspHover:
    async def test_returns_hover_result(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.hover = AsyncMock(return_value={"contents": "test hover"})
        server_module._client = mock_client

        test_file = tmp_path / "test.scm"
        test_file.write_text("(define x 1)")

        result = await server_module.lsp_hover(str(test_file), 0, 7)
        assert result["content"] == {"contents": "test hover"}

    async def test_returns_error_on_lsp_failure(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.hover = AsyncMock(side_effect=LspError(-32600, "Invalid params"))
        server_module._client = mock_client

        test_file = tmp_path / "test.scm"
        test_file.write_text("(define x 1)")

        result = await server_module.lsp_hover(str(test_file), 0, 7)
        assert result["content"]["error"] is True
        assert "note" in result["content"]


class TestFormatDiagnostics:
    def test_sorts_by_severity_and_summarizes(self) -> None:
        raw = {
            "file:///test.scm": [
                {"message": "hint", "severity": 4},
                {"message": "error", "severity": 1, "source": "scheme-langserver", "code": "E_UNBOUND"},
                {"message": "warning", "severity": 2},
            ]
        }
        formatted = server_module._format_diagnostics(raw)
        items = formatted["file:///test.scm"]["diagnostics"]
        assert [d["message"] for d in items] == ["error", "warning", "hint"]
        assert formatted["file:///test.scm"]["summary"] == {
            "error": 1,
            "warning": 1,
            "information": 0,
            "hint": 1,
            "total": 3,
        }
        assert items[0]["source"] == "scheme-langserver"
        assert items[0]["code"] == "E_UNBOUND"

    def test_surfaces_source_and_code_fields(self) -> None:
        raw = {
            "file:///test.scm": [
                {"message": "unbound identifier", "source": "scheme-langserver", "code": "E_UNBOUND"}
            ]
        }
        formatted = server_module._format_diagnostics(raw)
        item = formatted["file:///test.scm"]["diagnostics"][0]
        assert item["source"] == "scheme-langserver"
        assert item["code"] == "E_UNBOUND"


class TestLspDiagnostics:
    async def test_returns_diagnostics(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.get_diagnostics = MagicMock(return_value={
            "file:///test.scm": [{"message": "unbound identifier"}]
        })
        server_module._client = mock_client

        result = await server_module.lsp_diagnostics("/test.scm")
        assert "unbound identifier" in str(result["content"])
        assert "summary" in result["content"]["file:///test.scm"]

    async def test_returns_all_diagnostics_when_no_path(self) -> None:
        mock_client = MagicMock()
        mock_client.get_diagnostics = MagicMock(return_value={
            "file:///a.scm": [{"message": "error1"}],
            "file:///b.scm": [{"message": "error2"}],
        })
        server_module._client = mock_client

        result = await server_module.lsp_diagnostics(None)
        assert "error1" in str(result["content"])
        assert "error2" in str(result["content"])


class TestLspWorkspaceSymbol:
    async def test_returns_workspace_symbol_result(self) -> None:
        mock_client = MagicMock()
        mock_client.workspace_symbol = AsyncMock(return_value=[
            {"name": "foo", "kind": 12, "location": {"uri": "file:///test.scm", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}}}
        ])
        server_module._client = mock_client

        result = await server_module.lsp_workspace_symbol("foo")
        assert result["content"] == [
            {"name": "foo", "kind": 12, "location": {"uri": "file:///test.scm", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}}}
        ]
        mock_client.workspace_symbol.assert_awaited_once_with("foo")

    async def test_returns_error_on_lsp_failure(self) -> None:
        mock_client = MagicMock()
        mock_client.workspace_symbol = AsyncMock(side_effect=LspError(-32600, "Invalid params"))
        server_module._client = mock_client

        result = await server_module.lsp_workspace_symbol("foo")
        assert result["content"]["error"] is True
        assert "note" in result["content"]


class TestInferRootDir:
    def test_infer_root_dir_from_git(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        file_path = tmp_path / "src" / "test.scm"
        file_path.parent.mkdir()
        file_path.write_text("(define x 1)")

        result = server_module._infer_root_dir(str(file_path))
        assert result == str(tmp_path)

    def test_infer_root_dir_fallback_to_parent(self, tmp_path: Path) -> None:
        file_path = tmp_path / "test.scm"
        file_path.write_text("(define x 1)")

        result = server_module._infer_root_dir(str(file_path))
        assert result == str(tmp_path)


class TestAutoInitialize:
    async def test_auto_initialize_on_hover(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.scm"
        test_file.write_text("(define x 1)")

        mock_client = MagicMock()
        mock_client.hover = AsyncMock(return_value={"contents": "test hover"})

        async def mock_initialize(root_dir: str):
            server_module._client = mock_client
            server_module._doc_manager = MagicMock()
            return {"content": {"initialized": True}}

        with patch("scheme_langserver_bridge.server.lsp_initialize", side_effect=mock_initialize):
            result = await server_module.lsp_hover(str(test_file), 0, 7)

        assert result["content"] == {"contents": "test hover"}
        mock_client.hover.assert_awaited_once()

    async def test_auto_initialize_failure_returns_error(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.scm"
        test_file.write_text("(define x 1)")

        with patch(
            "scheme_langserver_bridge.server.lsp_initialize",
            return_value={"content": {"error": True, "message": "init failed"}},
        ):
            result = await server_module.lsp_hover(str(test_file), 0, 7)

        assert result["content"]["error"] is True
        assert "Auto-initialization failed" in result["content"]["message"]

    async def test_auto_initialize_on_workspace_symbol(self) -> None:
        mock_client = MagicMock()
        mock_client.workspace_symbol = AsyncMock(return_value=[{"name": "foo"}])

        async def mock_initialize(root_dir: str):
            server_module._client = mock_client
            server_module._doc_manager = MagicMock()
            return {"content": {"initialized": True}}

        with patch("scheme_langserver_bridge.server.lsp_initialize", side_effect=mock_initialize):
            result = await server_module.lsp_workspace_symbol("foo")

        assert result["content"] == [{"name": "foo"}]
        mock_client.workspace_symbol.assert_awaited_once_with("foo")

    async def test_auto_initialize_concurrent(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.scm"
        test_file.write_text("(define x 1)")

        mock_client = MagicMock()
        mock_client.hover = AsyncMock(return_value={"contents": "test hover"})

        call_count = 0

        async def mock_initialize(root_dir: str):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            server_module._client = mock_client
            server_module._doc_manager = MagicMock()
            return {"content": {"initialized": True}}

        with patch("scheme_langserver_bridge.server.lsp_initialize", side_effect=mock_initialize):
            results = await asyncio.gather(
                server_module.lsp_hover(str(test_file), 0, 7),
                server_module.lsp_hover(str(test_file), 0, 7),
            )

        assert call_count == 1
        assert all(r["content"] == {"contents": "test hover"} for r in results)
        assert mock_client.hover.await_count == 2


class TestAutoReinitialize:
    async def test_auto_reinitialize_after_crash(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        crashed_client = MagicMock()
        crashed_client._crashed = True
        server_module._client = crashed_client

        shutdown_called = []
        init_called = []

        async def mock_shutdown():
            shutdown_called.append(True)
            server_module._client = None
            server_module._doc_manager = None

        async def mock_initialize(root_dir: str):
            init_called.append(root_dir)
            new_client = MagicMock()
            new_client._crashed = False
            server_module._client = new_client
            return {"content": {"initialized": True}}

        monkeypatch.setattr(server_module, "lsp_shutdown", mock_shutdown)
        monkeypatch.setattr(server_module, "lsp_initialize", mock_initialize)

        test_file = tmp_path / "test.scm"
        test_file.write_text("(define x 1)")

        client = await server_module._ensure_initialized(str(test_file))
        assert shutdown_called
        assert init_called
        assert client is server_module._client
        assert client._crashed is False

    async def test_no_reinit_when_healthy(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        healthy_client = MagicMock()
        healthy_client._crashed = False
        server_module._client = healthy_client

        init_called = []

        async def mock_initialize(root_dir: str):
            init_called.append(root_dir)

        monkeypatch.setattr(server_module, "lsp_initialize", mock_initialize)

        test_file = tmp_path / "test.scm"
        test_file.write_text("(define x 1)")

        client = await server_module._ensure_initialized(str(test_file))
        assert client is healthy_client
        assert not init_called


class TestLspResultWrappers:
    def test_lsp_result_with_none(self) -> None:
        result = server_module._lsp_result(None)
        assert result["content"]["result"] is None
        assert "No result" in result["content"]["note"]

    def test_lsp_result_with_data(self) -> None:
        result = server_module._lsp_result({"key": "value"})
        assert result["content"] == {"key": "value"}

    def test_lsp_error_with_lsp_error(self) -> None:
        exc = LspError(-32600, "Bad request", {"detail": "x"})
        result = server_module._lsp_error(exc)
        assert result["content"]["error"] is True
        assert result["content"]["lsp_code"] == -32600
        assert "scheme-langserver returned an error" in result["content"]["note"]

    def test_lsp_error_with_generic_exception(self) -> None:
        exc = ValueError("something broke")
        result = server_module._lsp_error(exc)
        assert result["content"]["error"] is True
        assert "unexpected error" in result["content"]["note"]

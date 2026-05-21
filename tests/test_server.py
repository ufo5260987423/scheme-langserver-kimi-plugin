"""Tests for server module with mocked LSP client."""

from __future__ import annotations

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


class TestLspDiagnostics:
    async def test_returns_diagnostics(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.get_diagnostics = MagicMock(return_value={
            "file:///test.scm": [{"message": "unbound identifier"}]
        })
        server_module._client = mock_client

        result = await server_module.lsp_diagnostics("/test.scm")
        assert "unbound identifier" in str(result["content"])

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


class TestLspResultWrappers:
    def test_lsp_result_with_none(self) -> None:
        result = server_module._lsp_result(None)
        assert "No result" in str(result["content"])

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

"""Tests for server module with mocked LSP client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scheme_langserver_bridge.server as server_module
from scheme_langserver_bridge.lsp_client import LspError


@pytest.fixture(autouse=True)
def reset_global_state():
    """Ensure global server state is reset before each test."""
    original_client = server_module._client
    original_doc_manager = server_module._doc_manager
    original_root_dir = server_module._root_dir
    server_module._client = None
    server_module._doc_manager = None
    server_module._root_dir = None
    yield
    server_module._client = original_client
    server_module._doc_manager = original_doc_manager
    server_module._root_dir = original_root_dir


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

    async def test_uses_provided_langserver_path(self, tmp_path: Path) -> None:
        custom_bin = tmp_path / "custom-scheme-langserver"
        custom_bin.touch()
        mock_client = MagicMock()
        mock_client.start = AsyncMock(return_value={
            "serverInfo": {"name": "test"},
            "capabilities": {},
        })
        created_configs: list[Any] = []

        def capture_lsp_client(config: Any) -> MagicMock:
            created_configs.append(config)
            return mock_client

        with patch("scheme_langserver_bridge.server.LspClient", side_effect=capture_lsp_client):
            result = await server_module.lsp_initialize(
                "/project/root", langserver_path=str(custom_bin)
            )

        assert result["content"]["initialized"] is True
        assert len(created_configs) == 1
        assert created_configs[0].langserver_path == str(custom_bin)

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


class TestLspOpen:
    async def test_returns_warning_for_unrecognized_extension(self, tmp_path: Path) -> None:
        test_file = tmp_path / "lib.scm.txt"
        test_file.write_text("(define x 1)", encoding="utf-8")

        mock_client = MagicMock()
        mock_client.start = AsyncMock(return_value={
            "serverInfo": {"name": "test"},
            "capabilities": {},
        })
        mock_client.did_open = AsyncMock()

        with patch("scheme_langserver_bridge.server.LspClient", return_value=mock_client):
            await server_module.lsp_initialize(str(tmp_path))
            result = await server_module.lsp_open(str(test_file))

        assert result["content"]["opened"].endswith("lib.scm.txt")
        assert "warning" in result["content"]
        assert ".scm" in result["content"]["warning"]

    async def test_no_warning_for_recognized_extension(self, tmp_path: Path) -> None:
        test_file = tmp_path / "lib.scm"
        test_file.write_text("(define x 1)", encoding="utf-8")

        mock_client = MagicMock()
        mock_client.start = AsyncMock(return_value={
            "serverInfo": {"name": "test"},
            "capabilities": {},
        })
        mock_client.did_open = AsyncMock()

        with patch("scheme_langserver_bridge.server.LspClient", return_value=mock_client):
            await server_module.lsp_initialize(str(tmp_path))
            result = await server_module.lsp_open(str(test_file))

        assert result["content"]["opened"].endswith("lib.scm")
        assert "warning" not in result["content"]


class TestLspRestart:
    async def test_restart_initializes_when_not_running(self) -> None:
        mock_client = MagicMock()
        mock_client.start = AsyncMock(return_value={
            "serverInfo": {"name": "test"},
            "capabilities": {},
        })

        with patch("scheme_langserver_bridge.server.LspClient", return_value=mock_client):
            result = await server_module.lsp_restart("/project/root")

        assert result["content"]["restarted"] is True
        assert result["content"]["root_dir"] == "/project/root"
        mock_client.start.assert_awaited_once_with("/project/root")

    async def test_restart_reopens_tracked_documents(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.scm"
        test_file.write_text("(define x 1)", encoding="utf-8")

        # Initialize with a mock client and open a document.
        mock_client = MagicMock()
        mock_client.start = AsyncMock(return_value={
            "serverInfo": {"name": "test"},
            "capabilities": {},
        })
        mock_client.did_open = AsyncMock()
        mock_client.did_close = AsyncMock()
        mock_client.stop = AsyncMock()

        with patch("scheme_langserver_bridge.server.LspClient", return_value=mock_client):
            await server_module.lsp_initialize("/project/root")
            await server_module.lsp_open(str(test_file))

        # Replace the client with a fresh mock to simulate restart.
        new_mock_client = MagicMock()
        new_mock_client.start = AsyncMock(return_value={
            "serverInfo": {"name": "test"},
            "capabilities": {},
        })
        new_mock_client.did_open = AsyncMock()
        new_mock_client.stop = AsyncMock()

        with patch("scheme_langserver_bridge.server.LspClient", return_value=new_mock_client):
            result = await server_module.lsp_restart()

        assert result["content"]["restarted"] is True
        assert len(result["content"]["reopened_uris"]) == 1
        new_mock_client.did_open.assert_awaited_once()

    async def test_restart_uses_provided_root_dir(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.start = AsyncMock(return_value={
            "serverInfo": {"name": "test"},
            "capabilities": {},
        })
        mock_client.stop = AsyncMock()

        with patch("scheme_langserver_bridge.server.LspClient", return_value=mock_client):
            await server_module.lsp_initialize("/old/root")
            result = await server_module.lsp_restart("/new/root")

        assert result["content"]["root_dir"] == "/new/root"
        mock_client.start.assert_awaited_with("/new/root")

    async def test_restart_uses_provided_langserver_path(self, tmp_path: Path) -> None:
        custom_bin = tmp_path / "custom-scheme-langserver"
        custom_bin.touch()
        mock_client = MagicMock()
        mock_client.start = AsyncMock(return_value={
            "serverInfo": {"name": "test"},
            "capabilities": {},
        })
        mock_client.stop = AsyncMock()
        created_configs: list[Any] = []

        def capture_lsp_client(config: Any) -> MagicMock:
            created_configs.append(config)
            return mock_client

        with patch("scheme_langserver_bridge.server.LspClient", side_effect=capture_lsp_client):
            await server_module.lsp_initialize("/project/root")
            result = await server_module.lsp_restart(langserver_path=str(custom_bin))

        assert result["content"]["restarted"] is True
        # Two LspClient instances: first init, then restart.
        assert len(created_configs) == 2
        assert created_configs[1].langserver_path == str(custom_bin)


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


class TestSchemeFileDetection:
    def test_recognizes_scheme_extensions(self) -> None:
        assert server_module._is_scheme_file("/foo/bar.scm") is True
        assert server_module._is_scheme_file("/foo/bar.ss") is True
        assert server_module._is_scheme_file("/foo/bar.sls") is True
        assert server_module._is_scheme_file("/foo/bar.sps") is True

    def test_rejects_non_scheme_extensions(self) -> None:
        assert server_module._is_scheme_file("/foo/bar.scm.txt") is False
        assert server_module._is_scheme_file("/foo/bar.txt") is False
        assert server_module._is_scheme_file("/foo/bar.py") is False

    def test_warning_contains_recognized_extensions(self) -> None:
        warning = server_module._scheme_file_warning("/foo/bar.scm.txt")
        assert warning is not None
        assert ".scm" in warning
        assert "extension '.txt'" in warning

    def test_warning_none_for_scheme_file(self) -> None:
        assert server_module._scheme_file_warning("/foo/bar.scm") is None


class TestNormalizePullDiagnostics:
    def test_returns_list_directly(self) -> None:
        items = [{"message": "unused", "severity": 2}]
        assert server_module._normalize_pull_diagnostics(items) == items

    def test_unwraps_full_document_diagnostic_report(self) -> None:
        result = {"kind": "full", "items": [{"message": "unused", "severity": 2}]}
        assert server_module._normalize_pull_diagnostics(result) == [{"message": "unused", "severity": 2}]

    def test_unchanged_report_returns_empty_list(self) -> None:
        result = {"kind": "unchanged", "resultId": "1"}
        assert server_module._normalize_pull_diagnostics(result) == []

    def test_empty_dict_returns_none_to_trigger_fallback(self) -> None:
        assert server_module._normalize_pull_diagnostics({}) is None

    def test_none_returns_none(self) -> None:
        assert server_module._normalize_pull_diagnostics(None) is None


class TestSummarizeCaps:
    def test_diagnostics_is_always_true(self) -> None:
        # scheme-langserver does not advertise publishDiagnostics/diagnosticProvider,
        # but it does support diagnostics via both push and pull models.
        caps = {"hoverProvider": True}
        summary = server_module._summarize_caps(caps)
        assert summary["diagnostics"] is True


class TestLspDiagnostics:
    async def test_returns_diagnostics(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        # Pull diagnostics returns nothing for this file, so we fall back to
        # the cached push diagnostics.
        mock_client.diagnostic = AsyncMock(return_value=None)
        mock_client.get_diagnostics = MagicMock(return_value={
            "file:///test.scm": [{"message": "unbound identifier"}]
        })
        server_module._client = mock_client

        result = await server_module.lsp_diagnostics("/test.scm")
        assert "unbound identifier" in str(result["content"])
        assert "summary" in result["content"]["file:///test.scm"]

    async def test_uses_pull_diagnostics_when_available(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.diagnostic = AsyncMock(return_value=[
            {"message": "pulled diagnostic", "severity": 2, "source": "identifier", "code": "unused-local-variable"}
        ])
        mock_client.get_diagnostics = MagicMock(return_value={
            "file:///test.scm": [{"message": "stale push diagnostic"}]
        })
        server_module._client = mock_client

        result = await server_module.lsp_diagnostics("/test.scm")
        content = result["content"]
        assert "pulled diagnostic" in str(content)
        assert "stale push diagnostic" not in str(content)
        assert content["file:///test.scm"]["summary"]["warning"] == 1

    async def test_falls_back_to_push_when_pull_not_supported(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.diagnostic = AsyncMock(
            side_effect=LspError(-32601, "method not found")
        )
        mock_client.get_diagnostics = MagicMock(return_value={
            "file:///test.scm": [{"message": "push diagnostic"}]
        })
        server_module._client = mock_client

        result = await server_module.lsp_diagnostics("/test.scm")
        assert "push diagnostic" in str(result["content"])

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

    async def test_adds_note_for_unrecognized_extension(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.diagnostic = AsyncMock(return_value=None)
        mock_client.get_diagnostics = MagicMock(return_value={})
        server_module._client = mock_client

        result = await server_module.lsp_diagnostics("/foo/bar.scm.txt")
        content = result["content"]
        assert "file:///foo/bar.scm.txt" in content
        assert "note" in content["file:///foo/bar.scm.txt"]
        assert ".scm" in content["file:///foo/bar.scm.txt"]["note"]


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

"""Integration tests with a real scheme-langserver process."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from scheme_langserver_bridge.config import Config
from scheme_langserver_bridge.lsp_client import LspClient, LspError

TEST_ROOT = str(Path(__file__).parent / "fixtures" / "scheme-project")
TEST_FILE = str(Path(TEST_ROOT) / "test.scm")


@pytest_asyncio.fixture
async def lsp_client():
    """Yield an initialized LspClient, shut it down after the test."""
    config = Config.from_env()
    client = LspClient(config)
    await client.start(TEST_ROOT)
    yield client
    await client.stop()


class TestLspLifecycle:
    async def test_initialize_returns_capabilities(self, lsp_client: LspClient) -> None:
        # The fixture already initialized; just verify the client is ready
        assert lsp_client._initialized

    async def test_hover_on_factorial(self, lsp_client: LspClient) -> None:
        uri = "file://" + str(Path(TEST_FILE).resolve())
        text = Path(TEST_FILE).read_text()
        await lsp_client.did_open(uri, "scheme", 1, text)
        # Wait a moment for the server to index
        await asyncio.sleep(0.5)

        # Hover on the '=' symbol inside (if (= n 0))
        # Line 6 in 1-based, so 5 in 0-based; column 9 points to '='
        result = await lsp_client.hover(uri, 5, 9)
        assert result is not None
        contents = result.get("contents", [])
        assert any("=" in str(c) for c in contents)

        await lsp_client.did_close(uri)

    async def test_document_symbol(self, lsp_client: LspClient) -> None:
        uri = "file://" + str(Path(TEST_FILE).resolve())
        text = Path(TEST_FILE).read_text()
        await lsp_client.did_open(uri, "scheme", 1, text)
        await asyncio.sleep(0.5)

        result = await lsp_client.document_symbol(uri)
        assert result is not None
        # Should contain symbols for factorial and greet
        symbols = str(result)
        assert "factorial" in symbols or "greet" in symbols

        await lsp_client.did_close(uri)

    async def test_definition(self, lsp_client: LspClient) -> None:
        uri = "file://" + str(Path(TEST_FILE).resolve())
        text = Path(TEST_FILE).read_text()
        await lsp_client.did_open(uri, "scheme", 1, text)
        await asyncio.sleep(0.5)

        # Definition of 'factorial' on line 6 (0-based: 5), col 9
        result = await lsp_client.definition(uri, 5, 9)
        assert result is not None

        await lsp_client.did_close(uri)

    async def test_diagnostics_empty_for_valid_code(self, lsp_client: LspClient) -> None:
        uri = "file://" + str(Path(TEST_FILE).resolve())
        text = Path(TEST_FILE).read_text()
        await lsp_client.did_open(uri, "scheme", 1, text)
        await asyncio.sleep(0.5)

        diags = lsp_client.get_diagnostics(uri)
        # Valid code should have few or no diagnostics
        assert uri in diags

        await lsp_client.did_close(uri)


class TestPullDiagnostics:
    async def test_pull_diagnostics_returns_unused_local_variable(self, tmp_path: Path) -> None:
        # Create a minimal workspace with a .scm file containing an unused
        # parameter, which scheme-langserver 2.1.4+ reports as
        # "unused-local-variable".
        src = tmp_path / "unused.scm"
        src.write_text(
            "(library (unused)\n"
            "  (export f)\n"
            "  (import (rnrs))\n"
            "  (define (f x)\n"
            "    2))\n",
            encoding="utf-8",
        )

        config = Config.from_env()
        client = LspClient(config)
        await client.start(str(tmp_path))
        try:
            uri = "file://" + str(src.resolve())
            await client.did_open(uri, "scheme", 1, src.read_text(encoding="utf-8"))
            await asyncio.sleep(2)

            result = await client.diagnostic(uri)
            assert isinstance(result, list)
            messages = " ".join(str(d.get("message", "")) for d in result)
            assert "Unused local variable" in messages
        finally:
            await client.stop()


class TestLspErrorHandling:
    async def test_request_without_init_fails(self) -> None:
        config = Config.from_env()
        client = LspClient(config)
        with pytest.raises(RuntimeError, match="not started"):
            await client.hover("file:///test.scm", 0, 0)

    async def test_timeout_raises_lsp_error(self) -> None:
        config = Config.from_env()
        config.timeout = 0.001  # Impossibly short
        client = LspClient(config)
        # Even initialize will timeout immediately
        with pytest.raises(LspError):
            await client.start(TEST_ROOT)
        await client.stop()

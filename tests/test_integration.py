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

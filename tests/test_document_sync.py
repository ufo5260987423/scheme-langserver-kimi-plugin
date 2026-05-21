"""Tests for document_sync module."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scheme_langserver_bridge.document_sync import DocumentManager


class TestDocumentManager:
    def test_initially_empty(self) -> None:
        dm = DocumentManager(client=AsyncMock())
        assert dm.list_uris() == []
        assert dm.get("file:///test.scm") is None

    async def test_open_tracks_document(self) -> None:
        dm = DocumentManager(client=AsyncMock())
        await dm.open("file:///test.scm", "scheme", "(define x 1)")
        doc = dm.get("file:///test.scm")
        assert doc is not None
        assert doc["uri"] == "file:///test.scm"
        assert doc["language_id"] == "scheme"
        assert doc["version"] == 1
        assert doc["text"] == "(define x 1)"

    async def test_change_updates_version_and_text(self) -> None:
        dm = DocumentManager(client=AsyncMock())
        await dm.open("file:///test.scm", "scheme", "(define x 1)")
        await dm.change("file:///test.scm", "(define x 2)")
        doc = dm.get("file:///test.scm")
        assert doc is not None
        assert doc["version"] == 2
        assert doc["text"] == "(define x 2)"

    async def test_close_removes_document(self) -> None:
        dm = DocumentManager(client=AsyncMock())
        await dm.open("file:///test.scm", "scheme", "(define x 1)")
        await dm.close("file:///test.scm")
        assert dm.get("file:///test.scm") is None
        assert dm.list_uris() == []

    async def test_list_uris_returns_all_open(self) -> None:
        dm = DocumentManager(client=AsyncMock())
        await dm.open("file:///a.scm", "scheme", "")
        await dm.open("file:///b.scm", "scheme", "")
        uris = dm.list_uris()
        assert len(uris) == 2
        assert "file:///a.scm" in uris
        assert "file:///b.scm" in uris

    async def test_change_without_open_creates_implicitly(self) -> None:
        dm = DocumentManager(client=AsyncMock())
        await dm.change("file:///test.scm", "text")
        doc = dm.get("file:///test.scm")
        assert doc is not None
        assert doc["version"] == 1
        assert doc["text"] == "text"

    async def test_close_without_open_silent(self) -> None:
        dm = DocumentManager(client=AsyncMock())
        # Should not raise
        await dm.close("file:///test.scm")

    async def test_open_notifies_lsp_client(self) -> None:
        mock_client = AsyncMock()
        dm = DocumentManager(client=mock_client)
        await dm.open("file:///test.scm", "scheme", "(define x 1)")
        mock_client.did_open.assert_awaited_once_with(
            "file:///test.scm", "scheme", 1, "(define x 1)"
        )

    async def test_change_notifies_lsp_client(self) -> None:
        mock_client = AsyncMock()
        dm = DocumentManager(client=mock_client)
        await dm.open("file:///test.scm", "scheme", "(define x 1)")
        await dm.change("file:///test.scm", "(define x 2)")
        mock_client.did_change.assert_awaited_once_with(
            "file:///test.scm", 2, "(define x 2)"
        )

    async def test_close_notifies_lsp_client(self) -> None:
        mock_client = AsyncMock()
        dm = DocumentManager(client=mock_client)
        await dm.open("file:///test.scm", "scheme", "(define x 1)")
        await dm.close("file:///test.scm")
        mock_client.did_close.assert_awaited_once_with("file:///test.scm")

"""Document sync state manager for the LSP bridge."""

from __future__ import annotations

from typing import Any

from .lsp_client import LspClient


class DocumentManager:
    """Track open documents and mirror changes to the LSP server."""

    def __init__(self, client: LspClient) -> None:
        self._client = client
        self._docs: dict[str, dict[str, Any]] = {}

    async def open(self, uri: str, language_id: str, text: str) -> None:
        """Open a document in the language server and track it locally."""
        version = 1
        self._docs[uri] = {
            "uri": uri,
            "version": version,
            "text": text,
            "language_id": language_id,
        }
        await self._client.did_open(uri, language_id, version, text)

    async def change(self, uri: str, text: str) -> None:
        """Notify the server of a full-document change and bump the version."""
        doc = self._docs.get(uri)
        if doc is None:
            version = 1
            self._docs[uri] = {
                "uri": uri,
                "version": version,
                "text": text,
                "language_id": "scheme",
            }
        else:
            version = doc["version"] + 1
            doc["version"] = version
            doc["text"] = text

        await self._client.did_change(uri, version, text)

    async def close(self, uri: str) -> None:
        """Close a document and drop local state."""
        self._docs.pop(uri, None)
        await self._client.did_close(uri)

    def get(self, uri: str) -> dict[str, Any] | None:
        """Return the tracked state for a document, or None if not open."""
        return self._docs.get(uri)

    def list_uris(self) -> list[str]:
        """Return all currently tracked document URIs."""
        return list(self._docs.keys())

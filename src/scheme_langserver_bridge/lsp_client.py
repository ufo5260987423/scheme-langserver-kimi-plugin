"""LSP client for scheme-langserver over stdio."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from .config import Config

logger = logging.getLogger(__name__)


class LspError(Exception):
    """LSP operation failed."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"LSP error {code}: {message}")


class LspClient:
    """Async LSP client communicating with scheme-langserver via stdio."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._initialized = False
        self._reader_task: asyncio.Task[None] | None = None
        self._shutdown = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, root_dir: str) -> dict[str, Any]:
        """Launch scheme-langserver and perform LSP initialize handshake."""
        cmd = self.config.build_cmd(root_dir)
        logger.info("Starting scheme-langserver: %s", " ".join(cmd))

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._reader_task = asyncio.create_task(self._read_loop())

        init_params = {
            "processId": None,
            "rootPath": root_dir,
            "rootUri": _path_to_uri(root_dir),
            "capabilities": {
                "textDocument": {
                    "synchronization": {
                        "dynamicRegistration": False,
                        "willSave": True,
                        "willSaveWaitUntil": True,
                        "didSave": True,
                    },
                    "completion": {
                        "dynamicRegistration": False,
                        "completionItem": {
                            "snippetSupport": False,
                            "commitCharactersSupport": False,
                            "documentationFormat": ["markdown", "plaintext"],
                            "deprecatedSupport": False,
                            "preselectSupport": False,
                        },
                    },
                    "hover": {
                        "dynamicRegistration": True,
                        "contentFormat": ["markdown", "plaintext"],
                    },
                    "definition": {"dynamicRegistration": True, "linkSupport": True},
                    "references": {"dynamicRegistration": False},
                    "documentSymbol": {
                        "dynamicRegistration": False,
                        "hierarchicalDocumentSymbolSupport": True,
                    },
                    "rename": {"dynamicRegistration": True, "prepareSupport": True},
                    "signatureHelp": {
                        "dynamicRegistration": False,
                        "signatureInformation": {
                            "documentationFormat": ["markdown", "plaintext"],
                            "parameterInformation": {"labelOffsetSupport": True},
                        },
                    },
                    "codeAction": {"dynamicRegistration": True},
                },
                "workspace": {
                    "applyEdit": True,
                    "workspaceFolders": True,
                    "didChangeConfiguration": {"dynamicRegistration": False},
                },
            },
            "workspaceFolders": [
                {"uri": _path_to_uri(root_dir), "name": root_dir}
            ],
        }

        result = await self._request("initialize", init_params)
        await self._notify("initialized", {})
        self._initialized = True
        logger.info("scheme-langserver initialized")
        return result

    async def stop(self) -> None:
        """Gracefully shut down the LSP server."""
        if self._shutdown or self.process is None:
            return
        self._shutdown = True
        try:
            await self._request("shutdown", None)
        except Exception as exc:
            logger.warning("Shutdown request failed: %s", exc)
        await self._notify("exit", None)
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        logger.info("scheme-langserver stopped")

    # ------------------------------------------------------------------
    # Document sync
    # ------------------------------------------------------------------

    async def did_open(self, uri: str, language_id: str, version: int, text: str) -> None:
        await self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": version,
                    "text": text,
                }
            },
        )

    async def did_change(
        self, uri: str, version: int, text: str
    ) -> None:
        # scheme-langserver supports full document sync only
        await self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}],
            },
        )

    async def did_close(self, uri: str) -> None:
        await self._notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        self._diagnostics.pop(uri, None)

    # ------------------------------------------------------------------
    # LSP requests
    # ------------------------------------------------------------------

    async def hover(self, uri: str, line: int, character: int) -> Any:
        return await self._request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}},
        )

    async def completion(self, uri: str, line: int, character: int) -> Any:
        return await self._request(
            "textDocument/completion",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}},
        )

    async def definition(self, uri: str, line: int, character: int) -> Any:
        return await self._request(
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}},
        )

    async def references(
        self, uri: str, line: int, character: int, include_declaration: bool = False
    ) -> Any:
        return await self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": include_declaration},
            },
        )

    async def rename(self, uri: str, line: int, character: int, new_name: str) -> Any:
        return await self._request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "newName": new_name,
            },
        )

    async def signature_help(self, uri: str, line: int, character: int) -> Any:
        return await self._request(
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}},
        )

    async def document_symbol(self, uri: str) -> Any:
        return await self._request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
        )

    async def code_action(self, uri: str, start_line: int, start_char: int, end_line: int, end_char: int) -> Any:
        return await self._request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": start_line, "character": start_char},
                    "end": {"line": end_line, "character": end_char},
                },
                "context": {"diagnostics": self._diagnostics.get(uri, [])},
            },
        )

    # ------------------------------------------------------------------
    # Diagnostics access
    # ------------------------------------------------------------------

    def get_diagnostics(self, uri: str | None = None) -> dict[str, list[dict[str, Any]]]:
        if uri:
            return {uri: self._diagnostics.get(uri, [])}
        return dict(self._diagnostics)

    # ------------------------------------------------------------------
    # Internal I/O
    # ------------------------------------------------------------------

    async def _request(self, method: str, params: Any) -> Any:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("LSP server not started")

        req_id = self._next_id()
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        message = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            message["params"] = params

        payload = json.dumps(message, ensure_ascii=False)
        data = f"Content-Length: {len(payload.encode('utf-8'))}\r\n\r\n{payload}"
        self.process.stdin.write(data.encode("utf-8"))
        await self.process.stdin.drain()

        logger.debug("LSP request -> %s", payload)

        try:
            return await asyncio.wait_for(future, timeout=self.config.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise LspError(-32001, f"Request '{method}' timed out after {self.config.timeout}s")

    async def _notify(self, method: str, params: Any) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("LSP server not started")

        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params

        payload = json.dumps(message, ensure_ascii=False)
        data = f"Content-Length: {len(payload.encode('utf-8'))}\r\n\r\n{payload}"
        self.process.stdin.write(data.encode("utf-8"))
        await self.process.stdin.drain()
        logger.debug("LSP notify -> %s", payload)

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _read_loop(self) -> None:
        """Continuously read messages from LSP server stdout."""
        if self.process is None or self.process.stdout is None:
            return

        reader = self.process.stdout

        try:
            while not self._shutdown:
                # Read headers until blank line
                length = -1
                while True:
                    header = await reader.readline()
                    if not header:
                        return
                    header_str = header.decode("utf-8", errors="replace").strip()
                    if header_str.startswith("Content-Length:"):
                        length = int(header_str.split(":", 1)[1].strip())
                    elif header_str == "":
                        # blank line -> end of headers
                        break

                if length < 0:
                    continue

                # Read body
                body = await reader.readexactly(length)
                try:
                    msg = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning("Failed to decode LSP message: %s", body)
                    continue

                logger.debug("LSP message <- %s", msg)
                self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except asyncio.IncompleteReadError:
            logger.info("LSP server stdout closed")
        except Exception as exc:
            logger.exception("LSP read loop error: %s", exc)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg:
            # Response
            req_id = msg["id"]
            future = self._pending.pop(req_id, None)
            if future is None:
                return
            if "error" in msg:
                err = msg["error"]
                future.set_exception(LspError(err.get("code", 0), err.get("message", ""), err.get("data")))
            else:
                future.set_result(msg.get("result"))
        else:
            # Notification
            method = msg.get("method")
            params = msg.get("params", {})
            if method == "textDocument/publishDiagnostics":
                uri = params.get("uri", "")
                self._diagnostics[uri] = params.get("diagnostics", [])
                logger.debug("Received diagnostics for %s: %d items", uri, len(self._diagnostics[uri]))
            else:
                logger.debug("Unhandled notification: %s", method)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _path_to_uri(path: str) -> str:
    from pathlib import Path
    abs_path = str(Path(path).resolve())
    return "file://" + abs_path

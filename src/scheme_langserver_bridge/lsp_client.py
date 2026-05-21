"""LSP client for scheme-langserver over stdio."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
from typing import Any

from .config import Config

try:
    import resource

    _HAS_RESOURCE = True
except ImportError:
    _HAS_RESOURCE = False

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
        self._stderr_task: asyncio.Task[None] | None = None
        self._shutdown = False
        self._crashed = False
        self._write_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, root_dir: str) -> dict[str, Any]:
        """Launch scheme-langserver and perform LSP initialize handshake."""
        cmd = self.config.build_cmd(root_dir)
        logger.info("Starting scheme-langserver: %s", " ".join(cmd))
        logger.info(
            "Resource limits: max_memory=%dMB, max_cpu=%ds",
            self.config.max_memory_mb,
            self.config.max_cpu_seconds,
        )

        # Detect Akku environment and set CHEZSCHEMELIBDIRS
        import os
        from .config import _find_akku_libdirs

        env = os.environ.copy()
        akku_dirs = _find_akku_libdirs(root_dir)
        if akku_dirs:
            existing = env.get("CHEZSCHEMELIBDIRS", "")
            if existing:
                env["CHEZSCHEMELIBDIRS"] = ":".join(akku_dirs) + ":" + existing
            else:
                env["CHEZSCHEMELIBDIRS"] = ":".join(akku_dirs)
            logger.info("Set CHEZSCHEMELIBDIRS=%s", env["CHEZSCHEMELIBDIRS"])

        spawn_kwargs: dict[str, Any] = {"env": env}
        if _HAS_RESOURCE:
            spawn_kwargs["preexec_fn"] = functools.partial(
                _set_resource_limits,
                self.config.max_memory_mb * 1024 * 1024,
                self.config.max_cpu_seconds,
            )

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **spawn_kwargs,
        )

        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

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

        try:
            result = await self._request("initialize", init_params)
        except Exception:
            await self._cleanup()
            raise
        await self._notify("initialized", {})
        self._initialized = True
        logger.info("scheme-langserver initialized")
        return result

    async def stop(self) -> None:
        """Gracefully shut down the LSP server."""
        if self._shutdown or self.process is None:
            return
        self._shutdown = True
        if not self._crashed and self.process.returncode is None:
            try:
                await self._request("shutdown", None)
            except Exception as exc:
                logger.warning("Shutdown request failed: %s", exc)
            try:
                await self._notify("exit", None)
            except Exception as exc:
                logger.warning("Exit notification failed: %s", exc)
        await self._cleanup()

    async def _cleanup(self) -> None:
        self._shutdown = True
        if self._stderr_task:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self.process and self.process.returncode is None:
            self.process.kill()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except TimeoutError:
                pass
        self.process = None

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
            timeout=self.config.completion_timeout,
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

    async def workspace_symbol(self, query: str) -> Any:
        return await self._request(
            "workspace/symbol",
            {"query": query},
        )

    async def code_action(
        self,
        uri: str,
        start_line: int,
        start_char: int,
        end_line: int,
        end_char: int,
    ) -> Any:
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

    async def _request(
        self, method: str, params: Any, timeout: float | None = None
    ) -> Any:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("LSP server not started")
        if self._crashed or self.process.returncode is not None:
            raise RuntimeError(
                "scheme-langserver process has crashed. "
                "Please call lsp_shutdown and lsp_initialize to restart."
            )
        if self._shutdown:
            raise RuntimeError("LSP client is shutting down")

        req_id = self._next_id()
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        message = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            message["params"] = params

        payload = json.dumps(message, ensure_ascii=False)
        data = f"Content-Length: {len(payload.encode('utf-8'))}\r\n\r\n{payload}"
        async with self._write_lock:
            self.process.stdin.write(data.encode("utf-8"))
            await self.process.stdin.drain()

        logger.debug("LSP request -> %s", payload)

        effective_timeout = timeout if timeout is not None else self.config.timeout
        try:
            return await asyncio.wait_for(future, timeout=effective_timeout)
        except TimeoutError:
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(
                        LspError(-32001, "LSP server terminated due to timeout")
                    )
            self._pending.clear()
            if self.process and self.process.returncode is None:
                logger.warning(
                    "Request '%s' timed out after %.1fs; terminating scheme-langserver",
                    method,
                    effective_timeout,
                )
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=3.0)
                except TimeoutError:
                    logger.warning("scheme-langserver did not terminate; killing")
                    self.process.kill()
                    await self.process.wait()
                self._crashed = True
            raise LspError(
                -32001, f"Request '{method}' timed out after {effective_timeout}s"
            ) from None

    async def _notify(self, method: str, params: Any) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("LSP server not started")
        if self._crashed or self.process.returncode is not None:
            raise RuntimeError(
                "scheme-langserver process has crashed. "
                "Please call lsp_shutdown and lsp_initialize to restart."
            )
        if self._shutdown:
            raise RuntimeError("LSP client is shutting down")

        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params

        payload = json.dumps(message, ensure_ascii=False)
        data = f"Content-Length: {len(payload.encode('utf-8'))}\r\n\r\n{payload}"
        async with self._write_lock:
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
                        self._crashed = True
                        return
                    header_str = header.decode("utf-8", errors="replace").strip()
                    if header_str.startswith("Content-Length:"):
                        try:
                            length = int(header_str.split(":", 1)[1].strip())
                        except ValueError:
                            logger.warning("Malformed Content-Length header: %s", header_str)
                            continue
                    elif header_str == "":
                        # blank line -> end of headers
                        break

                if length < 0:
                    continue

                # Read body
                body = await reader.readexactly(length)
                try:
                    msg = json.loads(body.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    logger.warning("Failed to decode LSP message: %s", body)
                    continue

                logger.debug("LSP message <- %s", msg)
                self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except asyncio.IncompleteReadError:
            logger.info("LSP server stdout closed")
            self._crashed = True
        except Exception as exc:
            logger.exception("LSP read loop error: %s", exc)
            self._crashed = True

    async def _drain_stderr(self) -> None:
        """Continuously read stderr to prevent the subprocess from blocking."""
        if self.process is None or self.process.stderr is None:
            return
        while True:
            try:
                line = await self.process.stderr.readline()
            except asyncio.CancelledError:
                raise
            if not line:
                break
            logger.debug(
                "LSP stderr: %s",
                line.decode("utf-8", errors="replace").rstrip(),
            )

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg:
            # Response
            req_id = msg["id"]
            future = self._pending.pop(req_id, None)
            if future is None:
                return
            if "error" in msg:
                err = msg["error"]
                future.set_exception(
                    LspError(
                        err.get("code", 0), err.get("message", ""), err.get("data")
                    )
                )
            else:
                future.set_result(msg.get("result"))
        else:
            # Notification
            method = msg.get("method")
            params = msg.get("params", {})
            if method == "textDocument/publishDiagnostics":
                uri = params.get("uri", "")
                self._diagnostics[uri] = params.get("diagnostics", [])
                logger.debug(
                    "Received diagnostics for %s: %d items",
                    uri,
                    len(self._diagnostics[uri]),
                )
            else:
                logger.debug("Unhandled notification: %s", method)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _set_resource_limits(max_memory_bytes: int, max_cpu_seconds: int) -> None:
    """Set RLIMIT_AS and RLIMIT_CPU in the child process.

    Called via preexec_fn so it runs in the scheme-langserver child
    before Chez Scheme starts.
    """
    if not _HAS_RESOURCE:
        return
    try:
        resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))
    except ValueError:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_seconds, max_cpu_seconds))
    except ValueError:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except ValueError:
        pass


def _path_to_uri(path: str) -> str:
    from pathlib import Path
    abs_path = str(Path(path).resolve())
    return "file://" + abs_path

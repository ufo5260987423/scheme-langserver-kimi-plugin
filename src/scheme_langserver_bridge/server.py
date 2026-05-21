"""MCP server bridging Kimi to scheme-langserver."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Config
from .document_sync import DocumentManager
from .lsp_client import LspClient, LspError

logger = logging.getLogger(__name__)

mcp = FastMCP("scheme-langserver-bridge")
_client: LspClient | None = None
_doc_manager: DocumentManager | None = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _file_uri(path: str) -> str:
    return "file://" + str(Path(path).resolve())


def _ensure_client() -> LspClient:
    if _client is None:
        raise RuntimeError(
            "LSP server not initialized. Call lsp_initialize first."
        )
    return _client


def _ensure_doc_manager() -> DocumentManager:
    if _doc_manager is None:
        raise RuntimeError(
            "Document manager not initialized. Call lsp_initialize first."
        )
    return _doc_manager


def _lsp_result(result: Any) -> dict[str, Any]:
    """Wrap LSP result for MCP tool output."""
    if result is None:
        return {
            "content": {
                "result": None,
                "note": "No result from language server.",
            }
        }
    return {"content": result}


def _lsp_error(exc: Exception) -> dict[str, Any]:
    """Wrap LSP error for MCP tool output."""
    if isinstance(exc, LspError):
        return {
            "content": {
                "error": True,
                "lsp_code": exc.code,
                "message": exc.message,
                "data": exc.data,
                "note": (
                    "scheme-langserver returned an error. "
                    "This may be due to incomplete code, unsupported constructs, "
                    "or a known limitation of the server. "
                    "Consider verifying with your own knowledge."
                ),
            }
        }
    return {
        "content": {
            "error": True,
            "message": str(exc),
            "note": (
                "An unexpected error occurred while communicating with scheme-langserver. "
                "You may proceed based on your own understanding of the code."
            ),
        }
    }


# ------------------------------------------------------------------
# Lifecycle tools
# ------------------------------------------------------------------


@mcp.tool()
async def lsp_initialize(root_dir: str) -> dict[str, Any]:
    """Initialize the scheme-langserver connection.

    Must be called before any other LSP tool. Provides the project root
    directory so the language server can resolve imports and analyze
    the codebase.
    """
    global _client, _doc_manager
    if _client is not None:
        return {
            "content": {
                "warning": "LSP server already initialized.",
                "previous_root": _client.config.build_cmd(root_dir),
            }
        }

    config = Config.from_env()
    _client = LspClient(config)
    _doc_manager = DocumentManager(_client)
    try:
        result = await _client.start(root_dir)
        return {
            "content": {
                "initialized": True,
                "server_info": result.get("serverInfo", {}),
                "capabilities": _summarize_caps(result.get("capabilities", {})),
            }
        }
    except Exception as exc:
        _client = None
        _doc_manager = None
        return _lsp_error(exc)


@mcp.tool()
async def lsp_shutdown() -> dict[str, Any]:
    """Gracefully shut down the scheme-langserver connection."""
    global _client, _doc_manager
    if _client is None:
        return {"content": {"warning": "LSP server was not running."}}
    try:
        await _client.stop()
        _client = None
        _doc_manager = None
        return {"content": {"shutdown": True}}
    except Exception as exc:
        return _lsp_error(exc)


# ------------------------------------------------------------------
# Document sync tools
# ------------------------------------------------------------------


@mcp.tool()
async def lsp_open(file_path: str, language_id: str = "scheme") -> dict[str, Any]:
    """Open a file in the language server.

    The server needs to know file contents before it can provide
    hover, completion, or diagnostics for that file.
    """
    doc_mgr = _ensure_doc_manager()
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except Exception as exc:
        return {
            "content": {
                "error": True,
                "message": f"Failed to read file: {exc}",
            }
        }

    uri = _file_uri(file_path)
    try:
        await doc_mgr.open(uri, language_id, text)
        return {"content": {"opened": uri, "lines": text.count("\n") + 1}}
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_change(file_path: str, text: str) -> dict[str, Any]:
    """Notify the language server that a file has changed.

    Send the new full text of the file. This keeps the server's
    internal state in sync with the actual file contents.
    """
    doc_mgr = _ensure_doc_manager()
    uri = _file_uri(file_path)
    try:
        await doc_mgr.change(uri, text)
        return {"content": {"changed": uri, "lines": text.count("\n") + 1}}
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_close(file_path: str) -> dict[str, Any]:
    """Close a file in the language server."""
    doc_mgr = _ensure_doc_manager()
    uri = _file_uri(file_path)
    try:
        await doc_mgr.close(uri)
        return {"content": {"closed": uri}}
    except Exception as exc:
        return _lsp_error(exc)


# ------------------------------------------------------------------
# Query tools
# ------------------------------------------------------------------


@mcp.tool()
async def lsp_hover(file_path: str, line: int, character: int) -> dict[str, Any]:
    """Get hover information (type, docs) for a symbol at a position.

    Args:
        file_path: Absolute path to the file.
        line: Zero-based line number.
        character: Zero-based character (column) position.
    """
    client = _ensure_client()
    uri = _file_uri(file_path)
    try:
        result = await client.hover(uri, line, character)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_complete(file_path: str, line: int, character: int) -> dict[str, Any]:
    """Get completion suggestions at a position.

    Returns identifiers available in the current scope, including
    local bindings (let, lambda parameters) that may not be obvious
    from a simple text search.
    """
    client = _ensure_client()
    uri = _file_uri(file_path)
    try:
        result = await client.completion(uri, line, character)
        return _lsp_result(result)
    except LspError as exc:
        if exc.code == -32001 and "timed out" in exc.message:
            return {
                "content": {
                    "error": True,
                    "message": (
                        "completion 请求超时，scheme-langserver 的补全功能"
                        "在当前位置响应较慢"
                    ),
                    "note": (
                        "scheme-langserver 的代码补全在某些位置可能需要较长时间。"
                        "你可以尝试在其他位置请求补全，或者继续基于已有知识编写代码。"
                    ),
                }
            }
        return _lsp_error(exc)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_definition(file_path: str, line: int, character: int) -> dict[str, Any]:
    """Find the definition location of a symbol.

    Returns file URI, line, and column where the identifier is defined.
    """
    client = _ensure_client()
    uri = _file_uri(file_path)
    try:
        result = await client.definition(uri, line, character)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_references(
    file_path: str, line: int, character: int, include_declaration: bool = False
) -> dict[str, Any]:
    """Find all references to a symbol across the workspace.

    Args:
        include_declaration: Whether to include the definition site
            in the results.
    """
    client = _ensure_client()
    uri = _file_uri(file_path)
    try:
        result = await client.references(uri, line, character, include_declaration)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_rename(file_path: str, line: int, character: int, new_name: str) -> dict[str, Any]:
    """Compute workspace edits to rename a symbol.

    Returns a set of text document edits that can be applied to
    safely rename the symbol across all files.
    """
    client = _ensure_client()
    uri = _file_uri(file_path)
    try:
        result = await client.rename(uri, line, character, new_name)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_signature(file_path: str, line: int, character: int) -> dict[str, Any]:
    """Get signature help for a function call.

    Shows parameter names and types for the function being called
    at the given position.
    """
    client = _ensure_client()
    uri = _file_uri(file_path)
    try:
        result = await client.signature_help(uri, line, character)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_document_symbol(file_path: str) -> dict[str, Any]:
    """List all symbols defined in a file.

    Useful for getting an overview of a file's structure
    (functions, variables, macros, etc.).
    """
    client = _ensure_client()
    uri = _file_uri(file_path)
    try:
        result = await client.document_symbol(uri)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_workspace_symbol(query: str) -> dict[str, Any]:
    """Search symbols across the entire workspace.

    Performs a cross-workspace symbol search using the language server.
    Returns all symbols matching the query string across all indexed files.
    """
    client = _ensure_client()
    try:
        result = await client.workspace_symbol(query)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_code_action(
    file_path: str, start_line: int, start_character: int, end_line: int, end_character: int
) -> dict[str, Any]:
    """Get code actions (quick fixes, refactorings) for a range.
    """
    client = _ensure_client()
    uri = _file_uri(file_path)
    try:
        result = await client.code_action(uri, start_line, start_character, end_line, end_character)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_diagnostics(file_path: str | None = None) -> dict[str, Any]:
    """Get diagnostic messages (errors, warnings) from the language server.

    Args:
        file_path: If provided, returns diagnostics for that file only.
            If omitted, returns diagnostics for all open files.
    """
    client = _ensure_client()
    try:
        uri = _file_uri(file_path) if file_path else None
        result = client.get_diagnostics(uri)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


# ------------------------------------------------------------------
# Internal
# ------------------------------------------------------------------


def _summarize_caps(caps: dict[str, Any]) -> dict[str, bool]:
    """Summarize LSP server capabilities for the init response."""
    # scheme-langserver uses top-level provider flags (e.g. hoverProvider)
    # rather than nested textDocument objects
    return {
        "hover": bool(caps.get("hoverProvider")),
        "completion": bool(caps.get("completionProvider")),
        "definition": bool(caps.get("definitionProvider")),
        "references": bool(caps.get("referencesProvider")),
        "rename": bool(caps.get("renameProvider")),
        "signatureHelp": bool(caps.get("signatureHelpProvider")),
        "documentSymbol": bool(caps.get("documentSymbolProvider")),
        "workspaceSymbol": bool(caps.get("workspaceSymbolProvider")),
        "codeAction": bool(caps.get("codeActionProvider")),
        "diagnostics": bool(caps.get("publishDiagnostics")),
    }

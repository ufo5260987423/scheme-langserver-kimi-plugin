"""MCP server bridging Kimi to scheme-langserver."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Config, _find_akku_libdirs
from .crash_reporter import CrashReporter
from .document_sync import DocumentManager
from .lsp_client import LspClient, LspError

logger = logging.getLogger(__name__)

_INSTRUCTIONS = """\
You are assisting Kimi to analyze and generate Scheme code via the scheme-langserver LSP
bridge.

## Core Principles
- scheme-langserver is a reference tool, NOT an absolute authority. Cross-check its output
  with your own training knowledge.
- Do NOT call LSP tools for simple Scheme language questions, trivial code, or when the
  user explicitly says "no tools".
- DO call LSP tools when you need: local scope bindings, precise type info, cross-file
  definitions/references, syntax/semantic validation, or refactoring impact analysis.

## Editor-Style Workflow
Treat this bridge like an IDE/editor, not a query API.
1. Call lsp_initialize(root_dir=...) once per session. It may take a few seconds
   because scheme-langserver performs initial indexing. Be patient and do NOT
   restart repeatedly.
2. Open all files you plan to work on via lsp_open (like opening tabs in an editor).
   If you are working on multiple related Scheme files (e.g., a library and its tests),
   open all of them at the start of the session rather than one by one on demand.
   Keep them open for the duration of the session. Do NOT open and close repeatedly.
3. When you edit a file via filesystem tools (WriteFile/StrReplaceFile), immediately
   call lsp_change to sync the new content to the server (like pressing Save).
4. After syncing, call lsp_diagnostics to check for errors (like IDE real-time linting).
5. Iterate: edit -> lsp_change -> lsp_diagnostics -> edit -> ...
6. Only call lsp_close when you are truly done with a file, or at session end.

## Fallback Policy
If lsp_initialize fails to start (timeout, executable not found, crash on startup),
do NOT keep retrying or troubleshooting. Simply abandon the LSP tools for this
session and proceed based entirely on your own training knowledge.
If a tool returns timeout (-32001), retry once; if it still fails, fall back to your
own knowledge.
If a tool returns "method not found" (-32601), fall back to your own knowledge and do
NOT expose the raw error to the user.

## Confidence Levels
- HIGH (trust): definition, references, basic diagnostics (bracket matching, undefined id)
- MEDIUM (reference only): completion, hover, documentSymbol, workspaceSymbol
- LOW (verify carefully): type inference (experimental, often wrong on higher-order
  functions and macro-expanded code)

## Known Limitations
- Type inference is experimental and may give absurd results for complex higher-order
  functions or macro-expanded expressions.
- Macro support is incomplete: syntax-case/syntax-rules expansion may mis-capture ids.
- Analysis of incomplete code is best-effort and may misjudge.
- Chez Scheme-specific extensions (foreign-procedure, ftype, thread primitives) may not
  be recognized.
- The server is actively developed and contains bugs.
- rename and signatureHelp are on the roadmap and may return -32601.

## Cross-Validation Rule
When LSP output contradicts your training data, trust your training data. Treat LSP
results as clues for further investigation, not as final verdicts.
"""

mcp = FastMCP("scheme-langserver-bridge", instructions=_INSTRUCTIONS)
_client: LspClient | None = None
_doc_manager: DocumentManager | None = None
_init_lock = asyncio.Lock()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _file_uri(path: str) -> str:
    from urllib.parse import quote
    abs_path = str(Path(path).absolute())
    return "file://" + quote(abs_path, safe="/")


def _infer_root_dir(file_path: str) -> str:
    """Infer project root directory from a file path."""
    path = Path(file_path).resolve()
    for p in [path] + list(path.parents):
        if (p / ".akku").exists() or (p / "Akku.manifest").exists():
            return str(p)
    for p in [path] + list(path.parents):
        if (p / ".git").exists():
            return str(p)
    return str(path.parent if path.is_file() else path)


async def _ensure_initialized(file_path: str | None = None) -> LspClient:
    """Ensure LSP client is initialized, auto-restarting if it crashed."""
    crashed = getattr(_client, "_crashed", False) is True
    if _client is None or crashed:
        async with _init_lock:
            crashed = getattr(_client, "_crashed", False) is True
            if _client is None or crashed:
                # Snapshot open documents before shutdown so we can re-open them.
                stale_docs: dict[str, tuple[str, str]] = {}
                if _doc_manager is not None:
                    for uri in _doc_manager.list_uris():
                        doc = _doc_manager.get(uri)
                        if doc:
                            stale_docs[uri] = (doc["language_id"], doc["text"])
                if crashed:
                    logger.info("LSP server crashed, shutting down before restart...")
                    try:
                        await lsp_shutdown()
                    except Exception as exc:
                        logger.warning("lsp_shutdown during auto-restart failed: %s", exc)
                root_dir = _infer_root_dir(file_path or os.getcwd())
                result = await lsp_initialize(root_dir)
                content = result.get("content", {})
                if content.get("error"):
                    raise RuntimeError(f"Auto-initialization failed: {content}")
                # Re-open documents that were open before the crash.
                if _doc_manager is not None and stale_docs:
                    for uri, (lang_id, text) in stale_docs.items():
                        try:
                            await _doc_manager.open(uri, lang_id, text)
                        except Exception as exc:
                            logger.warning(
                                "Failed to re-open %s after restart: %s", uri, exc
                            )
    if _client is None:
        raise RuntimeError("LSP server not initialized. Call lsp_initialize first.")
    return _client


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
    crashed = getattr(_client, "_crashed", False) is True
    if _client is not None and not crashed:
        return {
            "content": {
                "warning": "LSP server already initialized.",
                "previous_root": root_dir,
            }
        }

    if _client is not None and crashed:
        try:
            await _client.stop()
        except Exception as exc:
            logger.warning("Cleanup of crashed client failed: %s", exc)
        _client = None
        _doc_manager = None

    try:
        config = Config.load(root_dir)
    except Exception as exc:
        return _lsp_error(exc)

    _client = LspClient(config)
    _doc_manager = DocumentManager(_client)

    crash_reporter = CrashReporter(config)
    crash_reporter.attach(_client, _doc_manager)
    _client._crash_reporter = crash_reporter

    try:
        result = await _client.start(root_dir)
        response: dict[str, Any] = {
            "content": {
                "initialized": True,
                "server_info": result.get("serverInfo", {}),
                "capabilities": _summarize_caps(result.get("capabilities", {})),
                "server_version": config.version_info.get("tag", "unknown"),
                "server_source": config.server_source,
                "version_check": config.version_info,
            }
        }
        return response
    except Exception as exc:
        _client = None
        _doc_manager = None
        return _lsp_error(exc)


@mcp.tool()
async def lsp_export_debug_report(output_path: str | None = None) -> dict[str, Any]:
    """Export a debug report for scheme-langserver upstream issue reporting.

    The report contains the LSP traffic log, open project files, environment
    info, and server stderr. Review before sharing publicly — it includes
    source code.

    **When to use**: scheme-langserver crashed or returned clearly wrong results,
    and you want to help debug by generating a report for the upstream issue
    tracker (https://github.com/ufo5260987423/scheme-langserver/issues).
    """
    try:
        client = _ensure_client()
        if client._crash_reporter is None:
            return {
                "content": {
                    "error": True,
                    "message": "Crash reporter not initialized. Call lsp_initialize first.",
                }
            }

        target = Path(output_path) if output_path else None
        report_dir = client._crash_reporter.generate_report(
            output_dir=target, reason="manual"
        )

        return {
            "content": {
                "report_path": str(report_dir),
                "note": (
                    "Report generated. It contains your source code. "
                    "Please review before posting to a public issue tracker."
                ),
            }
        }
    except Exception as exc:
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
    """Open a file in the language server (like opening a tab in an editor).

    The server needs to know file contents before it can provide
    hover, completion, or diagnostics for that file.

    **When to use**: When you start working on a Scheme file. Keep the file open
    for the duration of the session; do NOT open and close repeatedly.
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
    """Notify the language server that a file has changed (like pressing Save).

    Send the new full text of the file. This keeps the server's
    internal state in sync with the actual file contents.

    **When to use**: IMMEDIATELY after you modify a Scheme file via filesystem
    tools (WriteFile/StrReplaceFile). If you skip this step, subsequent
    diagnostics and queries will operate on stale content.
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
    """Close a file in the language server.

    **When to use**: When you are completely done with a file or at session end.
    Do NOT close files just because you paused editing; frequent open/close wastes
    server resources.
    """
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
    """Show hover tooltip (type, docs) for the symbol under the cursor.

    Like hovering the mouse over an identifier in an IDE.

    **Confidence**: MEDIUM. Type inference is experimental; info about
    macro-expanded identifiers may be inaccurate. Cross-check with your knowledge.

    Args:
        file_path: Absolute path to the file.
        line: Zero-based line number.
        character: Zero-based character (column) position.
    """
    uri = _file_uri(file_path)
    try:
        client = await _ensure_initialized(file_path)
        result = await client.hover(uri, line, character)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_complete(file_path: str, line: int, character: int) -> dict[str, Any]:
    """Trigger auto-completion at the cursor position.

    Like pressing Ctrl+Space in an IDE. Returns identifiers available in the
    current scope, including local bindings (let, lambda parameters) that may
    not be obvious from a simple text search.

    **Confidence**: MEDIUM. The list may miss identifiers generated by macros.
    """
    uri = _file_uri(file_path)
    try:
        client = await _ensure_initialized(file_path)
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
    """Go to Definition: jump to where the symbol is defined.

    Like pressing F12 in an IDE. Returns file URI, line, and column.

    **Confidence**: HIGH. Reliable for locating definitions across files.
    """
    uri = _file_uri(file_path)
    try:
        client = await _ensure_initialized(file_path)
        result = await client.definition(uri, line, character)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_references(
    file_path: str, line: int, character: int, include_declaration: bool = False
) -> dict[str, Any]:
    """Find All References: list every usage of the symbol across the workspace.

    Like Shift+F12 in an IDE.

    **Confidence**: HIGH. Reliable for assessing impact before refactoring.

    Args:
        include_declaration: Whether to include the definition site
            in the results.
    """
    uri = _file_uri(file_path)
    try:
        client = await _ensure_initialized(file_path)
        result = await client.references(uri, line, character, include_declaration)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_rename(file_path: str, line: int, character: int, new_name: str) -> dict[str, Any]:
    """Compute workspace edits to rename a symbol.

    Returns a set of text document edits that can be applied to
    safely rename the symbol across all files.

    **Limitation**: rename is on the scheme-langserver roadmap and may return
    -32601 "method not found". If so, fall back to manual renaming.
    """
    uri = _file_uri(file_path)
    try:
        client = await _ensure_initialized(file_path)
        result = await client.rename(uri, line, character, new_name)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_signature(file_path: str, line: int, character: int) -> dict[str, Any]:
    """Get signature help for a function call.

    Shows parameter names and types for the function being called
    at the given position.

    **Limitation**: signatureHelp is on the scheme-langserver roadmap and may
    return -32601 "method not found". If so, fall back to your own knowledge.
    """
    uri = _file_uri(file_path)
    try:
        client = await _ensure_initialized(file_path)
        result = await client.signature_help(uri, line, character)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_document_symbol(file_path: str) -> dict[str, Any]:
    """Show the file's symbol outline (functions, variables, macros).

    Like the Outline / Structure panel in an IDE.

    **Confidence**: MEDIUM. Symbols generated by macros may be missing.
    """
    uri = _file_uri(file_path)
    try:
        client = await _ensure_initialized(file_path)
        result = await client.document_symbol(uri)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_workspace_symbol(query: str) -> dict[str, Any]:
    """Search symbols across the entire workspace.

    Like Ctrl+T / Go to Symbol in an IDE. Returns all symbols matching the query
    string across all indexed files.

    **Confidence**: MEDIUM. Accuracy depends on index completeness.
    Requires scheme-langserver >= 2.1.0.
    """
    try:
        client = await _ensure_initialized(os.getcwd())
        result = await client.workspace_symbol(query)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_code_action(
    file_path: str, start_line: int, start_character: int, end_line: int, end_character: int
) -> dict[str, Any]:
    """Get code actions (quick fixes, refactorings) for a range.

    **Limitation**: codeAction is on the scheme-langserver roadmap and may
    return -32601 "method not found" or behave incompletely.
    """
    uri = _file_uri(file_path)
    try:
        client = await _ensure_initialized(file_path)
        result = await client.code_action(uri, start_line, start_character, end_line, end_character)
        return _lsp_result(result)
    except Exception as exc:
        return _lsp_error(exc)


@mcp.tool()
async def lsp_diagnostics(file_path: str | None = None) -> dict[str, Any]:
    """Get diagnostic messages (errors, warnings) from the language server.

    **When to use**: After lsp_change to check for errors, like IDE real-time
    linting. Also useful before finishing a task to ensure no errors were introduced.

    **Confidence**: HIGH for basic syntax (brackets, undefined ids). MEDIUM/LOW
    for semantic errors and implementation-specific extensions (Chez-specific
    forms may be falsely flagged).

    Args:
        file_path: If provided, returns diagnostics for that file only.
            If omitted, returns diagnostics for all open files.
    """
    try:
        client = await _ensure_initialized(file_path or os.getcwd())
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

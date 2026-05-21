# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-05-21

### Added
- Initial release of `scheme-langserver-bridge`.
- MCP server built on `FastMCP` exposing LSP operations as MCP tools for Kimi Code CLI.
- **Lifecycle tools**: `lsp_initialize`, `lsp_shutdown`.
- **Document sync tools**: `lsp_open`, `lsp_change`, `lsp_close` with full-document synchronization.
- **Query tools**: `lsp_hover`, `lsp_complete`, `lsp_definition`, `lsp_references`, `lsp_rename`, `lsp_signature`, `lsp_document_symbol`, `lsp_workspace_symbol`, `lsp_code_action`.
- **Diagnostics tool**: `lsp_diagnostics` collects `textDocument/publishDiagnostics` notifications from the server.
- **Environment-based configuration** via `SCHEME_LANGSERVER_PATH`, `SCHEME_LANGSERVER_LOG_PATH`, `SCHEME_LANGSERVER_MULTI_THREAD`, `SCHEME_LANGSERVER_TYPE_INFERENCE`, `SCHEME_LANGSERVER_TOP_ENVIRONMENT`, `SCHEME_LANGSERVER_TIMEOUT`, `SCHEME_LANGSERVER_COMPLETION_TIMEOUT`, `SCHEME_LANGSERVER_MAX_MEMORY_MB`, `SCHEME_LANGSERVER_MAX_CPU_SECONDS`, and `SCHEME_BRIDGE_LOGLEVEL`.
- **Automatic executable discovery**: falls back from environment variable → `PATH` → known local development paths.
- **Resource limits** (`RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_CORE`) applied to the scheme-langserver child process via `preexec_fn`.
- **Timeout handling**: requests time out after a configurable duration; the server is terminated (and killed if necessary) and marked as crashed.
- **Crash detection**: unexpected process exit or EOF sets a crashed flag; subsequent calls prompt Kimi to restart via `lsp_shutdown` + `lsp_initialize`.
- **Concurrent write safety**: async lock serializes JSON-RPC writes to the LSP server stdin.
- **Graceful shutdown**: signal handlers for `SIGINT` / `SIGTERM` stop the LSP server before the bridge exits.
- **Friendly error wrapping**: LSP errors and unexpected exceptions are returned to Kimi with explanatory notes instead of raw stack traces.
- **Nix flake** with dev shell (`nix develop`) and package (`nix run .#scheme-langserver-bridge`).
- **Test suite**: unit tests for LSP client I/O, timeout cleanup, crash recovery, resource limits, concurrency, malformed messages, and integration tests against a real scheme-langserver process.

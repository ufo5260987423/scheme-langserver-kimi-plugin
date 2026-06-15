# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Project-level configuration** via `.scheme-langserver.toml` or `.scheme-langserver.json` in the project root.
  - Supported fields: `langserver_path`, `top_environment`, `multi_thread`, `type_inference`, `log_path`, `cache_path`, `auto_update`.
  - Configuration priority: project config > environment variables > defaults.
- **Auto-download** scheme-langserver from GitHub Releases when no local executable is found.
  - Uses GitHub's static redirect URL (`releases/latest/download/...`) with `HEAD` requests to detect the latest version **without consuming API rate limits**.
  - Downloads are cached to `~/.cache/scheme-langserver-bridge/versions/<version>/`.
  - Version-check results are cached with a 1-hour TTL to avoid repeated network requests.
  - Controlled by `auto_update` in project config or `SCHEME_LANGSERVER_AUTO_UPDATE` env var (default `true`).
  - Currently supports Linux x86_64 glibc only; other platforms gracefully fall back to manual installation instructions.
- **Debug report collection** for upstream bug reporting.
  - New module `crash_reporter.py` collects LSP traffic, project snapshots, stderr, and environment metadata.
  - `ready-for-analyse.log` is generated in the exact format consumed by scheme-langserver's replay scripts (`bin/log-debug.sps` / `bin/parallel-log-debug.sps`).
  - Automatic report generation on crash (EOF, IncompleteReadError, timeout kill).
  - New MCP tool `lsp_export_debug_report` for manual on-demand capture.
  - Privacy warning embedded in every report — users must review before sharing publicly.
  - New environment variable `SCHEME_BRIDGE_REPORT_DIR`.
- `lsp_diagnostics` now returns a structured report per file:
  - `summary` with counts for each LSP severity (`error`, `warning`, `information`, `hint`).
  - `diagnostics` sorted by severity (most severe first).
  - Surfaces `source` and `code` fields when provided by the server (scheme-langserver 2.1.0+).
- **scheme-langserver launch command** now uses named flags instead of positional arguments, matching `run.ss` in scheme-langserver 2.1.3+.
  - Adds `--top-environment` to the default launch flags.
  - Enables `--cache-path` by default for scheme-langserver 2.1.3+, using `.scheme-langserver-cache` in the project root. Override via the `cache_path` project config field or `SCHEME_LANGSERVER_CACHE_PATH` env var.

### Changed
- Confirmed compatibility with scheme-langserver **2.1.3**.
- Updated documentation to reflect server-side feature availability:
  - `workspace/symbol` requires scheme-langserver ≥ 2.1.0 (no protocol changes in 2.1.3).
  - `textDocument/rename`, `textDocument/signatureHelp`, and `textDocument/codeAction` are exposed by the bridge but remain on the scheme-langserver roadmap; the server may not yet implement them.
- Expanded known-limitations to cover 2.1.0+ diagnostics enhancements (duplicate identifiers, unused imports, tokenizer errors) and macro auto-resolution status (experimentally correct but disabled in production).
- Documented 2.1.1 server-side fixes: `typed-lambda/lambda` dotted-formals crash, `identifier-compare? symbol?` guard, `rename/alias` unused-import false positive, R7RS/S7 tokenizer compatibility, and `display-condition` diagnostics improvement.
- Documented scheme-langserver 2.1.2 restoration of bracket-mismatch diagnostics (`unclosed parenthesis`, `unexpected close bracket`) in the fault-tolerant tokenizer; this capability remains in 2.1.3.
- `flake.nix` now downloads the pinned scheme-langserver release binary from GitHub Releases for Linux x86_64 glibc, instead of relying solely on the nixpkgs package version. The pinned version is now **v2.1.3**; other platforms continue to fall back to `pkgs.scheme-langserver` when available.
- `config.build_cmd()` now passes scheme-langserver options as named flags (`--log-path`, `--multi-thread`, `--type-inference`, `--top-environment`, optional `--cache-path`) because `run.ss` ignores positional operands.

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

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-05-21

### Added
- Initial release of `scheme-langserver-bridge`.
- MCP server exposing LSP operations as MCP tools for Kimi Code CLI.
- Tools: `lsp_initialize`, `lsp_open`, `lsp_change`, `lsp_close`, `lsp_hover`, `lsp_complete`, `lsp_definition`, `lsp_references`, `lsp_rename`, `lsp_signature`, `lsp_document_symbol`, `lsp_code_action`, `lsp_diagnostics`, `lsp_shutdown`.
- Configurable via environment variables (`SCHEME_LANGSERVER_PATH`, `SCHEME_LANGSERVER_LOG_PATH`, etc.).
- Nix flake with dev shell and package.

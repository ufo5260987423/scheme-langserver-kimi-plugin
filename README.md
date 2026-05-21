# scheme-langserver-bridge

MCP (Model Context Protocol) bridge that connects **Kimi Code CLI** to **scheme-langserver**, giving Kimi real-time language intelligence when working with Scheme code.

## What this does

When you ask Kimi to write, refactor, or explain Scheme code, Kimi can now call scheme-langserver behind the scenes to obtain:

- **Precise type information** — via `textDocument/hover`
- **Scope-aware completions** — via `textDocument/completion` (includes local `let` and `lambda` bindings)
- **Exact definition locations** — via `textDocument/definition`
- **Cross-reference search** — via `textDocument/references`
- **Syntax / semantic diagnostics** — via `textDocument/publishDiagnostics`
- **Safe rename edits** — via `textDocument/rename`
- **Function signatures** — via `textDocument/signatureHelp`

**Important**: You (the user) never interact with scheme-langserver directly. Kimi invokes the bridge tools automatically when it judges that precise code information would help its reasoning.

## Known limitations of scheme-langserver

scheme-langserver is actively developed and **not infallible**:

- Type inference is experimental and may be wrong or hang on complex code.
- Macro support (`syntax-case`, `syntax-rules`) is incomplete.
- Analysis of unfinished code is best-effort.
- Implementation-specific Chez Scheme extensions may not be recognized.

Kimi is expected to treat LSP output as a **reference**, cross-check it against its own training knowledge, and gracefully fall back when the server returns errors or nonsense.

## Installation

### Via Nix (recommended for NixOS)

```bash
nix run .#scheme-langserver-bridge
```

Or enter the development shell:

```bash
nix develop
uv sync --extra dev
```

### Via Python (any OS)

Requires Python 3.12+ and a local scheme-langserver executable.

```bash
git clone <this-repo>
cd scheme-langserver-bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuring Kimi

Add the bridge as an MCP server:

```bash
kimi mcp add --transport stdio scheme-langserver -- \
  python3 -m scheme_langserver_bridge
```

Or manually edit `~/.kimi/mcp.json`:

```json
{
  "mcpServers": {
    "scheme-langserver": {
      "command": "python3",
      "args": ["-m", "scheme_langserver_bridge"],
      "env": {
        "SCHEME_LANGSERVER_PATH": "/path/to/scheme-langserver/run"
      }
    }
  }
}
```

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `SCHEME_LANGSERVER_PATH` | Path to the scheme-langserver executable | auto-discover |
| `SCHEME_LANGSERVER_LOG_PATH` | Log file path | `.scheme-langserver.log` in cwd |
| `SCHEME_LANGSERVER_MULTI_THREAD` | `enable` / `disable` | `enable` |
| `SCHEME_LANGSERVER_TYPE_INFERENCE` | `enable` / `disable` | `enable` |
| `SCHEME_LANGSERVER_TOP_ENVIRONMENT` | `R6RS` / `R7RS` / `s7` / `goldfish` | `R6RS` |
| `SCHEME_LANGSERVER_TIMEOUT` | Request timeout in seconds | `30.0` |

## Available MCP Tools

All tools are prefixed with `lsp_`:

| Tool | Purpose |
|------|---------|
| `lsp_initialize` | Start scheme-langserver for a project root |
| `lsp_open` | Open a file so the server can analyze it |
| `lsp_change` | Push updated file contents to the server |
| `lsp_close` | Close a file |
| `lsp_hover` | Get type/docs for a symbol at a position |
| `lsp_complete` | Get completion candidates at a position |
| `lsp_definition` | Find where a symbol is defined |
| `lsp_references` | Find all references to a symbol |
| `lsp_rename` | Compute workspace edits to rename a symbol |
| `lsp_signature` | Get function signature help |
| `lsp_document_symbol` | List all symbols in a file |
| `lsp_code_action` | Get quick fixes / refactorings for a range |
| `lsp_diagnostics` | Get errors and warnings |
| `lsp_shutdown` | Stop the language server |

## Development

```bash
nix develop          # enter dev shell
uv sync --extra dev  # install Python deps
pytest               # run tests
ruff check --fix     # lint
pyright              # type check
```

## Architecture

```
Kimi CLI  <--MCP (stdio)-->  scheme-langserver-bridge  <--LSP (stdio)-->  scheme-langserver
```

The bridge is a thin Python layer that:
1. Spawns scheme-langserver as a subprocess.
2. Speaks JSON-RPC 2.0 over stdio with the LSP server.
3. Exposes LSP operations as MCP tools for Kimi to call.

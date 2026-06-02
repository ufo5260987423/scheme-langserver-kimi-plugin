# scheme-langserver-bridge

MCP (Model Context Protocol) bridge that connects **Kimi Code CLI** to **scheme-langserver**, giving Kimi real-time language intelligence when working with Scheme code.

## What this does

When you ask Kimi to write, refactor, or explain Scheme code, Kimi can now call scheme-langserver behind the scenes to obtain:

- **Precise type information** — via `textDocument/hover`
- **Scope-aware completions** — via `textDocument/completion` (includes local `let` and `lambda` bindings)
- **Exact definition locations** — via `textDocument/definition`
- **Cross-reference search** — via `textDocument/references`
- **Syntax / semantic diagnostics** — via `textDocument/publishDiagnostics`
- **Safe rename edits** — via `textDocument/rename` (server support is on the roadmap)
- **Function signatures** — via `textDocument/signatureHelp` (server support is on the roadmap)
- **Workspace-wide symbol search** — via `workspace/symbol` (requires scheme-langserver ≥ 2.1.0)
- **Code actions** — via `textDocument/codeAction` (server support is on the roadmap)

**Important**: You (the user) never interact with scheme-langserver directly. Kimi invokes the bridge tools automatically when it judges that precise code information would help its reasoning.

## Known limitations of scheme-langserver

scheme-langserver is actively developed and **not infallible**:

- Type inference is experimental and may be wrong or hang on complex code.
- Macro support (`syntax-case`, `syntax-rules`) is incomplete. Production builds fall back to hand-written rules.
- Analysis of unfinished code is best-effort.
- Implementation-specific Chez Scheme extensions may not be recognized.
- `workspace/symbol` requires scheme-langserver **≥ 2.1.0**.
- `textDocument/rename`, `textDocument/signatureHelp`, and `textDocument/codeAction` are exposed by the bridge but still on the server's roadmap; the server may return "method not found".

Kimi is expected to treat LSP output as a **reference**, cross-check it against its own training knowledge, and gracefully fall back when the server returns errors or nonsense.

## Installation

### Via PyPI (any OS)

Requires Python 3.12+ and a local [scheme-langserver](https://github.com/ufo5260987423/scheme-langserver) executable.

```bash
pip install scheme-langserver-bridge
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install scheme-langserver-bridge
```

### Via Nix

```bash
nix run .#scheme-langserver-bridge
```

Or enter the development shell:

```bash
nix develop
uv sync --extra dev
```

### From source

```bash
git clone <this-repo>
cd scheme-langserver-bridge
uv sync --extra dev
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

## Configuration

Configuration is resolved in the following priority (highest first):

1. **Project config** — `.scheme-langserver.toml` (or `.scheme-langserver.json`) in the project root
2. **Environment variables**
3. **Built-in defaults**

### Project configuration file

Create `.scheme-langserver.toml` in your project root:

```toml
langserver_path = "/nix/store/.../bin/scheme-langserver"
multi_thread = "enable"
type_inference = "enable"
top_environment = "R6RS"
auto_update = true
```

Supported fields:

| Field | Type | Description |
|-------|------|-------------|
| `langserver_path` | string | Override the scheme-langserver executable path |
| `multi_thread` | string | `enable` / `disable` |
| `type_inference` | string | `enable` / `disable` |
| `top_environment` | string | `R6RS` / `R7RS` / `s7` / `goldfish` |
| `log_path` | string | Override the log file path |
| `auto_update` | bool | Allow auto-download when no executable is found |

### Auto-download

If no scheme-langserver executable is found locally, the bridge can **automatically download** the latest release from GitHub:

- Uses GitHub's static redirect URL (`releases/latest/download/...`) with `HEAD` requests to detect the latest version **without consuming API rate limits**.
- Downloads are cached to `~/.cache/scheme-langserver-bridge/versions/<version>/`.
- Version-check results are cached with a 1-hour TTL.
- Controlled by `auto_update` in project config or `SCHEME_LANGSERVER_AUTO_UPDATE` env var (default `true`).
- **Currently supports Linux x86_64 glibc only.** Other platforms will receive a manual-installation hint.

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SCHEME_LANGSERVER_PATH` | Path to the scheme-langserver executable | auto-discover |
| `SCHEME_LANGSERVER_LOG_PATH` | Log file path | `.scheme-langserver.log` in cwd |
| `SCHEME_LANGSERVER_MULTI_THREAD` | Multi-threading `enable` / `disable` | `enable` |
| `SCHEME_LANGSERVER_TYPE_INFERENCE` | Type inference `enable` / `disable` | `enable` |
| `SCHEME_LANGSERVER_TOP_ENVIRONMENT` | Top-level environment: `R6RS` / `R7RS` / `s7` / `goldfish` | `R6RS` |
| `SCHEME_LANGSERVER_TIMEOUT` | Request timeout in seconds | `30.0` |
| `SCHEME_LANGSERVER_COMPLETION_TIMEOUT` | Completion request timeout in seconds | `30.0` |
| `SCHEME_LANGSERVER_MAX_MEMORY_MB` | Sub-process memory limit in MB | `1024` |
| `SCHEME_LANGSERVER_MAX_CPU_SECONDS` | Sub-process CPU time limit in seconds | `180` |
| `SCHEME_LANGSERVER_AUTO_UPDATE` | Allow auto-download when no executable is found | `true` |
| `SCHEME_BRIDGE_LOGLEVEL` | Bridge log level: `DEBUG` / `INFO` / `WARNING` / `ERROR` | `INFO` |
| `SCHEME_BRIDGE_REPORT_DIR` | Default directory for debug crash reports | current working dir |

## Resource Limits

The bridge applies **hard resource limits** to the scheme-langserver child process via Unix `setrlimit`:

- **Memory** (`RLIMIT_AS`): capped at `SCHEME_LANGSERVER_MAX_MEMORY_MB` (default 1024 MB). If the server tries to allocate beyond this limit, the OS will deny the allocation.
- **CPU time** (`RLIMIT_CPU`): capped at `SCHEME_LANGSERVER_MAX_CPU_SECONDS` (default 180 s). If the server consumes more CPU time, the kernel sends `SIGXCPU` and terminates it.
- **Core dumps** (`RLIMIT_CORE`): disabled to avoid filling disk on crashes.

### Timeout and force-kill behavior

If an LSP request exceeds its timeout (default 30 s, or the completion-specific timeout):

1. The bridge first **terminates** (`SIGTERM`) the scheme-langserver process.
2. It waits up to 3 seconds for graceful exit.
3. If the process is still alive, it is **killed** (`SIGKILL`).
4. All pending requests receive a timeout error so Kimi can fall back to its own knowledge.

### Crash detection and recovery

The bridge monitors the LSP process:

- If the process exits unexpectedly (stdout EOF, stderr close), the bridge marks it as **crashed**.
- Any subsequent tool call returns an error telling Kimi to call `lsp_shutdown` followed by `lsp_initialize` to restart the server.
- On shutdown signals (`SIGINT`, `SIGTERM`), the bridge gracefully stops the LSP server before exiting.

### Debug reporting

When scheme-langserver crashes or behaves abnormally, the bridge **automatically generates a debug report** containing:

- LSP traffic log (`ready-for-analyse.log`) — compatible with upstream replay scripts
- Open file snapshots
- Server stderr and own log
- Environment metadata (versions, args, diagnostics)

You can also manually export a report at any time:

```
lsp_export_debug_report(output_path="/optional/path")
```

> ⚠️ Reports contain your source code. Review before posting to a public issue tracker.
>
> Reports are saved to `SCHEME_BRIDGE_REPORT_DIR` (or the current working directory) as:
> `scheme-langserver-debug-report-YYYYMMDD-HHMMSS/`

## Usage Examples

Below are typical Kimi conversations that trigger LSP tools automatically:

### 1. "帮我看看这个 factorial 函数的定义在哪里"

Kimi will call:

```
lsp_definition(file_path="/project/math.scm", line=5, character=9)
```

And receive the exact file, line, and column where `factorial` is defined, e.g.:

```json
{
  "uri": "file:///project/utils.scm",
  "range": {
    "start": { "line": 12, "character": 7 },
    "end": { "line": 12, "character": 16 }
  }
}
```

### 2. "补全这行代码"

When your cursor is inside a `let` binding, Kimi calls:

```
lsp_complete(file_path="/project/main.scm", line=8, character=14)
```

The result includes local bindings (e.g., `n`, `acc`) that a plain text search would miss:

```json
[
  { "label": "factorial", "kind": 3 },
  { "label": "n", "kind": 6 },
  { "label": "acc", "kind": 6 }
]
```

### 3. "这个变量是什么类型"

Kimi calls:

```
lsp_hover(file_path="/project/main.scm", line=4, character=12)
```

And gets the inferred type signature and documentation:

```json
{
  "contents": [
    "```scheme\n(: factorial (-> integer? integer?))\n```",
    "Compute n! recursively."
  ]
}
```

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
| `lsp_workspace_symbol` | Search symbols across the workspace |
| `lsp_code_action` | Get quick fixes / refactorings for a range |
| `lsp_diagnostics` | Get errors and warnings |
| `lsp_export_debug_report` | Export a debug report for upstream issue reporting |

## NixOS Specific Notes

- Enter the development environment with `nix develop`.
- `ruff` and `pyright` must be installed through nixpkgs (they are included in the dev shell via `uv` / `pyproject.toml` dev dependencies).
- On NixOS, the bridge will pick up `scheme-langserver` from `PATH` if it is installed via nixpkgs.

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
1. Spawns scheme-langserver as a subprocess with configurable resource limits.
2. Speaks JSON-RPC 2.0 over stdio with the LSP server.
3. Exposes LSP operations as MCP tools for Kimi to call.
4. Manages document sync, timeouts, crash detection, and graceful shutdown.

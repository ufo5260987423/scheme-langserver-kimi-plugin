# scheme-langserver-kimi-plugin

## 项目目标

为 Kimi Code CLI 提供 scheme-langserver 的集成能力，让 Kimi 在编写 Scheme 代码时能获得实时的语言服务器辅助。

本质上，这是一个 **LSP-MCP Bridge**：在 Kimi（MCP 客户端）和 scheme-langserver（LSP 服务器）之间架设协议转换层。

## 为什么需要这个项目

### Kimi 写 Scheme 的痛点

Kimi 是基于大模型的 AI 助手，它对 Scheme 的理解来自训练数据中的统计模式。但 Scheme 是一门高度灵活的 Lisp 方言，具有以下特征让纯 LLM 推理容易出错：

- **宏系统**：`define-syntax`、`syntax-rules`、`syntax-case` 等宏展开后的代码结构无法仅靠文本推断
- **一等函数与高阶函数**：函数可以像数据一样传递，调用点与定义点之间的类型关系复杂
- **动态类型 + 隐式作用域**：局部绑定（`let`、`lambda` 参数）与顶层绑定的区分需要精确的静态分析
- **S-expression 的括号敏感**：少一个右括号可能导致整个文件结构错乱，LLM 难以靠肉眼精确匹配
- **实现特定扩展**：Chez Scheme 的 `foreign-procedure`、`ftype`、线程原语等不在 r6rs 标准中

### scheme-langserver 能提供的精确信息

scheme-langserver 是基于 Chez Scheme 的静态分析器，能提供以下 LLM 无法靠统计准确推断的信息：

| LSP 能力 | Kimi 获得的具体好处 | 典型场景（Kimi 在做什么） |
|---------|-------------------|------------------------|
| `textDocument/hover` | 精确获知光标处符号的**类型签名**和**文档字符串** | Kimi 分析一段代码时，想知道某个标识符的类型，不再靠训练数据猜测 |
| `textDocument/completion` | 获取当前作用域内**真正可用的标识符**（含局部 `let` 绑定、`lambda` 参数） | Kimi 生成补全建议时，知道哪些名字在当前位置是合法的，避免生成未定义或未导入的符号 |
| `textDocument/definition` | 获知符号定义的**精确文件路径**和**行列号** | Kimi 需要理解代码结构时，直接定位到定义处，不需要全文搜索或靠文件名推测 |
| `textDocument/references` | 获知符号在代码库中的**所有引用位置** | Kimi 重构代码（如重命名、提取函数）时，知道哪些代码会受影响，避免遗漏 |
| `textDocument/publishDiagnostics` | 获取**语法错误**和**语义错误**的实时列表 | Kimi 生成或修改代码后，立即知道是否有错误，及时在回复中修正 |
| `textDocument/rename` | 获取安全重命名所需的**所有修改位置**（跨文件） | Kimi 执行重命名重构时，能一次性给出所有需要改动的位置，保证一致性 |
| `textDocument/signatureHelp` | 获取函数调用的**参数列表**和**参数类型** | Kimi 写函数调用时，知道每个参数应该是什么类型，减少类型不匹配的错误 |
| `workspace/symbol` | 获取工作区中所有匹配的符号 | Kimi 需要在整个项目中搜索某个函数或变量时，快速定位 |
| 类型推断（实验性） | 获取复杂表达式的**推导类型** | Kimi 分析高阶函数、宏展开后的表达式时，有类型信息作为依据，推理更可靠 |

### scheme-langserver 的能力边界（Kimi 必须知道）

scheme-langserver 的返回信息**并非完全可靠**。Kimi 在调用 LSP 工具时，必须把它当作"一个有用的参考来源"，而不是"绝对权威"。

**已知局限性**：

- **类型推断是实验性的**：作者明确标注为 early stage，对复杂高阶函数、宏展开后的表达式可能给出错误类型或不终止
- **宏支持不完善**：`syntax-case`、`syntax-rules` 等宏展开后的标识符捕捉和作用域分析可能出错
- **未完成代码的近似分析**：scheme-langserver 虽然声称支持未完成代码，但静态分析对残缺 AST 的推断本质上是"尽力而为"，可能误判
- **实现特定扩展覆盖有限**：Chez Scheme 的 `foreign-procedure`、`ftype`、线程原语等非标准特性，LSP 可能无法识别
- **活跃开发中，有 bug**：作者多次在 release note 和文档中承认"There're many many bugs"
- **多线程机制增加不确定性**：`-m enable` 开启多线程后，竞态条件可能导致偶发的分析结果不一致

**Kimi 的使用策略**：

1. **交叉验证**：LSP 返回的信息应与 Kimi 自身的训练知识对照。如果两者矛盾，优先相信自己的训练数据，但把 LSP 结果作为疑点进一步排查
2. **置信度分级**：
   - **高置信度**：`definition`（跳转到定义）、`references`（查找引用）、基础语法 `diagnostics`（括号匹配、未定义标识符）
   - **中置信度**：`completion`（补全列表可能漏掉宏生成的标识符）
   - **低置信度**：`type inference`（实验性）、宏相关的 `hover` 信息
3. **fallback 机制**：当 LSP 返回异常、超时或明显荒谬的结果时，Kimi 应优雅降级，直接基于自身知识回答，而不是把错误信息传给用户
4. **不盲从**：如果 LSP 说某个代码有错误，但 Kimi 从语义上判断是正确的（例如使用了实现特定扩展），Kimi 应优先相信自己的判断

### 核心价值：Kimi 直接获益

- **没有 LSP**：Kimi 对当前代码库的理解完全来自训练数据的统计模式。面对 Scheme 的宏、局部作用域、动态类型等特性时，容易"幻觉"——生成在当前上下文不合法的代码，或给出错误的类型推断。
- **有了 LSP**：Kimi 在生成或分析代码的过程中，可以随时调用 Bridge 工具向 scheme-langserver 查询参考信息。Kimi 把这些信息当作**辅助输入**之一，结合自身训练数据进行交叉验证，输出质量显著提升。

**注意**：用户不需要直接与 scheme-langserver 交互，也不需要理解 LSP 协议。用户只是正常地与 Kimi 对话；是 **Kimi 在后台自动调用** LSP 工具来辅助自己的推理过程。

理想的工作流：

```
用户向 Kimi 提出请求（"帮我写个函数"、"这段代码什么意思"、"重构一下"）
    │
    ▼
Kimi 评估当前任务是否需要精确代码信息
    │
    ├── 不需要 → 直接基于训练数据生成回答
    │
    └── 需要 → Kimi **自动**调用 LSP Bridge 工具
                  │
                  ▼
            scheme-langserver 对当前代码做静态分析
                  │
                  ▼
            返回精确事实（类型/定义/引用/错误）
                  │
                  ▼
            Kimi 基于事实生成更准确、更安全的代码或解释
                  │
                  ▼
            用户收到高质量回答（对 LSP 调用无感知）
```

## NixOS 环境要求

本项目**必须在 NixOS 上开发和运行**。所有依赖和环境配置都必须通过 Nix 管理，确保可复现性。

### 为什么必须是 NixOS

1. **scheme-langserver 在 NixOS 上是 first-class**：nixpkgs 中已有 `scheme-langserver` 包，安装和更新由 Nix 管理
2. **Chez Scheme 的复杂性**：scheme-langserver 依赖 Chez Scheme 的 boot files 和 kernel files，Nix 能精确管理这些实现特定依赖
3. **可复现性**：LSP 服务器的行为高度依赖运行时环境（Chez Scheme 版本、线程支持、库路径），Nix 确保开发和生产环境完全一致
4. **本项目的目标用户**：Scheme 社区与 NixOS 社区重叠度较高，许多 Scheme 开发者使用 NixOS

### Nix 开发环境规范

- **使用 Nix Flakes**：项目根目录必须有 `flake.nix` 和 `flake.lock`
- **开发 shell**：`nix develop` 必须提供完整的开发环境（Python、scheme-langserver、测试工具等）
- **scheme-langserver 来源**：优先使用 nixpkgs 中的 `scheme-langserver` 包，而非手动下载二进制
- **非 NixOS 兼容**：允许通过 `nix develop` 在非 NixOS 系统上开发，但**运行和测试必须在 NixOS 或 Nix 环境中完成**
- **动态链接**：scheme-langserver 的二进制在 NixOS 上可能依赖特定版本的 glibc，必须通过 Nix 的 `patchelf` 或 `buildFHSUserEnv` 处理

### NixOS 特有注意事项

- **FHS 兼容性**：scheme-langserver 可能假设标准 FHS 路径（如 `/usr/lib`），需要在 Nix 包装器中处理
- **Chez Scheme boot files**：Nix 的 `chez` 包会将 boot files 放在非标准路径，启动 scheme-langserver 时可能需要传递 `--bootpath`
- **Akku 包管理器**：如果 scheme-langserver 需要加载 Akku 管理的依赖，需要确保 Akku 在 Nix 环境中可用

## 技术架构

### 整体架构

```
┌─────────────┐      MCP (stdio)      ┌──────────────────┐      LSP (stdio)      ┌──────────────────┐
│  Kimi CLI   │ ◄──────────────────► │  本项目 Bridge   │ ◄──────────────────► │ scheme-langserver│
│  (Client)   │   JSON-RPC 2.0        │ (MCP Server)     │   JSON-RPC 2.0        │  (LSP Server)    │
└─────────────┘                       └──────────────────┘                       └──────────────────┘
                                              │
                                              │ 文件读写
                                              ▼
                                       ┌──────────────┐
                                       │ 项目代码文件  │
                                       │ (.ss .scm    │
                                       │  .sld .sls)  │
                                       └──────────────┘
```

### Bridge 内部模块

```
scheme_langserver_bridge/
├── __init__.py
├── __main__.py        # python3 -m 入口 / CLI 入口
├── server.py          # MCP 服务器主入口 (FastMCP)
├── lsp_client.py      # LSP 客户端：管理 scheme-langserver 子进程 + JSON-RPC + 资源限制
├── document_sync.py   # 文档同步：将文件变更通知 LSP 服务器
└── config.py          # 配置管理：LSP 服务器路径、启动参数、环境变量解析
```

### 协议映射

MCP Tools ↔ LSP Methods 映射：

| MCP Tool | LSP Method | 说明 |
|---------|-----------|------|
| `lsp_initialize` | `initialize` | 初始化 LSP 连接，传入项目根目录 |
| `lsp_open` | `textDocument/didOpen` | 打开文件，通知 LSP 文件内容 |
| `lsp_change` | `textDocument/didChange` | 文件内容变更（全量同步） |
| `lsp_hover` | `textDocument/hover` | 获取光标处符号信息 |
| `lsp_complete` | `textDocument/completion` | 代码补全 |
| `lsp_definition` | `textDocument/definition` | 跳转到定义 |
| `lsp_references` | `textDocument/references` | 查找引用 |
| `lsp_diagnostics` | `textDocument/publishDiagnostics` | 获取诊断信息 |
| `lsp_rename` | `textDocument/rename` | 重命名符号 |
| `lsp_signature` | `textDocument/signatureHelp` | 函数签名帮助 |
| `lsp_document_symbol` | `textDocument/documentSymbol` | 文件内符号列表 |
| `lsp_workspace_symbol` | `workspace/symbol` | 工作区符号搜索 |
| `lsp_code_action` | `textDocument/codeAction` | 代码动作 / 快速修复 |
| `lsp_close` | `textDocument/didClose` | 关闭文件 |
| `lsp_shutdown` | `shutdown` | 优雅关闭 LSP 服务器 |

## 开发规范

### 语言与工具

- **Bridge 实现语言**：Python 3.12+
- **MCP SDK**：使用 `mcp` 官方 Python SDK（`pip install mcp`），通过 `FastMCP` 构建服务器
- **LSP 通信**：自研轻量 JSON-RPC 客户端（scheme-langserver 只走 stdio，不需要完整 LSP 库）
- **依赖管理**：Python 依赖通过 `pyproject.toml` + `uv` 管理，同时在 `flake.nix` 中声明
- **代码风格**：`ruff` 格式化 + `ruff` lint，`pyright` 类型检查

### 代码组织

- 所有 Python 代码放在 `src/scheme_langserver_bridge/` 下
- 测试放在 `tests/` 下，使用 `pytest`
- 脚本放在 `scripts/` 下
- Nix 配置放在项目根目录：`flake.nix`、`flake.lock`

### 配置规范

- **LSP 服务器发现优先级**：
  1. 环境变量 `SCHEME_LANGSERVER_PATH`
  2. PATH 中的 `scheme-langserver` 或 `run`
  3. 已知本地开发路径（项目内 `./scheme-langserver/run`、上级目录、`~/Documents/workspace/scheme-langserver/run`）
- **日志路径**：默认使用当前工作目录下的 `.scheme-langserver.log`，可通过环境变量覆盖
- **项目根目录**：通过 `lsp_initialize` 工具参数传入

### 错误处理

- LSP 服务器崩溃或返回错误时，MCP 工具必须返回友好的错误信息，不能抛未处理异常
- 必须实现 LSP 服务器进程的健康检查和自动重启（通过 `lsp_shutdown` + `lsp_initialize`）
- 所有 LSP 通信超时必须可配置（默认 30 秒，补全单独可配）
- 子进程必须设置硬资源限制（内存、CPU）防止失控

## 构建与运行

### 开发环境搭建

```bash
# 进入 Nix 开发 shell
nix develop

# 安装 Python 依赖
uv sync --extra dev

# 运行测试
pytest

# 格式化代码
ruff format
ruff check --fix
pyright
```

### 安装到 Kimi

```bash
# 方式一：作为 stdio MCP 服务器注册
kimi mcp add --transport stdio scheme-langserver -- \
  python3 -m scheme_langserver_bridge

# 方式二：通过配置文件 ~/.kimi/mcp.json
{
  "mcpServers": {
    "scheme-langserver": {
      "command": "python3",
      "args": ["-m", "scheme_langserver_bridge"],
      "env": {
        "SCHEME_LANGSERVER_PATH": "scheme-langserver"
      }
    }
  }
}
```

### NixOS 特有运行方式

```bash
# 通过 nix run 直接运行
nix run .#scheme-langserver-bridge

# 或通过 nix develop 进入环境后运行
nix develop
python3 -m scheme_langserver_bridge
```

## 测试策略

### 单元测试

- `tests/test_lsp_client.py`：测试 JSON-RPC 通信、并发写锁、超时清理、崩溃恢复、资源限制、异常输入处理
- `tests/test_server.py`：测试每个 MCP tool 的参数校验和响应格式
- `tests/test_document_sync.py`：测试文件同步逻辑
- `tests/test_config.py`：测试配置解析和环境变量读取

### 集成测试

- `tests/test_integration.py`：需要真实 scheme-langserver 进程的测试
- 使用一个最小的 Scheme 项目（`tests/fixtures/scheme-project/`）作为测试靶子
- CI 必须在 NixOS 环境下运行（可通过 GitHub Actions + Nix 实现）

### 手动测试清单

- [ ] `lsp_initialize` 能正确启动 scheme-langserver
- [ ] `lsp_open` + `lsp_hover` 能返回正确的符号信息
- [ ] `lsp_complete` 能列出当前作用域的可用绑定
- [ ] `lsp_definition` 能正确定位到定义文件和行列
- [ ] `lsp_diagnostics` 能报告语法错误
- [ ] Kimi 实际对话中能调用这些工具并基于结果回答

## 文件清单

```
.
├── AGENTS.md                           # 本文件
├── README.md                           # 面向用户的说明
├── CHANGELOG.md                        # 变更日志
├── LICENSE                             # MIT 许可证
├── flake.nix                           # Nix Flake 定义
├── flake.lock                          # Nix 依赖锁定
├── pyproject.toml                      # Python 项目配置
├── uv.lock                             # Python 依赖锁定 (uv)
├── Makefile                            # 常用命令快捷方式
├── src/
│   └── scheme_langserver_bridge/
│       ├── __init__.py
│       ├── __main__.py                 # python3 -m 入口 / CLI 入口
│       ├── server.py                   # MCP 服务器
│       ├── lsp_client.py               # LSP 客户端
│       ├── document_sync.py            # 文档同步
│       └── config.py                   # 配置管理
├── tests/
│   ├── fixtures/
│   │   └── scheme-project/             # 测试用 Scheme 项目
│   │       └── test.scm
│   ├── test_config.py
│   ├── test_document_sync.py
│   ├── test_integration.py
│   ├── test_lsp_client.py
│   ├── test_lsp_client_cleanup.py
│   ├── test_lsp_client_concurrency.py
│   ├── test_lsp_client_malformed.py
│   └── test_server.py
└── scripts/
    └── test-lsp-connection.py          # 手动测试 LSP 连通性
```

## 关键决策记录

### 为什么不用现有 Bridge（如 @Tritlo/lsp-mcp）

现有 Bridge 是通用型的，存在以下问题：
1. **Node.js 依赖**：需要 npm/npx，在纯 NixOS 环境中引入不必要的复杂度
2. **Scheme 特殊逻辑缺失**：无法处理 `.sld`/`.sls` 文件类型、R6RS 库路径、Chez Scheme 特定扩展
3. **启动参数不灵活**：scheme-langserver 需要 `-e R6RS`、`-m enable` 等特定参数，通用 Bridge 的传参方式不够直观
4. **生命周期管理**：scheme-langserver 对项目根目录和 Chez Scheme 环境敏感，需要专门的发现和配置逻辑

### 为什么用 Python 而不是其他语言

1. **Kimi CLI 本身是用 Python 写的**：生态一致，便于调试
2. **MCP Python SDK 成熟**：官方支持，文档完善
3. **NixOS 上 Python 环境管理完善**：`nix develop` 可以提供精确的 Python 版本和依赖
4. **开发效率**：快速迭代，测试方便

### 为什么 Plugin 机制不可用

Kimi 的 Plugin 机制（`plugin.json`）每次工具调用都启动一个独立进程，无法维持与 LSP 服务器的长期连接。LSP 需要保持文档状态同步（`didOpen`/`didChange`/`didClose`），因此必须使用持续运行的 MCP 服务器模式。

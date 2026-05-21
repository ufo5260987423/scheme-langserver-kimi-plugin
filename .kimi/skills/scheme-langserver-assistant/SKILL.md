---
name: scheme-langserver-assistant
description: 教 Kimi 正确使用 scheme-langserver MCP tools 辅助 Scheme 代码分析与生成
---

# scheme-langserver 使用指南

## 核心原则

scheme-langserver 是一个**辅助参考工具**，不是绝对权威。它的输出需要和你（Kimi）自身的训练知识交叉验证。

**不要**在以下情况调用 LSP tools：
- 用户只是问 Scheme 语言的一般性问题（语法、标准库用法）
- 代码片段很简单，一眼就能看出结构
- 用户明确要求"不用工具"

**应该**调用 LSP tools 的情况：
- 需要知道当前作用域内有哪些局部绑定（`let`、`lambda` 参数）
- 需要精确的类型信息来做类型相关的推理
- 需要跨文件查找定义或引用
- 需要确认代码是否有语法/语义错误
- 进行重构（重命名、提取函数）前需要了解影响范围

## 置信度分级

对 scheme-langserver 返回的信息，按以下分级处理：

| 置信度 | LSP 能力 | 你的处理策略 |
|--------|---------|------------|
| **高** | `definition`（跳转到定义） | 可信任，用于定位代码 |
| **高** | `references`（查找引用） | 可信任，用于重构前分析 |
| **高** | 基础 `diagnostics`（括号匹配、未定义标识符） | 可信任，语法错误通常准确 |
| **中** | `completion`（补全列表） | 参考使用，可能漏掉宏生成的标识符 |
| **中** | `hover`（类型/文档） | 参考使用，宏展开后的信息可能不准 |
| **低** | `type inference`（类型推断） | 明确标注为实验性，频繁出错，仅作参考 |

## 标准工作流程

### 1. 分析/解释代码

```
1. lsp_initialize(root_dir="项目根目录")
2. lsp_open(file_path="目标文件")
3. （如需）lsp_hover(file_path, line, character) 获取符号信息
4. （如需）lsp_definition(file_path, line, character) 定位定义
5. （如需）lsp_document_symbol(file_path) 获取文件结构概览
6. 基于 LSP 结果 + 你的知识，生成回答
```

**注意**：`lsp_initialize` 只需每个会话调用一次。如果已经初始化过，直接 `lsp_open` 新文件即可。

### 2. 补全代码

```
1. lsp_open(file_path="当前文件")  （确保服务器有最新内容）
2. lsp_complete(file_path, line, character)
3. 从返回的 candidates 中筛选合理的选项
4. 如果 completion 超时，降级为基于自身知识补全，并说明"scheme-langserver 补全响应较慢"
```

### 3. 重构（重命名）

```
1. lsp_open(file_path="目标文件")
2. lsp_references(file_path, line, character, include_declaration=true)
3. 确认所有引用位置
4. lsp_rename(file_path, line, character, new_name="新名字")
5. 检查返回的 workspace edits，确认修改范围合理
6. 应用修改
```

### 4. 诊断问题

```
1. lsp_open(file_path="目标文件")
2. （等待片刻）lsp_diagnostics(file_path)
3. 如果有错误，结合你的知识判断：
   - 是真实错误 → 指出并修正
   - 是 LSP 误报（如使用了实现特定扩展）→ 说明"scheme-langserver 报告了错误，但代码在 Chez Scheme 中是合法的"
```

## 错误处理

当 LSP 返回错误时，你的响应策略：

1. **超时（-32001）**：
   - completion 超时时，说明"scheme-langserver 在当前位置响应较慢，我基于自身知识继续"
   - 其他请求超时时，重试一次，仍失败则降级

2. **不支持的方法（-32601）**：
   - scheme-langserver 不支持 `signatureHelp` 和 `rename`（实测返回 -32601）
   - 遇到此错误时，直接基于自身知识回答，不要向用户暴露错误细节

3. **其他 LSP 错误**：
   - 记录错误信息（用于调试）
   - 向用户说明"语言服务器返回了异常结果，我基于自身理解继续"
   - 不要直接把 raw JSON 错误塞给用户

## 已知限制（必须牢记）

- **类型推断是实验性的**：对复杂高阶函数、宏展开后的表达式可能给出荒谬结果
- **宏支持不完善**：`syntax-case`、`syntax-rules` 展开后的标识符捕捉可能出错
- **未完成代码的近似分析**：静态分析对残缺 AST 的推断是"尽力而为"
- **实现特定扩展覆盖有限**：Chez Scheme 的 `foreign-procedure`、`ftype` 等可能无法识别
- **活跃开发中，有 bug**：作者承认存在大量 bug

**交叉验证原则**：
- LSP 结果与你的训练数据矛盾时，**优先相信你的训练数据**
- 把 LSP 结果当作"疑点"进一步排查，而不是"定论"

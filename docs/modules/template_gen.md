# 模板生成模块 `template_gen/`

根据用户的自然语言描述生成 LaTeX 模板。提供两种模式：

1. **单次生成（旧）**：`TemplateGenService.generate_template` 一次 LLM 调用产出 preamble + body 骨架，`save_template` 组装为单个 `.tex` 文件。保留向后兼容。
2. **自主 Agent（新，默认推荐）**：`TemplateAgentService` 基于 Function Calling 自主迭代生成完整可编译的多文件 LaTeX 工程。

## 自主 Agent（`agent.py`）

### 设计目标

将"单次 LLM 出一个 .tex 骨架"升级为基于 Function Calling 的自主 Agent：Agent 自主规划文件结构 → 逐个 `write_file` → 调用 `compile_latex` 验证 → 读取编译日志 → 修正错误 → 再编译，最多 N 轮，最终产出一个完整可编译的多文件 LaTeX 项目。

### 工具体系（session 级私有）

工具 schema 由 `TemplateAgentService._build_tools_schema()` 动态构建，随 `chat_with_tools(tools=...)` 注入。**不注册到全局 `ToolRegistry`** —— 因为这些工具绑定到一次性沙箱目录，注册成全局能力会污染其他模块（如 `writing_chat`）的工具集。执行函数封装在 `_ToolExecutor` 中（持有沙箱路径）。

| 工具 | 功能 | 备注 |
| --- | --- | --- |
| `write_file` | 创建/覆盖沙箱内文件，完整内容写入 | 路径校验 + 文件数/大小上限 |
| `read_file` | 读取沙箱内已写文件 | 修错时回看内容 |
| `list_files` | 列出沙箱内全部文件 | 验证项目结构 |
| `compile_latex` | `latexmk -xelatex` 在沙箱目录编译 | 受 `agent_compile_enabled` 开关控制，失败时截取错误行附近日志回传 LLM |
| `finish` | 信号项目完成，附带总结与主文件路径 | 调用后立即终止循环 |

### 沙箱与路径安全

每次生成在 `output_dir/agent/<session_id>/` 下创建独立目录。所有 `file_path` 参数经 `TemplateAgentService._resolve_sandbox_path()` 校验：

- 禁止空、null 字节、绝对路径
- 禁止 `..` 路径段
- `resolve()` 后必须仍在沙箱内
- 扩展名白名单：`.tex/.sty/.cls/.bib/.md/.txt/.cfg/.bst/.def/.ltx/.latex`

### 沙箱自动清理（`cleanup.py`）

为防止沙箱目录无限积累占用磁盘空间，提供基于 `asyncio` 的后台自动清理服务。

**清理逻辑**：
- `cleanup_expired_sandboxes()`：扫描 `output_dir/agent/` 下所有子目录（每个子目录 = 一个沙箱），取目录内最新文件的 mtime 作为该沙箱的"最后活跃时间"，若距今超过 `cleanup_retention_days` 天则删除整个目录
- 使用 mtime 而非创建时间——用户读取/下载文件时 mtime 会更新，避免清理仍在使用中的沙箱
- 仅清理 `agent/` 子目录下的会话沙箱，不清理用户通过 `/save` 端点手动保存的单个 `.tex` 文件

**后台循环**：
- `cleanup_loop()` 作为 `asyncio.Task` 在 FastAPI `lifespan` 中启动
- 每 `cleanup_interval_seconds` 秒执行一次清理扫描
- FastAPI 关闭时优雅取消该任务（`task.cancel()`）
- 通过 `logger` 记录每次扫描的删除数，异常时记录失败详情

### Agent 循环

`generate()` 返回 SSE 事件流：

| 事件 type | 含义 |
| --- | --- |
| `plan` | Agent 的思考/说明文本 |
| `tool_call` | 工具调用开始（含 args 摘要） |
| `tool_result` | 工具执行结果（`ok`/`summary`，write_file 额外带 `file_path`/`size`） |
| `compile` | 编译结果，单事件便于前端高亮（`success`/`log`/`errors`）。失败时 `errors` 为结构化错误列表 |
| `warning` | Agent 行为可疑——最后一次编译仍未通过却调用了 `finish` |
| `done` | 完成，携带 `session_id`/`sandbox`/`files`/`summary` |
| `error` | 错误终止 |

循环逻辑：
1. 调用 `chat_with_tools`，若 LLM 无 `tool_calls` → 主动结束
2. 否则把 assistant 消息追加进历史，依次执行每个 `tool_call`，将工具结果以 `role:"tool"` 回传 LLM
3. `finish` 工具立即跳出整体循环
4. 循环中跟踪 `compile_state`（`called`/`success`）。若最后一次编译仍失败时触发 `finish`，额外推送 `warning` 事件（不阻断 `done`）。
5. 达到 `agent_max_rounds` 仍未 finish → 自动结束并推送提示；若此时编译仍失败，同样推送 `warning`。

### 编译错误修复协议

Agent 的 `agent_system` prompt 中写入了完整的编译错误修复协议（**COMPILATION ERROR RECOVERY PROTOCOL**），强制 LLM 在编译失败时走以下循环：

```
a. ANALYZE — 日志中的结构化错误摘要（见下文 `_extract_errors`）已置于回传文本
   顶部，聚焦第一个错误（LaTeX 错误级联，后续错误通常是第一个的连锁反应）
b. READ — `read_file` 读取错误所在文件
c. FIX — `write_file` 最小修改根因
d. RECOMPILE — `compile_latex` 验证
e. 重复 a–d 直到 `success=true` 才允许 `finish`
```

prompt 同时列出 6 种常见 LaTeX 编译错误的根因与修复指引（`Undefined control sequence` / `File not found` / `Missing $ inserted` / `Environment undefined` / `begin/end mismatch` / `Runaway argument`），并硬性约束同错误连续 3 次修复失败应在 `finish` 中告知用户，防止死循环。

### 编译日志结构化错误提取

编译失败时由 `backend/modules/template_gen/agent.py` 中的两个辅助函数处理：

**`_extract_errors(log)`**：从 latexmk 日志中提取结构化错误列表，每个错误包含：
- `type`：错误类型（如 `"Undefined control sequence."`）
- `file`：错误所在源文件相对路径
- `line`：错误行号（`int` 或 `None`）
- `detail`：行内容（如 `\unknowncommand`）

识别逻辑：`^! ...` 声明行 → 回溯最近 `(xxx.tex` 上下文作为文件名 → 向后扫 15 行寻找 `l.NNN` 行号。

**`_format_error_analysis(errors)`**：格式化为给 LLM 看的聚焦错误摘要，强调"Fix the FIRST error and recompile"。

`compile` SSE 事件的 `errors` 字段直接携带此列表供前端展示，`log` 字段仍保留原始截断日志。给 LLM 的回传文本则拼接为 `_format_error_analysis() + "\n\n--- Raw log ---\n" + _trim_log()`。

### 沙箱文件读取安全

`load_sandbox(output_dir, session_id)` 为文件读取端点（`/agent/files`、`/agent/file`）提供路径基准解析。安全约束：
- `session_id` 仅允许字母数字 / `-` / `_`，杜绝 `..` / `/` 注入
- 解析后必须严格位于 `output_dir/agent/` 子目录内，无法逃逸到其他模板或兄弟会话目录

### 配置项（`features.template_gen`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `agent_enabled` | `true` | 是否启用自主 Agent |
| `agent_max_rounds` | `30` | Agent 最大工具调用轮数 |
| `agent_max_files` | `30` | 单次生成最大文件数 |
| `agent_max_file_size_kb` | `512` | 单文件大小上限 |
| `agent_compile_enabled` | `true` | 是否允许 Agent 调用 `compile_latex` |
| `agent_compile_timeout` | `120` | 编译超时（秒） |
| `cleanup_enabled` | `true` | 是否启用沙箱自动清理 |
| `cleanup_retention_days` | `7` | 沙箱保留天数（按最新文件 mtime） |
| `cleanup_interval_seconds` | `3600` | 清理扫描间隔（秒） |

## 旧模式 Pydantic 输出模型

- `SectionSkeleton`（section, suggested_content）
- `TemplateGenResult`（template_name, preamble, body_structure, packages, explanation）

## Service 核心方法

| 方法 | 所属 | 功能 |
| --- | --- | --- |
| `generate(description)` | `TemplateAgentService` | 自主 Agent 生成（SSE） |
| `generate_template(description)` | `TemplateGenService` | 旧：单次 LLM 生成模板结构 |
| `save_template(template)` | `TemplateGenService` | 旧：组装完整 `.tex` 文件并保存到 `output_dir` |
| `_assemble_template` | `TemplateGenService` | 旧：将模板结构组装为完整 LaTeX 源码 |

## API 路由

Prefix: `/api/template-gen`, Tag: `template_gen`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/agent/generate` | 自主 Agent 生成（SSE 流式） |
| GET | `/agent/files` | 列出某次 Agent 生成的文件树 |
| GET | `/agent/file` | 读取沙箱内单个文件内容 |
| GET | `/agent/download` | 下载整个沙箱为 ZIP 文件（流式响应） |
| POST | `/generate` | 旧：根据描述生成 LaTeX 模板（JSON） |
| POST | `/save` | 旧：将生成的模板保存为 `.tex` 文件 |
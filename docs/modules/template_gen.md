# 模板生成模块 `template_gen/`

根据用户的自然语言描述，通过**自主 Agent**（`TemplateAgentService`）基于 Function Calling 自主迭代生成完整可编译的多文件 LaTeX 工程。

## 模块分层

遵循 `router.py`（薄路由）→ `service.py`（业务编排）→ `agent.py`（Agent 主循环）的三层结构：

| 文件 | 职责 |
| --- | --- |
| `router.py` | 薄路由：参数校验、SSE 响应与状态码映射 |
| `service.py` | `TemplateGenService`：创建沙箱工作区、驱动 Agent、产物浏览（`list_session_files` / `read_session_file` / `zip_session` / `get_session_trace`） |
| `agent.py` | `TemplateAgentService`：Function Calling 主循环、工具 schema 构建、`_ToolExecutor` 工具执行、编译日志错误解析 |
| `trace.py` | Agent 运行追踪（`trace.jsonl` 记录与读取） |
| `cleanup.py` | 过期沙箱自动清理（后台任务） |

## 设计目标

Agent 自主规划文件结构 → 逐个 `write_file` → 调用 `compile_latex` 验证 → 读取编译日志 → 修正错误 → 再编译，最多 N 轮，最终产出一个完整可编译的多文件 LaTeX 项目。

## 工具体系（session 级私有）

工具 schema 由 `TemplateAgentService._build_tools_schema()` 动态构建，随 `chat_with_tools(tools=...)` 注入。**不注册到全局 `ToolRegistry`** —— 因为这些工具绑定到一次性沙箱目录，注册成全局能力会污染其他模块（如 `writing_chat`）的工具集。执行函数封装在 `_ToolExecutor` 中（持有沙箱路径）。

| 工具 | 功能 | 备注 |
| --- | --- | --- |
| `write_file` | 创建/覆盖沙箱内文件，完整内容写入 | 路径校验 + 文件数/大小上限 + LaTeX 内容校验（写盘前拦截 JSON 转义损坏，失败回传 LLM 自纠） |
| `read_file` | 读取沙箱内已写文件 | 修错时回看内容 |
| `list_files` | 列出沙箱内全部文件 | 验证项目结构 |
| `compile_latex` | `latexmk -xelatex` 在沙箱目录编译 | 受 `agent_compile_enabled` 开关控制，失败时截取错误行附近日志回传 LLM |
| `finish` | 信号项目完成，附带总结与主文件路径 | 调用后立即终止循环 |

## 沙箱与路径安全

每次生成由 `service.py` 在 `output_dir/agent/<session_id>/` 下创建独立目录。所有 `file_path` 参数经 `TemplateAgentService._resolve_sandbox_path()` 校验：

- 禁止空、null 字节、绝对路径
- 禁止 `..` 路径段
- `resolve()` 后必须仍在沙箱内
- 扩展名白名单：`.tex/.sty/.cls/.bib/.md/.txt/.cfg/.bst/.def/.ltx/.latex`

## 沙箱自动清理（`cleanup.py`）

为防止沙箱目录无限积累占用磁盘空间，提供基于 `asyncio` 的后台自动清理服务。

**清理逻辑**：

- `cleanup_expired_sandboxes()`：扫描 `output_dir/agent/` 下所有子目录（每个子目录 = 一个沙箱），取目录内最新文件的 mtime 作为该沙箱的"最后活跃时间"，若距今超过 `cleanup_retention_days` 天则删除整个目录
- 使用 mtime 而非创建时间——用户读取/下载文件时 mtime 会更新，避免清理仍在使用中的沙箱
- 仅清理 `agent/` 子目录下的会话沙箱

**后台循环**：

- `cleanup_loop()` 作为 `asyncio.Task` 在 FastAPI `lifespan` 中启动
- 每 `cleanup_interval_seconds` 秒执行一次清理扫描
- FastAPI 关闭时优雅取消该任务（`task.cancel()`）
- 通过 `logger` 记录每次扫描的删除数，异常时记录失败详情

## Agent 循环

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

1. 调用 `chat_with_tools`；若 LLM 无 `tool_calls`，先按"规划瘫痪防护"引导
   继续行动（见下文），连续 2 次仍无工具调用才结束
2. 否则把 assistant 消息追加进历史，依次执行每个 `tool_call`，将工具结果以 `role:"tool"` 回传 LLM
3. `finish` 工具立即跳出整体循环
4. 循环中跟踪 `compile_state`（`called`/`success`）。若最后一次编译仍失败时触发 `finish`，额外推送 `warning` 事件（不阻断 `done`）。
5. 达到 `agent_max_rounds` 仍未 finish → 自动结束并推送提示；若此时编译仍失败，同样推送 `warning`。

## 规划瘫痪防护（no-tool-call nudge）

LLM 面对复杂需求时可能出现"规划瘫痪"：首轮输出长篇思考文本却不调用任何工具，
旧逻辑会把它当作主动结束，导致生成 0 个文件就 `done`。现循环对"无 tool_call 的
回复"做如下处理：

- 累计最多 2 次无工具回复时，注入一条用户消息引导其立即调用
  `write_file / read_file / list_files / compile_latex / finish`，
  并写入 trace（`type: "nudge"`）供分析；
- 超过 2 次仍无工具调用才视为异常结束。

引导文案仅注入 LLM 历史并记录到 trace，**不推送 `plan` 事件到前端**——
它是面向模型的指令，对用户没有展示价值。

系统提示词同步要求"首轮回复必须包含至少一个 tool_call"，且每轮 `content`
不超过 2 句，把规划收敛到行动上。

## 编译错误修复协议

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

## 编译日志结构化错误提取

编译失败时由 `backend/modules/template_gen/agent.py` 中的两个辅助函数处理：

**`_extract_errors(log)`**：从 latexmk 日志中提取结构化错误列表，每个错误包含：

- `type`：错误类型（如 `"Undefined control sequence."`）
- `file`：错误所在源文件相对路径
- `line`：错误行号（`int` 或 `None`）
- `detail`：行内容（如 `\unknowncommand`）
- `font`（可选）：缺字诊断中涉及的字体名

识别逻辑：`^! ...` 声明行 → 回溯最近 `(xxx.tex` 上下文作为文件名 → 向后扫 15 行寻找 `l.NNN` 行号。

除 `!` 行外，额外识别 **Missing character** 缺字诊断（如中文内容未配置 CJK
字体时 xelatex 报 `Missing character: There is no 基 (U+57FA) in font ...`）：
这类问题不产生 `!` 行但会导致 latexmk 退出码非 0 且 PDF 缺字，Agent 之前无法
诊断。提取后 `_format_error_analysis` 会附带"加载 xeCJK/ctex 并配置 CJK 字体"
的修复提示。

缺字诊断会逐字符刷屏（一段文本可能产生成百上千条），为防挤占 8 条错误上限，
`_extract_errors` 只保留**第一条**缺字诊断（含所在文件与字体名），后续刷屏行
仅累计数量并合并进 `detail`（如 `+N more missing-character lines`）。

字体名可能含空格（如 `Fira Sans Regular`），提取正则取 `in font` 到 `/`、`;` 或 `!` 之前的完整名称。

**`_format_error_analysis(errors)`**：格式化为给 LLM 看的聚焦错误摘要，强调"Fix the FIRST error and recompile"。

`compile` SSE 事件的 `errors` 字段直接携带此列表供前端展示，`log` 字段仍保留原始截断日志。给 LLM 的回传文本则拼接为 `_format_error_analysis() + "\n\n--- Raw log ---\n" + _trim_log()`。

## 沙箱文件读取安全

`load_sandbox(output_dir, session_id)` 为文件读取端点（`/files`、`/file`）提供路径基准解析。安全约束：

- `session_id` 仅允许字母数字 / `-` / `_`，杜绝 `..` / `/` 注入
- 解析后必须严格位于 `output_dir/agent/` 子目录内，无法逃逸到其他模板或兄弟会话目录

## 配置项（`features.template_gen`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 模块总开关 |
| `output_dir` | `templates/` | Agent 沙箱根目录 |
| `agent_max_rounds` | `30` | Agent 最大工具调用轮数 |
| `agent_max_files` | `30` | 单次生成最大文件数 |
| `agent_max_file_size_kb` | `512` | 单文件大小上限 |
| `agent_compile_enabled` | `true` | 是否允许 Agent 调用 `compile_latex` |
| `agent_compile_timeout` | `120` | 编译超时（秒） |
| `cleanup_enabled` | `true` | 是否启用沙箱自动清理 |
| `cleanup_retention_days` | `7` | 沙箱保留天数（按最新文件 mtime） |
| `cleanup_interval_seconds` | `3600` | 清理扫描间隔（秒） |

## Service 核心方法

| 方法 | 所属 | 功能 |
| --- | --- | --- |
| `generate(description)` | `TemplateGenService` | 创建沙箱并驱动 Agent 生成（SSE），转发事件流 |
| `list_session_files(session_id)` | `TemplateGenService` | 列出某次 Agent 生成的文件树 |
| `read_session_file(session_id, path)` | `TemplateGenService` | 读取沙箱内单个文件内容 |
| `zip_session(session_id)` | `TemplateGenService` | 打包沙箱为 ZIP（返回 bytes + 文件名） |
| `get_session_trace(session_id)` | `TemplateGenService` | 读取 Agent 运行追踪（meta + events） |
| `generate(description, sandbox, llm)` | `TemplateAgentService` | Function Calling 主循环（SSE） |

## API 路由

Prefix: `/api/template-gen`, Tag: `template_gen`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/generate` | 自主 Agent 生成（SSE 流式） |
| GET | `/files` | 列出某次 Agent 生成的文件树 |
| GET | `/file` | 读取沙箱内单个文件内容 |
| GET | `/download` | 下载整个沙箱为 ZIP 文件（流式响应） |
| GET | `/trace` | 读取 Agent 运行追踪日志（JSON） |
| GET | `/trace/download` | 导出 Agent 运行追踪日志（JSON 附件） |

## 运行追踪

`agent.py` 在每次生成时把 Agent 主循环的完整交互按时间顺序逐条追加到 session 沙箱的 `trace.jsonl`，用于分析、改进 prompt 与复现调试。

- 由 `trace.py` 提供追加与读取：`append_trace()`（沙箱级锁 + 线程内写文件，防并发交错）、`read_trace()`
- 事件类型：`run_start`（描述/语言/system prompt/初始 messages/最大轮数）、`llm`（每次 LLM 回复含 tool_calls）、`tool_result`（工具名/完整参数/结果文本/前端事件）、`warning`、`error`、`run_end`（总结/轮数/最终编译状态）
- `trace.jsonl` 随沙箱一起被 `cleanup.py` 清理，并包含在下载的 ZIP 中
- 通过 `/api/template-gen/trace` 查看或 `/trace/download` 导出 JSON，供分析与改进 Agent

前端 `TemplateGenPage` 提供 Agent 日志卡片（`查看日志` / `导出日志`），查看时在弹窗中逐条展示追踪事件。

## 质量约束（系统提示词，基准驱动）

`agent_system` 提示词按"工业级模板基准"重构，包含：

- **需求→实现 1:1 映射清单**：边距→`geometry`、字体→`fontspec`/`ctex`、
  主题色→`\definecolor`+`\colorlet`、三线表→`booktabs`、参考文献→
  `natbib`/`biblatex`+`.bib`、中文→`ctex`/`xeCJK`（XeLaTeX）等；
- **格式与内容分离**：样式封装 `.cls`/`.sty`，正文只用语义命令；
- **分类玩法**：学位论文（front/main/back matter、`\ctexset` "第N章"、
  元数据命令勿裸排 `\@title`）、演示文稿（扁平化、进度条、独立 color theme、
  中文内容必须配 CJK 字体）、简历（单页纪律、语义命令、紧凑排版）、会议论文
  （author-year 引用、双栏）；
- **字体安全**：只引用系统确定存在的字体（Windows 下 SimSun/SimHei/Times New
  Roman 等），Fira/Noto 等缺失字体须 `\IfFontExistsTF` 兜底；
- **图片占位**：Agent 无法创建二进制文件，禁止引用不存在的 PNG，改用 TikZ 或
  `\fcolorbox`/`\rule`；
- **finish 前质量门禁**：对照需求清单复核文件、示例内容覆盖
  公式/图表/引用/目录，README 说明编译引擎，日志无未定义引用。

## 离线评测工具链（templategen_opt/）

`templategen_opt/` 是复现 Agent 优化闭环的离线工具（与 `plotgen_opt/` 同模式）：

| 脚本 | 用途 |
| --- | --- |
| `run_templategen.py <tag> [场景,...]` | 驱动 Agent 生成并采集指标（轮数/编译/需求遵守/启发式评分），结果写入 `results/<tag>/<场景>/` |
| `analyze_trace.py <tag>` | 分析 trace.jsonl：工具分布、失败工具、编译轮次、时间线 |
| `visual_review.py <tag>` | pdftoppm 渲染 PDF 前若干页 → LLM 视觉审查（字体/版式/美学/需求遵守，0-10 分） |
| `compare.py <tagA> <tagB>` | 对比两个 tag 的启发式与视觉评分 |
| `descriptions.py` | 用户口吻的场景描述（学位论文/演示文稿/简历/需求遵守）与硬性要求检查表 |

场景说明：每个场景的 `summary.json` 记录 `req.detail` 逐条需求遵守结果、
`visual_review` 记录视觉评分与问题清单，用于诊断 Agent 质量缺失环节。

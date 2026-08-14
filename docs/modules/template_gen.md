# 模板生成模块 `template_gen/`

根据用户的自然语言描述，通过**自主 Agent**（`TemplateAgentService`）基于 Function Calling 自主迭代生成完整可编译的多文件 LaTeX 工程。

## 模块分层

遵循 `router.py`（薄路由）→ `service.py`（业务编排）→ `agent.py`（Agent 主循环）的三层结构：

| 文件 | 职责 |
| --- | --- |
| `router.py` | 薄路由：参数校验、SSE 响应与状态码映射 |
| `service.py` | `TemplateGenService`：创建沙箱工作区、驱动 Agent、产物浏览（`list_session_files` / `read_session_file` / `zip_session` / `get_session_trace`） |
| `agent.py` | `TemplateAgentService`：Function Calling 主循环、模块文件校验策略、编译日志错误解析 |
| `trace.py` | Agent 运行追踪（`trace.jsonl` 记录与读取） |
| `cleanup.py` | 过期沙箱自动清理（后台任务） |

## 设计目标

Agent 自主规划文件结构 → 逐个 `write_file` → 调用 `compile_latex` 验证 → 读取编译日志 → 修正错误 → 再编译，最多 N 轮，最终产出一个完整可编译的多文件 LaTeX 项目。

## 工具体系（session 级私有）

工具 schema 由 `TemplateAgentService._build_tools_schema()` 动态构建，随 `chat_with_tools(tools=...)` 注入。**不注册到全局 `ToolRegistry`** —— 因为这些工具绑定到一次性沙箱目录，注册成全局能力会污染其他模块（如 `writing_chat`）的工具集。`write_file` / `read_file` / `list_files` 的 schema、参数处理和执行模板来自 `backend/utils/agent_file_tools.py`，`_ToolExecutor` 只提供模板沙箱的校验与存储策略。

| 工具 | 功能 | 备注 |
| --- | --- | --- |
| `write_file` | 创建/覆盖沙箱内文件，完整内容写入 | 路径校验 + **不同文件数**/大小上限 + LaTeX 内容校验（写盘前拦截 JSON 转义损坏，失败回传 LLM 自纠）；重写已有文件不消耗文件预算，防止修复迭代中被预算卡死 |
| `read_file` | 读取沙箱内文件 | 除已写源文件外，允许读取编译生成文件（`.log/.aux/.toc/.out/.blg/.fls` 等）；`line_range: [start, end]` 按 1-based 闭区间读取局部源码 |
| `list_files` | 列出沙箱内全部文件 | 验证项目结构 |
| `compile_latex` | `latexmk -xelatex` 在沙箱目录编译 | 受 `agent_compile_enabled` 开关控制，失败时截取错误行附近日志回传 LLM；latexmk 输出无 `!` 行时自动从 `<main>.log` 兜底提取结构化错误 |
| `finish` | 信号项目完成，附带总结与主文件路径 | 调用后立即终止循环 |

## 沙箱与路径安全

每次生成由 `service.py` 在 `output_dir/<session_id>/` 下创建独立目录。所有 `file_path` 参数经 `TemplateAgentService._resolve_sandbox_path()` 校验：

- 禁止空、null 字节、绝对路径
- 禁止 `..` 路径段
- `resolve()` 后必须仍在沙箱内
- 写入扩展名白名单：`.tex/.sty/.cls/.bib/.md/.txt/.cfg/.bst/.def/.ltx/.latex`
- 读取额外白名单（只读诊断）：`.log/.aux/.toc/.lof/.lot/.out/.blg/.bbl/.bcf/.run.xml/.fls/.fdb_latexmk/.xdv/.nav/.snm/.vrb/.synctex.gz`

## 沙箱自动清理（`cleanup.py`）

为防止沙箱目录无限积累占用磁盘空间，提供基于 `asyncio` 的后台自动清理服务。

**清理逻辑**：

- `cleanup_expired_sandboxes()`：扫描 `output_dir/` 下所有子目录（每个子目录 = 一个沙箱），取目录内最新文件的 mtime 作为该沙箱的"最后活跃时间"，若距今超过 `cleanup_retention_days` 天则删除整个目录
- 使用 mtime 而非创建时间——用户读取/下载文件时 mtime 会更新，避免清理仍在使用中的沙箱
- 仅清理输出目录下的会话沙箱

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

1. 历史消息超过上下文预算时自动压缩（保留最近若干轮，更早轮次替换为摘要），
   防止超大 tool_call 参数把上下文撑爆
2. 调用 `chat_with_tools`；若 LLM 无 `tool_calls`，先按"规划瘫痪防护"引导
   继续行动（见下文），连续 2 次仍无工具调用才结束
3. 否则把 assistant 消息追加进历史，依次执行每个 `tool_call`，将工具结果以 `role:"tool"` 回传 LLM
4. `finish` 工具立即跳出整体循环
5. 循环中跟踪 `compile_state`（`called`/`success`）。若最后一次编译仍失败时触发 `finish`，额外推送 `warning` 事件（不阻断 `done`）。

## 写盘静态校验

`_ToolExecutor._write_file()` 在写盘前执行多层通用校验，把常见 LaTeX
结构错误从"编译期"前移到"写盘期"：

- `.tex` 正文拒绝 `\newcommand`/`\renewcommand`/`\setlength` 等样式定义
- beamer 主文件含实际中文内容、未加载 ctex/xeCJK 且本地依赖也未加载 ctex/xeCJK 时拒绝
- `.cls/.sty` 的 `\ProvidesClass`/`\ProvidesPackage` 可选日期必须 `YYYY/MM/DD`
- `.cls/.sty` 内部允许 `@` 私有名；正文 `.tex` 禁止定义或调用 `@` 命令
- `.cls` 的 `\DeclareOption`/`\ExecuteOptions` 必须在 `\ProcessOptions` 之前
- setter 的 accessor 必须已有 `\newcommand` 默认值
- 正文文件禁止使用 `\@...` 私有控制序列

`compile_latex` 的质量门禁只读取最后一次主 `.log`，避免把早期 pass 的
undefined citation/reference 误判为失败；未解析引用/缺字仍会阻断成功。
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

## 循环防呆干预（nudge）

Agent 循环对三类"看似在工作、实际在空转"的行为注入 `[SYSTEM INTERVENTION]`
干预（记录为 `type: "nudge"`，不影响工具结果）：

1. **写而不编**：首次编译后连续 5 次成功 `write_file` 仍未调用
   `compile_latex`，或同一文件连续重写 5 次，注入"停止盲改、立即编译验证"
   的指令（`write_without_compile`）。
2. **成功源码重复编译**：`compile_latex` 检测到源码与上次成功编译完全
   一致时直接跳过并提示"项目已验证，应 finish 或先改源码"
   （`already_verified`）；连续 2 次触发后注入 `success_recompile_loop`。
3. **成功编译后无限打磨**：连续 4 次成功编译（源码有变化）仍不 finish 时，
   注入"非致命质量警告可交付，除非有具体新缺陷否则应 finish"
   （`finish_after_successes`），防止模型追 overfull box 等警告烧光轮次。

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

prompt 同时列出多种常见 LaTeX 编译错误的根因与修复指引（`Undefined control sequence` / `File not found` / `Missing $ inserted` / `Environment undefined` / `begin/end mismatch` / `Runaway argument`），并硬性约束同错误连续 3 次修复失败应在 `finish` 中告知用户，防止死循环。

### 编译错误兜底提取与针对性提示

latexmk 的 stdout/stderr 有时不含 `!` 错误行（错误只写进了 `<main>.log`），
旧逻辑会让 Agent 面对一段没有错误标记的日志盲目重试。现在：

1. `compile_latex` 失败且未从输出中解析出 `!` 行时，自动读取沙箱内
   `<main>.log` 兜底调用 `_extract_errors()`；若仍无 `!` 行，则把 `.log`
   尾部追加到回传文本，并提示 Agent 用 `read_file main.log` 查看全文。
2. `_format_error_analysis()` 会按已知的**通用失败签名**追加 `HINTS` 区块：
   - `\spacefactor in vertical mode` → 元数据陷阱：正文文件不得使用
     `\@...` 内部宏，类中需提供公开访问命令（如 `\thesistitlevalue`）或
     `\maketitlepage`；
   - 字体缺失 / fontspec 错误 → 删除显式 `\setCJKmainfont` 等命令，交给
     ctex 自动平台字体集；拉丁字体回退 Latin Modern / 类默认；
   - `Command ... already defined` → 改用 `\renewcommand` 或新命令名。
   - `macro parameter character #` → 文本中的字面 `#` 写 `\#`；HTML 颜色
     用 `\definecolor{...}{HTML}{1F4E79}` 且不带 `#` 前缀。
   - `Missing \endcsname` → key-value 上下文里的命令未定义/畸形（如
     `\wuhao` 不是命令，应用 `\zihao{5}` 或 `font=small`）。
   - `Extra alignment tab` → `p{}` 单元格内 `\centering` 会重定义 `\\`，
     行尾需 `\tabularnewline` 或用 `>{\centering\arraybackslash}p{...}`。
   - `Environment xxx undefined` → 加载对应宏包（如 `algorithm`）。
   - `Undefined color 'X'` → 自定义颜色须先用 `\definecolor` 定义再使用，
     检查拼写与大小写；主题不会凭空提供未定义的颜色。
   - `Bibliography not compatible with author-year citations` → natbib
     模式与 `.bst` 不一致；`latexmk -gg` 会自动重新生成 `.bbl`，无需删除，
     只需把 natbib 选项与 `\bibliographystyle` 对齐到同一种风格。
   - 参考文献失败（无 `.bbl`）→ 自动追加 `main.blg` 尾部并提示检查
     `.bst` 名称与 `.bib` 条目字段。
3. 同一编译错误签名连续出现 3 次时，循环注入 `[SYSTEM INTERVENTION]`
   纠正消息（记录为 `nudge`），要求停止症状修补、读日志与源文件后一次性
   重写根因块。
4. 累计出现 6 个不同编译错误签名时，注入"系统性重写"干预：停止逐个修补，
   通读 `.cls/.sty` 与主文件后拆分/简化重写一次。

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
- 解析后必须严格位于 `output_dir/` 子目录内，无法逃逸到其他模板或兄弟会话目录

## 配置项（`features.template_gen`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 模块总开关 |
| `output_dir` | `data/templates/` | Agent 沙箱根目录 |
| `agent_max_rounds` | `30` | Agent 最大工具调用轮数 |
| `agent_max_files` | `30` | 单次生成最大**不同文件数**（重写已有文件不计入预算） |
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
- **分类玩法**：学位论文（front/main/back matter、元数据命令勿裸排
  `\@title`）、演示文稿（典型结构与字体机制、中文内容必须配 CJK 字体）、
  简历（语义命令、紧凑排版、页数服从用户要求）、会议论文（引用风格一致、
  双栏实现方式）；所有 playbook 以用户明确需求优先，未指定时才采用通用
  默认并在 README 记录，禁止写入学校/模板专属结构或数值；
- **字体安全**：只假设各 TeX 发行版默认提供的字体（Latin Modern 与
  ctex/xeCJK 默认 fontset）；用户指定字体一律用 `\IfFontExistsTF`
  兜底便携回退；
- **图片占位**：Agent 无法创建二进制文件，禁止引用不存在的 PNG，改用 TikZ 或
  `\fcolorbox`/`\rule`；
- **finish 前质量门禁**：对照需求清单复核文件；按文档类型校验结构
  完整性（标签/引用可解析、引用条目存在、标题层级连贯、已用构造使用正确），
  禁止为满足门禁加入用户未要求的公式/图表/引用/目录；README 说明编译引擎，
  日志无未定义引用。

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

## 闭环优化加固（templategen_opt 迭代产出）

`template_gen` 的 Agent 提示词保持通用（面向任意模板生成需求，不绑定特定
学校/模板/环境）。针对闭环评测暴露的共性问题做了以下加固：

- **缺失资源协议**：不假设环境里有哪些字体/宏包（用户环境可能不同、可能
  自行安装），也不允许 `test_fonts.tex` 之类的环境探测旁路；遇到
  `fontspec Error` / `File 'xxx.sty' not found` 时，用
  `\IfFontExistsTF` 便携回退或标准等价宏包一次性修复、README 记录用户
  期望字体、最多 1–2 轮处理同一缺失资源，并在 `finish` 摘要中说明替代。
- **Walking Skeleton 工作流**：先写最小可编译骨架并编译通过，再逐层丰富
  内容，禁止一次性写完整套复杂宏再编译。
- **输出 token 上限放宽**：全局默认 `llm.max_tokens` 由 4096 提至 8192
  （`backend/core/config.py` / `config.example.json` / SettingsDialog 同步）；
  template_gen 仍保留 `max(cfg.llm.max_tokens, 8192)` 下限，即使用户把全局
  `max_tokens` 调得很小，`.cls` 等大文件在工具参数尾部也不会被截断导致
  花括号不配平。
- **重复被拒内容防呆**：同一 `write_file` 内容连续被拒 3 次时，向对话注入
  `[SYSTEM INTERVENTION]` 纠正消息，打断模型"原样重发"的退化循环。
- **校验错误定位**：`latex_json_guard.check_latex_text()` 的花括号不配平
  错误现在返回首个问题位置、行号与上下文片段，便于 LLM 直接修复。
- **轮次上限**：`features.template_gen.agent_max_rounds` 由 40 提至 50
  （`config.json` / `config.example.json` / SettingsDialog 上限同步）。

评测数据（`tools/templategen_opt/results/`）显示：加固后简单场景
（如 `requirements`）可在 30 轮内编译通过并获视觉 8.5/10；tjuthesis 复刻
场景（`tju`）需求关键词检查全过（13/13），但当前模型在 50 轮内仍未能收敛
到编译通过，说明该场景对模型的规划/调试能力要求高于其余场景，是后续
模型升级或评测场景调优方向的主要验收基准。

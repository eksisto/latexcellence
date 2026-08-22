# LateXcellence — 后端核心模块

本文档按文件模块组织，适合作为参考手册按需查询。

## 目录

- [后端核心模块](#后端核心模块)：config、security_middleware、llm_client、logger、project_manager、base_paragraph_check、prompts、tool_registry、workspace_manager
- [后端工具模块](#后端工具模块)：scholarly_research、tool_result_protocol、file_ops、path_guard、json_utils、latex_json_guard、sandbox（Docker 沙箱执行）、tex_compile、compile_error_translator、latex_formatter、latex_parser、latex_literals、latex_package_analyzer、input_resolver 等
- [项目管理路由 project_router.py](#项目管理路由-project_routerpy)

---

## 后端核心模块

### `backend/core/config.py` — 配置管理

定义完整的 Pydantic v2 配置模型树，每个 JSON 配置段对应一个 `BaseModel` 子类：

- `ServerConfig` — 服务配置（host, port, dev_mode, vite_dev_url, allowed_origins）
- `SecurityConfig` — 跨域访问安全配置（access_token）
- `LLMConfig` — LLM 配置（api_key, base_url, model, temperature 等）
- `TypoCheckConfig` / `ExpressionOptConfig` / `TerminologyCheckConfig` / `FormulaOCRConfig` / `TableOCRConfig` / `TemplateGenConfig` / `CodeReviewConfig` / `CompileConfig` / `GitDiffConfig` / `FormatConverterConfig` / `StyleModifyConfig` / `StyleModifyAgentConfig` / `WritingChatConfig` / `BibManagerConfig` / `ParagraphTranslateConfig` — 各功能模块配置
  - `CompileConfig` 包含 `engine`（编译引擎：auto/pdflatex/xelatex/lualatex）和 `pdfa`（PDF/A-2b 归档格式输出）配置
  - `TemplateGenConfig` 包含 `cleanup_enabled`/`cleanup_interval_seconds`/`cleanup_retention_days` 沙箱清理配置
  - `StyleModifyAgentConfig` 包含项目 Agent 会话目录、轮次、大小、shadow 编译和清理配置
- `ProjectToolsConfig` / `EditToolsConfig` / `WebSearchConfig` / `ScholarlyResearchConfig` — Function Calling 工具配置
  - `EditToolsConfig` 包含 `require_confirmation`（敏感操作是否需要前端确认弹窗）
  - `ScholarlyResearchConfig` 包含多 Provider 数据源开关、API Key、结果上限与请求超时，默认启用 OpenAlex
- `FeaturesConfig` — 功能聚合
- `LoggingConfig` — 日志配置
- `HttpConfig` — 外部 HTTP 请求全局配置（user_agent）
- `AppConfig` — 顶层聚合（server, security, llm, http, features, logging）

**核心接口**：

| 函数 | 功能 |
| --- | --- |
| `load_config(path)` | 加载 JSON + 环境变量覆盖 `LX_LLM_KEY` + Pydantic 校验 |
| `get_config()` | 获取缓存的 `AppConfig` 单例；**禁止在 `__init__` 中缓存** |
| `save_config()` | 将当前配置 `model_dump()` 写回磁盘；环境变量来源的 `api_key` 会自动清空，不会落盘 |
| `update_config_from_dict(updates)` | 用字典部分更新配置（前端 SettingsDialog 使用）；用户手动输入新 Key 时更新内存并允许持久化 |

**安全策略**：

- 环境变量 `LX_LLM_KEY` 优先级高于 `config.json` 中的 `api_key`
- 从环境变量注入的 API Key **禁止写回磁盘**：`save_config()` 内部通过 `_api_key_from_env` 标记自动识别 Key 来源，落盘前清空 `api_key` 字段
- 用户通过 UI 手动输入的新 Key 解除环境变量标记，正常持久化到磁盘
- 前端提交的 `api_key` 若以 `***` 开头（掩码），保留原值不更新

### `backend/core/security_middleware.py` — CORS 白名单与访问令牌 / 会话

统一处理跨域与访问控制：

- CORS 白名单来自 `server.allowed_origins`，白名单外的 `Origin` 一律返回 403，避免任意网页驱动用户本地后端（读取/修改 `.tex`、消耗 API Key）；空列表表示拒绝所有跨域来源（fail-closed）。
- 配置未加载时中间件拒绝所有请求（503），不降级为放行。
- 与请求 `Host` 同源的 `Origin` 自动放行——浏览器对同源 POST 也会携带 `Origin`，保证 FastAPI 直接托管 `static/` 的同源生产模式可用；白名单仍覆盖跨域部署场景。
- 每次请求实时读取 `get_config()`，配置热重载后立即生效。
- 前端 `api/index.js` 提供 `resolveUrl()` 解析后端返回的相对路径（不追加令牌）；第三方绝对 URL 原样返回，避免令牌泄漏给外部站点。

**单用户模式**：`security.access_token` 非空时，所有 `/api/*` 请求（`/api/health` 除外）必须携带 `X-Latexcellence-Token` 请求头；仅 EventSource 等确实无法自定义请求头的流式场景通过 `?token=` 查询参数传递。下载与 PDF/图片预览均通过带令牌头的 fetch + Blob 完成，避免令牌进入访问日志。

**多用户模式**（`multi_user.enabled=true`）：跳过共享访问令牌逻辑，改为会话认证——

- 公开端点：`/api/health`、`/api/config/status`、`/api/auth/login`、`/api/auth/register`；
- 其余所有 `/api/*` 请求必须携带有效的 HttpOnly 会话 Cookie（`latexcellence_session`），会话过期或账号被禁用即时失效（返回 401 `unauthorized`）；
- **请求级用户上下文全局注入（v2 P1b）**：会话验证成功后中间件即调用 `userspace.set_current_user_id(user.id)`——本中间件为纯 ASGI，设置的 ContextVar 与下游路由/依赖/SSE 生成器同任务上下文，对全部端点可见；LLM 每用户治理与 P6 会话类资源归属校验均依赖该上下文，不再要求各路由单独声明 `current_user_context` 依赖；
- **双提交 CSRF**：登录时额外下发可被 JS 读取的 CSRF Cookie（`latexcellence_csrf`），变更类请求（POST/PUT/PATCH/DELETE）必须携带 `X-Latexcellence-CSRF` 请求头且其值与会话携带的 CSRF Cookie 一致（常量时间比较）；缺失或不一致返回 403 `invalid_csrf`。与 `SameSite=Lax` 配合，覆盖同站子域/放宽同站点等场景；
- 多用户模式使用 Cookie 会话，允许的跨源响应（如 Vite dev `localhost:5173`→`8000`）会携带 `Access-Control-Allow-Credentials: true`（origin 仍回显具体值而非 `*`）。

### `backend/core/llm_client.py` — LLM API 客户端

封装 `openai.AsyncOpenAI`，提供统一的调用、重试和限流逻辑。

**职责边界**：`llm_client` 只负责网络通信和重试，**不负责 JSON 解析**。JSON 解析与 schema 校验统一由 `json_utils.py` 的 `safe_parse_json()` 和 `retry_with_validation()` 负责。

**核心接口**：

| 方法 | 功能 |
| --- | --- |
| `client` (property) | 懒加载获取 `AsyncOpenAI` 实例，使用 `threading.Lock` 双检锁保护并发创建（支持 `asyncio.to_thread()` 多线程场景） |
| `reset()` | 重置缓存的 `AsyncOpenAI` 实例，下次访问 `client` 时重新读取配置创建 |
| `chat()` | 非流式对话：调用 `chat.completions.create`，返回原始 `str`，由调用方负责 JSON 解析 |
| `chat_with_usage()` | 非流式对话：返回 `(text, UsageInfo \| None)`，摘要/记账等需要真实 token 用量的场景使用 |
| `chat_stream()` | 流式对话：返回 `AsyncIterator[str]`，按 token 产出，用于 SSE 推送 |
| `chat_stream_with_usage()` | 流式对话 + 末尾携带 UsageInfo：返回 `AsyncIterator[StreamChunk]`，`StreamChunk.content` 为文本片段、`StreamChunk.usage` 为末次 LLM token 用量（用 `stream_options={"include_usage": True}` 透传，第三方不支持时降级为 `None`） |
| `chat_stream_with_tools()` | 流式对话 + Function Calling：与 `chat_stream_with_usage` 类似，但额外接受 `tools` 参数。流中同时 yield content（文本 token 实时推送）和 tool_calls（末次 yield 完整 `ChatCompletionMessageToolCall` 列表） |
| `get_context_window()` | 获取模型上下文窗口（token）：显式配置 → `/models` 探测 → 超限错误学习 → 内置模型表 → 默认 200000；结果按 `(base_url, model)` 缓存，`reset()` 时失效，探测失败绝不抛出；探测地址与主服务不同源时不携带 API key |
| `_rate_limit()` | 内部限流器：确保两次请求间隔 ≥ `config.llm.request_interval` |

**数据类**：

```python
@dataclass class UsageInfo: prompt_tokens, completion_tokens, total_tokens
@dataclass class StreamChunk: content: str | None, usage: UsageInfo | None, tool_calls: list | None = None
```

**重试策略**（`chat()` 内部）：

- 最多重试 3 次
- 指数退避：第 N 次重试前等待 `2^(N-1)` 秒（1s → 2s → 4s）
- 仅网络/API 异常触发重试（`json.JSONDecodeError` 不再由客户端处理）

**单例模式**：全局 `_llm_client` 通过 `get_llm_client()` 访问，使用 `threading.Lock` 双检锁保护多线程场景。`client` 属性首次访问时读取 `get_config().llm` 创建实例并缓存；当用户通过 SettingsDialog 更新 `llm` 配置时，`config.update_config_from_dict()` 会调用 `get_llm_client().reset()` 清除缓存，使下一次访问 `client` 时按新配置重新创建，从而支持运行时热重载 `api_key` / `base_url`。

**每用户治理门（多用户 v2 P1b）**：全部公共入口（`chat_with_usage` / `chat_stream` / `chat_with_tools` / `chat_stream_with_usage` / `chat_stream_with_tools`）在最外层套 `core/multiuser/llm_governance.py` 的 `admission_gate()`：多用户模式下读取请求级当前用户上下文（由 `AccessSecurityMiddleware` 会话验证成功后全局注入，覆盖全部 `/api` 端点），在单事务内完成"滑动窗口限流校验 + 事件落账 + 并发租约获取"，超限抛 429 语义 RuntimeError（`gov_code` = `llm_rate_limited` / `llm_concurrency_limited`，携带 `retry_after`），由 main.py 注册的全局异常处理器（`register_governance_handler`）翻译为 HTTP 429 + Retry-After + 既有 `detail` 包装的结构化错误体；调用结束 finally 释放租约（流式提前断开同样覆盖）。单用户模式或无用户上下文（后台任务）直通且零数据库依赖。实现细节见下方"多用户模式基础设施"一节的 `llm_governance.py` 行。

### `backend/core/logger.py` — 日志系统

- `TimedRotatingFileHandler`，每天午夜旋转，保留 30 天
- 文件日志 `DEBUG`，控制台由 `config.logging.level` 控制
- 静默第三方 HTTP 库噪声日志

**核心接口**：`get_logger(name)` / `setup_logging(level, log_dir)`

### `backend/core/project_manager.py` — LaTeX 项目管理

管理 LaTeX 项目目录的生命周期：打开、文件树、段落提取、原地修改、撤销/重做。

**异常类**：

- `FileConflictError` — 乐观锁冲突异常。当 `apply_change()` 检测到文件 MD5 与预期不符时抛出，携带 `file_path`、`expected_md5`、`current_md5` 三个属性，供上层路由返回 HTTP 409。

**数据类**：

```python
@dataclass
class Paragraph:
    index: int           # 段落序号（从 0 开始）
    text: str            # 段落文本（多行以 \n 连接，保留原始缩进）
    file_path: str       # 文件相对路径
    line_start: int      # 起始行号（1-indexed）
    line_end: int        # 结束行号（1-indexed）

@dataclass
class FileNode:
    name: str            # 文件名
    path: str            # 相对路径
    is_dir: bool         # 是否为目录
    children: list["FileNode"]  # 子节点

@dataclass
class ProjectInfo:
    root_path: str       # 项目根目录绝对路径
    main_file: Optional[str]  # 主文档路径（如 main.tex），未找到时为 None
    file_tree: list[FileNode]
    dependency_graph: dict[str, list[str]]
```

**核心接口**：

| 方法 | 功能 |
| --- | --- |
| `open(path)` | 打开项目目录，扫描文件树，检测主文档，构建依赖图 |
| `get_file_tree()` | 返回文件树（仅展示 `.tex`/`.bib`/`.cls`/`.sty`/`.pdf` 文件） |
| `get_file_md5(file_path)` | 返回指定文件的当前 MD5 哈希值（乐观锁版本号），同时更新内部缓存 |
| `get_paragraphs(file_path)` | 按空行分割文件为段落，记录行号范围 |
| `apply_change(file_path, idx, text, expected_md5=None)` | 原地修改段落：乐观锁校验 → 备份 → 替换行范围 → 原子写入 → 更新 MD5 缓存 |
| `apply_whole_files(updates, expected_md5s=None, allowed_suffixes=None)` | 批量替换多个文件的完整内容：先校验全部 MD5，再逐文件备份、原子写入；任一文件失败时恢复本次已写入文件 |
| `set_main_file(file_path)` | 手动设置主文档路径 |
| `undo(file_path)` / `redo(file_path)` | 撤销 / 重做（委托给 `BackupManager`） |
| `can_undo(file_path)` / `can_redo(file_path)` | 检查撤销/重做栈是否有记录 |

**乐观锁机制**：`apply_change()` 支持 `expected_md5` 参数。优先使用调用方传入的 MD5（跨工作区乐观锁），否则退回到实例内部缓存的 MD5（单工作区内校验）。两者均不为 `None` 时，若当前文件 MD5 与预期不符，抛出 `FileConflictError`。

**whole-file 批量事务**：`apply_whole_files()` 供 style_modify 项目 Agent 的多文件 proposal 写回与 bib_manager 的 `.bib` 整文件写回使用。方法在一个事务锁内先完成路径、后缀、符号链接和 MD5 校验，再为每个文件调用 `BackupManager.push()` 并 `atomic_write()`。写入失败时恢复全部目标文件，成功后更新内部 MD5 缓存。

**主文档检测逻辑**（递归深度限制 2 层）：

1. 精确匹配文件名为 `main.tex`（不区分大小写） → 使用
2. 文件名包含 `"main"` 子串的 `.tex` 文件（如 `main-chapter1.tex`） → 使用
3. 若仍未找到，取文件树中第一个 `.tex` 文件（仅顶层，不递归）
4. 若无任何 `.tex` 文件，返回 `None`

**依赖图构建**：扫描所有 `.tex` 文件中 `\include{`、`\input{`、`\subfile{` 引用关系。

### `backend/core/base_paragraph_check.py` — 逐段检查服务基类

多个逐段检查模块（typo_check、expression_opt、code_review、terminology_check、paragraph_translate）共享的基类，封装文件解析、marker 替换、LLM 编排（批量+流式）、结果写回等流程。

**核心接口**：

| 方法/属性 | 功能 |
| --- | --- |
| `BaseParagraphCheckService` | 抽象基类，定义多个 hooks 供子类实现 |
| `_get_system_prompt()` | 返回 System Prompt（含 marker 保护约束） |
| `_build_user_content(text)` | 构建发给 LLM 的 User 内容 |
| `_get_model_cls()` | 返回结果 Pydantic 模型类 |
| `_get_llm_params()` | 返回 LLM 调用参数（temperature 等） |
| `_get_batch_size()` | 返回批处理大小（从配置读取） |
| `_build_success_result(idx, validated, original_text, file_path, plain_text)` | 构建成功结果对象 |
| `_restore_change_markers(changes, file_path, para_index, fields)` | 将 changes 中的 marker 还原为 LaTeX（供前端展示）；返回 `(restored_changes, marker_map)`，调用方可复用 marker_map |
| `preview_paragraphs(file_path, resolve_input)` | 预览段落：提取 + marker 替换 → 返回纯文本 |
| `_run_batch_check()` / `_run_stream_check()` | 执行检查：批量发送给 LLM 或 SSE 流式返回 + 校验 |
| `apply_changes(file_path, changes, workspace_id, operation_id)` | 应用修改：还原 marker → 乐观锁校验 → 原子写入；冲突或 marker_map 缺失时跳过该段并返回 `errors` |

**模块级辅助函数**：

| 函数 | 功能 |
| --- | --- |
| `inject_expected_md5s(changes, fallback_file, md5s_map)` | 为 change 列表补充 `expected_md5`：从文件级 `md5s_map` 中查找并注入，供各逐段检查模块的 router 统一调用 |
| `normalize_changes_contract(changes, output_text, plain_text)` | 丢弃与主字段不一致的虚假 changes 条目：主字段与原文逐字符一致但 changes 非空 → 清空。后端不伪造展示数据（type 是前端本地化展示字段，合成条目会引入不一致的 type 值）；"有修改时 changes 非空"由 system prompt 约束。typo_check / expression_opt / paragraph_translate 的 `_build_success_result` 在 marker 还原前调用 |
| `has_unlisted_changes(changes, output_text, plain_text)` | 检测"文本已修改但 changes 为空"的段落：主字段与原文不同且 changes 为空 → True。不合成整段条目，由前端把 `unlisted_changes=true` 的段落视为有问题并展示全文修改（应用/忽略），避免修改静默丢失 |

**关键设计**：

- `ApplyChangeItem` 共享模型（`paragraph_index`, `new_text`, `file_path`）：各逐段检查模块 router 的 apply 接口统一使用
- `CheckOperationContext` 操作上下文：每次 preview/check 生成唯一 `operation_id`，深拷贝 `marker_maps` 存入**进程级共享存储**（`store_operation_context` / `get_operation_context`，带锁保护）；服务实例按请求创建，apply 时通过 `operation_id` 从共享存储取回上下文还原 marker。若 context 丢失（过期/重启/新操作覆盖），逐段检查 `marker_map` 缺失时跳过写入并返回 `errors`，防止字面 marker 污染文件
- 类变量控制差异：
  - `_output_field`: 结果字段名（`"corrected"` / `"optimized"` / `"translated"` 等）
  - `use_marker_protection`: 是否启用 marker 保护（typo_check / expression_opt / terminology_check / paragraph_translate 为 `True`；code_review 为 `False`，见 docs/modules/code_review.md）
- **轻量内容校验**：`_check_one_paragraph` 在模型校验通过后、marker 计数/顺序校验前，对输出字段调用 `check_latex_text_lite()` 兜底——只拦控制字符（排除 `\t` / `\n`，`\r` 仍拦截）与垃圾占位符，不查配平，避免 marker 化段落的残余结构误报；失败直接回退原文，防止损坏内容进入写回链路
- **上下文 TTL 清理**：默认 1 小时后自动过期

### `backend/core/prompts.py` — 中央提示词管理

集中存储所有 LLM 提示词模板，按模块和功能分类。提供 `get_prompt(module, key, **kwargs)` 函数获取提示词，支持参数化模板（如 `language` 输出语言注入）。各模块通过 `from backend.core.prompts import get_prompt` 统一导入使用，便于统一维护、版本控制和快速调整。

**格式化约定**：`get_prompt` 对所有模板执行 `str.format()`（即使未传 kwargs），因此模板中的字面花括号必须写成 `{{` / `}}`；含占位符的模板漏传参数会立即抛 `KeyError` 快速失败，避免把 `{language}` 之类的字面占位符发送给 LLM。新增或修改模板后应运行 `tests/test_prompts.py` 回归。

**统一提示词结构**：常规（非 Agent）system 提示词按 `<role>` → `<task>` → `<critical_rules>` → `[<input>]` → `<output_format>` → `<examples>` 组织；`<input>` 仅在输入嵌入 system 时存在（如 bib_manager 的 format_analysis），逐段检查类（typo_check / expression_opt / paragraph_translate）的文本由 user 提示词以 `<input>` 包裹，code_review 的 user 提示词以 `<latex_source>` 包裹不可信源码；expression_opt 允许在末尾追加 `<custom_instructions>`（用户自定义指令，仅影响风格、语气与术语偏好，受 `CUSTOM_INSTRUCTIONS_ISOLATION_RULE` 约束，不得覆盖 Marker 保护、输出协议、无修改契约与工具授权）。Agent 提示词在阶段 3 拆分时接入同一结构：核心/常驻 system 内的规则进一步用一致的 XML 分区（workflow、quality gate、tool protocol、failure budget 各占一个成对闭合标签），每个阶段块（template_gen 的 planning / compile_phase / validation_phase，plot_gen 与 style_modify 的 refine）以 `<stage_contract>` 记录 entry / input / output / done / fallback，阶段提示词仍延迟注入。共享规则常量包括 `MARKER_PROTECTION_RULES`（Marker 保护）、`JSON_ONLY_RULE`（只输出 JSON）、`NO_INVENTION_RULE`（事实/数据提取类任务禁止臆造输入中不存在的内容）、`GENERATION_FIDELITY_RULE`（生成类任务必须基于输入，不得虚构输入中不存在或无法推导的数据/事实）、`UNTRUSTED_DATA_RULE`（外部数据不可信）、`TOOL_RESULT_PROTOCOL_RULE`（工具结果统一协议：`shown`/`total`/`truncated`/`next_step` 状态块，`truncated=yes` 时按 `next_step` 缩小范围或继续读取），供各模块提示词复用，`tests/test_prompts.py` 有结构化回归测试。

**JSON 转义规则常量**：`LATEX_JSON_ESCAPE_RULES` 用于"JSON 字符串直接承载 LaTeX 源码"的提示词（如 code_review）；`BIBTEX_JSON_ESCAPE_RULES` 用于承载 BibTeX 源码的场景（bib_manager 的 format_analysis / bibtex_generation），其差异在于 BibTeX 的花括号是结构定界符、不反斜杠转义，`$` 是普通字符而非数学模式定界符。

**changes 契约**：typo_check / expression_opt / paragraph_translate 的 system prompt 双向约束 `changes` 与主字段一致性——无修改时 `changes` 必须为 `[]`，有修改时 `changes` 必须非空且逐条对应。后端兜底两条：主字段未变但 changes 非空 → 清空；主字段已变但 changes 为空 → 结果带 `unlisted_changes=true`，前端展示全文修改而非静默丢弃。typo_check 的 `type` 是前端展示用的本地化错误类型（由模型按 `{language}` 输出），不参与逻辑判断。`compile_error_translator` 的 system prompt 拼接 `LATEX_JSON_ESCAPE_RULES`，`fixes` 中的 LaTeX 建议按 JSON 转义形式输出（每个反斜杠写两个）。

### `backend/core/tool_registry.py` — Function Calling 工具注册表

全局工具注册表，所有可暴露给 LLM 的工具统一管理。通过 `get_tool_registry()` 获取单例。

**核心接口**：

| 方法 | 功能 |
| --- | --- |
| `register(name, schema, executor, enabled, config_path)` | 注册工具。`config_path` 声明该工具受哪个配置字段控制 |
| `get_enabled_tools()` | 返回当前可用的工具 schema 列表（注入 LLM `tools` 参数） |
| `execute(tool_name, arguments)` | 根据 tool_call.name 分发到对应执行函数 |
| `set_enabled(name, enabled)` | 运行时动态启用/禁用 |

#### 如何添加新工具

工具注册分为两步：**① 实现执行函数** → **② 在合适的初始化时机注册**。以下以添加 `get_current_time` 工具为例。

**1. 实现执行函数**

```python
# backend/utils/time_utils.py 中新增
async def _get_time_executor(tool_name: str, arguments: dict) -> str:
    """返回当前日期时间。"""
    from backend.utils.time_utils import get_current_time_context
    ctx = get_current_time_context()
    return f"Current date: {ctx['date']} ({ctx['weekday']}), time: {ctx['time']}"
```

执行函数签名必须为 `async (tool_name: str, arguments: dict[str, Any]) -> Any`。返回类型不限（会被 LLM 以 JSON 形式接收）。

**2. 注册到 ToolRegistry**

将注册代码放在所依赖模块的初始化位置。例如工具依赖 `web_search` 模块，就放在 `SearchManager` 初始化末尾：

```python
# backend/utils/web_search/manager.py — get_search_manager() 末尾
registry.register(
    name="web_search",
    schema=schema[0],        # OpenAI function schema（不含 tools wrapper）
    executor=_web_search_executor,
    config_path="features.function_calling.web_search.enabled",  # ← 声明配置路径
)
```

`config_path` 参数将工具和 `config.json` 中的功能开关绑定：

- `config_path="features.function_calling.web_search.enabled"` → 当 `config.json` 中 `features.function_calling.web_search.enabled = false` 时，`get_enabled_tools()` 自动排除此工具
- `config_path=None`（默认）→ 不受配置控制，仅受 `enabled` 参数或 `set_enabled()` 控制

**3. 在消费端启用**

已在用 `chat_with_tools()` 的模块（如 `writing_chat/service/`）中：

```python
# writing_chat 的 _get_enabled_tools()
from backend.core.tool_registry import get_tool_registry
tools = get_tool_registry().get_enabled_tools()
```

`get_enabled_tools()` 会自动过滤：同时满足 `config_path` 指向的配置开关为 `true` 且 `entry.enabled = true`。

**完整示例：注册 `compile_latex` 编译工具**

```python
# backend/modules/compile/service.py（或模块初始化点）
from backend.core.tool_registry import get_tool_registry
from backend.core.logger import get_logger

_logger = get_logger("compile")

COMPILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compile_latex",
        "description": "编译当前 LaTeX 项目并返回错误列表",
        "parameters": {
            "type": "object",
            "properties": {
                "main_file": {
                    "type": "string",
                    "description": "主文档路径（相对于项目根目录）",
                },
            },
            "required": ["main_file"],
        },
    },
}

async def _compile_executor(tool_name: str, arguments: dict) -> str:
    """执行编译，返回结构化结果。"""
    from backend.core.config import get_config
    from backend.utils.tex_compile import run_tex_compile
    from pathlib import Path

    cfg = get_config()
    main_file = arguments["main_file"]
    result = await run_tex_compile(
        main_file=main_file,
        cwd=Path.cwd(),
        timeout=cfg.features.compile.latexmk_timeout,
        engine=cfg.features.compile.engine,
    )
    return result.model_dump_json()

# 注册（通常在模块首次初始化时调用）
get_tool_registry().register(
    name="compile_latex",
    schema=COMPILE_SCHEMA,
    executor=_compile_executor,
    config_path="features.compile.enabled",
)
```

### 目前已注册的全局工具

| 管理器 | 工具 | 配置路径 | 说明 |
| --- | --- | --- | --- |
| `SearchManager` | `web_search` / `open_page` / `find_in_page` / 条件启用的 `read_page_sections` | `features.function_calling.web_search.enabled` | 联网搜索与网页读取 |
| `ProjectToolsManager` | `list_files` / `read_file_content` / `get_section_structure` / `search_across_files` | `features.function_calling.project_tools.enabled` | 项目文件浏览 |
| `EditToolsManager` | `edit_paragraph` / `undo_last_edit` | `features.function_calling.edit_tools.enabled` | 段落编辑（敏感操作需用户确认） |
| `ResearchManager` | 学术检索、详情、关联作品、摘要/OA 定位、数据资源检索 | `features.function_calling.scholarly_research.enabled` | 多 Provider 联邦学术研究，默认 OpenAlex |

### 工具结果统一协议（Tool Result Protocol）

`backend/utils/tool_result_protocol.py` 提供 `format_result_status()`，为可能返回不完整结果的工具在结果末尾追加统一状态块：

```
────────────────────────────────────────────
shown: 50
source: ...
total: 312
truncated: yes
next_step: ...
```

- 字段固定为英文 key：`shown`（本次返回数）、`total`（结果总数，未知为 `?`）、
  `truncated`（`yes`/`no`，结果是否被截断）、`next_step`（下一步动作），
  可选 `source` 与附加说明行。
- 已接入工具：`list_files`（上限 200 个文件）、`read_file_content`（未指定
  `end_line` 时按 200 行预算，超限给出下一段 `start_line`；单行 500 字符截断）、
  `search_across_files`（匹配总数统计上限 1000，截断时提示缩小范围）、
  `search_scholarly_works` / `find_related_works` / `search_research_data_resources`
  （`total_estimated` / `total` 大于展示数时标记截断）。
- `prompts.py` 的共享常量 `TOOL_RESULT_PROTOCOL_RULE` 注入 `writing_chat.chat_system`
  与 `scholarly_research_tool_guide`，要求模型在 `truncated=yes` 时按 `next_step`
  缩小范围、继续读取或换用工具，不能把截断结果当成完整集合。

### SearchManager 聚合策略

`web_search.strategy` 决定搜索运行时：`legacy` 保留主引擎失败后串行降级的既有行为；`aggregate` 将已启用引擎与结构化查询计划的变体组成 lane，在总截止时间内并发执行。每个 lane 返回 `EngineStatus`，单个引擎超时、限流或失败只把响应标为 `partial`，不会丢弃其他引擎的成功结果。连续失败会短时打开进程内熔断器；配置热重载重建 `SearchManager` 时熔断状态随之清空。

聚合候选先通过 `url_utils.py` 仅保留公开 HTTP(S) URL，移除 fragment、默认端口和常见跟踪参数，并拒绝凭据、私网地址及非标准私网 IP 写法。`ranking.py` 合并规范 URL和近重复文本，记录各 lane 原始名次与来源，再按 RRF、跨引擎共识、来源类型权重排序，并应用单域名结果上限。`ranking.mode=legacy` 可保留聚合 lane 的稳定旧版顺序；显式传入 `engine_name` 始终绕过聚合，提供单引擎兼容路径。

查询重写由 `SearchQueryPlanner` 生成 `SearchQueryPlan`，LLM JSON 必须经过 `retry_with_validation()` 和 Pydantic 严格校验。重试耗尽或 LLM 不可用时使用只保留原查询的规则计划，搜索本身仍可继续。

### 结构化网页读取

`web_search.page_reading.strategy=structured` 时，`page_reader.py` 编排 `fetch/` 与 `extract/`：`FetchCoordinator` 只访问公网 HTTPS，逐跳复核重定向，限制响应大小，并通过阻断检测、域名调度、`Retry-After`、退避和熔断返回带诊断信息的 `FetchFailure`；原始缓存使用 URL 哈希键，只保存正文及 ETag、Last-Modified、Content-Type 等安全元数据。新鲜条目直接命中，过期条目发送条件请求。抽取缓存键包含内容 hash 与 `extractor_version`，算法版本变化会自然失效。启用浏览器回退时，JavaScript shell 或低质量 HTTP 页面进入隔离的 Playwright context；导航和子资源继续执行 SSRF 校验，登录、验证码、挑战和付费墙只返回明确阻断状态。网页读取处于 `structured` 时，`read_page_sections` 工具注册并可用；切回 `legacy` 后 `open_page` / `find_in_page` 继续使用 DeepReader，`read_page_sections` 不注册。

ExtractorRegistry 先处理已知公开端点（当前代码托管页面使用 GitHub 官方 raw 端点），再按 MIME 分派。HTML 抽取比较语义 DOM 与 Trafilatura 候选，通过确定性质量分选优；`ContentBlock` 保留块类型、标题路径、链接和稳定块 ID，`DocumentSection` 构成大纲。`chunking.py` 不拆散普通块，先用 BM25 召回，再用 MMR 抑制重复，并把 block ID 写入 `EvidenceChunk`。CPU 密集型解析通过 `asyncio.to_thread()` 执行。切回 `legacy` 后 `open_page` / `find_in_page` 继续使用 DeepReader，`read_page_sections` 不注册。

**注意事项**：

- `schema` 是整个 OpenAI function schema（包含 `type` 和 `function`），不含外层 `tools` 数组
- 注册时机避免在模块 `__init__` 中执行，宜在首次调用时或 FastAPI `lifespan` 中注册
- 敏感工具（如写文件）建议添加用户确认环节：工具执行前推 SSE `confirmation_required` 事件
- 若工具需要私有不暴露给其他模块，可在模块内部维护私有 executor（如 `template_gen/agent/executor.py` 的 `_ToolExecutor`），不注册到全局 `ToolRegistry`

### `backend/core/workspace_manager.py` — 多工作区管理器

支持多浏览器同时处理不同 LaTeX 项目。

**核心接口**：`get_workspace_manager()` / `get_project_manager_for_workspace(workspace_id)` / `WorkspaceManager.remove()` / `WorkspaceManager.list_workspaces()`

**并发保护**：工作区表由 `RLock` 保护，`get_or_create()` / `get()` / `remove()` / `list_workspaces()` 为线程安全操作；每个 `LatexProject` 实例内部用独立锁保护 `_file_md5` 缓存读写。

**workspace_id** 通过查询参数传递，前端在 localStorage 生成唯一 ID。

### 多用户模式基础设施（`backend/core/multiuser/` + `backend/core/access_control.py` + `backend/modules/auth/` + `backend/manage.py`）

多用户模式（`multi_user.enabled=true`）的后端基础设施，按阶段（见 [docs/multi-user-plan.md](multi-user-plan.md)）实施。P1 已完成：SQLite 初始化、注册/审批/登录/会话/Cookie/CSRF、管理员引导与 CLI；P2 已完成：用户空间目录、统一路径解析、项目访问控制、workspace 绑定与现有端点收紧；P3 已完成：上传/解压管线、项目命名、配额记账、回收站与下载；P4 已完成：协作者管理、转交、viewer 写保护与共享项目撤销禁用；P5 已完成：前端登录/注册/工作台/上传/权限/管理面板全套；P6 已完成：migrate-legacy 存量迁移、会话类资源按用户归属校验与存量路径改造；P7 已完成：部署指南（HTTPS、风险声明，见 [deployment.md](deployment.md)）与全量文档同步。

**包结构**：

| 文件 | 职责 |
| --- | --- |
| `backend/core/multiuser/db.py` | SQLite 初始化（v1 业务表 + v2 沙箱/治理表，WAL + 外键）、连接管理与统一查询入口（`db_execute` / `db_query_one` / `db_query_all`，同步线程安全）、版本迁移框架（PRAGMA `user_version` + 有序幂等步骤） |
| `backend/core/multiuser/userspace.py` | 用户空间目录布局（`<users_root>/<user_id>/{projects, writing_chat_sessions, style_modify_sessions, templates, plots}`），`ensure_user_dirs()` 注册时创建；P6 新增请求级当前用户上下文（`set/get_current_user_id`）与统一会话资源根解析 `user_resource_root(subdir, legacy_root)`（单用户回退存量全局路径）、`iterate_user_resource_roots()`（后台清理枚举各用户资源目录） |
| `backend/core/multiuser/auth.py` | 密码哈希（argon2id）、会话令牌（库只存 SHA-256 哈希）、用户管理（注册 pending / 审批 / 禁用 / 重置密码 / 配额覆盖）、登录限流（每用户名+每 IP 分钟级；v2 P1b 起计数入 `rate_limit_events` 表 kind='login'，替代进程内字典）、`ensure_bootstrap_admin()` |
| `backend/core/multiuser/dependencies.py` | FastAPI 认证依赖：`get_current_user`（401）、`require_admin`（403）、`get_current_user_optional`、`require_admin_if_multiuser`；P6 新增 `current_user_context`（注入请求级当前用户用于会话类资源归属校验） |
| `backend/core/multiuser/quota.py` | 配额记账核心：有效额度解析（用户覆盖优先）、项目/衍生用量统计、上传/写回/恢复前置校验（超限抛 413 `quota_exceeded`）、项目唯一命名与建行（`xx (2)`）、对账（`reconcile_project_sizes`）、回收站过期清理 |
| `backend/core/multiuser/upload.py` | 上传解压管线核心：zip（UTF-8/GBK/cp437 编码、路径穿越/符号链接/加密/炸弹拦截）与 7z（py7zr）安全解压、rar（外部 unar 管线，多用户 v2 P2：进程组限时/超配额击杀、RAR4 加密与分卷标志预扫、受保护包输出关键词兜底、零产物兜底）、项目根归一化、`move_into_user_space()` 落位 |
| `backend/core/multiuser/maintenance.py` | 后台维护循环：配额对账 + 回收站/暂存区清理 + 沙箱/LLM 租约与限流事件清理（`main.py` lifespan 启动） |
| `backend/core/multiuser/sandbox_leases.py` | 沙箱并发池租约（v2 P1a）：`sandbox_slots` 表原子获取/释放，TTL 清理崩溃残留 |
| `backend/core/multiuser/llm_governance.py` | LLM 每用户治理（v2 P1b）：60s 滑动窗口限流（`rate_limit_events` kind='llm'）+ 并发租约（`llm_concurrency_leases`，TTL 900s），单事务原子准入；超限抛带 `gov_code` 的 RuntimeError（429），`governance_http_response()` 翻译为 429 + Retry-After + `detail` 包装结构化错误体，`register_governance_handler()` 供 main.py 与测试注册同一处理器；`admission_gate()` 供 `llm_client` 各入口包裹，单用户/无用户上下文直通；维护入口 `purge_expired_llm_leases()` / `purge_old_rate_limit_events()` |
| `backend/core/multiuser/migrate.py` | 存量数据迁移工具（P6）：`migrate_legacy()` 把存量全局目录（writing_chat_sessions / style_modify_sessions / templates / plots / formats / prompts）幂等复制到管理员用户空间，`--dry-run` 预演，报告 + 目录树校验 |
| `backend/core/access_control.py` | 统一访问控制：`owner/editor/viewer` 权限矩阵、`require_project_access()` / `get_projects_for_user()` / `get_user_role_for_project()` / `is_shared_project()`、基于 workspace 绑定的请求级依赖 `require_workspace(write=...)`；权限实时查库不缓存 |
| `backend/modules/projects/router.py` | 薄路由层：`/api/projects`（列表 / 上传创建）与 `/api/projects/{id}`（重命名 / 回收站 / 恢复 / 永久删除 / 下载 / 权限协作 / 转交） |
| `backend/modules/projects/service.py` | 项目业务逻辑：流式接收压缩包、安全解压（`asyncio.to_thread`）、配额校验、唯一命名、落位、回收站流转、打包下载、协作者管理与项目转交 |
| `backend/modules/auth/router.py` | 薄路由层：`/api/auth/*`（register/login/logout/me）与 `/api/admin/*`（用户列表/审批/禁用/重置密码/配额） |
| `backend/modules/auth/service.py` | 认证业务逻辑（`asyncio.to_thread` 调度压库操作；错误映射为结构化 `{"error": code, "message": ...}`） |
| `backend/manage.py` | CLI：`python -m backend.manage create-admin / reset-password / list-users / set-status / migrate-legacy` |

**关键约定**：

- `init_db()` 与各业务函数实时读取 `get_config()`，不缓存路径/阈值；数据库路径在测试/换配置时变化会自动重连。
- 所有 SQLite 压库操作为同步函数，事件循环中通过 `asyncio.to_thread` 调用，禁止阻塞事件循环（v1 单进程，SQLite WAL 足够）。
- 会话 Cookie：`HttpOnly` + `SameSite=Lax`，HTTPS 请求下 `Secure`；默认 7 天、记住我 30 天；会话吊销在禁用/重置密码时立即生效。
- 登录凭据错误返回 401 `invalid_credentials`（独立于会话过期的 `unauthorized`，前端据此展示「用户名或密码错误」且不触发全局登出；避免用户名枚举）；**登录采用恒定时长校验**：用户名不存在时也对占位哈希执行 argon2 验证，用户名枚举响应耗时一致，封堵时序侧信道；待审批返回 403 `account_pending`，禁用返回 403 `account_disabled`。
- 管理员引导：首次启动且用户表为空时，从环境变量（`LATEXCELLENCE_ADMIN_USERNAME` / `LATEXCELLENCE_ADMIN_PASSWORD`）创建管理员；未设置则等待 CLI `create-admin`。
- 端到端注册审批流程：注册（pending）→ 管理员审批（active）→ 登录 → 会话访问；未登录访问受保护接口 / 会话过期返回 401。

**P2 用户空间与访问控制要点**：

- 项目目录由 `project_id` 派生（`data/users/<owner_id>/projects/<project_id>/`）；项目文件读写统一经 `resolve_user_path()`（`path_guard`，基于 `resolve_project_path`）解析，禁止直接拼接路径；符号链接默认拒绝。
- `WorkspaceManager` 增加 `bind()/get_binding()/unbind()`：多用户模式下 `/api/project/open` 打开项目时写入 `workspace_id → (user_id, project_id, owner_id, root_path)`；`/remove` 时一并清理绑定。
- `access_control.require_workspace(write=...)` 为请求级 FastAPI 依赖（`require_workspace_read` / `require_workspace_write`）：单用户模式直接放行；多用户模式按当前登录用户**实时查库**校验所属项目角色，写操作要求 `viewer` 之外的权限；未绑定工作区返回 400 `invalid_workspace`。
- 端点收紧（多用户模式）：`POST /api/project/open` 仅接受 `project_id`（携带 `path` 返回 400 `path_disabled`，无 `project_id` 返回 400 `invalid_request`）；`GET /api/project/browse` 返回 403 `no_permission`；`apply-change` / `set-main-file` / `undo` / `redo` 经 `require_workspace_write` 校验；共享项目（存在任意协作者）v1 禁用撤销/重做（`undo`/`redo` 403，`can-undo`/`can-redo`/`undo-status` 恒为 False）。

**P3 上传与配额要点**：

- 上传入口 `POST /api/projects`（multipart 字段 `archive`，可选 `name`；也支持 `ProjectId` 之外的命名表单字段）。压缩包**流式写入** `data/staging/<upload_id>/`，不整包载入内存；支持 `.zip`、`.7z`（`py7zr`）与 `.rar`（外部 `unar`，v2 P2；二进制缺失返回 400 `unar_missing`，Docker 部署由后端镜像内置）。
- 解压校验（失败即拒绝，不落用户空间）：压缩包大小、文件数、单文件大小、解压总量 ≤ 剩余配额、压缩比（炸弹拦截）、拒绝绝对路径 / `..` / 符号链接 / 设备文件 / 加密包 / 分卷包、大小写不敏感重名、解压限时；zip 文件名按 UTF-8 标志优先、GBK 回退、cp437 兜底。rar 解压前对 RAR4 头链做最小预扫，命中加密 / 分卷标志即确定性拒绝（`archive_unsafe`）；RAR5 由 unar 失败输出关键词（password 等）与“成功但零产物”（多卷集第一卷单独上传的静默形态）兜底拒绝。已知差异：unar 对非 UTF-8 文件名依赖字符集探测，解码结果可能与 zip 管线不一致（GBK 名字可能被还原为其他字符），不影响安全校验与配额记账。
- 项目根归一化：暂存区仅一个顶层目录 → 以该目录为项目根（兼容“打包上层目录”）；否则暂存区本身为项目根（兼容“直接打包内容”）。
- 项目命名：与同 owner 活动项目去重，自动生成 `xx (2)`；唯一索引（`UNIQUE(owner_id, name) WHERE active`）+ 捕获 `IntegrityError` 重试保证并发安全。
- 配额记账：项目空间算 `projects.size_bytes`（活动项目之和，回收站不计入）；`/api/auth/me` 与项目列表实时返回用量；后台 `reconcile_project_sizes()` 周期对账修正缓存漂移。上传、恢复、写回（`apply-change`）超限统一返回 413 `quota_exceeded`；写回超限会**回滚本次修改**（不允许部分写入）。
- 回收站：`DELETE /api/projects/{id}` 进回收站（`status='deleted'`，删除即腾出配额）；`restore` 恢复（超限禁止）；`permanent` 提前永久删除；后台按 `recycle_bin_days` 清理过期回收站项目。
- 下载：`GET /api/projects/{id}/download` 打包 zip，owner / editor / viewer 均可，临时文件由 `BackgroundTasks` 清理。
- 项目 API 均只在校验读/写/owner 权限（`access_control`）后执行；管理型动作（重命名/删除/恢复/永久删除）仅 owner。

**P4 权限协作要点**：

- 协作者管理端点（owner 专属，`/api/projects/{id}/permissions`）：`GET` 列表（owner + 协作者及其角色/加入时间）；`POST` 按用户名添加（已存在则更新角色，`editor` / `viewer`）；`PUT /{user_id}` 改角色；`DELETE /{user_id}` 移除。
- 校验规则：协作者必须是 `active` 用户、不能是 owner 自身；非 owner 访问返回 403 `no_permission`；未知用户 404 `user_not_found`；非 active 400 `invalid_user`；非法角色由 Pydantic `Literal` 校验（422）。
- 权限**实时查库**：添加 / 改角色 / 移除后，下一次请求立即生效（对端体验可即时验证，如被移除的协作者下一次读请求即 403）。
- 转交端点 `POST /api/projects/{id}/transfer`（body：`username` + `keep_access`）：owner 将项目转给其他 `active` 用户；`keep_access=True` 时原 owner 转后保留为 viewer，否则直接退出协作。
- 转交实现要点：转交前校验**接收方项目空间配额**（超限 413 `quota_exceeded`，与上传/恢复同口径）；项目目录沿 `data/users/<owner_id>/projects/<project_id>/` 规范**迁移到新 owner 空间**（保证 `root_path` 派生、配额记账与对账一致）；新 owner 有同名活动项目时自动生成 `xx (2)`（复用 `next_available_name`）；新 owner 原为协作者则转正并移除其协作者行；已打开该项目的 workspace 绑定同步更新 `owner_id` / `root_path` 并重新打开文件树。
- viewer 写保护与共享项目撤销禁用（P2 已落地）：viewer 的 `apply-change` / `set-main-file` / `undo` / `redo` 返回 403；共享项目（存在任意协作者）v1 禁用撤销/重做。

**P6 迁移与存量隔离要点**：

- 会话类资源归属校验：`userspace.user_resource_root(subdir, legacy_root)` 为统一解析入口——多用户模式按请求级当前用户返回 `<users_root>/<user_id>/<子目录>/`（写作会话 / style_modify 会话 / 模板 / 绘图四类资源按用户隔离），单用户模式返回存量全局路径（行为不变）。当前用户由路由依赖 `dependencies.current_user_context`（基于会话 Cookie 的 `get_current_user_optional`）注入，四个路由（writing-chat / style-modify / template-gen / plot-gen）统一挂载。
- 存量路径改造：写作会话 `_sessions_dir`、style_modify `get_sessions_root`、模板 `_output_root`、绘图 `get_plot_output_dir` 全部改为经 `user_resource_root` 解析；对应 `cleanup.py` 后台清理在单用户模式扫存量全局目录、多用户模式经 `userspace.iterate_user_resource_roots()` 枚举全部用户资源目录。
- 迁移工具 `python -m backend.manage migrate-legacy`：把存量 `data/writing_chat_sessions/`、`data/style_modify_sessions/`、`data/templates/`、`data/plots/`、`data/formats/`、`data/prompts/` 幂等复制到管理员用户空间对应子目录；目标文件已存在则跳过（不覆盖迁移后新数据）；复制后逐文件校验目录树并输出报告；`--dry-run` 预演。核心逻辑 `backend/core/multiuser/migrate.py`。

---

## 后端工具模块

### `scholarly_research/` — 多 Provider 学术研究

`models.py` 定义统一的作者、主题、作品和结果模型；`base.py` 定义可插拔数据源接口；`manager.py` 并发调用启用的 Provider，执行去重、排序、详情/关联作品降级和 Function Calling 注册。`providers/` 包含 OpenAlex、Semantic Scholar、Crossref、arXiv、CORE、Unpaywall、PubMed 与 DataCite 适配器；默认只启用 OpenAlex。共享 `httpx.AsyncClient` 在 FastAPI lifespan 中创建和关闭，方法内实时读取超时、结果上限、Provider 开关和 API Key 配置。

检索支持主题、年份、作者、作品类型与排序；各 Provider 把请求映射到自身 API，结果按稳定标识符或题名年份关联。`ranking_mode=legacy` 保留 Provider 顺序兼容排序，`rrf` 启用跨源 RRF、版本族与字段溯源；`full_text_enabled=false` 只返回摘要和开放获取定位，启用后通过结构化页面读取器返回实际获得的章节及缺失状态。灰度和回滚流程见 [网络访问灰度与运维](web-access-operations.md)。

### `file_ops.py` — 文件安全操作与撤销栈

**核心接口**：

| 函数 | 功能 |
| --- | --- |
| `compute_md5(file_path)` | 二进制 MD5 哈希（避免 Windows `\r\n` 差异导致文本模式不一致） |
| `safe_read(file_path)` | UTF-8 读取，含 UnicodeDecodeError 详细报告 |
| `safe_read_lines(file_path)` | 逐行读取，保留换行符 |
| `atomic_write(file_path, content)` | 写临时文件 → flush + fsync → rename 原子覆盖（防止写中断损坏）；文件流接管失败时关闭底层描述符并清理临时文件 |
| `replace_lines_in_place(path, start, end, lines)` | 替换指定行范围后原子写入 |

**BackupManager**：每文件维护 `undo: deque` 和 `redo: deque`，最大 50 层历史。

| 方法 | 功能 |
| --- | --- |
| `push(file_path)` | 当前内容压入 undo，清空 redo，返回 MD5 |
| `verify_md5(file_path, expected_md5)` | 检查文件是否被外部修改 |
| `undo(file_path)` | 当前压入 redo，从 undo 恢复，返回恢复后的内容 |
| `redo(file_path)` | 当前压入 undo，从 redo 恢复，返回恢复后的内容 |
| `can_undo(file_path)` | undo 栈是否非空 |
| `can_redo(file_path)` | redo 栈是否非空 |
| `clear(file_path=None)` | 清空指定文件或所有文件的历史 |

### `agent_file_tools.py` — Agent 私有文件工具共享层

`template_gen`、`plot_gen` 与 `style_modify` 项目 Agent 的文件工具统一复用该模块。共享层负责：

- 构建 `write_file` / `read_file` / `list_files` Function Calling schema；style_modify 通过名称映射继续暴露 `stage_replace` / `read_project_file` / `list_project_files`
- 校验公共参数、分发工具、构造统一的成功/失败事件，并用 `asyncio.to_thread()` 执行同步文件 I/O
- 对支持局部读取的 Agent 解析 `line_range: [start, end]`，按 1-based 闭区间切片并执行输出截断
- 通过 `_write_agent_file()` / `_read_agent_file()` / `_list_agent_files()` 钩子保留各模块自己的后缀白名单、保护路径、内容校验、文件预算与存储目标

共享层不绕过 `resolve_project_path()`，也不改变 style_modify 的 overlay / proposal 事务边界。plot_gen 仍只提供整文件读取；template_gen 与 style_modify 使用 `line_range`。

### `path_guard.py` — 项目路径安全校验

**设计理念**：所有需要根据项目根目录拼接 `file_path` 的调用方**必须**经过此模块的 `resolve_project_path()` 函数，作为防止路径穿越（path traversal）的唯一入口。该函数在 `project_manager`、`input_resolver`、`bib_manager`、`compile`、`git_diff` 等模块中统一使用。

**校验规则（六层防御）**：

1. 不为空 / 不含 null 字节
2. 不是绝对路径（`os.path.isabs()` + `PureWindowsPath` 跨平台判断）
3. 不含 `..` 路径段（快速拦截）
4. 默认拒绝符号链接：先对原始相对路径逐组件检测 `os.path.islink()`，
   再 `resolve()` 后 `is_relative_to(root.resolve())`（精确拦截符号链接逃逸）。
   顺序不能颠倒，否则指向项目外的链接会先命中 “resolves outside”
   而丢失 “symbolic link” 的精确错误
5. `allow_symlink=True` 时返回未 `resolve()` 的链接路径本身（契约），
   但越界链接仍会被 `is_relative_to` 拦截，安全性质不变
6. 可选：扩展名白名单（基于 `resolve()` 后的真实后缀）

**核心接口**：`resolve_project_path(root, file_path, *, allowed_suffixes, allow_symlink)`

**调用方约定**：

| 模块 | 调用点 | allowed_suffixes |
| --- | --- | --- |
| `project_manager` | `get_paragraphs` / `set_main_file` | `(".tex",)` |
| `project_manager` | `apply_change` / `undo` / `redo` / `can_undo` / `can_redo` | `None`（不限） |
| `input_resolver` | `resolve_inputs` 入口 + 递归引用 | `None` |
| `project_tools` | 浏览工具（`list_files` 等） | `None`；`search_across_files` 读取 .tex 时 `(".tex",)` |
| `edit_tools_manager` | `edit_paragraph` / `undo_last_edit` | `(".tex",)` |
| `bib_manager` | `read_bib_file` / `save_bib_entry` | `(".bib",)` |
| `compile` | `_validate_main_file` | `(".tex",)` |
| `git_diff` | `_validate_project_path`（`render_diff_pdf` + `\input` 展开） | `require_tex` 可选 |
| `template_gen` | Agent 沙箱文件读写与编译入口 | 写入和只读诊断分别使用后缀白名单；允许沙箱内符号链接但拒绝越界 |
| `style_modify` | session 文件读写 | 默认 `(".tex", ".sty", ".cls", ".bib")`（来自配置），禁符号链接 |
| `plot_gen` | Agent 沙箱文件读写与执行入口 | 文件工具按写入后缀白名单；专属执行工具另行收窄 |

### `json_utils.py` — LLM JSON 输出校验与重试

**设计理念**：OpenAI 的 `response_format={"type": "json_object"}` 只保证输出是合法 JSON，**不保证字段符合预期 schema**。因此必须在收到回复后执行 Pydantic 校验。`safe_parse_json()` 是整个系统**唯一的 JSON 解析入口**，负责处理 LLM 输出中的各种格式问题（Markdown 围栏、尾逗号、单引号等）。schema 校验拦不住“合法 JSON + 内容损坏”的 LaTeX 字段（JSON 反斜杠转义），需配合 `latex_json_guard.py` 的字段级内容校验。

**核心接口**：

| 函数 | 功能 |
| --- | --- |
| `safe_parse_json(text)` | 容错解析：去除 markdown 围栏、尾逗号、单引号替换（只替换双引号字符串之外的引号，不破坏 `author's` 等字符串内容），支持 `str` 和 `dict` 输入 |
| `validate_model(data, model_cls)` | Pydantic `.model_validate()` 严格校验 |
| `format_validation_error(errors)` | 将 Pydantic ValidationError 格式化为 LLM 可读的中文错误信息 |
| `parse_and_validate(text, model)` | 一步完成：解析 + 校验，返回 `(model, error)` 元组 |
| `retry_with_validation(chat_fn, messages, model, max_retries=3)` | **核心重试循环**：调用 LLM → 解析 JSON → Pydantic 校验 → 失败时反馈错误给 LLM 重试 |

**`retry_with_validation()` 流程**：

```
for attempt in range(max_retries):
    response_text = await chat_fn(messages, **kwargs)   # chat_fn 返回原始 str
    result, error = parse_and_validate(response_text, ModelClass)
    if error is None:
        return result          # ✅ 成功
    else:
        messages += [correction_prompt_with_error_details]  # 🔁 反馈错误重试
raise ValueError("Validation failed after N attempts")        # ❌ 耗尽
```

### `latex_json_guard.py` — LaTeX×JSON 转义防护校验器

**背景**：LaTeX 命令以反斜杠开头，而 JSON 字符串里反斜杠是转义符。LLM 输出经 JSON 传输时，未转义的反斜杠会被 JSON 静默损坏（如 `\rho` → 回车 + "ho"）或直接解析失败（如 `\sigma`）。schema 校验只能保证字段类型正确，拦不住这种“合法 JSON + 损坏内容”。

**职责**：对 LaTeX/BibTeX 文本做内容级校验，把静默损坏变成可重试/可拦截错误。校验项：

- 控制字符（除换行外）—— JSON 反斜杠转义损坏特征
- `[object Object]` 等垃圾占位符
- 花括号配平（忽略 `\{` / `\}`，跳过注释、verbatim、URL 内容）
- `$` 配平（同上跳过规则）
- 可选：`\begin{X}` / `\end{X}` 环境栈式配平

**换行损坏检测边界**：JSON 路径（`relaxed=False`）下，值首部的"换行 + 小写字母"是 `\n` 开头命令（`\nabla` / `\newcommand` 等）被 JSON 转义吞掉反斜杠的损坏特征，guard 会拦截。换行本身是合法 LaTeX 空白，guard 无法仅凭结果完整区分损坏与合法换行，因此只拦最典型特征；完整方案是让原始 LaTeX 脱离 JSON（分隔符协议），多行内容走 `relaxed=True` 宽松校验。

**BibTeX 模式**：`bibtex=True` 时 `%` 视为字面字符（非注释起点），避免 percent-encoding URL（`https://x/y%20z`）被误判为花括号不配平。

**字面内容命令补全**：`_strip_verbatim` 支持逐字类内容（不参与配平/控制字符扫描）——`\verb` / `\verb*`、`\lstinline`（含可选参数与数字定界符）、fancyvrb `\Verb`（内容可跨行）、`\mintinline{lang}{...}`（code 含嵌套花括号）、`verbatim` / `Verbatim` / `lstlisting` / `minted` / `comment` / `alltt` 环境。识别逻辑统一来自 `latex_literals.py`，与 `latex_parser` 共享同一策略。

**URL 字面化**：`\url` / `\path` / `\href` 的 URL 参数在 LaTeX 与 BibTeX 两种模式均按字面剥离（URL 中的 `$`、`{` 是字面字符），括号计数支持 URL 内嵌套花括号；`\href` 的第二个参数（显示文本）保留并正常校验。

**错误归因分诊**：按错误类型给出不同修复提示，不再一律"把反斜杠双写"——

- 控制字符：JSON 转义损坏（提示反斜杠双写）或内容损坏（宽松/轻量模式）
- 垃圾占位符：提示不要将对象序列化为字符串
- 花括号 / `$` 配平：提示检查未配对符号、字面花括号应写 `\{` / `\}`、字面美元符应写 `\$`

**宽松模式（`relaxed=True`）**：用于分隔符协议（`<<<LATEX>>>...<<<END>>>`）等非 JSON 传输路径。反斜杠原样传输、不存在 JSON 转义风险，因此放行 `\t`（U+0009）与 `\r`（U+000D）这类合法 LaTeX 空白，仅拦截 `\b` / `\f` 等真正的静默损坏特征；错误归因也不再提示"把反斜杠双写"。

**轻量模式（`check_latex_text_lite` / `LatexTextLite`）**：用于 marker 模块（typo_check / expression_opt / paragraph_translate）的 LLM 输出字段。这些字段仍走 JSON 传输，但 marker 化后常规段落不再含反斜杠，花括号 / `$` 配平检查可能因残余结构（如 `\~{n}`）误报，因此只做最小校验：

- 控制字符：排除 `\t` / `\n`（合法空白），`\r` 仍拦截（JSON 未转义反斜杠的静默损坏特征）
- `[object Object]` 等垃圾占位符

失败消息按"内容损坏"归因，不提示"把反斜杠双写"；模型字段挂 `LatexTextLite` 后，损坏内容在 `retry_with_validation` 的模型校验阶段即被拦截并回喂 LLM 重试。

**核心接口**：

| 接口 | 功能 |
| --- | --- |
| `check_latex_text(value, *, bibtex=False, relaxed=False)` | 基础内容校验，通过返回 `None`，失败返回错误消息 |
| `check_latex_text_lite(value)` | 轻量内容校验（marker 模块输出字段）：控制字符 + 垃圾占位符，不查配平 |
| `check_latex_environments(value)` | 环境栈式配平校验 |
| `validate_latex_field(value, *, check_environments=False, bibtex=False, relaxed=False)` | Pydantic 校验器回调：失败抛 `ValueError` |
| `validate_latex_field_lite(value)` | Pydantic 校验器回调（轻量）：失败抛 `ValueError` |
| `LatexText` | `Annotated[str, AfterValidator(...)]` 类型别名，字段声明直接复用 |
| `LatexTextLite` | 轻量 `Annotated` 类型别名：marker 模块输出字段（corrected / optimized / translated）复用 |

**接入方式**：

- 模型字段：`LatexText` 等 Pydantic 字段级校验（code_review / bib_manager / formula_ocr / table_ocr）
- marker 模块输出字段：typo_check `corrected`、expression_opt `optimized`、paragraph_translate `translated` 挂 `LatexTextLite` 轻量校验；`base_paragraph_check._check_one_paragraph` 在模型校验之后、marker 计数校验之前再做一次轻量兜底
- 写盘前：template_gen `write_file`、edit_tools `edit_paragraph` 在写盘前调用 `check_latex_text()`，损坏内容直接拒绝并回喂 LLM 自纠
- 分隔符协议路径：writing_chat `generate_edit` 提取 `<<<LATEX>>>...<<<END>>>` 块后调用 `validate_latex_field(relaxed=True)` 宽松校验（避免 CRLF / tab 原文误报）；formula_ocr / table_ocr / style_modify 同样采用分隔符协议 + 内容校验重试（暂用默认严格模式）

### `tex_compile.py` — 统一 TeX 编译执行模块

**设计理念**：将 LaTeX 编译执行（latexmk 调用）和日志过滤（texfot）统一封装，供 `compile`（自动编译）、`git_diff`（差异高亮）、`template_gen`（模板生成）等模块复用。texfot 是 TeX Live 自带的 Perl 工具，能从海量编译日志中自动提取错误、警告等关键信息，比自写正则更可靠。当 texfot 不可用时，静默降级为原始 latexmk 输出 + 正则 fallback。

**核心功能**：

- **引擎自动检测**：根据 `.tex` 文件内容自动选择编译引擎（pdflatex/xelatex/lualatex）
- **PDF/A 归档输出**：在 `\documentclass` 之后注入 `\usepackage[a-2b]{pdfx}` 以支持 PDF/A-2b 归档格式
- **临时目录管理**：编译在临时目录中进行，完成后自动清理并复制 PDF 回原目录

**核心接口**：

| 函数 | 功能 |
| --- | --- |
| `run_tex_compile(main_file, cwd, timeout, engine="auto", pdfa=False)` | 通过 texfot 包装 latexmk 执行编译，返回过滤后的输出 |
| `detect_engine_from_file(tex_path)` | 根据 .tex 文件内容自动检测编译引擎 |
| `inject_pdfx_for_pdfa(tex_path, output_path)` | 注入 PDF/A 支持包 |
| `_check_texfot_available()` | 检测 texfot 是否在 PATH 中可用（模块级缓存） |
| `_filter_with_regex(raw_output)` | 降级路径：用正则从原始日志中提取关键错误行 |

**Pydantic 模型**：`TexCompileResult`（success, filtered_output, raw_output, timed_out, oom_killed, used_texfot）

> v2 P1a 起，`run_tex_compile` 的实际执行统一经 `backend/utils/sandbox/` 的 SandboxRunner：sandbox docker 模式进 tex 镜像容器（texfot 内置），auto 探测失败或 local 模式回落本地子进程（行为不变）；`oom_killed` 标志由容器 OOM 翻译而来，compile 服务据此返回 `error_code=sandbox_killed_oom`。详见[沙箱执行模块](#sandbox-包--docker-沙箱执行模块v2-p1a)。

**调用方**：

| 模块 | 场景 |
| --- | --- |
| `compile/service.py` | `compile_latex()` 调用 |
| `git_diff/service/` | `_compile_diff()` 调用 |
| `template_gen/agent/executor.py` | `_ToolExecutor._compile_latex()` 调用 |
| `style_modify/session.py` | `compile_staged_project()` 调用 |
| `plot_gen/executor.py` | TikZ 编译（`_compile_tikz`）调用 |

### `sandbox/` 包 — Docker 沙箱执行模块（v2 P1a）

**设计理念**：全部外部命令执行（latexmk/texfot 编译、git/latexdiff、pandoc、Python 绘图、latexindent）收口到统一入口，按配置选择 Docker 容器或本地子进程执行，实现多用户环境下的隔离与资源治理。调用方只描述"命令 + 工作目录 + 超时"（ExecSpec），不感知后端差异；结果统一为 ExecResult（returncode/stdout/stderr/timed_out/oom_killed），上层错误管线零改动。

```
backend/utils/sandbox/
├── models.py          # ExecSpec / ExecResult / SandboxMount / SandboxLimits 契约
├── errors.py          # sandbox_unavailable(503) / sandbox_busy(429)：RuntimeError + sandbox_code 属性（遵守"禁止自定义异常类"规范）
├── runner.py          # SandboxRunner 单例：模式解析、daemon 探测（TTL 缓存）、并发租约、生命周期
├── local_backend.py   # 本地后端 = 既有 subprocess 行为封装（进程组建组 + 超时整树击杀），回落路径零行为变化
└── docker_backend.py  # Docker 后端：一次性容器，安全基线由 runner 注入
```

**模式语义**（`config.sandbox.mode`，实时读取支持热重载）：

| mode | 行为 |
| --- | --- |
| `local` | 始终本地子进程（开发机无 Docker 的确定性路径） |
| `auto`（默认） | 探测到 daemon 即容器执行；不可达回落本地并告警 |
| `docker` | daemon 不可达直接抛 `sandbox_unavailable`（503），不静默回落 |

**Docker 容器安全基线**（runner 强制注入）：`--network none`、根文件系统只读 + tmpfs `/tmp`（容量 `tmpfs_mb`）、`--memory`/`--cpus`/`--pids-limit`、`no-new-privileges`、drop ALL capabilities、以宿主后端同 uid:gid 运行（产物属主一致）。挂载使用**同绝对路径**（host_path 即容器内路径），保证 template_gen 等解析 .log 中文件路径的逻辑零改动。超时强杀容器并标记 `timed_out`；退出码 137 或 inspect OOMKilled 标志翻译为 `oom_killed`。

**并发治理**：全局进程内信号量（`max_concurrent_global`，单/多用户均生效）+ 多用户模式每用户 DB 租约（`core/multiuser/sandbox_leases.py`，`sandbox_slots` 表，TTL = 执行超时 + 120s，崩溃残留由维护循环清理；P7 多实例阶段在同一共享卷上跨副本生效）。租约满抛 `sandbox_busy`（429）。

**接入的调用点**：

| 调用点 | 镜像类别 |
| --- | --- |
| `utils/tex_compile.run_tex_compile`（compile / git_diff / template_gen / style_modify / plot_gen 共用） | tex |
| `modules/git_diff/service/process.py::_run_process`（git 子命令 + latexdiff） | tex |
| `modules/format_converter/service.py`（pandoc 文本/文件转换，输入统一走临时文件——契约不含 stdin） | convert |
| `utils/latexindent_utils.format_with_latexindent` | tex |
| `modules/plot_gen/executor._run_script`（LLM 生成的绘图脚本；审计钩子保留为纵深防御） | plot |

**镜像体系**：仓库 `docker/` 目录提供四镜像（backend/tex/convert/plot）Dockerfile 与 `build.sh`（支持 buildx amd64/arm64 双架构），详见 [deployment.md](deployment.md) 与 [docker/README.md](../docker/README.md)。缺 TeX 宏包时改 `docker/tex/Dockerfile` 的 `EXTRA_TL_PACKAGES` 清单重建镜像（运行期不装包，沙箱无网络原则）。

**核心接口**：

| 函数/类 | 功能 |
| --- | --- |
| `get_sandbox_runner()` / `reset_sandbox_runner()` | 进程级单例获取 / 重置（测试） |
| `SandboxRunner.execute(spec, user_id=None)` | 统一执行入口：选后端 → 并发治理 → 结果归一 |
| `initialize_sandbox_runner()` / `close_sandbox_runner()` | lifespan 启动预热探测 / 关闭释放 |
| `cached_backend_for(kind)` | 同步读取最近一次后端解析结果（pandoc/texfot/latexindent 可用性门控："docker" 表示工具由镜像提供，宿主无需安装） |
| `to_http_exception(err)` | 把带 `sandbox_code` 的错误翻译为 HTTPException（503/429），供路由层优先匹配 |
| `current_sandbox_user_id()` | 读取多用户上下文当前用户（每用户租约归属） |

**测试**：契约与租约测试见 `tests/test_utils/test_sandbox_models.py`、`tests/test_utils/test_sandbox_runner.py`、`tests/test_multiuser/test_sandbox_leases.py`；真容器冒烟见 `tests/test_docker/`（marker `docker`，无 daemon 自动 skip）。

### `compile_error_translator.py` — LaTeX 编译错误 LLM 翻译

**设计理念**：将 LaTeX 编译失败日志翻译为中文解释和修复建议的逻辑提取为独立工具，供 `compile`（自动编译）和 `git_diff`（差异高亮）两个模块复用。避免在不同模块中重复实现相同的 LLM 调用、错误提取和重试逻辑。

**核心接口**：`extract_key_errors(log_text, max_chars=3000)` / `translate_compile_errors(stdout, stderr, language="zh", *, filtered_output=None)`

当调用方已通过 `tex_compile` 获取了 texfot 过滤后的输出时，通过 `filtered_output` 参数直接传入，可跳过 `extract_key_errors` 正则过滤。

**Pydantic 模型**：`ErrorTranslation`（explanation, fixes）

**调用方**：

| 模块 | 场景 |
| --- | --- |
| `compile/service.py` | `compile_latex()` 编译失败时调用 |
| `git_diff/service/` | `render_diff_pdf()` 编译失败时调用 |

### `latexindent_utils.py` — latexindent.pl 调用封装

供统一格式化工具 `latex_formatter` 使用（`code_review` / `formula_ocr` / `table_ocr` 共用）。负责检测 `latexindent` 是否可用，并将 LaTeX 源码片段写入临时文件后调用 `latexindent` 进行格式化。默认加载项目内置 `backend/utils/latexindent.yaml`（`defaultIndent` 为每层 4 个空格），覆盖 latexindent 默认的制表符缩进；`config_path` 参数可传入自定义配置覆盖。

调用时通过 `-c` 把备份文件（`*.bak*`）与日志统一写入临时目录，并用 `-g` 指定每次运行独立的日志名；运行结束后整目录清理（Windows 上短暂重试应对句柄延迟释放），避免在项目目录残留文件。v2 P1a 起执行经 SandboxRunner：docker 后端下 latexindent 由 tex 镜像提供（宿主无需安装），配置文件随临时目录挂载进容器。

**核心接口**：

| 函数 | 功能 |
| --- | --- |
| `is_latexindent_available()` | 检测 `latexindent` 是否可用（sandbox docker 后端下恒为 True） |
| `format_with_latexindent(source, file_path=None, config_path=None)` | 异步调用 `latexindent` 格式化 LaTeX 源码 |

### `builtin_formatter.py` — 内置轻量 LaTeX 格式化器

无外部依赖的兜底格式化器，当 `latexindent.pl` 不可用时由 `code_review` 模块使用。

**处理范围**：环境缩进（每层 4 个空格，与 latexindent 路径一致）、去除行尾多余空白、压缩连续空行、一致性换行。

**已知限制**：逐行 `strip()` 会把 `verbatim` / `Verbatim` / `lstlisting` 等逐字环境内容的前导空白规范化，无法保真（逐字内容中空格是字面字符）。该路径仅作 latexindent 不可用时的兜底，需要保真时应确保 latexindent 可用。

**核心接口**：`format_latex(source: str) -> str`

### `latex_formatter.py` — 统一 LaTeX 格式化工具

- 对外提供单一异步入口 `format_latex_source()`：优先调用 `latexindent.pl`（加载项目内置 `latexindent.yaml`，每层 4 个空格缩进），不可用时回退到内置轻量格式化器（同样每层 4 个空格）
- `code_review` / `formula_ocr` / `table_ocr` 共用，避免各模块重复实现格式化调度
- latexindent 可用性探测结果在进程内缓存
- OCR 模块可通过 `features.formula_ocr.format_latex` / `features.table_ocr.format_latex`（默认 `true`）按模块关闭格式化层。

**核心接口**：

| 函数 | 功能 |
| --- | --- |
| `format_latex_source(source)` | 异步统一格式化：latexindent → 内置格式化器兜底（空白源码原样返回，不抛错） |
| `reset_latexindent_cache()` | 重置 latexindent 可用性缓存（测试 / 环境变化后重新探测） |

### `time_utils.py` — 时间工具函数

为 LLM prompt 注入当前日期/时间/星期信息，帮助模型理解相对时间概念。

**核心接口**：`get_current_time_context()` → 返回 `{date, time, weekday}`

### `latex_parser.py` — LaTeX 文本提取与 Marker 保护

**设计理念**：将 LaTeX 源码转为纯文本供 LLM 处理，同时通过 marker（标记符）反向映射策略确保修改后能精确写回源文件。所有使用 Marker 保护的模块（typo_check、expression_opt、terminology_check、paragraph_translate）共享此模块。

**核心接口**：

| 接口 | 功能 |
| --- | --- |
| `MARKER_PATTERN` | 模块级常量：`re.compile(r"__[A-Z]+_\d{4,}__")`，用于从文本中提取 marker。各 service 从此处统一导入，避免重复定义 |
| `LatexParser.strip_to_plain_text(source)` | 将 LaTeX 源码转为带 marker 的纯文本，返回 `(plain_text, marker_map)` |
| `LatexParser.restore_from_markers(processed, marker_map)` | 按创建顺序（counter）降序还原（外层 marker 先还原），非标准 marker 回退为按长度降序，防止短 marker 误匹配长 marker 前缀 |

**Marker 格式**：`__{PREFIX}_{COUNTER:04d}__`（PREFIX: `CMD`/`MATH`/`ENV`/`VERB`/`COM`）

**注释保护策略**：注释替换（`%` → 行尾 → `__COM_NNNN__`）放在内联逐字、URL 参数与逐字类环境（`verbatim`/`Verbatim`/`lstlisting` 等）之后、其他环境/数学/命令之前执行，确保注释内的 `\command{}`、`$math$`、`\begin{env}` 不被单独提取。逐字类环境（`_VERBATIM_ENVS`）在注释之前，因其内部可含字面 `%`；URL 参数（`\url` / `\path` / `\href`）同样在注释之前整体 marker 化，`%20` 不会被误当作注释起点。注释不再是直接删除，而是 marker 化，LLM 无法修改，还原时完整恢复。

**解析流水线**：VERB → URL → VERBATIM_ENVS → COM → ENV → MATH → CMD（9 步顺序执行）

**Marker 匹配规则（v2 修复）**：

- **通用控制符号**：`\` 后跟单个非字母字符（`\?`、`\'`、`\"`、`\7`、`\~`、`\^`、`\`+换行、EOF 处孤立 `\`、未配对的 `\[`/`\(`）整体记为 CMD marker，消除纯文本中的残余反斜杠
- **控制符号 `{arg}` 吸收**：控制符号/转义字符后紧跟 `{...}` 时并入同一 marker（如 `\~{n}` → 单一 marker），不再残留 `{n}` 花括号；裸字母形式（`\~n`）保持现状
- **行内数学与美元符号**：`$...$` 非贪婪匹配（`$x + y$` 带空格也照常匹配）；被反斜杠转义的 `\$`（如 `\$500`）不参与数学匹配，由转义字符规则整体替换为 marker；裸 `$` 不成对属非法 LaTeX（正确写法是 `\$`），不做特殊处理，仅保证往返不变
- **内联逐字扩展**：`\verb`/`\verb*`/`\Verb`/`\Verb*`/`\lstinline`/`\lstinline*`/`\mintinline` 整体记为 VERB marker。支持数字等任意非字母定界符（`\verb1a{b}$1`）、`\lstinline` 可选 `[...]` 参数、花括号形式（`\lstinline{...}`，可含嵌套花括号）与 fancyvrb `\Verb` 跨行内容，逐字内容 LLM 不可见。识别逻辑统一来自 `latex_literals.py`
- **URL 参数保护**：`\url` / `\path` / `\href` 的完整命令（含可选参数与全部花括号参数）在注释替换之前整体记为 CMD marker，避免 URL 中的字面 `%` 被当作注释

**还原策略**：按创建顺序（counter）降序还原（外层 marker 先还原），非标准 marker 回退为按长度降序。

### `latex_literals.py` — LaTeX 字面内容统一识别

**设计理念**：逐字命令、逐字环境与 URL 参数的识别规则此前在 `latex_parser` 与 `latex_json_guard` 中各自维护，规则分叉导致 parser 漏保护数字定界符、`\Verb`、`\lstinline` 可选参数，以及 URL 中的 `%` 被注释规则吞掉。本模块把字面内容识别收敛为单一策略，供三处复用：

- `latex_parser`：字面内容整体替换为 marker（VERB / CMD / ENV）
- `latex_json_guard`：字面内容先剥离再做控制字符 / 配平扫描
- 分隔符协议提取器（formula_ocr / table_ocr / style_modify / writing_chat）：跳过逐字内容中的 `<<<END>>>`，避免正文含协议标记时提前截断

**核心接口**：

| 接口 | 功能 |
| --- | --- |
| `find_literal_spans(text, kinds=None)` | 返回字面区间列表（按下标排序、重叠合并），kind 为 `verb` / `verb_fancy` / `mintinline` / `env` / `url` |
| `blank_literal_spans(text, kinds=None)` | 剔除字面内容，保留其余文本（guard 剥离校验用） |
| `extract_delimited_block(text, open, end)` | 提取分隔符协议块内容，跳过字面内容中的协议标记（大小写不敏感） |
| `remove_delimited_block(text, open, end)` | 移除第一个分隔符协议块，返回剩余文本（表格 OCR 解析 CONFIDENCE 用） |

**识别范围**：`\verb` / `\Verb` / `\lstinline`（分隔符与花括号形式）/ `\mintinline`、逐字环境（`verbatim` / `Verbatim` / `lstlisting` / `minted` / `comment` / `alltt`）、`\url` / `\path` / `\href` 的 URL 参数（括号计数支持嵌套花括号，`\href` 第二参数保留校验）。

### `latex_package_analyzer.py` — LaTeX 宏包分析器

分析 LaTeX 代码使用的命令/环境/列说明符，判断所需宏包。供公式 OCR、表格 OCR、Markdown 转换等模块使用。

**核心接口**：`detect_required_packages(latex_code, domain="general", exclude_loaded=True)` / `analyze_latex_packages(latex_code, llm=None, domain="general")` / `prepend_package_comments(llm, latex_code, domain="general")` / `packages_to_comments(packages)`

**Pydantic 模型**：`LatexPackageAnalysis`（packages）

**检测流程**：主路径为确定性规则表 `latex_package_rules.py`（命令/环境/列说明符 → 宏包映射），
零 LLM 开销、结果可复现；规则表未命中时若传入 `llm`，才走 LLM 兜底（输出经 schema 校验 +
归一化：小写 / 去 `.sty` / 去重 / 排序，再按领域与已加载过滤）。
`domain` 参数（`general` / `math` / `table`）按领域排除无关宏包：公式 OCR 传 `math`
（剔除表格/版式/引用类），表格 OCR 传 `table`，格式转换传 `general`。
`exclude_loaded=True`（默认）会扫描 `\usepackage` / `\RequirePackage`（含可选参数、
逗号分隔多包），把已加载宏包从结果剔除，避免整篇文档重复推荐。

**误报防护**：扫描前屏蔽逐字内容（`\verb` / `\lstlisting` / `\url` 等）与 `%` 注释；
只收录"必须加载该宏包才能编译"的命令/环境，LaTeX 内核能力（`tabular` / `\frac` / `\mathcal` 等）不收录。
LLM 兜底提示词不携带映射表，只要求通用推理并显式排除内核能力
（如 `p{}` 列说明符、`\frac` / `\sum` / `equation` 环境），避免与规则表双源漂移。

**内核白名单守卫**：`KERNEL_COMMANDS` / `KERNEL_ENVS` 记录 LaTeX 内核能力清单，
测试断言规则表与白名单无交集，防止 `\frac` / `\mathcal` / `\hline` / `tabular` 误入。

**语料与验证**：`tests/fixtures/latex_packages/` 存放按来源分组的真实片段
（公式 OCR / 表格 OCR / Pandoc / 对抗样本），`test_latex_package_corpus.py`
逐项比对形成查准率/查全率基线；`tools/latex_packages/verify_rules.py` 在装有 TeX
的机器上离线编译验证（维护者专用，运行时不依赖 TeX）；`tools/latex_packages/coverage_report.py`
对照常用宏包清单输出未覆盖项。新增规则模板与评审清单见
[latex-package-rules.md](./latex-package-rules.md)。

**降级策略**：LLM 兜底或 JSON 校验失败 → 返回空列表，不影响主流程。

### `input_resolver.py` — `\input`/`\include` 递归解析器

**设计理念**：许多 LaTeX 项目使用 `\input{}` / `\include{}` / `\subfile{}` 将文档拆分为多个 `.tex` 文件。段落处理模块默认只分析用户选中的单个文件，开启 `resolve_input` 后，系统会递归展开所有引用的子文件，将它们的段落一并送入 LLM 处理。

**核心接口**：`resolve_inputs(main_file, project_root, pm)`

**关键设计约束**：

- **循环引用检测**：维护 `visited` 集合，已访问的文件不重复展开，防止无限递归
- **相对路径解析**：`\input{chapters/intro}` 相对于被引用文件所在目录解析
- **marker 保护顺序**：`resolve_inputs()` 必须在 `strip_to_plain_text()`（marker 替换）**之前**调用，否则 `\input` 命令会被替换为 marker 导致无法识别
- **段落索引语义**：多文件模式下，`paragraph_index` 为每个文件内的局部索引（从 0 开始），不是全局索引

**调用方模式**（各逐段检查模块的 service 均采用相同模式）：

```python
if resolve_input:
    files = self._get_files(file_path, resolve_input=True)
else:
    files = [file_path]
```

---

## 项目管理路由 `project_router.py`

Prefix: `/api/project`, Tag: `project`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/open` | 打开 LaTeX 项目目录，返回 ProjectInfo |
| GET | `/browse` | 浏览目录下的子文件夹列表 |
| GET | `/file-tree` | 获取文件树 |
| GET | `/paragraphs` | 获取指定文件的段落列表（同时返回 `file_md5`） |
| GET | `/file-md5` | 获取指定文件的当前 MD5（乐观锁版本校验） |
| POST | `/apply-change` | 原地修改段落（乐观锁校验，冲突返回 409） |
| POST | `/set-main-file` | 手动设置项目主文件 |
| POST | `/undo` / `/redo` | 撤销/重做 |
| GET | `/can-undo` / `/can-redo` | 检查撤销/重做状态 |
| GET | `/undo-status` | 一次返回 `can_undo` 与 `can_redo`（前端轮询用，减半请求数） |
| DELETE | `/workspace/{workspace_id}` | 关闭工作区 |
| GET | `/workspaces` | 列出所有活跃工作区 |

所有端点均需携带 `workspace_id` 查询参数。

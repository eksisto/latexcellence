# LateXcellence — 后端核心模块

## 后端核心模块

### `backend/core/config.py` — 配置管理

定义完整的 Pydantic v2 配置模型树，每个 JSON 配置段对应一个 `BaseModel` 子类：

- `ServerConfig` — 服务配置（host, port, dev_mode, vite_dev_url）
- `LLMConfig` — LLM 配置（api_key, base_url, model, temperature 等）
- `TypoCheckConfig` / `ExpressionOptConfig` / `TerminologyCheckConfig` / `FormulaOCRConfig` / `TableOCRConfig` / `TemplateGenConfig` / `CodeReviewConfig` / `CompileConfig` / `GitDiffConfig` / `MarkdownConverterConfig` / `StyleModifyConfig` / `WritingChatConfig` / `BibManagerConfig` — 各功能模块配置
    - `TemplateGenConfig` 包含 `cleanup_enabled`/`cleanup_interval_seconds`/`cleanup_retention_days` 沙箱清理配置
- `FeaturesConfig` — 功能聚合
- `LoggingConfig` — 日志配置
- `AppConfig` — 顶层聚合（server, llm, features, logging）

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

### `backend/core/llm_client.py` — LLM API 客户端

封装 `openai.AsyncOpenAI`，提供统一的调用、重试和限流逻辑。

**职责边界**：`llm_client` 只负责网络通信和重试，**不负责 JSON 解析**。JSON 解析与 schema 校验统一由 `json_utils.py` 的 `safe_parse_json()` 和 `retry_with_validation()` 负责。

**核心接口**：

| 方法 | 功能 |
| --- | --- |
| `client` (property) | 懒加载获取 `AsyncOpenAI` 实例，使用 `threading.Lock` 双检锁保护并发创建（支持 `asyncio.to_thread()` 多线程场景） |
| `reset()` | 重置缓存的 `AsyncOpenAI` 实例，下次访问 `client` 时重新读取配置创建 |
| `chat()` | 非流式对话：调用 `chat.completions.create`，返回原始 `str`，由调用方负责 JSON 解析 |
| `chat_stream()` | 流式对话：返回 `AsyncIterator[str]`，按 token 产出，用于 SSE 推送 |
| `chat_stream_with_usage()` | 流式对话 + 末尾携带 UsageInfo：返回 `AsyncIterator[StreamChunk]`，`StreamChunk.content` 为文本片段、`StreamChunk.usage` 为末次 LLM token 用量（用 `stream_options={"include_usage": True}` 透传，第三方不支持时降级为 `None`） |
| `_rate_limit()` | 内部限流器：确保两次请求间隔 ≥ `config.llm.request_interval` |

**数据类**：

```python
@dataclass class UsageInfo: prompt_tokens, completion_tokens, total_tokens
@dataclass class StreamChunk: content: Optional[str], usage: Optional[UsageInfo]
```

**重试策略**（`chat()` 内部）：
- 最多重试 3 次
- 指数退避：第 N 次重试前等待 `2^(N-1)` 秒（1s → 2s → 4s）
- 仅网络/API 异常触发重试（`json.JSONDecodeError` 不再由客户端处理）

**单例模式**：全局 `_llm_client` 通过 `get_llm_client()` 访问，使用 `threading.Lock` 双检锁保护多线程场景。`client` 属性首次访问时读取 `get_config().llm` 创建实例并缓存；当用户通过 SettingsDialog 更新 `llm` 配置时，`config.update_config_from_dict()` 会调用 `get_llm_client().reset()` 清除缓存，使下一次访问 `client` 时按新配置重新创建，从而支持运行时热重载 `api_key` / `base_url`。

### `backend/core/logger.py` — 日志系统

- `TimedRotatingFileHandler`，每天午夜旋转，保留 30 天
- 文件日志 `DEBUG`，控制台由 `config.logging.level` 控制
- 静默第三方 HTTP 库噪声日志

**核心接口**：`get_logger(name)` / `setup_logging(level, log_dir)`

### `backend/core/project_manager.py` — LaTeX 项目管理

管理 LaTeX 项目目录的生命周期：打开、文件树、段落提取、原地修改、撤销/重做。

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
| `get_paragraphs(file_path)` | 按空行分割文件为段落，记录行号范围，缓存 MD5 |
| `apply_change(file_path, idx, text)` | 原地修改段落：MD5 校验 → 备份 → 替换行范围 → 原子写入 |
| `set_main_file(file_path)` | 手动设置主文档路径 |
| `undo(file_path)` / `redo(file_path)` | 撤销 / 重做（委托给 `BackupManager`） |
| `can_undo(file_path)` / `can_redo(file_path)` | 检查撤销/重做栈是否有记录 |

**主文档检测逻辑**（递归深度限制 2 层）：
1. 精确匹配文件名为 `main.tex`（不区分大小写） → 使用
2. 文件名包含 `"main"` 子串的 `.tex` 文件（如 `main-chapter1.tex`） → 使用
3. 若仍未找到，取文件树中第一个 `.tex` 文件（仅顶层，不递归）
4. 若无任何 `.tex` 文件，返回 `None`

**依赖图构建**：扫描所有 `.tex` 文件中 `\include{`、`\input{`、`\subfile{` 引用关系。

### `backend/core/base_paragraph_check.py` — 逐段检查服务基类

四个逐段检查模块（typo_check、expression_opt、code_review、terminology_check）共享的基类，封装文件解析、marker 替换、LLM 编排（批量+流式）、结果写回等流程。

**核心接口**：

| 方法/属性 | 功能 |
| --- | --- |
| `BaseParagraphCheckService` | 抽象基类，定义 6 个 hooks 供子类实现 |
| `_get_system_prompt()` | 返回 System Prompt（含 marker 保护约束） |
| `_build_user_content(text)` | 构建发给 LLM 的 User 内容 |
| `_get_model_cls()` | 返回结果 Pydantic 模型类 |
| `_get_llm_params()` | 返回 LLM 调用参数（temperature 等） |
| `_get_batch_size()` | 返回批处理大小（从配置读取） |
| `_build_success_result(idx, validated, original_text, file_path, plain_text)` | 构建成功结果对象 |
| `preview_paragraphs(file_path, resolve_input)` | 预览段落：提取 + marker 替换 → 返回纯文本 |
| `_run_batch_check()` / `_run_stream_check()` | 执行检查：批量发送给 LLM 或 SSE 流式返回 + 校验 |
| `apply_changes(file_path, changes, workspace_id, operation_id)` | 应用修改：还原 marker → MD5 校验 → 原子写入；marker_map 缺失时跳过该段并返回 `errors` |

**关键设计**：
- `ApplyChangeItem` 共享模型（`paragraph_index`, `new_text`, `file_path`）：四个模块 router 的 apply 接口统一使用
- `CheckOperationContext` 操作上下文：每次 preview/check 生成唯一 `operation_id`，深拷贝 `marker_maps` 存入 `_operation_contexts` 字典；apply 时通过 `operation_id` 取回上下文还原 marker。若 context 丢失（过期/重启/新操作覆盖），逐段检查 `marker_map` 缺失时跳过写入并返回 `errors`，防止字面 marker 污染文件
- 类变量控制差异：
  - `_output_field`: 结果字段名（`"corrected"` / `"optimized"`）
  - `use_marker_protection`: 是否启用 marker 保护（code_review / typo_check / expression_opt / terminology_check 均为 `True`）
- **上下文 TTL 清理**：默认 1 小时后自动过期

### `backend/core/prompts.py` — 中央提示词管理

集中存储所有 LLM 提示词模板，按模块和功能分类。提供 `get_prompt(module, key, **kwargs)` 函数获取提示词，支持参数化模板（如 `language` 输出语言注入）。各模块通过 `from backend.core.prompts import get_prompt` 统一导入使用，便于统一维护、版本控制和快速调整。

### `backend/core/tool_registry.py` — Function Calling 工具注册表

统一管理所有可暴露给 LLM 的工具 schema 和执行函数。负责注册工具、校验参数、分发调用。当前主要承载 `web_search` 工具，供 Writing Chat 模块在流式对话中按需调用。

**核心接口**：`register_tool(name, schema, fn)` / `get_tool_schema(name)` / `execute(name, arguments)` / `list_tools()`

### `backend/core/workspace_manager.py` — 多工作区管理器

支持多浏览器同时处理不同 LaTeX 项目。

**核心接口**：`get_workspace_manager()` / `get_project_manager_for_workspace(workspace_id)` / `WorkspaceManager.remove()` / `WorkspaceManager.list_workspaces()`

**workspace_id** 通过查询参数传递，前端在 localStorage 生成唯一 ID。

---

## 后端工具模块

### `file_ops.py` — 文件安全操作与撤销栈

**核心接口**：

| 函数 | 功能 |
| --- | --- |
| `compute_md5(file_path)` | 二进制 MD5 哈希（避免 Windows `\r\n` 差异导致文本模式不一致） |
| `safe_read(file_path)` | UTF-8 读取，含 UnicodeDecodeError 详细报告 |
| `safe_read_lines(file_path)` | 逐行读取，保留换行符 |
| `atomic_write(file_path, content)` | 写临时文件 → flush → rename 原子覆盖（防止写中断损坏） |
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

### `path_guard.py` — 项目路径安全校验

**设计理念**：所有需要根据项目根目录拼接 `file_path` 的调用方**必须**经过此模块的 `resolve_project_path()` 函数，作为防止路径穿越（path traversal）的唯一入口。该函数在 `project_manager`、`input_resolver`、`bib_manager`、`compile`、`git_diff` 等模块中统一使用。

**校验规则（六层防御）**：
1. 不为空 / 不含 null 字节
2. 不是绝对路径（`os.path.isabs()`）
3. 不含 `..` 路径段（快速拦截）
4. `resolve()` 后 `is_relative_to(root.resolve())`（精确拦截符号链接逃逸）
5. 可选：拒绝符号链接（遍历路径组件检测 `os.path.islink()`）
6. 可选：扩展名白名单

**核心接口**：`resolve_project_path(root, file_path, *, allowed_suffixes, allow_symlink)`

**调用方约定**：

| 模块 | 调用点 | allowed_suffixes |
| --- | --- | --- |
| `project_manager` | `get_paragraphs` / `set_main_file` | `(".tex",)` |
| `project_manager` | `apply_change` / `undo` / `redo` / `can_undo` / `can_redo` | `None`（不限） |
| `input_resolver` | `resolve_inputs` 入口 + 递归引用 | `None` |
| `bib_manager` | `read_bib_file` / `save_bib_entry` | `(".bib",)` |
| `compile` | `_validate_main_file` | `(".tex",)` |
| `git_diff` | `_validate_project_path`（`render_diff_pdf` + `\input` 展开） | `require_tex` 可选 |

### `json_utils.py` — LLM JSON 输出校验与重试

**设计理念**：OpenAI 的 `response_format={"type": "json_object"}` 只保证输出是合法 JSON，**不保证字段符合预期 schema**。因此必须在收到回复后执行 Pydantic 校验。`safe_parse_json()` 是整个系统**唯一的 JSON 解析入口**，负责处理 LLM 输出中的各种格式问题（Markdown 围栏、尾逗号、单引号等）。

**核心接口**：

| 函数 | 功能 |
| --- | --- |
| `safe_parse_json(text)` | 容错解析：去除 markdown 围栏、尾逗号、单引号替换，支持 `str` 和 `dict` 输入 |
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

### `compile_error_translator.py` — LaTeX 编译错误 LLM 翻译

**设计理念**：将 LaTeX 编译失败日志翻译为中文解释和修复建议的逻辑提取为独立工具，供 `compile`（自动编译）和 `git_diff`（差异高亮）两个模块复用。避免在不同模块中重复实现相同的 LLM 调用、错误提取和重试逻辑。

**核心接口**：`extract_key_errors(log_text, max_chars=3000)` / `translate_compile_errors(stdout, stderr, language="zh")`

**Pydantic 模型**：`ErrorTranslation`（explanation, fixes）

**调用方**：

| 模块 | 场景 |
| --- | --- |
| `compile/service.py` | `compile_latex()` 编译失败时调用 |
| `git_diff/service.py` | `render_diff_pdf()` 编译失败时调用 |

### `latexindent_utils.py` — latexindent.pl 调用封装

供 `code_review` 模块使用。负责检测系统是否安装 `latexindent`，并将 LaTeX 源码片段写入临时文件后调用 `latexindent` 进行格式化。

**核心接口**：

| 函数 | 功能 |
| --- | --- |
| `is_latexindent_available()` | 检测 `latexindent` 是否在 PATH 中可用 |
| `format_with_latexindent(source, file_path=None, config_path=None)` | 异步调用 `latexindent` 格式化 LaTeX 源码 |

### `builtin_formatter.py` — 内置轻量 LaTeX 格式化器

无外部依赖的兜底格式化器，当 `latexindent.pl` 不可用时由 `code_review` 模块使用。

**处理范围**：环境缩进、去除行尾多余空白、压缩连续空行、一致性换行。

**核心接口**：`format_latex(source: str) -> str`

### `time_utils.py` — 时间工具函数

为 LLM prompt 注入当前日期/时间/星期信息，帮助模型理解相对时间概念。

**核心接口**：`get_current_time_context()` → 返回 `{date, time, weekday}`

### `latex_parser.py` — LaTeX 文本提取与 Marker 保护

**设计理念**：将 LaTeX 源码转为纯文本供 LLM 处理，同时通过 marker（标记符）反向映射策略确保修改后能精确写回源文件。所有使用 Marker 保护的模块（typo_check、expression_opt、terminology_check）共享此模块。

**核心接口**：

| 接口 | 功能 |
| --- | --- |
| `MARKER_PATTERN` | 模块级常量：`re.compile(r"__[A-Z]+_\d{4}__")`，用于从文本中提取 marker。各 service 从此处统一导入，避免重复定义 |
| `LatexParser.strip_to_plain_text(source)` | 将 LaTeX 源码转为带 marker 的纯文本，返回 `(plain_text, marker_map)` |
| `LatexParser.restore_from_markers(processed, marker_map)` | 按 marker 长度降序还原，防止短 marker 误匹配长 marker 前缀 |

**Marker 格式**：`__{PREFIX}_{COUNTER:04d}__`（PREFIX: `CMD`/`MATH`/`ENV`/`VERB`/`COM`）

**注释保护策略**：注释替换（`%` → 行尾 → `__COM_NNNN__`）放在逐字类环境（`verbatim`/`lstlisting`）之后、其他环境/数学/命令之前执行，确保注释内的 `\command{}`、`$math$`、`\begin{env}` 不被单独提取。逐字类环境（`_VERBATIM_ENVS`）在注释之前，因其内部可含字面 `%`。注释不再是直接删除，而是 marker 化，LLM 无法修改，还原时完整恢复。

**解析流水线**：VERB → VERBATIM_ENVS → COM → ENV → MATH → CMD（8 步顺序执行）

**还原策略**：按 marker 长度降序还原。

### `latex_package_analyzer.py` — LaTeX 宏包分析器

分析 LaTeX 代码使用的命令/环境，判断所需宏包。供公式 OCR、表格 OCR、Markdown 转换等模块使用。

**核心接口**：`analyze_latex_packages(latex_code, llm)` / `prepend_package_comments(llm, latex_code)` / `packages_to_comments(packages)`

**Pydantic 模型**：`LatexPackageAnalysis`（packages）

**降级策略**：LLM 或 JSON 校验失败 → 返回空列表，不影响主流程。

### `input_resolver.py` — `\input`/`\include` 递归解析器

**设计理念**：许多 LaTeX 项目使用 `\input{}` / `\include{}` / `\subfile{}` 将文档拆分为多个 `.tex` 文件。段落处理模块默认只分析用户选中的单个文件，开启 `resolve_input` 后，系统会递归展开所有引用的子文件，将它们的段落一并送入 LLM 处理。

**核心接口**：`resolve_inputs(main_file, project_root, pm)`

**关键设计约束**：
- **循环引用检测**：维护 `visited` 集合，已访问的文件不重复展开，防止无限递归
- **相对路径解析**：`\input{chapters/intro}` 相对于被引用文件所在目录解析
- **marker 保护顺序**：`resolve_inputs()` 必须在 `strip_to_plain_text()`（marker 替换）**之前**调用，否则 `\input` 命令会被替换为 marker 导致无法识别
- **段落索引语义**：多文件模式下，`paragraph_index` 为每个文件内的局部索引（从 0 开始），不是全局索引

**调用方模式**（四个模块的 service 均采用相同模式）：

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
| GET | `/paragraphs` | 获取指定文件的段落列表 |
| POST | `/apply-change` | 原地修改段落（MD5 校验） |
| POST | `/set-main-file` | 手动设置项目主文件 |
| POST | `/undo` / `/redo` | 撤销/重做 |
| GET | `/can-undo` / `/can-redo` | 检查撤销/重做状态 |
| DELETE | `/workspace/{workspace_id}` | 关闭工作区 |
| GET | `/workspaces` | 列出所有活跃工作区 |

所有端点均需携带 `workspace_id` 查询参数。

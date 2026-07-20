# LateXcellence — 后端核心模块

## 后端核心模块

### `backend/core/config.py` — 配置管理

定义完整的 Pydantic v2 配置模型树，每个 JSON 配置段对应一个 `BaseModel` 子类：

```python
ServerConfig → host, port, dev_mode, vite_dev_url
LLMConfig    → api_key, base_url, model, temperature, max_tokens, request_interval, max_concurrency
TypoCheckConfig → enabled, batch_size
ExpressionOptConfig → enabled, batch_size, default_prompt, custom_prompts_dir
TerminologyCheckConfig → enabled, batch_size
FormulaOCRConfig → enabled, max_image_size_mb
TableOCRConfig → enabled, max_image_size_mb
TemplateGenConfig → enabled, output_dir
FormattingConfig → enabled, batch_size
CompileConfig → enabled, latexmk_timeout
GitDiffConfig → enabled
MarkdownConverterConfig → enabled
StyleModifyConfig → enabled
WritingChatConfig → enabled, compression_threshold_tokens, sessions_dir, custom_prompts_dir, summary_temperature, summary_max_tokens
BibManagerConfig → enabled, format_configs_dir
FeaturesConfig → typo_check, expression_opt, terminology_check, formula_ocr, table_ocr, template_gen, formatting, compile, git_diff, markdown_converter, style_modify, writing_chat, bib_manager
LoggingConfig → level, dir
AppConfig    → server, llm, features, logging  # 顶层聚合
```

**核心接口**：

| 函数 | 功能 |
|---|---|
| `load_config(path)` | 加载 JSON + 环境变量覆盖 `OPENAI_API_KEY` + Pydantic 校验 |
| `get_config()` | 获取缓存的 AppConfig 单例（**禁止在 `__init__` 中缓存**） |
| `save_config()` | 将当前配置 `model_dump()` 写回磁盘；**环境变量来源的 `api_key` 会自动清空，不会落盘** |
| `update_config_from_dict(updates)` | 用字典部分更新配置（前端 SettingsDialog 使用）；用户手动输入新 Key 时更新内存并允许持久化 |

**安全策略**：
- 环境变量 `OPENAI_API_KEY` 优先级高于 `config.json` 中的 `api_key`
- 从环境变量注入的 API Key **禁止写回磁盘**：`save_config()` 内部通过 `_api_key_from_env` 标记自动识别 Key 来源，落盘前清空 `api_key` 字段
- 用户通过 UI 手动输入的新 Key 解除环境变量标记，正常持久化到磁盘
- 前端提交的 `api_key` 若以 `***` 开头（掩码），保留原值不变

### `backend/core/llm_client.py` — LLM API 客户端

封装 `openai.AsyncOpenAI`，提供统一的调用、重试和限流逻辑。所有模块（错字查找、表达优化、公式 OCR 等）都通过此模块调用 LLM。

**职责边界**：`llm_client` 只负责网络通信和重试，**不负责 JSON 解析**。JSON 解析与 schema 校验统一由 `json_utils.py` 的 `safe_parse_json()` 和 `retry_with_validation()` 负责。

**核心接口**：

| 方法 | 功能 |
|---|---|
| `client` (property) | 懒加载获取 `AsyncOpenAI` 实例，使用 `threading.Lock` 双检锁保护并发创建（支持 `asyncio.to_thread()` 多线程场景） |
| `chat()` | 非流式对话：调用 `chat.completions.create`，返回原始 `str`，由调用方负责 JSON 解析 |
| `chat_stream()` | 流式对话：返回 `AsyncIterator[str]`，逐 token 产出，用于 SSE 推送 |
| `chat_stream_with_usage()` | 流式对话 + 末尾携带 UsageInfo：返回 `AsyncIterator[StreamChunk]`，`StreamChunk.content` 为文本片段、`StreamChunk.usage` 为末次 LLM token 用量（用 `stream_options={"include_usage": True}` 透传，第三方不支持时降级为 None） |
| `_rate_limit()` | 内部限流器：确保两次请求间隔 ≥ `config.llm.request_interval` |

新增数据类：

```python
@dataclass class UsageInfo: prompt_tokens, completion_tokens, total_tokens
@dataclass class StreamChunk: content: Optional[str], usage: Optional[UsageInfo]
```

**重试策略**（`chat()` 内部）：
- 最多重试 3 次
- 指数退避：第 N 次重试前等待 `2^(N-1)` 秒（1s → 2s → 4s）
- 仅网络/API 异常触发重试（`json.JSONDecodeError` 不再由客户端处理）

**单例模式**：全局 `_llm_client` 通过 `get_llm_client()` 访问，使用 `threading.Lock` 双检锁保护多线程场景。

**初始化策略**：`AsyncOpenAI` 采用懒加载 + `threading.Lock` 双检锁。锁保护并发创建；懒加载确保每次创建时实时读取 `get_config().llm`，支持运行时热重载 `api_key` / `base_url`。

### `backend/core/logger.py` — 日志系统

- 使用 `logging.TimedRotatingFileHandler`，**每天午夜**旋转日志文件
- 保留最近 **30 天**的日志
- 文件日志级别：`DEBUG`（记录所有细节）
- 控制台日志级别：由 `config.logging.level` 控制（开发 `DEBUG`，生产 `INFO`）
- 静默第三方 HTTP 库（`httpx`, `openai`, `httpcore`）的噪声日志

**核心接口**：
- `get_logger(name)` → 返回命名 logger
- `setup_logging(level, log_dir)` → 初始化日志系统（在 `main.py` 启动时调用）

### `backend/core/project_manager.py` — LaTeX 项目管理

管理 LaTeX 项目目录的生命周期：打开、文件树、段落提取、原地修改、撤销/重做。

**数据结构**：

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
|---|---|
| `open(path)` | 打开项目目录，扫描文件树，检测主文档，构建依赖图 |
| `get_file_tree()` | 返回文件树（仅展示 `.tex`/`.bib`/`.cls`/`.sty`/`.pdf` 文件） |
| `get_paragraphs(file_path)` | 按空行分割文件为段落，记录行号范围，缓存 MD5 |
| `apply_change(file_path, idx, text)` | 原地修改段落：MD5 校验 → 备份 → 替换行范围 → 原子写入 |
| `set_main_file(file_path)` | 手动设置主文档路径 |
| `undo(file_path)` / `redo(file_path)` | 撤销 / 重做（委托给 `BackupManager`） |
| `can_undo(file_path)` / `can_redo(file_path)` | 检查撤销/重做栈是否有记录 |

**主文档检测逻辑**（递归深度限制为 2 层）：
1. 精确匹配文件名为 `main.tex`（不区分大小写） → 使用它
2. 文件名包含 `"main"` 子串的 `.tex` 文件（如 `main-chapter1.tex`） → 使用它
3. 若仍未找到 → 取文件树中第一个 `.tex` 文件（仅顶层，不递归）
4. 若无任何 `.tex` 文件 → 返回 `None`

**依赖图构建**：扫描所有 `.tex` 文件中 `\include{`、`\input{`、`\subfile{` 引用关系。

---

### `backend/core/base_paragraph_check.py` — 逐段检查服务基类

四个逐段检查模块（typo_check、expression_opt、formatting、terminology_check）共享的基类，封装了通用的段落检查流程。

**核心接口**：

| 方法/属性 | 功能 |
|---|---|
| `BaseParagraphCheckService` | 抽象基类，定义 6 个 hooks 供子类实现 |
| `_get_system_prompt()` | 返回 System Prompt（含 marker 保护约束） |
| `_build_user_content(text)` | 构建发给 LLM 的 User 内容 |
| `_get_model_cls()` | 返回结果 Pydantic 模型类 |
| `_get_llm_params()` | 返回 LLM 调用参数（temperature 等） |
| `_get_batch_size()` | 返回批处理大小（从配置读取） |
| `_build_success_result(idx, validated, original_text, file_path, plain_text)` | 构建成功结果对象 |
| `preview_paragraphs(file_path, resolve_input)` | 预览段落：提取 → marker 替换 → 返回纯文本 |
| `_run_batch_check()` / `_run_stream_check()` | 执行检查：批量发送给 LLM → SSE 流式返回 → 校验 |
| `apply_changes(file_path, changes, workspace_id, operation_id)` | 应用修改：还原 marker → MD5 校验 → 原子写入；marker_map 缺失时跳过该段并返回 `errors` |

**关键设计**：

- **ApplyChangeItem 共享模型**（`paragraph_index`, `new_text`, `file_path`）：四个模块 router 层 apply 接口统一使用
- **CheckOperationContext 操作上下文**：每次 preview/check 生成唯一 `operation_id`，深拷贝 `marker_maps` 存入 `_operation_contexts` 字典；apply 时通过 `operation_id` 取回上下文还原 marker。若 context 丢失（过期/重启/新操作覆盖），逐段检测 `marker_map` 缺失时跳过写入并返回 `errors`，防止字面 marker 污染文件
- **类变量控制差异**：
  - `_output_field`: 结果字段名（`"corrected"` 或 `"optimized"`）
  - `use_marker_protection`: 是否启用 marker 保护（formatting 为 `False`）
- **上下文 TTL 清理**：默认 1 小时后自动过期

---

### `backend/core/workspace_manager.py` — 多工作区管理器

支持多浏览器同时处理不同 LaTeX 项目，每个工作区拥有独立的 `LatexProject` 实例。

**核心接口**：

| 函数/方法 | 功能 |
|---|---|
| `get_workspace_manager()`（模块级函数） | 获取全局单例 |
| `get_project_manager_for_workspace(workspace_id)`（模块级函数） | 获取指定工作区的 `LatexProject` 实例（不存在则创建） |
| `WorkspaceManager.remove(workspace_id)` | 移除工作区，释放资源 |
| `WorkspaceManager.list_workspaces()` | 列出所有活跃工作区 |

**workspace_id 传递**：前端在 localStorage 生成唯一 ID，所有 API 端点通过 `?workspace_id=xxx` 查询参数传递。

---

## 后端工具模块

### `file_ops.py` — 文件安全操作与撤销栈

**核心接口**：

| 函数 | 功能 |
|---|---|
| `compute_md5(file_path)` | 二进制 MD5 哈希（避免 Windows `\r\n` 差异导致文本模式不一致） |
| `safe_read(file_path)` | UTF-8 读取，含 UnicodeDecodeError 详细报告 |
| `safe_read_lines(file_path)` | 逐行读取，保留换行符 |
| `atomic_write(file_path, content)` | 写临时文件 → flush → rename 原子覆盖（防止写中断损坏） |
| `replace_lines_in_place(path, start, end, lines)` | 替换指定行范围后原子写入 |

**BackupManager 类**：

每文件维护 `undo: deque` 和 `redo: deque`，最大 50 层历史。

| 方法 | 功能 |
|---|---|
| `push(file_path)` | 当前内容入 undo 栈 → 清空 redo 栈 → 返回 MD5 |
| `verify_md5(file_path, expected_md5)` | 检查文件是否被外部修改 |
| `undo(file_path)` | 当前入 redo → 从 undo 恢复 → 返回恢复后的内容 |
| `redo(file_path)` | 当前入 undo → 从 redo 恢复 → 返回恢复后的内容 |
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
5. 可选：拒绝符号链接（遍历路径组件检查 `os.path.islink()`）
6. 可选：扩展名白名单

**核心接口**：

| 函数 | 功能 |
|---|---|
| `resolve_project_path(root, file_path, *, allowed_suffixes, allow_symlink)` | 唯一路径解析入口：校验 + resolve → 返回安全的绝对路径 |

**调用方约定**：

| 模块 | 调用处 | allowed_suffixes |
|---|---|---|
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
|---|---|
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
    # parse_and_validate 内部：safe_parse_json(str) → dict → Pydantic 校验
    if error is None:
        return result          # ✅ 成功
    else:
        messages += [correction_prompt_with_error_details]  # 🔁 反馈错误重试
raise ValueError("Validation failed after N attempts")        # ❌ 耗尽
```

### `latex_parser.py` — LaTeX 文本提取与 Marker 保护

**设计理念**：将 LaTeX 源码转为纯文本供 LLM 处理，同时通过 marker（标记符）+ 反向映射策略确保修改后能精确写回源文件。所有使用 Marker 保护的模块（typo_check、expression_opt、terminology_check）共享此模块。

**核心接口**：

| 接口 | 功能 |
|---|---|
| `MARKER_PATTERN` | 模块级常量：`re.compile(r"__[A-Z]+_\d{4}__")`，用于从文本中提取 marker。各 service 从此处统一导入，避免重复定义 |
| `LatexParser.strip_to_plain_text(source)` | 将 LaTeX 源码转为带 marker 的纯文本，返回 `(plain_text, marker_map)` |
| `LatexParser.restore_from_markers(processed, marker_map)` | 按 marker 长度降序还原，防止短 marker 误匹配长 marker 前缀 |

**Marker 格式**：`__{PREFIX}_{COUNTER:04d}__`（PREFIX: `CMD`/`MATH`/`ENV`/`VERB`/`COM`）

**注释保护策略**：注释替换（`%` →行尾 → `__COM_NNNN__`）放在 verb、protected envs、数学公式、命令等所有保护步骤之后执行，避免 `\verb|100%|` 或 `verbatim` 中的 `%` 被误处理。注释不再是直接删除，而是 marker 化，LLM 无法修改，还原时完整恢复。`restore_from_markers()` 也支持对 `__COM_NNNN__` marker 的还原。

详细解析流水线、受保护环境列表、命令解析策略参见 [DESIGN.md §2](../DESIGN.md#23-解析流水线)。

### `latex_package_analyzer.py` — LaTeX 宏包分析器

**设计理念**：独立于具体业务模块的通用组件，分析 LaTeX 代码所使用的命令/环境，判断需要哪些额外宏包支持。目前被表格 OCR 模块在第二次 LLM 调用中使用，其他需要自动检测宏包依赖的场景也可复用。

**核心接口**：

| 函数 | 功能 |
|---|---|
| `analyze_latex_packages(latex_code, llm)` | 异步调用 LLM 分析 LaTeX 代码，返回宏包名称列表（`list[str]`）。LLM 调用失败时优雅降级，返回空列表 |
| `packages_to_comments(packages)` | 将宏包名称列表转换为 LaTeX 注释格式，每行形如 `% \usepackage{booktabs}` |

**Pydantic 模型**：

```python
class LatexPackageAnalysis(BaseModel):
    packages: list[str]  # 空列表表示不需要特定宏包
```

**降级策略**：LLM 调用或 JSON 校验失败 → 记录 `warning` 日志 → 返回空列表，不影响主流程。

### `input_resolver.py` — \input/\include 递归解析器

**设计理念**：许多 LaTeX 项目使用 `\input{}` / `\include{}` / `\subfile{}` 将文档拆分为多个 `.tex` 文件。段落处理模块默认只分析用户选中的单个文件，开启 `resolve_input` 后，系统会递归展开所有引用的子文件，将它们的段落一并送入 LLM 处理。

**核心接口**：

| 函数 | 功能 |
|---|---|
| `resolve_inputs(main_file, project_root, pm)` | 递归解析 `\input` / `\include` / `\subfile`，返回有序文件列表（含主文件） |
| `_strip_line_comment(line)` | 在匹配 `\input` 前先去除行尾 `%` 注释，避免注释中的 `\input` 被误识别（私有辅助函数） |

**关键设计约束**：

- **循环引用检测**：维护 `visited` 集合，已访问的文件不重复展开，防止无限递归
- **相对路径解析**：`\input{chapters/intro}` 相对于被引用文件所在目录解析
- **marker 保护顺序**：`resolve_inputs()` 必须在 `strip_to_plain_text()`（marker 替换）**之前**调用，否则 `\input` 命令会被替换为 marker 导致无法识别
- **段落索引语义**：多文件模式下，`paragraph_index` 为每个文件内的局部索引（从 0 开始），不是全局索引

**调用方**（4 个模块的 service 均采用相同模式）：

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
|---|---|---|
| `POST` | `/open` | 打开 LaTeX 项目目录，返回 ProjectInfo |
| `GET` | `/browse` | 浏览目录下的子文件夹列表（供前端文件夹选择器使用） |
| `GET` | `/file-tree` | 获取文件树 |
| `GET` | `/paragraphs` | 获取指定文件的段落列表（含行号） |
| `POST` | `/apply-change` | 原地修改段落（含 MD5 校验） |
| `POST` | `/set-main-file` | 手动设置项目主文件 |
| `POST` | `/undo` | 撤销上一次修改 |
| `POST` | `/redo` | 重做上一次撤销 |
| `GET` | `/can-undo` | 检查能否撤销 |
| `GET` | `/can-redo` | 检查能否重做 |
| `DELETE` | `/workspace/{workspace_id}` | 关闭工作区，释放资源 |
| `GET` | `/workspaces` | 列出所有活跃工作区 |

所有端点均需携带 `workspace_id` 查询参数。

# LateXcellence — 功能模块文档

本目录包含各个功能模块的详细文档。

## 模块列表

### LLM 文本处理模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 错字查找 | [typo_check.md](modules/typo_check.md) | LaTeX 源码的错字检查与修正 |
| 表达优化 | [expression_opt.md](modules/expression_opt.md) | 学术语言表达润色 |
| 术语一致性检查 | [terminology_check.md](modules/terminology_check.md) | 术语一致性检查 |
| 逐段翻译 | [paragraph_translate.md](modules/paragraph_translate.md) | 按段落翻译为目标语言，保留 LaTeX 结构 |
| LaTeX 代码审查 | [code_review.md](modules/code_review.md) | 工具格式化 + LLM 智能审查 |
| LaTeX 样式修改 | [style_modify.md](modules/style_modify.md) | 自然语言描述修改样式 |
| 写作助手 | [writing_chat.md](modules/writing_chat.md) | 多轮对话式写作辅助 |

### OCR 识别模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 公式识别 | [formula_ocr.md](modules/formula_ocr.md) | 图片转 LaTeX 公式 |
| 表格识别 | [table_ocr.md](modules/table_ocr.md) | 图片表格转 LaTeX |

### 文档生成模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 模板生成 | [template_gen.md](modules/template_gen.md) | 自主 Agent 规划并编译验证，生成可编译的多文件 LaTeX 工程 |
| 文档格式转换 | [format_converter.md](modules/format_converter.md) | Markdown/DOCX/HTML 与 LaTeX 互转 |

### 项目管理模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 自动编译 | [compile.md](modules/compile.md) | LaTeX 编译、引擎自动检测、PDF/A 支持与错误翻译 |
| Git 差异高亮 | [git_diff.md](modules/git_diff.md) | 版本差异可视化高亮 PDF |
| 参考文献管理 | [bib_manager.md](modules/bib_manager.md) | BibTeX 格式识别、DOI 多数据源查询 |

### 工具模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 联网搜索 | [web_search.md](modules/web_search.md) | 多引擎搜索 + Function Calling |
| 学术研究 | [writing_chat.md](modules/writing_chat.md) | OpenAlex 论文检索、详情、引文追踪与开放获取定位 |
| 项目文件浏览 | 无（自解释） | 4 个 LLM 浏览工具（list_files / read_file_content / get_section_structure / search_across_files），让 LLM 浏览项目结构和文件内容 |
| 段落编辑 | 无（自解释） | 2 个 LLM 编辑工具（edit_paragraph / undo_last_edit），允许 LLM 直接修改 .tex 文件内容 |

---

## 架构概览

### BaseParagraphCheckService 基类

多个逐段检查模块（typo_check / expression_opt / code_review / terminology_check / paragraph_translate）共享 `BaseParagraphCheckService` 基类和 `ApplyChangeItem` 共享模型。子类通过多个 hooks 定制行为。

### CheckOperationContext

每次 preview/check 生成唯一 `operation_id`，关联的上下文（marker_maps、file_md5s、TTL 1 小时）保存在服务实例中。apply 时通过 operation_id 取回上下文，避免并发或重启后 marker 映射丢失。

### 乐观锁

多工作区并发编辑同一文件时，通过 MD5 哈希检测冲突。读取段落时后端返回 `file_md5`，应用修改时前端传回 `expected_md5`，后端校验失败时抛出 `FileConflictError`（HTTP 409）。前端自动刷新 MD5 并提示用户重试。`inject_expected_md5s()` 辅助函数统一为各模块 router 注入预期 MD5。

### Marker 保护策略

- **使用 Marker 保护**：typo_check, expression_opt, terminology_check, code_review, paragraph_translate
- **不使用**：formula_ocr, table_ocr, compile, format_converter, style_modify, git_diff, template_gen

### 工具管理器自注册模式

所有全局工具管理器（`SearchManager` / `ResearchManager` / `ProjectToolsManager` / `EditToolsManager`）遵循相同的**全局单例 + 自注册**模式：

- 静态 `get_tools_schema()` 定义工具 schema，供 LLM Function Calling 注入
- 每个 executor 函数是独立的异步函数，接收 `tool_name` 和 `arguments`
- 管理器首次初始化时自动调用 `ToolRegistry.register()` 注册全部工具
- 配置通过 `features.function_calling.*.enabled` 统一控制开关

### Provider 架构（多数据源抽象）

bib_manager 的 DOI 查询模块和 web_search 模块共享类似的**可插拔 Provider** 架构模式：

- 抽象基类（`BaseSearchEngine` / `BaseMetadataProvider`）定义统一接口
- 多个具体实现各自包装一个外部 API
- 管理器（`SearchManager` / `ProviderManager`）统筹注册、优先级和降级
- 全局单例按配置初始化

两个模块的区别在于：web_search 的 fallback 是自动降级，bib_manager 的 Provider 使用"主索引 + 补全层串行降级"策略。

### LLM 调用模式

| 模式 | 模块 |
| --- | --- |
| 逐段检查 + SSE 流式 | typo_check, expression_opt, terminology_check, code_review, paragraph_translate |
| 多轮对话 | writing_chat |
| 单次请求-响应 | formula_ocr, table_ocr, template_gen, style_modify, compile, git_diff, format_converter |
| 无 LLM | compile（编译本身） |

### 外部依赖

| 依赖 | 模块 |
| --- | --- |
| latexmk + xelatex | compile, git_diff |
| pandoc | format_converter |
| git | git_diff |
| OpenAI 兼容 API | 大部分 LLM 模块 |
| Crossref / DataCite / OpenAlex / Europe PMC / Semantic Scholar API | bib_manager（DOI 查询数据源）与 writing_chat（OpenAlex 学术研究） |
| arXiv API | bib_manager（预印本查询） |
| DuckDuckGo HTML / SearXNG API | web_search（联网搜索） |

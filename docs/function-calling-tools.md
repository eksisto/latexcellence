# LaTeXcellence Function Calling 工具清单

## 全局工具（注册到 ToolRegistry）

由 `backend/core/tool_registry.py` 的 `ToolRegistry` 统一管理，通过 feature toggle 控制启用/禁用。

### 联网搜索

| 属性 | 值 |
|------|-----|
| **名称** | `web_search` / `open_page` / `find_in_page`；条件启用的 `read_page_sections`（仅 `web_search.page_reading.strategy=structured` 时注册） |
| **描述** | `web_search` 搜索互联网获取最新信息（自动注入当前时间上下文）；`open_page` 抓取指定 URL 全文；`find_in_page` 在页面内定位关键词。三者构成 agentic 检索循环（搜索 → 读页 → 再搜索）。`read_page_sections` 按 `open_page` 大纲模式返回的 section ID 读取指定章节，用于长文按节读取。 |
| **参数** | `web_search`: `query` (string, 必填), `max_results` (integer, 默认 10)；`open_page`: `url` (string, 必填), `max_chars` (integer, 默认 10000)；`find_in_page`: `url` + `query` (必填), `context_chars` (默认 600), `max_matches` (默认 5)；`read_page_sections`: `url` + `section_ids` (必填, 1–10 个), `max_chars` (默认 10000, 上限 20000) |
| **定义位置** | `backend/utils/web_search/manager.py` |
| **执行函数** | `_web_search_executor()` / `_open_page_executor()` / `_find_in_page_executor()` / `_read_page_sections_executor()` |
| **配置路径** | `features.function_calling.web_search.enabled`（`read_page_sections` 另受 `page_reading.strategy=structured` 控制） |
| **说明** | 支持查询重写、多角度并行搜索（合并上限 15 条）、引擎降级、Deep Read（默认关闭）、页面按需读取、引用校验与 untrusted 防护 |

---

### 项目浏览工具

| 属性 | 值 |
|------|-----|
| **名称** | `list_files` |
| **描述** | 列出项目文件（glob 模式匹配，默认 `**/*.tex`），返回相对路径。文件超过 200 个时结果截断，末尾标注 `truncated` 状态，需用更精确的 `pattern` 缩小范围 |
| **参数** | `pattern` (string, 可选, 默认 `"**/*.tex"`) |
| **定义位置** | `backend/utils/project_tools/manager.py` |
| **执行函数** | `_list_files_executor()` |
| **配置路径** | `features.function_calling.project_tools.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `read_file_content` |
| **描述** | 读取指定文件内容（可指定行范围），行号从 1 开始。省略 `end_line` 时单次最多读取 200 行，截断时末尾给出下一段 `start_line`。单行超过 500 字符截断并附注说明 |
| **参数** | `file_path` (string, 必填), `start_line` (integer, 可选), `end_line` (integer, 可选) |
| **定义位置** | `backend/utils/project_tools/manager.py` |
| **执行函数** | `_read_file_content_executor()` |
| **配置路径** | `features.function_calling.project_tools.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `get_section_structure` |
| **描述** | 获取文档的章节树（`\chapter`/`\section`/`\subsection` 等），返回层级+标题+所在文件+行号 |
| **参数** | `file_path` (string, 可选) |
| **定义位置** | `backend/utils/project_tools/manager.py` |
| **执行函数** | `_get_section_structure_executor()` |
| **配置路径** | `features.function_calling.project_tools.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `search_across_files` |
| **描述** | 跨项目文件搜索正则表达式模式，用于查找 `\label`、`\cite` 等。匹配总数统计上限 1000，结果截断时明确标注 `truncated` 状态并提示缩小范围或调高 `max_results` |
| **参数** | `pattern` (string, 必填), `file_pattern` (string, 可选), `max_results` (integer, 默认 50, 上限 200) |
| **定义位置** | `backend/utils/project_tools/manager.py` |
| **执行函数** | `_search_across_files_executor()` |
| **配置路径** | `features.function_calling.project_tools.enabled` |

> **工具结果统一协议**：以上项目浏览工具及学术检索工具在结果末尾追加统一状态块
> （`shown` / `total` / `truncated` / `next_step`），见
> `docs/backend-core.md` 的"工具结果统一协议"一节。`truncated=yes` 时模型应
> 依据 `next_step` 缩小范围、继续读取或换用工具。

### 段落编辑工具

| 属性 | 值 |
|------|-----|
| **名称** | `edit_paragraph` |
| **描述** | 修改指定 `.tex` 文件中某段落的内容。直接修改源文件，自动备份，支持撤销。 |
| **参数** | `file_path` (string, 必填), `paragraph_index` (integer, 必填, 0-based), `new_content` (string, 必填) |
| **定义位置** | `backend/utils/project_tools/edit_tools_manager.py` |
| **执行函数** | `_edit_paragraph_executor()` |
| **配置路径** | `features.function_calling.edit_tools.enabled` |
| **说明** | 使用 MD5 乐观锁防并发冲突，走 `ProjectManager.apply_change()` 流程。写作助手中默认需经前端确认弹窗（`edit_tools.require_confirmation`）后才执行。`new_content` 写盘前经 `latex_json_guard.py` 内容校验，JSON 转义损坏的 LaTeX 直接拒绝并提示 LLM 自纠 |

---

| 属性 | 值 |
|------|-----|
| **名称** | `undo_last_edit` |
| **描述** | 撤销对指定文件的最近一次修改。可多次调用回退到更早版本。 |
| **参数** | `file_path` (string, 必填) |
| **定义位置** | `backend/utils/project_tools/edit_tools_manager.py` |
| **执行函数** | `_undo_last_edit_executor()` |
| **配置路径** | `features.function_calling.edit_tools.enabled` |

---

### 学术检索与引文追踪工具

| 属性 | 值 |
|------|-----|
| **名称** | `search_scholarly_works` |
| **描述** | 跨多个学术数据源检索论文，支持按主题、作者、年份、文献类型筛选，返回含 DOI / OpenAlex ID 等稳定标识的结构化记录。结果超过展示上限时末尾标注 `truncated` 状态并提示细化查询或调高 `max_results` |
| **参数** | `query` (string, 必填), `year_from` / `year_to` (integer, 可选), `authors` (string[], 可选), `work_types` (string[], 可选), `sort_by` (enum: `relevance`/`citation_count`/`publication_date`, 默认 `relevance`), `max_results` (integer, 默认 20) |
| **定义位置** | `backend/utils/scholarly_research/manager.py` |
| **执行函数** | `_scholarly_research_executor()` |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |
| **说明** | 联邦搜索：并发查询所有启用的 Provider，结果去重、排序后合并 |

---

| 属性 | 值 |
|------|-----|
| **名称** | `get_work_details` |
| **描述** | 获取某篇论文的完整详细信息：摘要、作者、机构、期刊、DOI、被引数、主题与开放获取信息，支持多数据源 |
| **参数** | `work_id` (string, 必填) |
| **定义位置** | `backend/utils/scholarly_research/manager.py` |
| **执行函数** | `_scholarly_research_executor()` |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `find_related_works` |
| **描述** | 基于种子论文检索相关文献：参考文献（references）、施引文献（cited_by）、相似论文（similar），利用多数据源引文图。结果截断时末尾标注 `truncated` 状态并提示继续检索 |
| **参数** | `work_id` (string, 必填), `relation_type` (enum: `references`/`cited_by`/`similar`, 必填), `max_results` (integer, 默认 20), `sort_by` (enum, 默认 `citation_count`) |
| **定义位置** | `backend/utils/scholarly_research/manager.py` |
| **执行函数** | `_scholarly_research_executor()` |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `get_full_text_or_sections` |
| **描述** | 获取论文的摘要与开放获取（OA）全文位置信息，跨数据源提供最全的 OA 链接 |
| **参数** | `work_id` (string, 必填), `requested_sections` (string[], 可选, 如 `abstract`/`methods`/`results`) |
| **定义位置** | `backend/utils/scholarly_research/manager.py` |
| **执行函数** | `_scholarly_research_executor()` |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |
| **说明** | 目前仅 `abstract` 章节实际可取，其余章节返回不可用提示并注明 OA 位置 |

---

| 属性 | 值 |
|------|-----|
| **名称** | `search_research_data_resources` |
| **描述** | 跨多数据源检索与研究主题相关的数据集与研究数据。结果截断时末尾标注 `truncated` 状态并提示细化查询 |
| **参数** | `query` (string, 必填), `resource_type` (enum: `dataset`, 默认 `dataset`), `max_results` (integer, 默认 10) |
| **定义位置** | `backend/utils/scholarly_research/manager.py` |
| **执行函数** | `_scholarly_research_executor()` |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |

---

## 私有工具

### Agent 文件工具共享层

`backend/utils/agent_file_tools.py` 提供私有 Agent 文件工具的共享 schema 和执行模板。template_gen 与 plot_gen 使用标准名称 `write_file` / `read_file` / `list_files`。style_modify 使用名称映射保留 `stage_replace` / `read_project_file` / `list_project_files`，因此外部工具名与既有 trace 不变。

共享层统一参数校验、事件结构、列表格式、UTF-8 文本读取、输出截断和同步 I/O 的 `asyncio.to_thread()` 调度。模块执行器只实现路径/后缀策略、内容校验和实际存储。局部读取参数统一为 `line_range: [start, end]`，表示 1-based、首尾均包含的行区间。template_gen 和 style_modify 支持该参数，plot_gen 保持整文件读取。

### 模板生成 Agent

定义在 `backend/modules/template_gen/agent/`，由 `TemplateAgentService._build_tools_schema()` 构建，**不注册到全局 ToolRegistry**。这些工具在 session 级沙箱内运行，通过 `_ToolExecutor` 私有类分发。

| # | 名称 | 描述 | 必填参数 | 条件 |
|---|------|------|----------|------|
| 1 | `write_file` | 在沙箱中创建/覆写文件（仅允许 LaTeX 扩展名）；写盘前做 LaTeX 内容校验 | `file_path`, `content` | 始终 |
| 2 | `read_file` | 读取沙箱中已创建的文件内容，可用 `line_range` 局部读取 | `file_path` | 始终 |
| 3 | `list_files` | 列出沙箱中所有文件（与全局同名工具隔离） | 无 | 始终 |
| 4 | `compile_latex` | 用 latexmk 编译项目验证构建 | `main_file` | `agent_compile_enabled=true` |
| 5 | `finish` | 标记项目完成，结束 Agent 循环 | `summary` | 始终 |

---

### 科研绘图 Agent

定义在 `backend/modules/plot_gen/agent.py`，由 `PlotGenAgent._build_tools_schema()` 构建，**不注册到全局 ToolRegistry**。工具在 `data/plots/<session_id>/` 沙箱内运行，通过 `PlotExecutor` 私有类分发。

| # | 名称 | 描述 | 必填参数 | 条件 |
|---|------|------|----------|------|
| 1 | `write_file` | 在绘图沙箱中创建/覆写文件 | `file_path`, `content` | 始终 |
| 2 | `read_file` | 读取沙箱文件 | `file_path` | 始终 |
| 3 | `list_files` | 列出沙箱文件 | 无 | 始终 |
| 4 | `python_sandbox` | 执行绘图脚本并返回 stdout/stderr 和产物 | `script_name` | 始终 |
| 5 | `compile_tikz` | 编译 TikZ/PGFPlots 源文件 | `tikz_file` | `tikz_compile_enabled=true` |
| 6 | `stats_test` | 执行 scipy 统计检验 | `data_file`, `test` | 始终 |
| 7 | `list_styles` | 列出风格预设 | 无 | 始终 |
| 8 | `apply_style` | 切换当前风格 | `style` | 始终 |
| 9 | `finish` | 标记会话完成 | `summary` | 始终 |

---

### style_modify 项目 Agent

定义在 `backend/modules/style_modify/agent.py`，由 `StyleModifyAgent._build_tools_schema()` 构建，**不注册到全局 ToolRegistry**。工具绑定当前 workspace、项目根目录和 session，真实项目只读，候选文件写入会话 overlay。

| # | 名称 | 描述 | 必填参数 | 条件 |
|---|------|------|----------|------|
| 1 | `list_project_files` | 列出项目内允许后缀文件 | 无 | 始终 |
| 2 | `search_project_text` | 在允许文件中搜索文本 | `query` | 始终 |
| 3 | `read_project_file` | 读取文件或 `line_range` 指定的行区间 | `file_path` | 始终 |
| 4 | `stage_replace` | 将完整候选内容写入 overlay | `file_path`, `content` | 始终 |
| 5 | `discard_file_changes` | 丢弃某文件候选修改 | `file_path` | 始终 |
| 6 | `compile_staged_project` | 在 shadow 项目编译候选版本 | 无（可选 `main_file`） | 始终 |
| 7 | `finish` | 生成 proposal 并结束当前 run | `summary` | 始终 |

`stage_replace` 写 overlay 前用 `latex_json_guard.py` 校验内容。`.bib` 文件使用 `bibtex=True`。Agent 不提供创建、删除、重命名文件工具，也不允许执行任意 shell 命令。

### 写作助手专属工具

由 `WritingChatService._get_enabled_tools()` 直接定义 schema 并在工具循环中内联处理，**不注册到全局 ToolRegistry**。仅写作助手模块可用。

| 属性 | 值 |
|------|-----|
| **名称** | `request_clarification` |
| **描述** | 向用户提问以补充信息或做出选择。当关键参数缺失、需求存在歧义、需要执行高风险操作前确认，或多个合理方案会导致明显不同结果时使用。用户回答将作为工具结果返回。 |
| **参数** | `question` (string, 必填), `options` (string[], 可选) |
| **定义位置** | `backend/modules/writing_chat/service/` (`_get_enabled_tools()`) |
| **配置路径** | 始终可用，无条件注入，无配置开关 |
| **说明** | 返回用户回答的原始文本。可选的 `options` 数组让 LLM 提供预设候选项，用户在对话框中选择或自行输入。超时 120 秒无回复则返回提示文本让 LLM 自行判断。 |

---

## 工具组成方式

**写作助手 (writing_chat)**: `_get_enabled_tools()` 无条件注入 `request_clarification` 工具（始终可用），再按配置依次追加联网搜索 + 项目浏览 + 段落编辑 + 学术检索四类工具的 schema，传入 LLM `tools` 参数。其中段落编辑工具（`edit_paragraph` / `undo_last_edit`）默认需经前端确认弹窗（`edit_tools.require_confirmation=true`）后才执行，用户可修改参数或拒绝。

**编辑器助手 (writing_chat/editor)**: 复用同一 ToolRegistry。`_get_editor_tools()`（`editor_chat.py`）按白名单 `_EDITOR_TOOL_NAMES` 过滤三源 schema——项目浏览 + 联网搜索 + 学术检索（与写作助手注册表对齐，白名单扩自阶段五a），**不含**段落编辑工具（编辑器写入一律走缓冲区协议）。联网源受 `web_search.enabled` AND `writing_chat.web_search_enabled` 双开关、学术源受 `scholarly_research.enabled` AND `providers.openalex.enabled` 双开关控制（schema 收集层把关，工具循环执行前按同口径复核）。工具循环另有执行前白名单硬校验：模型幻觉调用越权工具名（含 `edit_paragraph`）或开关已关的联网/学术工具时，拒绝执行并注入失败 tool result。联网/学术九工具在编辑器侧**逐次确认执行**（阶段五b 确认流）：每次调用产生 pending 交互，经前端确认卡放行后执行，拒绝或 TTL 120s 过期时注入取消语义 tool result。pending 先登记后下发事件，confirm/cancel 复用本模块 `confirm-tool` / `cancel-tool` 端点（多用户表驱动跨副本、单用户 Event）。项目只读三件套直通执行。DDG（DuckDuckGo）验证挑战是确认之外唯一的人机交互。

**模板生成 (template_gen)**: `_build_tools_schema()` 私有构建工具列表，通过 `chat_with_tools()` 直接传入，工具数量随 `agent_compile_enabled` 配置变化。

**style_modify (项目 Agent)**: `StyleModifyAgent._build_tools_schema()` 私有构建项目浏览、搜索、读取、候选替换、丢弃、shadow 编译和 finish 工具，通过 `chat_with_tools()` 直接传入。

---

## 未接入 Function Calling 的模块

以下模块使用传统的 `llm.chat()` + `response_format={"type": "json_object"}` JSON 模式，**不注册 Function Calling 工具**：

| 模块 | 交互方式 |
|------|----------|
| **bib_manager**（参考文献管理） | `retry_with_validation` + Pydantic JSON schema，通过 `provider_manager.py` 的 HTTP API 查询 DOI 元数据 |
| **typo_check**（文本校对） | 逐段检查 + marker 保护，通过 `BaseParagraphCheckService` 编排 |
| **expression_opt**（表达优化） | 同上 |
| **code_review**（代码审查） | 同上 |
| **terminology_check**（术语一致性） | 同上 |
| **paragraph_translate**（逐段翻译） | 同上 |
| **formula_ocr** | 图片上传 → LLM Vision（`<<<LATEX>>>` 分隔符协议 + 内容校验，非 JSON） |
| **table_ocr** | 图片上传 → LLM Vision（`<<<LATEX>>>` 分隔符协议 + 内容校验，非 JSON） |
| **style_modify**（样式修改） | 粘贴代码模式使用分隔符协议；项目 Agent 模式使用私有 Function Calling 工具 |
| **format_converter**（格式转换） | Pandoc + LLM JSON |

---

## 配置结构

```python
class FunctionCallingConfig(BaseModel):
    web_search: WebSearchConfig         # features.function_calling.web_search
    project_tools: ProjectToolsConfig   # features.function_calling.project_tools
    edit_tools: EditToolsConfig         # features.function_calling.edit_tools
    scholarly_research: ScholarlyResearchConfig  # features.function_calling.scholarly_research
```

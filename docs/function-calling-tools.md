# LateXcellence Function Calling 工具清单

---

## 全局工具（注册到 ToolRegistry）

由 `backend/core/tool_registry.py` 的 `ToolRegistry` 统一管理，通过 feature toggle 控制启用/禁用。

### 联网搜索

| 属性 | 值 |
|------|-----|
| **名称** | `web_search` |
| **描述** | 搜索互联网获取最新信息。自动注入当前时间上下文辅助 LLM 理解时效性。 |
| **参数** | `query` (string, 必填), `max_results` (integer, 默认 5) |
| **定义位置** | `backend/utils/web_search/manager.py:151` |
| **执行函数** | `_web_search_executor()` (同文件:197) |
| **配置路径** | `features.function_calling.web_search.enabled` |
| **说明** | 支持查询重写、多角度并行搜索、引擎降级、Deep Read 全文抓取 |

### 项目浏览工具

| 属性 | 值 |
|------|-----|
| **名称** | `list_files` |
| **描述** | 列出项目文件（glob 模式匹配，默认 `**/*.tex`），返回相对路径 |
| **参数** | `pattern` (string, 可选, 默认 `"**/*.tex"`) |
| **定义位置** | `backend/utils/project_tools/manager.py:59` |
| **执行函数** | `_list_files_executor()` (同文件:180) |
| **配置路径** | `features.function_calling.project_tools.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `read_file_content` |
| **描述** | 读取指定文件内容（可指定行范围），行号从 1 开始 |
| **参数** | `file_path` (string, 必填), `start_line` (integer, 可选), `end_line` (integer, 可选) |
| **定义位置** | `backend/utils/project_tools/manager.py:84` |
| **执行函数** | `_read_file_content_executor()` (同文件:201) |
| **配置路径** | `features.function_calling.project_tools.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `get_section_structure` |
| **描述** | 获取文档的章节树（`\chapter`/`\section`/`\subsection` 等），返回层级+标题+所在文件+行号 |
| **参数** | `file_path` (string, 可选) |
| **定义位置** | `backend/utils/project_tools/manager.py:116` |
| **执行函数** | `_get_section_structure_executor()` (同文件:248) |
| **配置路径** | `features.function_calling.project_tools.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `search_across_files` |
| **描述** | 跨项目文件搜索正则表达式模式，用于查找 `\label`、`\cite` 等 |
| **参数** | `pattern` (string, 必填), `file_pattern` (string, 可选), `max_results` (integer, 默认 50) |
| **定义位置** | `backend/utils/project_tools/manager.py:140` |
| **执行函数** | `_search_across_files_executor()` (同文件:307) |
| **配置路径** | `features.function_calling.project_tools.enabled` |

### 段落编辑工具

| 属性 | 值 |
|------|-----|
| **名称** | `edit_paragraph` |
| **描述** | 修改指定 `.tex` 文件中某段落的内容。直接修改源文件，自动备份，支持撤销。 |
| **参数** | `file_path` (string, 必填), `paragraph_index` (integer, 必填, 0-based), `new_content` (string, 必填) |
| **定义位置** | `backend/utils/project_tools/edit_tools_manager.py:33` |
| **执行函数** | `_edit_paragraph_executor()` (同文件:103) |
| **配置路径** | `features.function_calling.edit_tools.enabled` |
| **说明** | 使用 MD5 乐观锁防并发冲突，走 `ProjectManager.apply_change()` 流程。写作助手中默认需经前端确认弹窗（`edit_tools.require_confirmation`）后才执行 |

---

| 属性 | 值 |
|------|-----|
| **名称** | `undo_last_edit` |
| **描述** | 撤销对指定文件的最近一次修改。可多次调用回退到更早版本。 |
| **参数** | `file_path` (string, 必填) |
| **定义位置** | `backend/utils/project_tools/edit_tools_manager.py:74` |
| **执行函数** | `_undo_last_edit_executor()` (同文件:158) |
| **配置路径** | `features.function_calling.edit_tools.enabled` |

---

### 学术检索与引文追踪工具

| 属性 | 值 |
|------|-----|
| **名称** | `search_scholarly_works` |
| **描述** | 跨多个学术数据源检索论文，支持按主题、作者、年份、文献类型筛选，返回含 DOI / OpenAlex ID 等稳定标识的结构化记录 |
| **参数** | `query` (string, 必填), `year_from` / `year_to` (integer, 可选), `authors` (string[], 可选), `work_types` (string[], 可选), `sort_by` (enum: `relevance`/`citation_count`/`publication_date`, 默认 `relevance`), `max_results` (integer, 默认 20) |
| **定义位置** | `backend/utils/scholarly_research/manager.py:233` |
| **执行函数** | `_scholarly_research_executor()` (同文件:378) |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |
| **说明** | 联邦搜索：并发查询所有启用的 Provider，结果去重、排序后合并 |

---

| 属性 | 值 |
|------|-----|
| **名称** | `get_work_details` |
| **描述** | 获取某篇论文的完整详细信息：摘要、作者、机构、期刊、DOI、被引数、主题与开放获取信息，支持多数据源 |
| **参数** | `work_id` (string, 必填) |
| **定义位置** | `backend/utils/scholarly_research/manager.py:275` |
| **执行函数** | `_scholarly_research_executor()` (同文件:378) |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `find_related_works` |
| **描述** | 基于种子论文检索相关文献：参考文献（references）、施引文献（cited_by）、相似论文（similar），利用多数据源引文图 |
| **参数** | `work_id` (string, 必填), `relation_type` (enum: `references`/`cited_by`/`similar`, 必填), `max_results` (integer, 默认 20), `sort_by` (enum, 默认 `citation_count`) |
| **定义位置** | `backend/utils/scholarly_research/manager.py:295` |
| **执行函数** | `_scholarly_research_executor()` (同文件:378) |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |

---

| 属性 | 值 |
|------|-----|
| **名称** | `get_full_text_or_sections` |
| **描述** | 获取论文的摘要与开放获取（OA）全文位置信息，跨数据源提供最全的 OA 链接 |
| **参数** | `work_id` (string, 必填), `requested_sections` (string[], 可选, 如 `abstract`/`methods`/`results`) |
| **定义位置** | `backend/utils/scholarly_research/manager.py:329` |
| **执行函数** | `_scholarly_research_executor()` (同文件:378) |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |
| **说明** | 目前仅 `abstract` 章节实际可取，其余章节返回不可用提示并注明 OA 位置 |

---

| 属性 | 值 |
|------|-----|
| **名称** | `search_research_data_resources` |
| **描述** | 检索与研究主题相关的数据集与研究数据，跨多数据源搜索 |
| **参数** | `query` (string, 必填), `resource_type` (enum: `dataset`, 默认 `dataset`), `max_results` (integer, 默认 10) |
| **定义位置** | `backend/utils/scholarly_research/manager.py:356` |
| **执行函数** | `_scholarly_research_executor()` (同文件:378) |
| **配置路径** | `features.function_calling.scholarly_research.enabled` |

---

## 私有工具

### 模板生成 Agent

定义在 `backend/modules/template_gen/agent.py`，由 `TemplateAgentService._build_tools_schema()` 构建，**不注册到全局 ToolRegistry**。这些工具在 session 级沙箱内运行，通过 `_ToolExecutor` 私有类分发。

| # | 名称 | 描述 | 必填参数 | 条件 |
|---|------|------|----------|------|
| 1 | `write_file` | 在沙箱中创建/覆写文件（仅允许 LaTeX 扩展名） | `file_path`, `content` | 始终 |
| 2 | `read_file` | 读取沙箱中已创建的文件内容 | `file_path` | 始终 |
| 3 | `list_files` | 列出沙箱中所有文件（与全局同名工具隔离） | 无 | 始终 |
| 4 | `compile_latex` | 用 latexmk 编译项目验证构建 | `main_file` | `agent_compile_enabled=true` |
| 5 | `finish` | 标记项目完成，结束 Agent 循环 | `summary` | 始终 |

---

### 科研绘图 Agent

定义在 `backend/modules/plot_gen/agent.py`，由 `PlotGenAgent._build_tools_schema()` 构建，**不注册到全局 ToolRegistry**。工具在 `plots/<session_id>/` 沙箱内运行，通过 `PlotExecutor` 私有类分发。

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

### 写作助手专属工具

由 `WritingChatService._get_enabled_tools()` 直接定义 schema 并在工具循环中内联处理，**不注册到全局 ToolRegistry**。仅写作助手模块可用。

| 属性 | 值 |
|------|-----|
| **名称** | `request_clarification` |
| **描述** | 向用户提问以补充信息或做出选择。当关键参数缺失、需求存在歧义、需要执行高风险操作前确认，或多个合理方案会导致明显不同结果时使用。用户回答将作为工具结果返回。 |
| **参数** | `question` (string, 必填), `options` (string[], 可选) |
| **定义位置** | `backend/modules/writing_chat/service.py` (`_get_enabled_tools()`) |
| **配置路径** | 始终可用，无条件注入，无配置开关 |
| **说明** | 返回用户回答的原始文本。可选的 `options` 数组让 LLM 提供预设候选项，用户在对话框中选择或自行输入。超时 120 秒无回复则返回提示文本让 LLM 自行判断。 |

---

## 工具组成方式

**写作助手 (writing_chat)**: `_get_enabled_tools()` 无条件注入 `request_clarification` 工具（始终可用），再按配置依次追加联网搜索 + 项目浏览 + 段落编辑 + 学术检索四类工具的 schema，注入 LLM `tools` 参数。其中段落编辑工具（`edit_paragraph` / `undo_last_edit`）默认需经前端确认弹窗（`edit_tools.require_confirmation=true`）后才执行，用户可修改参数或拒绝。

**模板生成 (template_gen)**: `_build_tools_schema()` 私有构建工具列表，通过 `chat_with_tools()` 直接传入，工具数量根据 `agent_compile_enabled` 配置为 4-5 个。

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
| **style_modify**（样式修改） | LLM JSON 修改建议 |
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

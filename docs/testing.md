# LateXcellence — 测试文档

运行方式：`pytest tests/ -v`

## 运行环境注意：TestClient 与受限沙箱

在禁用 AF_UNIX socket 写入的受限沙箱（如网络受限的 Codex Linux 沙箱）中，`TestClient` 会在首次请求处挂起，属环境限制而非代码问题。解决方式：`~/.codex/config.toml` 开启 `network_access` 等。

## 测试文件一览

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_latex_parser.py` | 注释、verb、环境、数学模式、命令替换、控制符号（`\?`/`\'`/`\~{n}` 等）、`\$` 转义处理（不进入数学匹配）、内联逐字扩展（`\verb*`/`\lstinline`）、往返一致性 |
| `test_marker_numbering_demo.py` | marker 编号机制演示 |
| `test_project_manager.py` | 项目打开、段落定位、原地修改、MD5 校验、撤销、路径穿越防御 |
| `test_json_utils.py` | safe_parse_json 容错解析、retry_with_validation |
| `test_llm_client.py` | chat_stream_with_usage 用量追踪与降级 |
| `test_config_security.py` | 环境变量 API Key 防落盘、手动 Key 持久化、掩码保持 |
| `test_utils/test_path_guard.py` | resolve_project_path 六层校验 |
| `test_utils/test_input_resolver.py` | 递归展开路径穿越检测 |
| `test_utils/test_compile_error_translator.py` | ErrorTranslation 模型、错误提取、LLM 流程、filtered_output |
| `test_utils/test_tex_compile.py` | TexCompileResult 模型、texfot 成功/失败/超时、引擎自动检测、PDF/A 注入 |
| `test_utils/test_latex_json_guard.py` | LaTeX/BibTeX 内容校验：控制字符、垃圾占位符、花括号/$/环境配平、bibtex 模式、宽松模式（relaxed=True 放行 `\t`/`\r`、错误归因） |
| `test_latex_output_adversarial.py` | JSON/LaTeX 静默损坏、Marker 保护、URL/逐字内容、分隔符截断和 BibTeX 字段边界的对抗性回归测试（历史缺口修复后均为正式用例，不再使用 xfail） |
| `test_utils/test_edit_tools_manager.py` | edit_paragraph / undo_last_edit 执行、LaTeX 内容校验拒绝、写回 |
| `test_utils/test_scholarly_research.py` | OpenAlex 元数据映射、摘要重建、去重、查询过滤、引文追踪、OA 定位和工具执行器 |
| `test_utils/test_project_tools_manager.py` | 项目浏览工具（list_files / read_file_content / get_section_structure / search_across_files）执行与路径保护 |
| `test_utils/test_agent_file_tools.py` | template_gen / plot_gen / style_modify 私有文件工具共享执行层与 `line_range` schema 契约 |
| `test_utils/test_web_search_agentic.py` / `test_web_search_tavily.py` | 联网搜索：查询重写、引擎路由、Tavily 请求与结果解析 |
| `test_utils/test_bib_manager_format_rules.py` | 参考文献格式确定性规则语料（格式识别命中与预期） |
| `test_utils/test_latex_package_analyzer.py` / `test_latex_package_rules.py` / `test_latex_package_corpus.py` / `test_latex_package_compile_verification.py` | 宏包规则表：分析器、规则数据完整性、语料查准/查全基线、离线编译验证 |
| `test_api/test_typo_check.py` | 模型校验、LLM mock、marker 保护、写回 |
| `test_api/test_expression_opt.py` | 模型校验、LLM mock、marker 保护、prompt CRUD |
| `test_api/test_terminology_check.py` | 模型校验、LLM mock、聚类逻辑、写回 |
| `test_api/test_template_gen.py` | Agent 循环、沙箱路径校验、写盘前 LaTeX 内容校验、编译错误解析、失败警告、运行追踪（trace.jsonl 读写与 get_session_trace） |
| `test_api/test_formula_ocr.py` | 分隔符协议、内容校验重试、图片校验、预处理、LLM mock |
| `test_api/test_table_ocr.py` | 分隔符协议、内容校验、图片校验、LLM mock |
| `test_api/test_code_review.py` | 模型校验、preview 原始 LaTeX、LLM mock、写回 |
| `test_api/test_compile.py` | 编译成功/失败/超时、LLM 错误翻译、PDF 端点、路径穿越防御 |
| `test_api/test_git_diff.py` | Git revision、差异 PDF 渲染、路径安全校验 |
| `test_api/test_format_converter.py` | 格式转换、pandoc mock |
| `test_api/test_style_modify.py` | 模型校验、LLM mock、样式修改 |
| `test_api/test_style_modify_agent.py` | 项目 Agent：LatexProject 批量事务、session overlay、proposal、应用冲突、撤销、workspace 隔离、路径穿越防御 |
| `test_api/test_writing_chat.py` | 模型、会话管理、发送/编辑/应用、RS2 分隔符协议提取/校验失败重试/CRLF 原文、助手提示词、搜索与学术研究工具事件 |
| `test_api/test_bib_manager.py` | 格式分析、元数据提取、BibTeX 生成、.bib 操作（含撤销/重做覆盖）、路径穿越防御、DOI 查询、多数据源 Provider 降级 |
| `test_api/test_paragraph_translate.py` | 模型校验、LLM mock、marker 保护、翻译写回 |
| `test_api/test_plot_gen.py` | 数据上传、Agent 生成/微调 SSE、沙箱校验、会话文件与 trace |
| `test_api/test_project_browse.py` | 项目目录浏览与路径解析 |
| `test_api/test_latex_formatter.py` | 统一格式化入口（latexindent / 内置兜底） |
| `test_changes_contract.py` / `test_prompts.py` | changes 契约（主字段与 changes 一致性）与提示词模板回归 |

`tests/test_plot_gen/` 另含 plot_gen 单元测试：`test_data_parser.py`、`test_recommender.py`、`test_styles.py`、`test_executor.py`、`test_tikzgen.py`、`test_trace.py`、`test_prompts.py`。

## style_modify Agent 测试要点

`tests/test_api/test_style_modify_agent.py` 覆盖项目 Agent 的事务语义：

- `LatexProject.apply_whole_files()` 成功写入全部文件并更新 MD5，MD5 冲突时快速失败且不覆盖新内容
- `stage_replace` 冻结 baseline、写 overlay，真实项目在应用前保持不变
- `read_project_file` 使用 `line_range: [start, end]`，返回事件不再暴露 `start_line` / `end_line`
- proposal 生成后应用会校验 workspace、项目根目录、revision 和真实文件 MD5
- 多文件操作以 all-or-nothing 语义写回，operation 保存 before/applied 内容
- undo 只恢复仍等于 applied MD5 的文件，不覆盖应用后的其他编辑
- 不同 workspace 不能读取同一 session

Agent 的 LLM 循环测试应 mock `chat_with_tools()`，不发起真实网络请求；shadow 编译测试应 mock 或复用 `run_tex_compile()` 的确定性路径。

## 人工评估工具（非 pytest）

`tests/fixtures/formula_ocr/` 是公式 OCR 的**人工评估语料**（每条公式一个 LaTeX 片段 + `manifest.json`），配套维护者工具位于 `tools/formula_ocr_eval/`：

- `render_corpus.py`：把语料渲染为评估输入图片（需本机 TeX Live）
- `run_eval.py`：逐条调用真实 OCR 管线（含 LLM），把识别结果渲染成图片并生成 HTML 比对报告

该流程涉及真实 LLM 调用且依赖人工判断，**不属于** `pytest tests/` 自动测试范围，也不会在 CI 中运行。使用说明见 `tools/formula_ocr_eval/README.md`。

---

`tests/fixtures/table_ocr/` 是表格 OCR 的**人工评估语料**（每条表格一个完整 LaTeX 浮动体片段 + `manifest.json`），配套维护者工具位于 `tools/table_ocr_eval/`：

- `render_corpus.py`：把语料渲染为评估输入图片（需本机 TeX Live）
- `run_eval.py`：逐条调用真实 OCR 管线（含 LLM），把识别结果渲染成图片并生成 HTML 比对报告

同样依赖人工判断，不属于 `pytest tests/` 自动测试范围。使用说明见 `tools/table_ocr_eval/README.md`。

---

`tests/fixtures/typo_check/` 是错字查找的**人工评估语料**（每条一个 LaTeX 段落片段 + `manifest.json`，预埋期望错字，含干净段落用于误报检测），配套维护者工具位于 `tools/typo_check_eval/`：

- `run_eval.py`：逐条调用真实逐段检查管线（含 LLM），把修正文本还原 marker 后生成原文/修正后对照 HTML 报告

同样依赖人工判断，不属于 `pytest tests/` 自动测试范围。使用说明见 `tools/typo_check_eval/README.md`。

---

`tests/fixtures/expression_opt/` 是表达优化的**人工评估语料**（每条一个 LaTeX 段落片段 + `manifest.json`，预埋期望优化，含已精炼段落用于误报检测），配套维护者工具位于 `tools/expression_opt_eval/`：

- `run_eval.py`：逐条调用真实逐段优化管线（含 LLM），把优化文本还原 marker 后生成原文/优化后对照 HTML 报告

同样依赖人工判断，不属于 `pytest tests/` 自动测试范围。使用说明见 `tools/expression_opt_eval/README.md`。

---

`tools/prompt_comprehension_eval/` 是提示词理解评测工具：对
`backend/core/prompts.py` 中每个 LLM 提示词构造最简探测任务，调用真实
LLM 并自动核验 JSON 格式、marker 保护、分隔符协议、JSON 转义、语言注入
等关键契约，输出 `logs/prompt_comprehension_eval/results.json` 供人工复核。
该流程依赖真实 LLM 调用，不属于 `pytest tests/` 自动测试范围。使用说明见
`tools/prompt_comprehension_eval/README.md`。

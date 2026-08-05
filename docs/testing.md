# LateXcellence — 测试文档

运行方式：`pytest tests/ -v`

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
| `test_api/test_writing_chat.py` | 模型、会话管理、发送/编辑/应用、RS2 分隔符协议提取/校验失败重试/CRLF 原文、助手提示词、搜索与学术研究工具事件 |
| `test_api/test_bib_manager.py` | 格式分析、元数据提取、BibTeX 生成、.bib 操作、路径穿越防御、DOI 查询、多数据源 Provider 降级 |

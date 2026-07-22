# LateXcellence — 测试文档

运行方式：`pytest tests/ -v`

| 测试文件 | 覆盖内容 |
|---|---|
| `test_latex_parser.py` | 注释、verb、环境、数学模式、命令替换、往返一致性 |
| `test_marker_numbering_demo.py` | marker 编号机制演示：用带注释的 LaTeX 段落展示编号范围、连续性分析和按前缀分类统计。运行：`pytest tests/test_marker_numbering_demo.py -v -s`，可修改 `latex_paragraph` 变量测试自定义段落 |
| `test_project_manager.py` | 项目打开、段落定位、原地修改、MD5 校验、撤销、路径穿越防御 |
| `test_json_utils.py` | safe_parse_json 容错解析、retry_with_validation |
| `test_llm_client.py` | chat_stream_with_usage 用量追踪与降级 |
| `test_config_security.py` | 环境变量 API Key 防落盘、手动 Key 持久化、掩码保持 |
| `test_utils/test_path_guard.py` | resolve_project_path 六层校验（空值、绝对路径、..、resolve逃逸、符号链接、扩展名） |
| `test_utils/test_input_resolver.py` | 递归展开路径穿越检测（引用外部文件时拒绝） |
| `test_utils/test_compile_error_translator.py` | ErrorTranslation 模型、extract_key_errors 过滤、translate_compile_errors LLM 流程（Mock） |
| `test_api/test_typo_check.py` | Pydantic 模型、LLM mock、marker 保护、写回 |
| `test_api/test_expression_opt.py` | Pydantic 模型、LLM mock、marker 保护、prompt CRUD |
| `test_api/test_terminology_check.py` | Pydantic 模型、LLM mock、聚类逻辑、去重、写回 |
| `test_api/test_template_gen.py` | Pydantic 模型、LLM mock、模板组装、保存 |
| `test_api/test_formula_ocr.py` | Pydantic 模型、图片校验、预处理、LLM mock |
| `test_api/test_table_ocr.py` | Pydantic 模型、图片校验、LLM mock |
| `test_api/test_formatting.py` | Pydantic 模型、preview 原始 LaTeX、LLM mock、写回 |
| `test_api/test_compile.py` | 编译成功/失败/超时、LLM 错误翻译、PDF 端点、路径穿越防御 |
| `test_api/test_git_diff.py` | Git revision 列表、差异 PDF 渲染、路径安全校验（`_validate_project_path` + `_normalize_include_path`） |
| `test_api/test_markdown_converter.py` | Markdown 转换、pandoc mock |
| `test_api/test_style_modify.py` | Pydantic 模型、LLM mock、样式修改 |
| `test_api/test_writing_chat.py` | writing_chat 模型、会话管理、发送/编辑/应用、助手提示词、web_search 工具结果事件构造 |
| `test_api/test_bib_manager.py` | 参考文献管理、格式分析、元数据提取、BibTeX 生成、.bib 操作、路径穿越防御 |
